# 관심 단지·구역 실거래 일일 브리핑 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 국토부 실거래가 API(체결)와 네이버부동산 호가를 매일 수집해 전일 대비 신규·소멸·가격변동을 대조하고, 카드형 브리핑 이미지를 Slack으로 자동 발송한다.

**Architecture:** 수집 → 저장·대조 → 판정 → 렌더 → 발송의 5단 파이프라인. 각 단계는 독립 실행 가능하고, 네이버 수집이 실패해도 국토부 경로는 끝까지 돈다. "오늘 새로 올라온 건"은 API가 알려주지 않으므로 SQLite에 쌓인 전일 스냅샷과의 대조로만 구한다.

**Tech Stack:** Python 3.11+, requests, playwright, sqlite3(표준), pandas/openpyxl, pyyaml, slack_sdk, jinja2, pytest

**Spec:** `docs/superpowers/specs/2026-09-29-realestate-daily-briefing-design.md`

## Global Constraints

- Python 3.11+ (`X | None` 문법, `datetime.UTC` 사용)
- SQLite는 표준 라이브러리 `sqlite3`만 사용. ORM 금지
- 스크레이퍼 출력의 `부동산상호`, `중개사이름`, `전화1`, `전화2` 네 컬럼은 **DB 적재 전에 버린다.** 원본 xlsx는 읽은 직후 삭제한다
- 네이버 스크레이퍼 호출 시 모듈 상수를 반드시 덮어쓴다: `ONLY_PREV_GT_SALE = False`, `ONLY_WITH_PREV_JEONSE = False`, `DETAIL_WORKERS = 4`, `MAX_COMPLEX_DETAIL = 100`, `MAX_ARTICLE_DETAIL = 1500`
- 국토부는 매일 **최근 3개월치**(당월 포함)를 전부 재조회한다
- 네이버 수집 실패 시 `listings.last_seen`을 갱신하지 않는다 (소멸 오판 방지)
- 네이버 차단 감지 시 재시도하지 않는다. 그날은 포기한다
- 신고가 판정: 같은 단지 + 같은 전용면적, 계약일 기준 최근 365일, 비교 표본 3건 미만이면 판정을 붙이지 않는다
- 실행은 1일 1회 08:00 (Windows 작업 스케줄러)
- `.env`와 `data/`, `briefings/`, `vendor/`는 git에 올리지 않는다 (`.gitignore` 기설정)
- 모든 금액은 DB에 **원 단위 정수**로 저장한다. 국토부 응답은 만원 단위 문자열이다
- 날짜는 DB에 `YYYY-MM-DD` 문자열로 저장한다

## Review Focus

스펙이 요구하지만 어느 태스크의 본 테스트도 자연히 건드리지 않는, 사람을 물 가능성이 높은 입력·실패 다섯 가지. 각 줄의 테스트는 해당 코드를 소유한 태스크에 넣는다.

1. **국토부 API가 HTTP 200으로 에러 본문을 돌려준다** (인증키 미승인, 트래픽 초과). 파싱은 성공하고 거래 0건이 되어 "오늘은 조용합니다"로 둔갑한다 — 수집 실패로 처리해야 한다. → Task 3
2. **같은 날 두 번 실행**. 중복 적재도, 이미 보고한 건의 "신규" 재보고도 없어야 한다. → Task 2
3. **연 경계에서 3개월 창**. 1월 15일에 돌리면 `202601, 202512, 202511`이 나와야 한다. → Task 3
4. **거래금액 문자열이 지저분하다** (`" 11,200"`, 빈 문자열, 전각 공백). 한 건 때문에 배치 전체가 죽으면 안 된다. → Task 3
5. **스크레이퍼가 xlsx를 아예 안 만든다** (수집 0건, 또는 차단). 파일을 못 찾아 크래시하지 말고 빈 결과로 처리해야 한다. → Task 11

---

## File Structure

```
hyun/
  watchlist.yml                     # 감시 대상 (Task 1)
  .env.example                      # 키 자리표 (Task 1)
  requirements.txt                  # (Task 1)
  src/
    config.py                       # .env + watchlist 로딩, 데이터클래스 (Task 1)
    models.py                       # TradeRow, ListingRow, Verdict 등 공용 타입 (Task 1)
    store.py                        # SQLite 스키마, 적재, 대조 (Task 2, 10)
    collect_kor.py                  # 국토부 수집·파싱 (Task 3)
    notify.py                       # Slack 발송 (Task 4, 9)
    run_daily.py                    # 오케스트레이션 (Task 5, 14)
    judge.py                        # 신고가·뱃지·온도계 판정 (Task 6, 7, 13)
    render.py                       # HTML 컨텍스트, PNG (Task 8, 9, 12, 13)
    collect_naver.py                # 스크레이퍼 래퍼 (Task 11)
  templates/briefing.html.j2        # 카드 템플릿 (Task 8, 12, 13)
  tests/
    fixtures/                       # 실제 API 응답, xlsx 샘플
    test_*.py
  docs/api-notes.md                 # 실제 API 응답 기록 (Task 3)
  scripts/register_task.ps1         # 작업 스케줄러 등록 (Task 15)
```

`config.py`와 `models.py`는 스펙 14절 목록에 없지만, watchlist 파싱과 공용 타입이 갈 곳이 필요해 추가한다. 나머지는 스펙 구조 그대로다.

---

## Task 1: 프로젝트 뼈대와 설정 로더

**Files:**
- Create: `requirements.txt`, `.env.example`, `watchlist.yml`, `src/__init__.py`, `src/models.py`, `src/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: 없음 (첫 태스크)
- Produces:
  - `models.Coord(lat: float, lon: float, zoom: int)`
  - `models.Target(name: str, kind: str, lawd_cd: str, dongs: list[str], complex_name: str | None, coord: Coord | None, asset: str | None, region: str)` — `region`은 `"수도권"` 또는 `"지방"`
  - `models.TradeRow(lawd_cd, dong, complex_name, deal_date, area, floor, price, deal_kind, released, released_date, register_date, asset)` — `deal_date`/`released_date`/`register_date`는 `str | None` (`YYYY-MM-DD`), `area: float`, `floor: int | None`, `price: int`(원), `released: bool`
  - `config.load_watchlist(path: Path) -> list[Target]` — 수도권·지방을 한 리스트로 합쳐 `region` 필드로 구분
  - `config.load_env(path: Path = Path(".env")) -> dict[str, str]` — `KOR_API_KEY`, `SLACK_BOT_TOKEN`, `SLACK_CHANNEL`

- [ ] **Step 1: 의존성 파일과 예시 설정 작성**

`requirements.txt`:
```
requests>=2.31
playwright>=1.45
pandas>=2.2
openpyxl>=3.1
pyyaml>=6.0
slack_sdk>=3.27
jinja2>=3.1
pytest>=8.0
```

`.env.example`:
```
KOR_API_KEY=공공데이터포털에서_발급한_디코딩된_인증키
SLACK_BOT_TOKEN=xoxb-...
SLACK_CHANNEL=#부동산브리핑
```

`watchlist.yml` (실제 대상은 사용자가 채운다. 아래는 형식 예시이자 기본값):
```yaml
수도권:
  - 이름: 장위 재개발 일대
    유형: 구역
    lawd_cd: "11290"
    법정동: ["장위동"]
    좌표: {lat: 37.6130, lon: 127.0480, zoom: 17}
    자산: "VL"

지방:
  - 이름: 대전 둔산 크로바
    lawd_cd: "30170"
    법정동: ["둔산동"]
    단지명: "크로바"
```

- [ ] **Step 2: 실패하는 테스트 작성**

`tests/test_config.py`:
```python
from pathlib import Path
import textwrap
import pytest
from src.config import load_watchlist


def write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "watchlist.yml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


def test_수도권_항목을_읽는다(tmp_path):
    p = write(tmp_path, """
        수도권:
          - 이름: 장위 재개발 일대
            유형: 구역
            lawd_cd: "11290"
            법정동: ["장위동"]
            좌표: {lat: 37.6130, lon: 127.0480, zoom: 17}
            자산: "VL"
    """)
    targets = load_watchlist(p)
    assert len(targets) == 1
    t = targets[0]
    assert t.name == "장위 재개발 일대"
    assert t.region == "수도권"
    assert t.lawd_cd == "11290"
    assert t.dongs == ["장위동"]
    assert t.coord.zoom == 17
    assert t.asset == "VL"
    assert t.complex_name is None


def test_지방_항목은_좌표와_자산이_없다(tmp_path):
    p = write(tmp_path, """
        지방:
          - 이름: 대전 둔산 크로바
            lawd_cd: "30170"
            법정동: ["둔산동"]
            단지명: "크로바"
    """)
    t = load_watchlist(p)[0]
    assert t.region == "지방"
    assert t.coord is None
    assert t.asset is None
    assert t.complex_name == "크로바"


def test_빈_파일이면_빈_리스트를_돌려준다(tmp_path):
    p = write(tmp_path, "")
    assert load_watchlist(p) == []


def test_파일이_없으면_빈_리스트를_돌려준다(tmp_path):
    assert load_watchlist(tmp_path / "없음.yml") == []


def test_lawd_cd가_5자리가_아니면_거부한다(tmp_path):
    p = write(tmp_path, """
        수도권:
          - 이름: 잘못된 항목
            lawd_cd: "112901"
            법정동: ["장위동"]
    """)
    with pytest.raises(ValueError, match="lawd_cd"):
        load_watchlist(p)
```

- [ ] **Step 3: 테스트가 실패하는지 확인**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.config'`

- [ ] **Step 4: models.py 작성**

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class Coord:
    lat: float
    lon: float
    zoom: int


@dataclass(frozen=True)
class Target:
    name: str
    kind: str
    lawd_cd: str
    dongs: list[str]
    complex_name: str | None
    coord: Coord | None
    asset: str | None
    region: str


@dataclass(frozen=True)
class TradeRow:
    lawd_cd: str
    dong: str
    complex_name: str
    deal_date: str            # YYYY-MM-DD
    area: float               # 전용면적 m2
    floor: int | None
    price: int                # 원
    deal_kind: str            # 중개거래 / 직거래 / ""
    released: bool            # 해제 여부
    released_date: str | None
    register_date: str | None # 등기일자
    asset: str                # APT / VL
```

- [ ] **Step 5: config.py 작성**

```python
from pathlib import Path
import os
import yaml
from src.models import Coord, Target


def _target(raw: dict, region: str) -> Target:
    lawd_cd = str(raw.get("lawd_cd", "")).strip()
    if len(lawd_cd) != 5 or not lawd_cd.isdigit():
        raise ValueError(f"lawd_cd는 숫자 5자리여야 합니다: {raw.get('이름')} -> {lawd_cd!r}")
    c = raw.get("좌표")
    coord = Coord(float(c["lat"]), float(c["lon"]), int(c["zoom"])) if c else None
    return Target(
        name=str(raw.get("이름", "")).strip(),
        kind=str(raw.get("유형", "단지")).strip(),
        lawd_cd=lawd_cd,
        dongs=[str(d).strip() for d in raw.get("법정동", [])],
        complex_name=(str(raw["단지명"]).strip() if raw.get("단지명") else None),
        coord=coord,
        asset=(str(raw["자산"]).strip() if raw.get("자산") else None),
        region=region,
    )


def load_watchlist(path: Path) -> list[Target]:
    path = Path(path)
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    targets: list[Target] = []
    for region in ("수도권", "지방"):
        for raw in data.get(region) or []:
            targets.append(_target(raw, region))
    return targets


def load_env(path: Path = Path(".env")) -> dict[str, str]:
    env: dict[str, str] = {}
    path = Path(path)
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    for key in ("KOR_API_KEY", "SLACK_BOT_TOKEN", "SLACK_CHANNEL"):
        if key in os.environ:
            env[key] = os.environ[key]
    return env
```

- [ ] **Step 6: 테스트 통과 확인**

Run: `python -m pytest tests/test_config.py -v`
Expected: PASS (5개)

- [ ] **Step 7: 커밋**

```bash
git add requirements.txt .env.example watchlist.yml src/ tests/
git commit -m "feat: 프로젝트 뼈대와 watchlist 설정 로더"
```

---

## Task 2: SQLite 스키마와 거래 대조

**Files:**
- Create: `src/store.py`
- Test: `tests/test_store_trades.py`

**Interfaces:**
- Consumes: `models.TradeRow` (Task 1)
- Produces:
  - `store.init_db(path: Path | str) -> sqlite3.Connection` — 스키마 생성 포함, 멱등
  - `store.trade_hash(row: TradeRow) -> str` — SHA1 hex 40자
  - `store.TradeDiff(new: list[TradeRow], released: list[TradeRow], total: int)`
  - `store.upsert_trades(conn, rows: list[TradeRow], today: str) -> TradeDiff`
  - `store.log_run(conn, stage: str, ok: bool, count: int, secs: float, err: str | None) -> None`
  - `store.recent_trades(conn, complex_name: str, area: float, before: str, days: int) -> list[sqlite3.Row]` — 판정용 조회 (Task 6이 쓴다)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_store_trades.py`:
```python
from dataclasses import replace
import pytest
from src.models import TradeRow
from src.store import init_db, upsert_trades, trade_hash


def t(**kw) -> TradeRow:
    base = dict(
        lawd_cd="11290", dong="장위동", complex_name="래미안장위",
        deal_date="2026-09-14", area=84.93, floor=15, price=1_120_000_000,
        deal_kind="중개거래", released=False, released_date=None,
        register_date=None, asset="APT",
    )
    base.update(kw)
    return TradeRow(**base)


@pytest.fixture
def conn(tmp_path):
    return init_db(tmp_path / "test.db")


def test_처음_들어온_건은_신규다(conn):
    d = upsert_trades(conn, [t()], today="2026-09-29")
    assert len(d.new) == 1
    assert d.total == 1


def test_같은_날_두번_실행해도_중복_신규가_안_나온다(conn):
    upsert_trades(conn, [t()], today="2026-09-29")
    d = upsert_trades(conn, [t()], today="2026-09-29")
    assert d.new == []
    assert d.total == 1
    rows = conn.execute("SELECT COUNT(*) FROM trades").fetchone()
    assert rows[0] == 1


def test_다음날_재조회해도_어제_건은_신규가_아니다(conn):
    upsert_trades(conn, [t()], today="2026-09-29")
    d = upsert_trades(conn, [t()], today="2026-09-30")
    assert d.new == []


def test_층이_다르면_다른_거래다(conn):
    upsert_trades(conn, [t()], today="2026-09-29")
    d = upsert_trades(conn, [t(), t(floor=3)], today="2026-09-29")
    assert len(d.new) == 1
    assert d.new[0].floor == 3


def test_해제여부가_바뀌면_파기로_잡힌다(conn):
    upsert_trades(conn, [t()], today="2026-09-29")
    d = upsert_trades(
        conn,
        [replace(t(), released=True, released_date="2026-09-28")],
        today="2026-09-30",
    )
    assert d.new == []
    assert len(d.released) == 1
    assert d.released[0].released_date == "2026-09-28"


def test_이미_파기된_건은_다시_보고하지_않는다(conn):
    upsert_trades(conn, [t()], today="2026-09-29")
    r = replace(t(), released=True, released_date="2026-09-28")
    upsert_trades(conn, [r], today="2026-09-30")
    d = upsert_trades(conn, [r], today="2026-10-01")
    assert d.released == []


def test_해시는_금액이_다르면_달라진다():
    assert trade_hash(t()) != trade_hash(t(price=1_000_000_000))


def test_init_db는_두번_불러도_안전하다(tmp_path):
    p = tmp_path / "x.db"
    init_db(p)
    init_db(p)
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `python -m pytest tests/test_store_trades.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.store'`

- [ ] **Step 3: store.py 작성**

```python
from dataclasses import dataclass, replace
from pathlib import Path
import hashlib
import sqlite3

from src.models import TradeRow

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    trade_hash    TEXT PRIMARY KEY,
    lawd_cd       TEXT NOT NULL,
    dong          TEXT NOT NULL,
    complex_name  TEXT NOT NULL,
    deal_date     TEXT NOT NULL,
    area          REAL NOT NULL,
    floor         INTEGER,
    price         INTEGER NOT NULL,
    deal_kind     TEXT,
    released      INTEGER NOT NULL DEFAULT 0,
    released_date TEXT,
    register_date TEXT,
    asset         TEXT NOT NULL,
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trades_lookup
    ON trades (complex_name, area, deal_date);
CREATE INDEX IF NOT EXISTS idx_trades_dong
    ON trades (lawd_cd, dong, deal_date);

CREATE TABLE IF NOT EXISTS runs (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ran_at   TEXT NOT NULL,
    stage    TEXT NOT NULL,
    ok       INTEGER NOT NULL,
    count    INTEGER NOT NULL DEFAULT 0,
    secs     REAL NOT NULL DEFAULT 0,
    err      TEXT
);
"""


@dataclass
class TradeDiff:
    new: list[TradeRow]
    released: list[TradeRow]
    total: int


def init_db(path: Path | str) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def trade_hash(row: TradeRow) -> str:
    key = "|".join([
        row.lawd_cd, row.dong, row.complex_name, row.deal_date,
        f"{row.area:.2f}", str(row.floor), str(row.price),
    ])
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


def upsert_trades(conn: sqlite3.Connection, rows: list[TradeRow], today: str) -> TradeDiff:
    new: list[TradeRow] = []
    released: list[TradeRow] = []

    for row in rows:
        h = trade_hash(row)
        prior = conn.execute(
            "SELECT released FROM trades WHERE trade_hash = ?", (h,)
        ).fetchone()

        if prior is None:
            conn.execute(
                """INSERT INTO trades (trade_hash, lawd_cd, dong, complex_name,
                       deal_date, area, floor, price, deal_kind, released,
                       released_date, register_date, asset, first_seen, last_seen)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (h, row.lawd_cd, row.dong, row.complex_name, row.deal_date,
                 row.area, row.floor, row.price, row.deal_kind, int(row.released),
                 row.released_date, row.register_date, row.asset, today, today),
            )
            new.append(row)
            continue

        if row.released and not bool(prior["released"]):
            released.append(row)

        conn.execute(
            """UPDATE trades
                  SET released = ?, released_date = ?, register_date = ?, last_seen = ?
                WHERE trade_hash = ?""",
            (int(row.released), row.released_date, row.register_date, today, h),
        )

    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    return TradeDiff(new=new, released=released, total=total)


def recent_trades(conn, complex_name: str, area: float, before: str, days: int) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT price, floor, deal_date FROM trades
            WHERE complex_name = ?
              AND ROUND(area, 2) = ROUND(?, 2)
              AND deal_date < ?
              AND deal_date >= DATE(?, ?)
              AND released = 0
            ORDER BY price DESC""",
        (complex_name, area, before, before, f"-{days} days"),
    ).fetchall()


def log_run(conn, stage: str, ok: bool, count: int, secs: float, err: str | None) -> None:
    conn.execute(
        "INSERT INTO runs (ran_at, stage, ok, count, secs, err) VALUES (datetime('now','localtime'),?,?,?,?,?)",
        (stage, int(ok), count, secs, err),
    )
    conn.commit()
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_store_trades.py -v`
Expected: PASS (8개)

- [ ] **Step 5: 커밋**

```bash
git add src/store.py tests/test_store_trades.py
git commit -m "feat: SQLite 스키마와 거래 대조 로직 (멱등 재실행, 파기 감지)"
```

---

## Task 3: 국토부 실거래가 수집

**Files:**
- Create: `src/collect_kor.py`, `docs/api-notes.md`, `tests/fixtures/kor_apt_sample.xml`, `tests/fixtures/kor_error.xml`
- Test: `tests/test_collect_kor.py`

**Interfaces:**
- Consumes: `models.TradeRow` (Task 1)
- Produces:
  - `collect_kor.recent_deal_ymds(today: date, months: int = 3) -> list[str]` — `["202609","202608","202607"]`
  - `collect_kor.parse_money_manwon(s: str) -> int` — 만원 문자열 → 원 정수. 실패 시 `ValueError`
  - `collect_kor.parse_trades(xml_text: str, lawd_cd: str, asset: str) -> list[TradeRow]` — 에러 본문이면 `KorApiError`
  - `collect_kor.fetch_trades(api_key, lawd_cd, deal_ymd, asset, session=None) -> list[TradeRow]`
  - `collect_kor.collect_all(api_key, targets: list[Target], today: date) -> list[TradeRow]`
  - `collect_kor.KorApiError(Exception)`

- [ ] **Step 1: 실제 API를 한 번 호출해 응답을 고정한다**

공공데이터포털에서 발급한 키를 `.env`에 넣은 뒤, 아파트 매매와 연립다세대 매매를 각각 1회 호출해 원문을 저장한다. **이 단계는 사람이 수행하며, 필드명을 눈으로 확인하는 것이 목적이다.**

```bash
python - <<'PY'
from pathlib import Path
from src.config import load_env
import requests
key = load_env()["KOR_API_KEY"]
url = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"
r = requests.get(url, params={"serviceKey": key, "LAWD_CD": "11290",
                              "DEAL_YMD": "202609", "numOfRows": 20}, timeout=30)
Path("tests/fixtures/kor_apt_sample.xml").write_text(r.text, encoding="utf-8")
print(r.status_code, r.text[:800])
PY
```

응답에서 확인할 것: 성공 여부 코드가 담긴 태그(`resultCode`), 항목 컨테이너 태그(`item`), 그리고 `거래금액 / 전용면적 / 층 / 년 / 월 / 일 / 아파트 / 법정동 / 해제여부 / 해제사유발생일 / 거래유형 / 등기일자`에 해당하는 실제 태그명.

**확인한 태그명과 응답 샘플을 `docs/api-notes.md`에 적는다.** 연립다세대(`RTMSDataSvcRHTrade`)도 같은 방식으로 한 번 호출해 단지명 태그가 `아파트`가 아니라 `연립다세대`임을 확인하고 기록한다. 이후 Step 4의 `FIELD_MAP`을 기록한 태그명에 맞춘다.

인증키가 미승인이면 200 응답에 에러 본문이 온다. 그 본문도 `tests/fixtures/kor_error.xml`로 저장한다. 즉시 승인된 경우라면 아래 내용으로 직접 만든다:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<OpenAPI_ServiceResponse>
  <cmmMsgHeader>
    <returnAuthMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</returnAuthMsg>
    <returnReasonCode>30</returnReasonCode>
  </cmmMsgHeader>
</OpenAPI_ServiceResponse>
```

- [ ] **Step 2: 실패하는 테스트 작성**

`tests/test_collect_kor.py`:
```python
from datetime import date
from pathlib import Path
import pytest
from src.collect_kor import (
    KorApiError, parse_money_manwon, parse_trades, recent_deal_ymds,
)

FIX = Path(__file__).parent / "fixtures"


def test_3개월_창을_만든다():
    assert recent_deal_ymds(date(2026, 9, 29)) == ["202609", "202608", "202607"]


def test_연_경계를_넘어간다():
    assert recent_deal_ymds(date(2026, 1, 15)) == ["202601", "202512", "202511"]


def test_2월_경계():
    assert recent_deal_ymds(date(2026, 3, 1)) == ["202603", "202602", "202601"]


@pytest.mark.parametrize("raw,expected", [
    ("11,200", 112_000_000),
    (" 11,200 ", 112_000_000),
    ("112000", 1_120_000_000),
    ("3,800", 38_000_000),
])
def test_만원_문자열을_원으로_바꾼다(raw, expected):
    assert parse_money_manwon(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", None, "-", "abc"])
def test_금액이_지저분하면_ValueError(raw):
    with pytest.raises(ValueError):
        parse_money_manwon(raw)


def test_에러_본문은_거래0건이_아니라_예외다():
    xml = (FIX / "kor_error.xml").read_text(encoding="utf-8")
    with pytest.raises(KorApiError):
        parse_trades(xml, lawd_cd="11290", asset="APT")


def test_정상_응답을_TradeRow로_바꾼다():
    xml = (FIX / "kor_apt_sample.xml").read_text(encoding="utf-8")
    rows = parse_trades(xml, lawd_cd="11290", asset="APT")
    assert rows, "샘플에 거래가 1건 이상 있어야 한다"
    r = rows[0]
    assert r.lawd_cd == "11290"
    assert r.asset == "APT"
    assert r.price > 0
    assert r.area > 0
    assert len(r.deal_date) == 10 and r.deal_date[4] == "-"
    assert isinstance(r.released, bool)


def test_금액이_깨진_항목은_건너뛰고_나머지는_살린다():
    xml = """<response><header><resultCode>00</resultCode></header><body><items>
      <item><거래금액> </거래금액><전용면적>84.93</전용면적><층>15</층>
        <년>2026</년><월>9</월><일>14</일><아파트>깨진건</아파트><법정동>장위동</법정동></item>
      <item><거래금액>11,200</거래금액><전용면적>84.93</전용면적><층>15</층>
        <년>2026</년><월>9</월><일>14</일><아파트>래미안장위</아파트><법정동>장위동</법정동></item>
    </items></body></response>"""
    rows = parse_trades(xml, lawd_cd="11290", asset="APT")
    assert len(rows) == 1
    assert rows[0].complex_name == "래미안장위"
```

- [ ] **Step 3: 테스트가 실패하는지 확인**

Run: `python -m pytest tests/test_collect_kor.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.collect_kor'`

- [ ] **Step 4: collect_kor.py 작성**

`FIELD_MAP`의 태그명은 Step 1에서 `docs/api-notes.md`에 기록한 실제 태그로 맞춘다.

```python
from datetime import date
from xml.etree import ElementTree as ET
import logging
import time

import requests

from src.models import Target, TradeRow

log = logging.getLogger(__name__)

ENDPOINTS = {
    "APT": ("https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev"
            "/getRTMSDataSvcAptTradeDev", "아파트"),
    "VL":  ("https://apis.data.go.kr/1613000/RTMSDataSvcRHTrade"
            "/getRTMSDataSvcRHTrade", "연립다세대"),
}


class KorApiError(Exception):
    pass


def recent_deal_ymds(today: date, months: int = 3) -> list[str]:
    out, y, m = [], today.year, today.month
    for _ in range(months):
        out.append(f"{y:04d}{m:02d}")
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return out


def parse_money_manwon(s: str | None) -> int:
    if s is None:
        raise ValueError("거래금액이 없습니다")
    cleaned = str(s).replace(",", "").replace("　", "").strip()
    if not cleaned.isdigit():
        raise ValueError(f"거래금액을 읽을 수 없습니다: {s!r}")
    return int(cleaned) * 10_000


def _text(item: ET.Element, *names: str) -> str | None:
    for n in names:
        el = item.find(n)
        if el is not None and el.text is not None and el.text.strip():
            return el.text.strip()
    return None


def parse_trades(xml_text: str, lawd_cd: str, asset: str) -> list[TradeRow]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise KorApiError(f"XML 파싱 실패: {e}") from e

    if root.find(".//cmmMsgHeader") is not None:
        msg = _text(root, ".//returnAuthMsg") or "알 수 없는 인증 오류"
        raise KorApiError(f"국토부 API 오류: {msg}")

    code = _text(root, ".//resultCode")
    if code is not None and code not in ("00", "0"):
        msg = _text(root, ".//resultMsg") or ""
        raise KorApiError(f"국토부 API 오류 {code}: {msg}")

    name_tag = ENDPOINTS[asset][1]
    rows: list[TradeRow] = []
    for item in root.findall(".//item"):
        try:
            price = parse_money_manwon(_text(item, "거래금액"))
            y = int(_text(item, "년"))
            m = int(_text(item, "월"))
            d = int(_text(item, "일"))
            area = float(_text(item, "전용면적"))
        except (ValueError, TypeError) as e:
            log.warning("항목 건너뜀 (%s): %s", e, ET.tostring(item, encoding="unicode")[:200])
            continue

        floor_raw = _text(item, "층")
        rel_date = _text(item, "해제사유발생일")
        rows.append(TradeRow(
            lawd_cd=lawd_cd,
            dong=_text(item, "법정동") or "",
            complex_name=_text(item, name_tag, "아파트", "연립다세대") or "",
            deal_date=f"{y:04d}-{m:02d}-{d:02d}",
            area=area,
            floor=int(floor_raw) if floor_raw and floor_raw.lstrip("-").isdigit() else None,
            price=price,
            deal_kind=_text(item, "거래유형") or "",
            released=bool((_text(item, "해제여부") or "").upper() == "O"),
            released_date=_norm_date(rel_date),
            register_date=_norm_date(_text(item, "등기일자")),
            asset=asset,
        ))
    return rows


def _norm_date(s: str | None) -> str | None:
    """'26.09.28' 또는 '20260928' 형태를 YYYY-MM-DD로."""
    if not s:
        return None
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) == 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:]}"
    if len(digits) == 6:
        return f"20{digits[:2]}-{digits[2:4]}-{digits[4:]}"
    return None


def fetch_trades(api_key: str, lawd_cd: str, deal_ymd: str, asset: str,
                 session: requests.Session | None = None) -> list[TradeRow]:
    url = ENDPOINTS[asset][0]
    sess = session or requests.Session()
    last: Exception | None = None
    for attempt in range(3):
        try:
            r = sess.get(url, params={
                "serviceKey": api_key, "LAWD_CD": lawd_cd,
                "DEAL_YMD": deal_ymd, "numOfRows": 1000, "pageNo": 1,
            }, timeout=30)
            r.raise_for_status()
            return parse_trades(r.text, lawd_cd, asset)
        except (requests.RequestException, KorApiError) as e:
            last = e
            log.warning("국토부 호출 실패 (%s/%s %s %s): %s",
                        attempt + 1, 3, lawd_cd, deal_ymd, e)
            time.sleep(2 ** attempt)
    raise KorApiError(f"{lawd_cd} {deal_ymd} {asset} 3회 실패") from last


def collect_all(api_key: str, targets: list[Target], today: date) -> list[TradeRow]:
    ymds = recent_deal_ymds(today)
    lawd_cds = sorted({t.lawd_cd for t in targets})
    sess = requests.Session()
    rows: list[TradeRow] = []
    for lawd_cd in lawd_cds:
        for asset in ("APT", "VL"):
            for ymd in ymds:
                rows.extend(fetch_trades(api_key, lawd_cd, ymd, asset, sess))
    return rows
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `python -m pytest tests/test_collect_kor.py -v`
Expected: PASS

샘플 XML의 태그명이 달라 `test_정상_응답을_TradeRow로_바꾼다`가 실패하면, **테스트를 고치지 말고** `_text()` 호출의 태그명을 `docs/api-notes.md`에 기록한 실제 이름으로 맞춘다.

- [ ] **Step 6: 실제 수집을 한 번 돌려본다**

```bash
python -c "from datetime import date; from src.config import load_env, load_watchlist; from src.collect_kor import collect_all; from pathlib import Path; rows=collect_all(load_env()['KOR_API_KEY'], load_watchlist(Path('watchlist.yml')), date.today()); print(len(rows), rows[:2])"
```
Expected: 거래 건수와 샘플 2건이 찍힌다

- [ ] **Step 7: 커밋**

```bash
git add src/collect_kor.py tests/ docs/api-notes.md
git commit -m "feat: 국토부 실거래가 수집 (에러 본문 구분, 3개월 창, 금액 파싱)"
```

---

## Task 4: Slack 텍스트 발송

**Files:**
- Create: `src/notify.py`
- Test: `tests/test_notify.py`

**Interfaces:**
- Consumes: `store.TradeDiff` (Task 2)
- Produces:
  - `notify.Summary(headline: str, counts: dict[str, int], lines: list[str], warnings: list[str])`
  - `notify.build_blocks(summary: Summary, target_date: str) -> list[dict]`
  - `notify.post(token: str, channel: str, blocks: list[dict], text: str, png_path: Path | None = None) -> None`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_notify.py`:
```python
import json
from src.notify import Summary, build_blocks


def s(**kw) -> Summary:
    base = dict(headline="장위동 신규 신고 7건", counts={"신규신고": 7, "계약파기": 1},
                lines=["래미안장위 84.9 11억2000 ▲신고가"], warnings=[])
    base.update(kw)
    return Summary(**base)


def test_헤더에_날짜가_들어간다():
    blocks = build_blocks(s(), "2026-09-29")
    assert "2026-09-29" in json.dumps(blocks, ensure_ascii=False)


def test_카운트가_모두_0이면_조용하다고_표시한다():
    blocks = build_blocks(s(counts={"신규신고": 0, "계약파기": 0}, lines=[]), "2026-09-29")
    assert "조용" in json.dumps(blocks, ensure_ascii=False)


def test_경고가_있으면_블록에_들어간다():
    blocks = build_blocks(s(warnings=["호가 미수집 — 네이버 차단"]), "2026-09-29")
    assert "호가 미수집" in json.dumps(blocks, ensure_ascii=False)


def test_블록이_슬랙_제한인_50개를_넘지_않는다():
    blocks = build_blocks(s(lines=[f"줄 {i}" for i in range(500)]), "2026-09-29")
    assert len(blocks) <= 50


def test_모든_블록에_type_키가_있다():
    for b in build_blocks(s(), "2026-09-29"):
        assert "type" in b
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `python -m pytest tests/test_notify.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.notify'`

- [ ] **Step 3: notify.py 작성**

```python
from dataclasses import dataclass, field
from pathlib import Path
import logging

log = logging.getLogger(__name__)

MAX_BLOCKS = 50
MAX_LINES = 30


@dataclass
class Summary:
    headline: str
    counts: dict[str, int]
    lines: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def build_blocks(summary: Summary, target_date: str) -> list[dict]:
    blocks: list[dict] = [{
        "type": "header",
        "text": {"type": "plain_text", "text": f"부동산 브리핑 {target_date}", "emoji": True},
    }]

    for w in summary.warnings:
        blocks.append({"type": "context",
                       "elements": [{"type": "mrkdwn", "text": f":warning: {w}"}]})

    if not any(summary.counts.values()):
        blocks.append({"type": "section",
                       "text": {"type": "mrkdwn", "text": "오늘은 조용합니다. 새로 올라온 건이 없습니다."}})
        return blocks[:MAX_BLOCKS]

    blocks.append({"type": "section",
                   "text": {"type": "mrkdwn", "text": f"*{summary.headline}*"}})
    blocks.append({"type": "section", "fields": [
        {"type": "mrkdwn", "text": f"*{k}*\n{v}"} for k, v in list(summary.counts.items())[:10]
    ]})

    shown = summary.lines[:MAX_LINES]
    if shown:
        blocks.append({"type": "divider"})
        blocks.append({"type": "section",
                       "text": {"type": "mrkdwn", "text": "\n".join(shown)}})
    if len(summary.lines) > MAX_LINES:
        blocks.append({"type": "context", "elements": [
            {"type": "mrkdwn", "text": f"외 {len(summary.lines) - MAX_LINES}건은 이미지 참조"}]})

    return blocks[:MAX_BLOCKS]


def post(token: str, channel: str, blocks: list[dict], text: str,
         png_path: Path | None = None) -> None:
    from slack_sdk import WebClient

    client = WebClient(token=token)
    client.chat_postMessage(channel=channel, blocks=blocks, text=text)
    if png_path and Path(png_path).exists():
        client.files_upload_v2(channel=channel, file=str(png_path),
                               title=text, initial_comment=None)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_notify.py -v`
Expected: PASS (5개)

- [ ] **Step 5: 실제 Slack 발송을 한 번 확인**

`.env`에 `SLACK_BOT_TOKEN`, `SLACK_CHANNEL`을 넣고 채널에 봇을 초대한 뒤:
```bash
python -c "from src.config import load_env; from src.notify import Summary, build_blocks, post; e=load_env(); post(e['SLACK_BOT_TOKEN'], e['SLACK_CHANNEL'], build_blocks(Summary('연결 확인', {'신규신고':1}, ['테스트'], []), '2026-09-29'), '연결 확인')"
```
Expected: Slack 채널에 메시지가 도착한다

- [ ] **Step 6: 커밋**

```bash
git add src/notify.py tests/test_notify.py
git commit -m "feat: Slack Block Kit 발송"
```

---

## Task 5: 일일 오케스트레이션 — 1주차 완료

**Files:**
- Create: `src/run_daily.py`
- Test: `tests/test_run_daily.py`

**Interfaces:**
- Consumes: `config.load_env`, `config.load_watchlist` (T1), `store.init_db/upsert_trades/log_run` (T2), `collect_kor.collect_all` (T3), `notify.Summary/build_blocks/post` (T4)
- Produces:
  - `run_daily.run(today: date, db_path: Path, watchlist_path: Path, env: dict, dry_run: bool = False) -> Summary`
  - `run_daily.main(argv: list[str] | None = None) -> int`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_run_daily.py`:
```python
from datetime import date
from pathlib import Path
import textwrap
import pytest
from src.collect_kor import KorApiError
from src.models import TradeRow
from src.run_daily import run
from src.store import init_db


@pytest.fixture
def wl(tmp_path) -> Path:
    p = tmp_path / "watchlist.yml"
    p.write_text(textwrap.dedent("""
        수도권:
          - 이름: 장위 재개발 일대
            유형: 구역
            lawd_cd: "11290"
            법정동: ["장위동"]
            좌표: {lat: 37.6130, lon: 127.0480, zoom: 17}
            자산: "VL"
    """), encoding="utf-8")
    return p


def a_trade(**kw) -> TradeRow:
    base = dict(lawd_cd="11290", dong="장위동", complex_name="래미안장위",
                deal_date="2026-09-14", area=84.93, floor=15, price=1_120_000_000,
                deal_kind="중개거래", released=False, released_date=None,
                register_date=None, asset="APT")
    base.update(kw)
    return TradeRow(**base)


def test_신규건이_요약에_들어간다(tmp_path, wl, monkeypatch):
    monkeypatch.setattr("src.run_daily.collect_all", lambda *a, **k: [a_trade()])
    s = run(date(2026, 9, 29), tmp_path / "d.db", wl, {"KOR_API_KEY": "x"}, dry_run=True)
    assert s.counts["신규신고"] == 1
    assert s.warnings == []


def test_국토부_실패해도_브리핑은_나간다(tmp_path, wl, monkeypatch):
    def boom(*a, **k):
        raise KorApiError("인증키 미승인")
    monkeypatch.setattr("src.run_daily.collect_all", boom)
    s = run(date(2026, 9, 29), tmp_path / "d.db", wl, {"KOR_API_KEY": "x"}, dry_run=True)
    assert any("국토부" in w for w in s.warnings)
    assert s.counts["신규신고"] == 0


def test_감시대상이_없으면_그렇게_말한다(tmp_path, monkeypatch):
    empty = tmp_path / "empty.yml"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setattr("src.run_daily.collect_all", lambda *a, **k: [])
    s = run(date(2026, 9, 29), tmp_path / "d.db", empty, {"KOR_API_KEY": "x"}, dry_run=True)
    assert "감시 대상" in s.headline


def test_실행이_runs에_기록된다(tmp_path, wl, monkeypatch):
    monkeypatch.setattr("src.run_daily.collect_all", lambda *a, **k: [a_trade()])
    db = tmp_path / "d.db"
    run(date(2026, 9, 29), db, wl, {"KOR_API_KEY": "x"}, dry_run=True)
    conn = init_db(db)
    rows = conn.execute("SELECT stage, ok FROM runs").fetchall()
    assert ("kor_api", 1) in [(r["stage"], r["ok"]) for r in rows]


def test_같은_날_두번_돌리면_두번째는_신규가_0(tmp_path, wl, monkeypatch):
    monkeypatch.setattr("src.run_daily.collect_all", lambda *a, **k: [a_trade()])
    db = tmp_path / "d.db"
    run(date(2026, 9, 29), db, wl, {"KOR_API_KEY": "x"}, dry_run=True)
    s = run(date(2026, 9, 29), db, wl, {"KOR_API_KEY": "x"}, dry_run=True)
    assert s.counts["신규신고"] == 0
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `python -m pytest tests/test_run_daily.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.run_daily'`

- [ ] **Step 3: run_daily.py 작성**

```python
from datetime import date
from pathlib import Path
import argparse
import logging
import sys
import time

from src.collect_kor import KorApiError, collect_all
from src.config import load_env, load_watchlist
from src.notify import Summary, build_blocks, post
from src.store import init_db, log_run, upsert_trades

log = logging.getLogger(__name__)


def _money(won: int) -> str:
    억, 나머지 = divmod(won, 100_000_000)
    만 = 나머지 // 10_000
    return f"{억}억 {만:,}" if 만 else f"{억}억"


def run(today: date, db_path: Path, watchlist_path: Path,
        env: dict, dry_run: bool = False) -> Summary:
    today_s = today.isoformat()
    conn = init_db(db_path)
    targets = load_watchlist(watchlist_path)
    warnings: list[str] = []

    new_trades, released = [], []
    t0 = time.monotonic()
    if targets:
        try:
            rows = collect_all(env.get("KOR_API_KEY", ""), targets, today)
            diff = upsert_trades(conn, rows, today_s)
            new_trades, released = diff.new, diff.released
            log_run(conn, "kor_api", True, len(rows), time.monotonic() - t0, None)
        except KorApiError as e:
            warnings.append(f"국토부 수집 실패 — {e}")
            log_run(conn, "kor_api", False, 0, time.monotonic() - t0, str(e))

    counts = {"신규신고": len(new_trades), "계약파기": len(released)}

    if not targets:
        headline = "감시 대상이 없습니다. watchlist.yml을 채워주세요."
    elif new_trades:
        top = max(new_trades, key=lambda r: r.price)
        headline = f"{top.dong} 신규 신고 {len(new_trades)}건, 최고 {top.complex_name} {_money(top.price)}"
    else:
        headline = "신규 신고 없음"

    lines = [
        f"• {r.complex_name} 전용 {r.area:.1f} {r.floor}층 — {_money(r.price)} (계약 {r.deal_date})"
        for r in sorted(new_trades, key=lambda r: r.price, reverse=True)
    ]
    lines += [f"• :x: 파기 — {r.complex_name} {_money(r.price)} ({r.released_date})" for r in released]

    summary = Summary(headline=headline, counts=counts, lines=lines, warnings=warnings)

    if not dry_run:
        t1 = time.monotonic()
        try:
            post(env["SLACK_BOT_TOKEN"], env["SLACK_CHANNEL"],
                 build_blocks(summary, today_s), headline)
            log_run(conn, "slack", True, len(lines), time.monotonic() - t1, None)
        except Exception as e:
            log.exception("Slack 발송 실패")
            log_run(conn, "slack", False, 0, time.monotonic() - t1, str(e))

    return summary


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--db", default="data/briefing.db")
    ap.add_argument("--watchlist", default="watchlist.yml")
    ap.add_argument("--dry-run", action="store_true")
    ns = ap.parse_args(argv)

    s = run(date.fromisoformat(ns.date), Path(ns.db), Path(ns.watchlist),
            load_env(), dry_run=ns.dry_run)
    print(s.headline)
    for line in s.lines:
        print(line)
    for w in s.warnings:
        print("경고:", w)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/ -v`
Expected: PASS (전체)

- [ ] **Step 5: 실제로 한 번 돌린다**

```bash
python -m src.run_daily --dry-run
python -m src.run_daily
```
Expected: 첫 명령은 콘솔에 요약 출력, 두 번째는 Slack에 실제 메시지 도착

- [ ] **Step 6: 커밋**

```bash
git add src/run_daily.py tests/test_run_daily.py
git commit -m "feat: 일일 오케스트레이션 — 첫 Slack 브리핑 동작 (1주차 완료)"
```

---

## Task 6: 신고가·하락 판정

**Files:**
- Create: `src/judge.py`
- Modify: `src/run_daily.py` (판정 결과를 `lines`에 반영)
- Test: `tests/test_judge_price.py`

**Interfaces:**
- Consumes: `store.recent_trades` (T2), `models.TradeRow` (T1)
- Produces:
  - `judge.Verdict(kind: str, pct: float | None, prev_high: int | None, sample: int)` — `kind`는 `"신고가" | "하락" | "보합" | "표본부족"`
  - `judge.judge_price(conn, trade: TradeRow, days: int = 365) -> Verdict`
  - `judge.MIN_SAMPLE = 3`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_judge_price.py`:
```python
import pytest
from src.judge import judge_price
from src.models import TradeRow
from src.store import init_db, upsert_trades


def t(price: int, deal_date: str, area: float = 84.93, floor: int = 15,
      complex_name: str = "래미안장위") -> TradeRow:
    return TradeRow(lawd_cd="11290", dong="장위동", complex_name=complex_name,
                    deal_date=deal_date, area=area, floor=floor, price=price,
                    deal_kind="중개거래", released=False, released_date=None,
                    register_date=None, asset="APT")


@pytest.fixture
def conn(tmp_path):
    return init_db(tmp_path / "j.db")


def seed(conn, rows):
    upsert_trades(conn, rows, today="2026-09-29")


def test_표본이_3건_미만이면_판정하지_않는다(conn):
    seed(conn, [t(1_000_000_000, "2026-05-01"), t(1_050_000_000, "2026-06-01")])
    v = judge_price(conn, t(1_120_000_000, "2026-09-14"))
    assert v.kind == "표본부족"
    assert v.sample == 2


def test_1년내_최고가를_넘으면_신고가(conn):
    seed(conn, [t(1_000_000_000, "2026-05-01"), t(1_050_000_000, "2026-06-01"),
                t(1_080_000_000, "2026-07-01")])
    v = judge_price(conn, t(1_120_000_000, "2026-09-14"))
    assert v.kind == "신고가"
    assert v.prev_high == 1_080_000_000
    assert v.sample == 3


def test_최고가보다_낮으면_하락과_비율(conn):
    seed(conn, [t(1_000_000_000, "2026-05-01"), t(1_050_000_000, "2026-06-01"),
                t(1_000_000_000, "2026-07-01")])
    v = judge_price(conn, t(945_000_000, "2026-09-14"))
    assert v.kind == "하락"
    assert v.pct == pytest.approx(-10.0, abs=0.1)


def test_1년보다_오래된_거래는_표본에서_빠진다(conn):
    seed(conn, [t(2_000_000_000, "2025-08-01"),   # 1년 밖
                t(1_000_000_000, "2026-05-01"), t(1_050_000_000, "2026-06-01"),
                t(1_080_000_000, "2026-07-01")])
    v = judge_price(conn, t(1_120_000_000, "2026-09-14"))
    assert v.kind == "신고가"
    assert v.prev_high == 1_080_000_000


def test_전용면적이_다르면_표본이_아니다(conn):
    seed(conn, [t(2_000_000_000, "2026-05-01", area=114.0),
                t(2_100_000_000, "2026-06-01", area=114.0),
                t(2_200_000_000, "2026-07-01", area=114.0)])
    v = judge_price(conn, t(1_120_000_000, "2026-09-14", area=84.93))
    assert v.kind == "표본부족"


def test_단지가_다르면_표본이_아니다(conn):
    seed(conn, [t(2_000_000_000, "2026-05-01", complex_name="다른단지"),
                t(2_100_000_000, "2026-06-01", complex_name="다른단지"),
                t(2_200_000_000, "2026-07-01", complex_name="다른단지")])
    v = judge_price(conn, t(1_120_000_000, "2026-09-14"))
    assert v.kind == "표본부족"


def test_해제된_거래는_표본에서_빠진다(conn):
    rows = [t(1_000_000_000, "2026-05-01"), t(1_050_000_000, "2026-06-01"),
            t(1_080_000_000, "2026-07-01")]
    seed(conn, rows)
    conn.execute("UPDATE trades SET released = 1 WHERE price = 1080000000")
    conn.commit()
    v = judge_price(conn, t(1_120_000_000, "2026-09-14"))
    assert v.kind == "표본부족"
    assert v.sample == 2
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `python -m pytest tests/test_judge_price.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.judge'`

- [ ] **Step 3: judge.py 작성**

```python
from dataclasses import dataclass

from src.models import TradeRow
from src.store import recent_trades

MIN_SAMPLE = 3


@dataclass(frozen=True)
class Verdict:
    kind: str                 # 신고가 / 하락 / 보합 / 표본부족
    pct: float | None
    prev_high: int | None
    sample: int


def judge_price(conn, trade: TradeRow, days: int = 365) -> Verdict:
    rows = recent_trades(conn, trade.complex_name, trade.area, trade.deal_date, days)
    if len(rows) < MIN_SAMPLE:
        return Verdict("표본부족", None, None, len(rows))

    prev_high = max(r["price"] for r in rows)
    if trade.price > prev_high:
        pct = (trade.price - prev_high) / prev_high * 100
        return Verdict("신고가", round(pct, 1), prev_high, len(rows))
    if trade.price == prev_high:
        return Verdict("보합", 0.0, prev_high, len(rows))
    pct = (trade.price - prev_high) / prev_high * 100
    return Verdict("하락", round(pct, 1), prev_high, len(rows))
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_judge_price.py -v`
Expected: PASS (7개)

- [ ] **Step 5: run_daily.py의 lines 생성에 판정을 붙인다**

`src/run_daily.py`의 `lines` 생성 부분을 교체한다:

```python
from src.judge import judge_price

TAG = {"신고가": ":arrow_up: 신고가", "하락": ":arrow_down:",
       "보합": "보합", "표본부족": "비교 표본 부족"}


def _line(conn, r) -> str:
    v = judge_price(conn, r)
    tag = TAG[v.kind]
    if v.kind == "하락":
        tag = f"{tag} {v.pct}%"
    return (f"• {r.complex_name} 전용 {r.area:.1f} {r.floor}층 — "
            f"{_money(r.price)} {tag} (계약 {r.deal_date})")
```

그리고 `lines` 조립을 `lines = [_line(conn, r) for r in sorted(new_trades, key=lambda r: r.price, reverse=True)]`로 바꾼다.

- [ ] **Step 6: 전체 테스트 통과 확인**

Run: `python -m pytest tests/ -v`
Expected: PASS (전체)

- [ ] **Step 7: 커밋**

```bash
git add src/judge.py src/run_daily.py tests/test_judge_price.py
git commit -m "feat: 신고가·하락 판정 (1년 창, 표본 3건 문턱)"
```

---

## Task 7: 특이사항 뱃지

**Files:**
- Modify: `src/judge.py`, `src/store.py` (최고층 조회 추가), `src/run_daily.py`
- Test: `tests/test_judge_badges.py`

**Interfaces:**
- Consumes: `models.TradeRow` (T1)
- Produces:
  - `store.max_floor(conn, complex_name: str) -> int | None`
  - `judge.badges(trade: TradeRow, top_floor: int | None, today: date) -> list[str]`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_judge_badges.py`:
```python
from datetime import date
from src.judge import badges
from src.models import TradeRow


def t(**kw) -> TradeRow:
    base = dict(lawd_cd="11290", dong="장위동", complex_name="래미안장위",
                deal_date="2026-09-14", area=84.93, floor=15, price=1_120_000_000,
                deal_kind="중개거래", released=False, released_date=None,
                register_date=None, asset="APT")
    base.update(kw)
    return TradeRow(**base)


TODAY = date(2026, 9, 29)


def test_1층은_뱃지가_붙는다():
    assert "1층" in badges(t(floor=1), top_floor=25, today=TODAY)


def test_2층도_저층_뱃지():
    assert "2층 이하" in badges(t(floor=2), top_floor=25, today=TODAY)


def test_최상층_뱃지():
    assert "최상층" in badges(t(floor=25), top_floor=25, today=TODAY)


def test_최고층을_모르면_최상층_판정을_안_한다():
    assert "최상층" not in badges(t(floor=25), top_floor=None, today=TODAY)


def test_직거래_뱃지():
    assert "직거래" in badges(t(deal_kind="직거래"), top_floor=25, today=TODAY)


def test_해제_뱃지():
    assert "해제" in badges(t(released=True, released_date="2026-09-20"), top_floor=25, today=TODAY)


def test_계약_60일_넘고_등기가_없으면_미등기():
    assert "미등기" in badges(t(deal_date="2026-06-01", register_date=None),
                             top_floor=25, today=TODAY)


def test_등기가_있으면_미등기가_아니다():
    assert "미등기" not in badges(t(deal_date="2026-06-01", register_date="2026-07-10"),
                                 top_floor=25, today=TODAY)


def test_계약_60일_이내면_미등기로_보지_않는다():
    assert "미등기" not in badges(t(deal_date="2026-09-14", register_date=None),
                                 top_floor=25, today=TODAY)


def test_평범한_거래는_뱃지가_없다():
    assert badges(t(), top_floor=25, today=TODAY) == []
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `python -m pytest tests/test_judge_badges.py -v`
Expected: FAIL — `ImportError: cannot import name 'badges'`

- [ ] **Step 3: judge.py에 badges 추가**

```python
from datetime import date

UNREGISTERED_DAYS = 60


def badges(trade: TradeRow, top_floor: int | None, today: date) -> list[str]:
    out: list[str] = []
    if trade.floor == 1:
        out.append("1층")
    elif trade.floor is not None and trade.floor <= 2:
        out.append("2층 이하")
    if top_floor is not None and trade.floor is not None and trade.floor >= top_floor:
        out.append("최상층")
    if trade.deal_kind and "직거래" in trade.deal_kind:
        out.append("직거래")
    if trade.released:
        out.append("해제")
    if not trade.register_date:
        elapsed = (today - date.fromisoformat(trade.deal_date)).days
        if elapsed > UNREGISTERED_DAYS:
            out.append("미등기")
    return out
```

- [ ] **Step 4: store.py에 max_floor 추가**

```python
def max_floor(conn, complex_name: str) -> int | None:
    row = conn.execute(
        "SELECT MAX(floor) AS f FROM trades WHERE complex_name = ?", (complex_name,)
    ).fetchone()
    return row["f"] if row and row["f"] is not None else None
```

- [ ] **Step 5: run_daily.py의 _line에 뱃지를 붙인다**

`_line` 함수를 교체한다:

```python
from src.judge import badges, judge_price
from src.store import max_floor


def _line(conn, r, today) -> str:
    v = judge_price(conn, r)
    tag = TAG[v.kind]
    if v.kind == "하락":
        tag = f"{tag} {v.pct}%"
    bs = badges(r, max_floor(conn, r.complex_name), today)
    suffix = f" `{' · '.join(bs)}`" if bs else ""
    return (f"• {r.complex_name} 전용 {r.area:.1f} {r.floor}층 — "
            f"{_money(r.price)} {tag} (계약 {r.deal_date}){suffix}")
```

호출부도 `_line(conn, r, today)`로 바꾼다.

- [ ] **Step 6: 전체 테스트 통과 확인**

Run: `python -m pytest tests/ -v`
Expected: PASS (전체)

- [ ] **Step 7: 커밋**

```bash
git add src/judge.py src/store.py src/run_daily.py tests/test_judge_badges.py
git commit -m "feat: 특이사항 뱃지 (저층·최상층·직거래·해제·미등기)"
```

---

## Task 8: HTML 카드 템플릿과 렌더

**Files:**
- Create: `src/render.py`, `templates/briefing.html.j2`
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: `store` 조회 (T2), `judge` 판정 (T6, T7)
- Produces:
  - `render.build_context(conn, target_date: date, summary_counts: dict, headline: str, cards: list[dict], warnings: list[str]) -> dict`
  - `render.render_html(context: dict) -> str`
  - `render.write_html(context: dict, out_dir: Path) -> Path`

`cards`의 각 항목: `{"complex_name","area","floor","price_text","verdict","pct","badges","deal_date"}`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_render.py`:
```python
from datetime import date
from pathlib import Path
from src.render import render_html, write_html


def ctx(**kw) -> dict:
    base = dict(
        target_date="2026-09-29",
        headline="장위동 신규 신고 7건",
        counts={"신규신고": 7, "신규매물": 8, "소멸매물": 5, "가격조정": 2, "계약파기": 1},
        cards=[{"complex_name": "래미안장위", "area": 84.93, "floor": 15,
                "price_text": "11억 2,000", "verdict": "신고가", "pct": 3.7,
                "badges": ["직거래"], "deal_date": "2026-09-14"}],
        warnings=[],
        listing_sections=[],
        price_changes=[],
        thermometer=[],
    )
    base.update(kw)
    return base


def test_헤드라인과_날짜가_들어간다():
    html = render_html(ctx())
    assert "장위동 신규 신고 7건" in html
    assert "2026-09-29" in html


def test_카드_내용이_들어간다():
    html = render_html(ctx())
    assert "래미안장위" in html
    assert "11억 2,000" in html
    assert "직거래" in html


def test_경고가_있으면_배너가_나온다():
    html = render_html(ctx(warnings=["호가 미수집 — 네이버 차단"]))
    assert "호가 미수집" in html


def test_카드가_없어도_렌더된다():
    html = render_html(ctx(cards=[], counts={"신규신고": 0}))
    assert "조용" in html


def test_HTML_특수문자가_이스케이프된다():
    html = render_html(ctx(headline="<script>alert(1)</script>"))
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_파일로_저장된다(tmp_path):
    p = write_html(ctx(), tmp_path)
    assert p.exists()
    assert p.name == "2026-09-29.html"
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `python -m pytest tests/test_render.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.render'`

- [ ] **Step 3: templates/briefing.html.j2 작성**

```html
<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<style>
  :root { --bg:#f5f6f8; --card:#fff; --line:#e3e6ea; --ink:#1a1d21;
          --muted:#6b7280; --up:#d92d20; --down:#1570ef; --accent:#0f7b6c; }
  * { box-sizing: border-box; }
  body { width:1200px; margin:0; padding:0 0 28px; background:var(--bg);
         font:14px/1.55 "Malgun Gothic","맑은 고딕",system-ui,sans-serif; color:var(--ink); }
  .hero { background:linear-gradient(100deg,#0f7b6c,#3aa17e); color:#fff; padding:28px 32px; }
  .hero .kicker { font-size:11px; letter-spacing:.14em; opacity:.85; }
  .hero h1 { margin:8px 0 6px; font-size:28px; }
  .hero .sub { font-size:13px; opacity:.92; }
  .warn { margin:14px 32px 0; padding:10px 14px; border-radius:8px;
          background:#fff4ed; border:1px solid #fdb022; color:#b54708; font-size:13px; }
  section { margin:26px 32px 0; }
  h2 { font-size:15px; margin:0 0 12px; display:flex; align-items:center; gap:8px; }
  .tag { font-size:10px; letter-spacing:.1em; background:#e7eef0; color:var(--accent);
         padding:3px 8px; border-radius:4px; }
  .snap { display:grid; grid-template-columns:repeat(5,1fr); gap:12px; }
  .snap div { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px 16px; }
  .snap .k { font-size:12px; color:var(--muted); }
  .snap .v { font-size:24px; font-weight:700; margin-top:4px; }
  .cards { display:grid; grid-template-columns:repeat(2,1fr); gap:12px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:10px;
          padding:14px 16px; border-left:3px solid var(--line); }
  .card.up { border-left-color:var(--up); }
  .card.down { border-left-color:var(--down); }
  .card .t { font-weight:700; }
  .card .p { font-size:19px; font-weight:700; margin:6px 0 2px; }
  .up-t { color:var(--up); } .down-t { color:var(--down); }
  .meta { font-size:12px; color:var(--muted); }
  .badge { display:inline-block; font-size:11px; background:#eef1f4; color:#475467;
           border-radius:4px; padding:2px 7px; margin:6px 5px 0 0; }
  .quiet { background:var(--card); border:1px solid var(--line); border-radius:10px;
           padding:24px; text-align:center; color:var(--muted); }
  table { width:100%; border-collapse:collapse; background:var(--card);
          border:1px solid var(--line); border-radius:10px; overflow:hidden; font-size:13px; }
  th,td { padding:9px 14px; text-align:left; border-bottom:1px solid var(--line); }
  th { background:#eef1f4; font-size:11px; color:#475467; letter-spacing:.06em; }
  tr:last-child td { border-bottom:none; }
  .foot { margin:26px 32px 0; font-size:11px; color:var(--muted);
          border-top:1px solid var(--line); padding-top:12px; }
</style></head><body>

<div class="hero">
  <div class="kicker">DAILY REAL ESTATE BRIEFING</div>
  <h1>{{ headline }}</h1>
  <div class="sub">{{ target_date }} 기준 · 국토부 실거래가 + 네이버 호가</div>
</div>

{% for w in warnings %}<div class="warn">⚠ {{ w }}</div>{% endfor %}

<section>
  <h2><span class="tag">SNAPSHOT</span> 오늘의 스냅샷</h2>
  <div class="snap">
    {% for k, v in counts.items() %}<div><div class="k">{{ k }}</div><div class="v">{{ v }}</div></div>{% endfor %}
  </div>
</section>

<section>
  <h2><span class="tag">TRADES</span> 수도권 상세</h2>
  {% if cards %}
  <div class="cards">
    {% for c in cards %}
    <div class="card {% if c.verdict == '신고가' %}up{% elif c.verdict == '하락' %}down{% endif %}">
      <div class="t">{{ c.complex_name }}</div>
      <div class="p">{{ c.price_text }}
        {% if c.verdict == '신고가' %}<span class="up-t">▲ 신고가</span>
        {% elif c.verdict == '하락' %}<span class="down-t">▼ {{ c.pct }}%</span>
        {% elif c.verdict == '표본부족' %}<span class="meta">비교 표본 부족</span>{% endif %}
      </div>
      <div class="meta">전용 {{ "%.1f"|format(c.area) }}㎡ · {{ c.floor }}층 · 계약 {{ c.deal_date }}</div>
      {% for b in c.badges %}<span class="badge">{{ b }}</span>{% endfor %}
    </div>
    {% endfor %}
  </div>
  {% else %}<div class="quiet">오늘은 조용합니다. 새로 올라온 거래가 없습니다.</div>{% endif %}
</section>

{% if listing_sections %}
<section>
  <h2><span class="tag">LISTINGS</span> 호가 동향</h2>
  <table><tr><th>구역</th><th>거래유형</th><th>매물</th><th>전일</th><th>증감</th><th>신규</th><th>소멸</th><th>최저호가</th></tr>
  {% for r in listing_sections %}
  <tr><td>{{ r.area_name }}</td><td>{{ r.deal_type }}</td><td>{{ r.count }}</td><td>{{ r.prev }}</td>
      <td>{{ r.delta_text }}</td><td>{{ r.new }}</td><td>{{ r.gone }}</td><td>{{ r.low_text }}</td></tr>
  {% endfor %}</table>
</section>
{% endif %}

{% if price_changes %}
<section>
  <h2><span class="tag">ADJUST</span> 가격 조정</h2>
  <table><tr><th>매물</th><th>방향</th><th>변동</th><th>직전</th><th>현재</th><th>경과일</th><th>누적</th></tr>
  {% for r in price_changes %}
  <tr><td>{{ r.name }}</td>
      <td class="{% if r.down %}down-t{% else %}up-t{% endif %}">{{ '▼ 인하' if r.down else '▲ 인상' }}</td>
      <td>{{ r.delta_text }}</td><td>{{ r.prev_text }}</td><td>{{ r.now_text }}</td>
      <td>{{ r.days }}일</td><td>{{ r.times }}회</td></tr>
  {% endfor %}</table>
</section>
{% endif %}

{% if thermometer %}
<section>
  <h2><span class="tag">REGIONAL</span> 지방 온도계</h2>
  <table><tr><th>단지</th><th>최근 3개월 거래</th><th>전년 동월</th><th>평당가</th><th>3개월 추세</th><th>1년 최고 대비</th></tr>
  {% for r in thermometer %}
  <tr><td>{{ r.name }}</td><td>{{ r.recent_counts }}</td><td>{{ r.yoy_text }}</td>
      <td>{{ r.pyeong_text }}</td><td>{{ r.trend_text }}</td><td>{{ r.from_high_text }}</td></tr>
  {% endfor %}</table>
</section>
{% endif %}

<div class="foot">본 자료는 국토교통부 실거래가 공개시스템과 공개 매물 정보를 개인 투자 판단 목적으로 정리한 것입니다. 투자의 최종 판단과 책임은 본인에게 있습니다.</div>
</body></html>
```

- [ ] **Step 4: render.py 작성**

```python
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"

_env = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    autoescape=select_autoescape(["html", "j2"]),
)


def render_html(context: dict) -> str:
    return _env.get_template("briefing.html.j2").render(**context)


def write_html(context: dict, out_dir: Path) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{context['target_date']}.html"
    path.write_text(render_html(context), encoding="utf-8")
    return path


def build_context(target_date: date, headline: str, counts: dict,
                  cards: list[dict], warnings: list[str],
                  listing_sections: list[dict] | None = None,
                  price_changes: list[dict] | None = None,
                  thermometer: list[dict] | None = None) -> dict:
    return {
        "target_date": target_date.isoformat(),
        "headline": headline,
        "counts": counts,
        "cards": cards,
        "warnings": warnings,
        "listing_sections": listing_sections or [],
        "price_changes": price_changes or [],
        "thermometer": thermometer or [],
    }
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `python -m pytest tests/test_render.py -v`
Expected: PASS (6개)

- [ ] **Step 6: 커밋**

```bash
git add src/render.py templates/ tests/test_render.py
git commit -m "feat: 카드형 HTML 브리핑 템플릿"
```

---

## Task 9: PNG 렌더와 이미지 발송 — 2주차 완료

**Files:**
- Modify: `src/render.py`, `src/run_daily.py`
- Test: `tests/test_render_png.py`

**Interfaces:**
- Consumes: `render.write_html` (T8), `notify.post` (T4)
- Produces: `render.html_to_png(html_path: Path, png_path: Path, width: int = 1200) -> Path`

- [ ] **Step 1: Playwright 브라우저 설치**

```bash
pip install -r requirements.txt
playwright install chromium
```

- [ ] **Step 2: 실패하는 테스트 작성**

`tests/test_render_png.py`:
```python
from pathlib import Path
import pytest
from src.render import html_to_png

pytest.importorskip("playwright")


def test_html을_png로_찍는다(tmp_path):
    html = tmp_path / "x.html"
    html.write_text("<body style='width:1200px'><h1>테스트 브리핑</h1></body>", encoding="utf-8")
    png = html_to_png(html, tmp_path / "x.png")
    assert png.exists()
    assert png.stat().st_size > 1000
    assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
```

- [ ] **Step 3: 테스트가 실패하는지 확인**

Run: `python -m pytest tests/test_render_png.py -v`
Expected: FAIL — `ImportError: cannot import name 'html_to_png'`

- [ ] **Step 4: render.py에 html_to_png 추가**

```python
def html_to_png(html_path: Path, png_path: Path, width: int = 1200) -> Path:
    from playwright.sync_api import sync_playwright

    html_path, png_path = Path(html_path), Path(png_path)
    png_path.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": width, "height": 900},
                                    device_scale_factor=2)
            page.goto(html_path.resolve().as_uri())
            page.wait_for_load_state("networkidle")
            page.screenshot(path=str(png_path), full_page=True)
        finally:
            browser.close()
    return png_path
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `python -m pytest tests/test_render_png.py -v`
Expected: PASS

- [ ] **Step 6: run_daily.py에 렌더·이미지 발송을 연결**

`run()`의 Slack 발송 직전에 렌더 단계를 넣는다:

```python
from src.render import build_context, html_to_png, write_html

    png_path = None
    t2 = time.monotonic()
    try:
        cards = [_card(conn, r, today) for r in sorted(new_trades, key=lambda r: r.price, reverse=True)]
        ctx = build_context(today, headline, counts, cards, warnings)
        html_path = write_html(ctx, Path("briefings"))
        png_path = html_to_png(html_path, html_path.with_suffix(".png"))
        log_run(conn, "render", True, len(cards), time.monotonic() - t2, None)
    except Exception as e:
        log.exception("렌더 실패")
        warnings.append("이미지 렌더 실패 — 텍스트만 보냅니다")
        log_run(conn, "render", False, 0, time.monotonic() - t2, str(e))
```

그리고 `_card` 헬퍼를 추가한다:

```python
def _card(conn, r, today) -> dict:
    v = judge_price(conn, r)
    return {
        "complex_name": r.complex_name, "area": r.area, "floor": r.floor,
        "price_text": _money(r.price), "verdict": v.kind, "pct": v.pct,
        "badges": badges(r, max_floor(conn, r.complex_name), today),
        "deal_date": r.deal_date,
    }
```

`post(...)` 호출에 `png_path=png_path`를 넘긴다.

- [ ] **Step 7: 실제로 돌려 이미지를 확인**

```bash
python -m src.run_daily
```
Expected: `briefings/YYYY-MM-DD.png`이 생기고 Slack에 이미지가 올라온다. 이미지를 눈으로 확인한다

- [ ] **Step 8: 커밋**

```bash
git add src/render.py src/run_daily.py tests/test_render_png.py
git commit -m "feat: PNG 렌더와 Slack 이미지 발송 (2주차 완료)"
```

---

## Task 10: 호가 스키마와 가격 이력 대조

**Files:**
- Modify: `src/store.py`, `src/models.py`
- Test: `tests/test_store_listings.py`

**Interfaces:**
- Consumes: 없음 (스키마 확장)
- Produces:
  - `models.ListingRow(listing_no: str, name: str, deal_type: str, price: int, floor: str, area: float, exclusive_area: float, direction: str, features: str, listed_date: str | None, prev_jeonse: int | None, gap: int | None, gap_ratio: float | None, area_name: str)`
  - `store.PriceChange(listing_no, name, prev: int, now: int, days: int, times: int)`
  - `store.ListingDiff(new: list[ListingRow], gone: list[sqlite3.Row], price_changes: list[PriceChange], counts: dict[tuple[str, str], int])`
  - `store.upsert_listings(conn, rows: list[ListingRow], area_name: str, today: str) -> ListingDiff`
  - `store.listing_counts(conn, area_name: str, on: str) -> dict[str, int]`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_store_listings.py`:
```python
from dataclasses import replace
import pytest
from src.models import ListingRow
from src.store import init_db, upsert_listings


def L(**kw) -> ListingRow:
    base = dict(listing_no="2444012345", name="장위빌라", deal_type="매매",
                price=380_000_000, floor="2/4", area=52.1, exclusive_area=42.1,
                direction="남동", features="역세권", listed_date="2026-09-01",
                prev_jeonse=None, gap=None, gap_ratio=None, area_name="장위 재개발 일대")
    base.update(kw)
    return ListingRow(**base)


@pytest.fixture
def conn(tmp_path):
    return init_db(tmp_path / "l.db")


def test_처음_본_매물은_신규다(conn):
    d = upsert_listings(conn, [L()], "장위 재개발 일대", "2026-09-29")
    assert len(d.new) == 1


def test_같은_매물은_다음날_신규가_아니다(conn):
    upsert_listings(conn, [L()], "장위 재개발 일대", "2026-09-29")
    d = upsert_listings(conn, [L()], "장위 재개발 일대", "2026-09-30")
    assert d.new == []


def test_오늘_안_보이면_소멸이다(conn):
    upsert_listings(conn, [L(), L(listing_no="999")], "장위 재개발 일대", "2026-09-29")
    d = upsert_listings(conn, [L()], "장위 재개발 일대", "2026-09-30")
    assert [r["listing_no"] for r in d.gone] == ["999"]


def test_가격이_내리면_조정으로_잡힌다(conn):
    upsert_listings(conn, [L()], "장위 재개발 일대", "2026-09-29")
    d = upsert_listings(conn, [L(price=360_000_000)], "장위 재개발 일대", "2026-10-02")
    assert len(d.price_changes) == 1
    c = d.price_changes[0]
    assert c.prev == 380_000_000 and c.now == 360_000_000
    assert c.days == 3
    assert c.times == 1


def test_두번_내리면_누적_횟수가_2다(conn):
    upsert_listings(conn, [L()], "장위 재개발 일대", "2026-09-29")
    upsert_listings(conn, [L(price=370_000_000)], "장위 재개발 일대", "2026-09-30")
    d = upsert_listings(conn, [L(price=360_000_000)], "장위 재개발 일대", "2026-10-01")
    assert d.price_changes[0].times == 2


def test_가격이_그대로면_이력이_안_쌓인다(conn):
    upsert_listings(conn, [L()], "장위 재개발 일대", "2026-09-29")
    upsert_listings(conn, [L()], "장위 재개발 일대", "2026-09-30")
    n = conn.execute("SELECT COUNT(*) FROM listing_price_history").fetchone()[0]
    assert n == 1


def test_거래유형별로_센다(conn):
    upsert_listings(conn, [L(), L(listing_no="2", deal_type="전세", price=300_000_000)],
                    "장위 재개발 일대", "2026-09-29")
    d = upsert_listings(conn, [L(), L(listing_no="2", deal_type="전세", price=300_000_000)],
                        "장위 재개발 일대", "2026-09-30")
    assert d.counts[("장위 재개발 일대", "매매")] == 1
    assert d.counts[("장위 재개발 일대", "전세")] == 1


def test_다른_구역의_매물은_소멸로_안_잡힌다(conn):
    upsert_listings(conn, [L(listing_no="A", area_name="구역A")], "구역A", "2026-09-29")
    d = upsert_listings(conn, [L(listing_no="B", area_name="구역B")], "구역B", "2026-09-29")
    assert d.gone == []
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `python -m pytest tests/test_store_listings.py -v`
Expected: FAIL — `ImportError: cannot import name 'ListingRow'`

- [ ] **Step 3: models.py에 ListingRow 추가**

```python
@dataclass(frozen=True)
class ListingRow:
    listing_no: str
    name: str
    deal_type: str            # 매매 / 전세 / 월세
    price: int                # 원
    floor: str                # "2/4"
    area: float
    exclusive_area: float
    direction: str
    features: str
    listed_date: str | None   # YYYY-MM-DD
    prev_jeonse: int | None
    gap: int | None
    gap_ratio: float | None
    area_name: str
```

- [ ] **Step 4: store.py에 스키마와 upsert_listings 추가**

`SCHEMA` 문자열 끝에 이어 붙인다:

```sql
CREATE TABLE IF NOT EXISTS listings (
    listing_no     TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    deal_type      TEXT NOT NULL,
    price          INTEGER NOT NULL,
    floor          TEXT,
    area           REAL,
    exclusive_area REAL,
    direction      TEXT,
    features       TEXT,
    listed_date    TEXT,
    prev_jeonse    INTEGER,
    gap            INTEGER,
    gap_ratio      REAL,
    area_name      TEXT NOT NULL,
    first_seen     TEXT NOT NULL,
    last_seen      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_listings_area ON listings (area_name, deal_type, last_seen);

CREATE TABLE IF NOT EXISTS listing_price_history (
    listing_no  TEXT NOT NULL,
    observed_on TEXT NOT NULL,
    price       INTEGER NOT NULL,
    PRIMARY KEY (listing_no, observed_on)
);
```

그리고 함수를 추가한다:

```python
from datetime import date as _date
from src.models import ListingRow


@dataclass
class PriceChange:
    listing_no: str
    name: str
    prev: int
    now: int
    days: int
    times: int


@dataclass
class ListingDiff:
    new: list[ListingRow]
    gone: list[sqlite3.Row]
    price_changes: list[PriceChange]
    counts: dict[tuple[str, str], int]


def upsert_listings(conn, rows: list[ListingRow], area_name: str, today: str) -> ListingDiff:
    new: list[ListingRow] = []
    changes: list[PriceChange] = []

    for row in rows:
        prior = conn.execute(
            "SELECT price, last_seen FROM listings WHERE listing_no = ?", (row.listing_no,)
        ).fetchone()

        if prior is None:
            conn.execute(
                """INSERT INTO listings (listing_no, name, deal_type, price, floor, area,
                       exclusive_area, direction, features, listed_date, prev_jeonse,
                       gap, gap_ratio, area_name, first_seen, last_seen)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (row.listing_no, row.name, row.deal_type, row.price, row.floor, row.area,
                 row.exclusive_area, row.direction, row.features, row.listed_date,
                 row.prev_jeonse, row.gap, row.gap_ratio, area_name, today, today),
            )
            conn.execute(
                "INSERT OR REPLACE INTO listing_price_history VALUES (?,?,?)",
                (row.listing_no, today, row.price),
            )
            new.append(row)
            continue

        if prior["price"] != row.price:
            conn.execute(
                "INSERT OR REPLACE INTO listing_price_history VALUES (?,?,?)",
                (row.listing_no, today, row.price),
            )
            times = conn.execute(
                "SELECT COUNT(*) - 1 FROM listing_price_history WHERE listing_no = ?",
                (row.listing_no,),
            ).fetchone()[0]
            days = (_date.fromisoformat(today) - _date.fromisoformat(prior["last_seen"])).days
            changes.append(PriceChange(row.listing_no, row.name,
                                       prior["price"], row.price, days, times))

        conn.execute(
            """UPDATE listings SET price = ?, features = ?, prev_jeonse = ?, gap = ?,
                   gap_ratio = ?, last_seen = ? WHERE listing_no = ?""",
            (row.price, row.features, row.prev_jeonse, row.gap, row.gap_ratio,
             today, row.listing_no),
        )

    gone = conn.execute(
        "SELECT * FROM listings WHERE area_name = ? AND last_seen < ? AND last_seen >= DATE(?, '-7 days')",
        (area_name, today, today),
    ).fetchall()

    conn.commit()

    counts_rows = conn.execute(
        "SELECT area_name, deal_type, COUNT(*) AS n FROM listings WHERE last_seen = ? GROUP BY 1, 2",
        (today,),
    ).fetchall()
    counts = {(r["area_name"], r["deal_type"]): r["n"] for r in counts_rows}

    return ListingDiff(new=new, gone=gone, price_changes=changes, counts=counts)


def listing_counts(conn, area_name: str, on: str) -> dict[str, int]:
    rows = conn.execute(
        "SELECT deal_type, COUNT(*) AS n FROM listings WHERE area_name = ? AND last_seen = ? GROUP BY 1",
        (area_name, on),
    ).fetchall()
    return {r["deal_type"]: r["n"] for r in rows}
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `python -m pytest tests/test_store_listings.py -v`
Expected: PASS (8개)

- [ ] **Step 6: 커밋**

```bash
git add src/store.py src/models.py tests/test_store_listings.py
git commit -m "feat: 호가 매물 스키마와 가격 이력 대조"
```

---

## Task 11: 네이버 스크레이퍼 래퍼

**Files:**
- Create: `src/collect_naver.py`, `tests/fixtures/naver_sample.xlsx`
- Test: `tests/test_collect_naver.py`

**Interfaces:**
- Consumes: `models.Target`, `models.ListingRow` (T1, T10)
- Produces:
  - `collect_naver.PII_COLUMNS = ("부동산상호", "중개사이름", "전화1", "전화2")`
  - `collect_naver.read_xlsx(path: Path, area_name: str) -> list[ListingRow]`
  - `collect_naver.newest_xlsx(dirpath: Path) -> Path | None`
  - `collect_naver.scrape_target(target: Target) -> list[ListingRow]`
  - `collect_naver.collect_all(targets: list[Target]) -> tuple[dict[str, list[ListingRow]], list[str]]` — (구역명→매물, 경고 목록)
  - `collect_naver.NaverBlocked(Exception)`

- [ ] **Step 1: 픽스처 xlsx 생성**

```bash
python - <<'PY'
import pandas as pd
from pathlib import Path
Path("tests/fixtures").mkdir(parents=True, exist_ok=True)
pd.DataFrame([
  {"매물명":"장위현대빌라","매물번호":"2444012345","거래유형":"매매","매매 금액(원)":380000000,
   "층수":"2/4","면적(㎡)":52.1,"전용면적":42.1,"방향":"남동","특징":"역세권","등록일":"20260901",
   "부동산상호":"장위부동산","중개사이름":"홍길동","전화1":"02-1234-5678","전화2":"010-1234-5678",
   "전세_기간(년)":3,"전세_기간내_최고(원)":350000000,"전세_기간내_최저(원)":320000000,
   "기전세금(원)":340000000,"갭금액(원)":40000000,"갭비율":0.1053},
  {"매물명":"장위삼성","매물번호":"2444099999","거래유형":"전세","매매 금액(원)":300000000,
   "층수":"3/5","면적(㎡)":60.0,"전용면적":49.5,"방향":"남","특징":"","등록일":"20260915",
   "부동산상호":"둘부동산","중개사이름":"김철수","전화1":"02-2222-3333","전화2":"",
   "전세_기간(년)":None,"전세_기간내_최고(원)":None,"전세_기간내_최저(원)":None,
   "기전세금(원)":None,"갭금액(원)":None,"갭비율":None},
]).to_excel("tests/fixtures/naver_sample.xlsx", index=False, engine="openpyxl")
print("ok")
PY
```

- [ ] **Step 2: 실패하는 테스트 작성**

`tests/test_collect_naver.py`:
```python
from pathlib import Path
import pandas as pd
import pytest
from src.collect_naver import PII_COLUMNS, newest_xlsx, read_xlsx

FIX = Path(__file__).parent / "fixtures"


def test_매물을_읽는다():
    rows = read_xlsx(FIX / "naver_sample.xlsx", area_name="장위 재개발 일대")
    assert len(rows) == 2
    r = rows[0]
    assert r.listing_no == "2444012345"
    assert r.deal_type == "매매"
    assert r.price == 380_000_000
    assert r.area_name == "장위 재개발 일대"
    assert r.listed_date == "2026-09-01"


def test_전세_매물도_버리지_않는다():
    rows = read_xlsx(FIX / "naver_sample.xlsx", area_name="x")
    assert {r.deal_type for r in rows} == {"매매", "전세"}


def test_개인정보_컬럼은_ListingRow에_없다():
    r = read_xlsx(FIX / "naver_sample.xlsx", area_name="x")[0]
    for col in PII_COLUMNS:
        assert not hasattr(r, col)
    blob = repr(r)
    assert "홍길동" not in blob
    assert "010-1234-5678" not in blob
    assert "장위부동산" not in blob


def test_갭_지표는_보존된다():
    r = read_xlsx(FIX / "naver_sample.xlsx", area_name="x")[0]
    assert r.gap == 40_000_000
    assert r.gap_ratio == pytest.approx(0.1053)


def test_갭_지표가_비어있어도_None으로_읽는다():
    r = read_xlsx(FIX / "naver_sample.xlsx", area_name="x")[1]
    assert r.gap is None
    assert r.prev_jeonse is None


def test_xlsx가_없으면_None을_돌려준다(tmp_path):
    assert newest_xlsx(tmp_path) is None


def test_가장_최근_xlsx를_고른다(tmp_path):
    import time
    for name in ("매물정보_확장_20260929_080000.xlsx", "매물정보_확장_20260929_090000.xlsx"):
        pd.DataFrame([{"a": 1}]).to_excel(tmp_path / name, index=False, engine="openpyxl")
        time.sleep(0.02)
    assert newest_xlsx(tmp_path).name == "매물정보_확장_20260929_090000.xlsx"
```

- [ ] **Step 3: 테스트가 실패하는지 확인**

Run: `python -m pytest tests/test_collect_naver.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.collect_naver'`

- [ ] **Step 4: collect_naver.py 작성**

```python
from pathlib import Path
import logging
import os
import random
import sys
import tempfile
import time

import pandas as pd

from src.models import ListingRow, Target

log = logging.getLogger(__name__)

PII_COLUMNS = ("부동산상호", "중개사이름", "전화1", "전화2")

SCRAPER_DIR = Path(__file__).resolve().parent.parent / "vendor" / "anti_bot_scraper"

OVERRIDES = {
    "ONLY_PREV_GT_SALE": False,
    "ONLY_WITH_PREV_JEONSE": False,
    "DETAIL_WORKERS": 4,
    "MAX_COMPLEX_DETAIL": 100,
    "MAX_ARTICLE_DETAIL": 1500,
}


class NaverBlocked(Exception):
    pass


def _int_or_none(v) -> int | None:
    if v is None or (isinstance(v, float) and pd.isna(v)) or v == "":
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _float_or_none(v) -> float | None:
    if v is None or (isinstance(v, float) and pd.isna(v)) or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _date_or_none(v) -> str | None:
    digits = "".join(ch for ch in str(v) if ch.isdigit())
    return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}" if len(digits) >= 8 else None


def read_xlsx(path: Path, area_name: str) -> list[ListingRow]:
    df = pd.read_excel(path, engine="openpyxl")
    df = df.drop(columns=[c for c in PII_COLUMNS if c in df.columns])

    rows: list[ListingRow] = []
    for rec in df.to_dict("records"):
        no = str(rec.get("매물번호", "")).strip()
        price = _int_or_none(rec.get("매매 금액(원)"))
        if not no or price is None:
            continue
        rows.append(ListingRow(
            listing_no=no,
            name=str(rec.get("매물명", "")).strip(),
            deal_type=str(rec.get("거래유형", "")).strip(),
            price=price,
            floor=str(rec.get("층수", "")).strip(),
            area=_float_or_none(rec.get("면적(㎡)")) or 0.0,
            exclusive_area=_float_or_none(rec.get("전용면적")) or 0.0,
            direction=str(rec.get("방향", "")).strip(),
            features=str(rec.get("특징", "")).strip(),
            listed_date=_date_or_none(rec.get("등록일")),
            prev_jeonse=_int_or_none(rec.get("기전세금(원)")),
            gap=_int_or_none(rec.get("갭금액(원)")),
            gap_ratio=_float_or_none(rec.get("갭비율")),
            area_name=area_name,
        ))
    return rows


def newest_xlsx(dirpath: Path) -> Path | None:
    files = sorted(Path(dirpath).glob("*.xlsx"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def scrape_target(target: Target) -> list[ListingRow]:
    if target.coord is None:
        return []

    if str(SCRAPER_DIR) not in sys.path:
        sys.path.insert(0, str(SCRAPER_DIR))
    import scraper_kr
    import asyncio

    for key, value in OVERRIDES.items():
        setattr(scraper_kr, key, value)
    if target.asset:
        scraper_kr.ASSET_TYPES = target.asset

    cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as tmp:
        os.chdir(tmp)
        try:
            asyncio.run(scraper_kr.crawl_detailed(
                target.coord.lat, target.coord.lon, target.coord.zoom))
            found = newest_xlsx(Path(tmp))
            if found is None:
                log.warning("%s: 스크레이퍼가 xlsx를 만들지 않았습니다 (수집 0건)", target.name)
                return []
            rows = read_xlsx(found, target.name)
            found.unlink(missing_ok=True)
            return rows
        finally:
            os.chdir(cwd)


def collect_all(targets: list[Target]) -> tuple[dict[str, list[ListingRow]], list[str]]:
    result: dict[str, list[ListingRow]] = {}
    warnings: list[str] = []
    seoul = [t for t in targets if t.region == "수도권" and t.coord is not None]

    for i, target in enumerate(seoul):
        if i:
            time.sleep(random.uniform(5, 15))
        try:
            result[target.name] = scrape_target(target)
        except NaverBlocked as e:
            warnings.append(f"호가 미수집 — 네이버 차단 ({target.name})")
            log.warning("차단 감지, 재시도하지 않고 중단: %s", e)
            break
        except Exception as e:
            warnings.append(f"호가 미수집 — {target.name} ({type(e).__name__})")
            log.exception("네이버 수집 실패: %s", target.name)
    return result, warnings
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `python -m pytest tests/test_collect_naver.py -v`
Expected: PASS (7개)

- [ ] **Step 6: 실제 구역 하나로 수집을 확인**

```bash
python -c "from pathlib import Path; from src.config import load_watchlist; from src.collect_naver import collect_all; r,w=collect_all(load_watchlist(Path('watchlist.yml'))); print({k: len(v) for k,v in r.items()}, w)"
```
Expected: 구역별 매물 건수가 찍힌다. 몇 분 걸린다

- [ ] **Step 7: 커밋**

```bash
git add src/collect_naver.py tests/
git commit -m "feat: 네이버 스크레이퍼 래퍼 (상수 덮어쓰기, PII 제거, xlsx 부재 처리)"
```

---

## Task 12: 호가 동향과 가격 조정 섹션 — 3주차 완료

**Files:**
- Modify: `src/run_daily.py`
- Test: `tests/test_run_daily_listings.py`

**Interfaces:**
- Consumes: `collect_naver.collect_all` (T11), `store.upsert_listings/listing_counts` (T10), `render.build_context` (T8)
- Produces: `run_daily.build_listing_rows(conn, diffs: dict, today: date) -> tuple[list[dict], list[dict]]` — (호가 동향 행, 가격 조정 행)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_run_daily_listings.py`:
```python
from datetime import date
from pathlib import Path
import textwrap
import pytest
from src.models import ListingRow
from src.run_daily import run
from src.store import init_db


@pytest.fixture
def wl(tmp_path) -> Path:
    p = tmp_path / "w.yml"
    p.write_text(textwrap.dedent("""
        수도권:
          - 이름: 장위 재개발 일대
            유형: 구역
            lawd_cd: "11290"
            법정동: ["장위동"]
            좌표: {lat: 37.6130, lon: 127.0480, zoom: 17}
            자산: "VL"
    """), encoding="utf-8")
    return p


def L(**kw) -> ListingRow:
    base = dict(listing_no="1", name="장위빌라", deal_type="매매", price=380_000_000,
                floor="2/4", area=52.1, exclusive_area=42.1, direction="남동",
                features="", listed_date="2026-09-01", prev_jeonse=None, gap=None,
                gap_ratio=None, area_name="장위 재개발 일대")
    base.update(kw)
    return ListingRow(**base)


def test_신규매물이_카운트에_들어간다(tmp_path, wl, monkeypatch):
    monkeypatch.setattr("src.run_daily.collect_all", lambda *a, **k: [])
    monkeypatch.setattr("src.run_daily.collect_naver_all",
                        lambda t: ({"장위 재개발 일대": [L()]}, []))
    s = run(date(2026, 9, 29), tmp_path / "d.db", wl, {"KOR_API_KEY": "x"}, dry_run=True)
    assert s.counts["신규매물"] == 1


def test_네이버_실패시_경고가_붙고_파이프라인은_산다(tmp_path, wl, monkeypatch):
    monkeypatch.setattr("src.run_daily.collect_all", lambda *a, **k: [])
    monkeypatch.setattr("src.run_daily.collect_naver_all",
                        lambda t: ({}, ["호가 미수집 — 네이버 차단 (장위 재개발 일대)"]))
    s = run(date(2026, 9, 29), tmp_path / "d.db", wl, {"KOR_API_KEY": "x"}, dry_run=True)
    assert any("호가 미수집" in w for w in s.warnings)
    assert s.counts["신규매물"] == 0


def test_네이버_실패한_날은_소멸로_찍히지_않는다(tmp_path, wl, monkeypatch):
    monkeypatch.setattr("src.run_daily.collect_all", lambda *a, **k: [])
    db = tmp_path / "d.db"
    monkeypatch.setattr("src.run_daily.collect_naver_all",
                        lambda t: ({"장위 재개발 일대": [L()]}, []))
    run(date(2026, 9, 29), db, wl, {"KOR_API_KEY": "x"}, dry_run=True)

    monkeypatch.setattr("src.run_daily.collect_naver_all", lambda t: ({}, ["호가 미수집"]))
    s = run(date(2026, 9, 30), db, wl, {"KOR_API_KEY": "x"}, dry_run=True)
    assert s.counts["소멸매물"] == 0
    conn = init_db(db)
    row = conn.execute("SELECT last_seen FROM listings WHERE listing_no='1'").fetchone()
    assert row["last_seen"] == "2026-09-29"


def test_가격조정이_카운트와_줄에_들어간다(tmp_path, wl, monkeypatch):
    monkeypatch.setattr("src.run_daily.collect_all", lambda *a, **k: [])
    db = tmp_path / "d.db"
    monkeypatch.setattr("src.run_daily.collect_naver_all",
                        lambda t: ({"장위 재개발 일대": [L()]}, []))
    run(date(2026, 9, 29), db, wl, {"KOR_API_KEY": "x"}, dry_run=True)

    monkeypatch.setattr("src.run_daily.collect_naver_all",
                        lambda t: ({"장위 재개발 일대": [L(price=360_000_000)]}, []))
    s = run(date(2026, 9, 30), db, wl, {"KOR_API_KEY": "x"}, dry_run=True)
    assert s.counts["가격조정"] == 1
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `python -m pytest tests/test_run_daily_listings.py -v`
Expected: FAIL — `AttributeError: module 'src.run_daily' has no attribute 'collect_naver_all'`

- [ ] **Step 3: run_daily.py에 호가 단계를 추가**

임포트에 추가한다:

```python
from src.collect_naver import collect_all as collect_naver_all
from src.store import listing_counts, upsert_listings
```

국토부 단계 뒤, 렌더 단계 앞에 넣는다:

```python
    listing_rows: list[dict] = []
    change_rows: list[dict] = []
    n_new = n_gone = n_chg = 0

    if targets:
        t3 = time.monotonic()
        by_area, naver_warns = collect_naver_all(targets)
        warnings.extend(naver_warns)
        for area_name, rows in by_area.items():
            prev = listing_counts(conn, area_name, (today - timedelta(days=1)).isoformat())
            d = upsert_listings(conn, rows, area_name, today_s)
            n_new += len(d.new)
            n_gone += len(d.gone)
            n_chg += len(d.price_changes)

            now = listing_counts(conn, area_name, today_s)
            for deal_type, count in sorted(now.items()):
                before = prev.get(deal_type, 0)
                delta = count - before
                lows = [r.price for r in rows if r.deal_type == deal_type]
                listing_rows.append({
                    "area_name": area_name, "deal_type": deal_type, "count": count,
                    "prev": before,
                    "delta_text": f"{delta:+d}" if delta else "―",
                    "new": sum(1 for r in d.new if r.deal_type == deal_type),
                    "gone": sum(1 for r in d.gone if r["deal_type"] == deal_type),
                    "low_text": _money(min(lows)) if lows else "―",
                })

            for c in d.price_changes:
                change_rows.append({
                    "name": c.name, "down": c.now < c.prev,
                    "delta_text": f"{(c.now - c.prev) / c.prev * 100:+.1f}%",
                    "prev_text": _money(c.prev), "now_text": _money(c.now),
                    "days": c.days, "times": c.times,
                })

        log_run(conn, "naver", not naver_warns, n_new + n_chg, time.monotonic() - t3,
                "; ".join(naver_warns) or None)

    counts.update({"신규매물": n_new, "소멸매물": n_gone, "가격조정": n_chg})
```

`counts` 초기화를 `{"신규신고": ..., "계약파기": ...}`로 두고 위에서 `update`하되, 렌더 컨텍스트 순서가 스펙 8절과 맞도록 최종 조립 시 재정렬한다:

```python
    counts = {k: counts.get(k, 0) for k in
              ("신규신고", "신규매물", "소멸매물", "가격조정", "계약파기")}
```

`build_context` 호출에 `listing_sections=listing_rows, price_changes=change_rows`를 넘긴다. 파일 상단에 `from datetime import timedelta`를 추가한다.

- [ ] **Step 4: 전체 테스트 통과 확인**

Run: `python -m pytest tests/ -v`
Expected: PASS (전체)

- [ ] **Step 5: 실제로 돌려 호가 섹션을 확인**

```bash
python -m src.run_daily
```
Expected: 브리핑 이미지에 "호가 동향" 표가 나타난다

- [ ] **Step 6: 커밋**

```bash
git add src/run_daily.py tests/test_run_daily_listings.py
git commit -m "feat: 호가 동향·가격 조정 섹션 (3주차 완료)"
```

---

## Task 13: 지방 온도계

**Files:**
- Modify: `src/judge.py`, `src/run_daily.py`
- Test: `tests/test_judge_thermo.py`

**Interfaces:**
- Consumes: `store` 조회 (T2), `models.Target` (T1)
- Produces:
  - `judge.Thermo(name, recent_counts: list[tuple[str, int]], yoy: int | None, pyeong: int | None, trend_pct: float | None, from_high_pct: float | None)`
  - `judge.thermometer(conn, target: Target, today: date) -> Thermo`

평당가는 `price / (area / 3.3058)`로 계산한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_judge_thermo.py`:
```python
from datetime import date
import pytest
from src.judge import thermometer
from src.models import Target, TradeRow
from src.store import init_db, upsert_trades


def target() -> Target:
    return Target(name="대전 둔산 크로바", kind="단지", lawd_cd="30170",
                  dongs=["둔산동"], complex_name="크로바", coord=None,
                  asset=None, region="지방")


def t(price: int, deal_date: str, area: float = 84.9) -> TradeRow:
    return TradeRow(lawd_cd="30170", dong="둔산동", complex_name="크로바",
                    deal_date=deal_date, area=area, floor=10, price=price,
                    deal_kind="중개거래", released=False, released_date=None,
                    register_date=None, asset="APT")


@pytest.fixture
def conn(tmp_path):
    return init_db(tmp_path / "th.db")


TODAY = date(2026, 9, 29)


def test_최근_3개월_월별_거래량을_센다(conn):
    upsert_trades(conn, [t(700_000_000, "2026-09-05"), t(710_000_000, "2026-09-20"),
                         t(690_000_000, "2026-08-11"), t(680_000_000, "2026-07-02")],
                  today="2026-09-29")
    th = thermometer(conn, target(), TODAY)
    assert th.recent_counts == [("2026-07", 1), ("2026-08", 1), ("2026-09", 2)]


def test_전년_동월_대비를_센다(conn):
    upsert_trades(conn, [t(700_000_000, "2026-09-05"), t(710_000_000, "2026-09-20"),
                         t(600_000_000, "2025-09-10")],
                  today="2026-09-29")
    th = thermometer(conn, target(), TODAY)
    assert th.yoy == 1


def test_거래가_없으면_전부_None이다(conn):
    th = thermometer(conn, target(), TODAY)
    assert th.recent_counts == [("2026-07", 0), ("2026-08", 0), ("2026-09", 0)]
    assert th.pyeong is None
    assert th.trend_pct is None
    assert th.from_high_pct is None


def test_평당가를_계산한다(conn):
    upsert_trades(conn, [t(700_000_000, "2026-09-05", area=84.9)], today="2026-09-29")
    th = thermometer(conn, target(), TODAY)
    assert th.pyeong == pytest.approx(27_257_000, rel=0.01)


def test_1년_최고가_대비를_계산한다(conn):
    upsert_trades(conn, [t(800_000_000, "2026-02-01"), t(700_000_000, "2026-09-05")],
                  today="2026-09-29")
    th = thermometer(conn, target(), TODAY)
    assert th.from_high_pct == pytest.approx(-12.5, abs=0.1)


def test_해제된_거래는_빠진다(conn):
    upsert_trades(conn, [t(700_000_000, "2026-09-05")], today="2026-09-29")
    conn.execute("UPDATE trades SET released = 1")
    conn.commit()
    th = thermometer(conn, target(), TODAY)
    assert th.recent_counts[-1][1] == 0
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `python -m pytest tests/test_judge_thermo.py -v`
Expected: FAIL — `ImportError: cannot import name 'thermometer'`

- [ ] **Step 3: judge.py에 thermometer 추가**

```python
PYEONG = 3.3058


@dataclass(frozen=True)
class Thermo:
    name: str
    recent_counts: list[tuple[str, int]]
    yoy: int | None
    pyeong: int | None
    trend_pct: float | None
    from_high_pct: float | None


def _months_back(today: date, n: int) -> list[str]:
    out, y, m = [], today.year, today.month
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return list(reversed(out))


def _where(target) -> tuple[str, tuple]:
    sql = "FROM trades WHERE lawd_cd = ? AND released = 0"
    params: list = [target.lawd_cd]
    if target.complex_name:
        sql += " AND complex_name = ?"
        params.append(target.complex_name)
    return sql, tuple(params)


def thermometer(conn, target, today: date) -> Thermo:
    base, params = _where(target)
    months = _months_back(today, 3)

    counts = []
    for ym in months:
        n = conn.execute(
            f"SELECT COUNT(*) {base} AND substr(deal_date, 1, 7) = ?", params + (ym,)
        ).fetchone()[0]
        counts.append((ym, n))

    this_ym = months[-1]
    last_year_ym = f"{today.year - 1:04d}-{today.month:02d}"
    yoy_prev = conn.execute(
        f"SELECT COUNT(*) {base} AND substr(deal_date, 1, 7) = ?", params + (last_year_ym,)
    ).fetchone()[0]
    yoy = counts[-1][1] - yoy_prev if (counts[-1][1] or yoy_prev) else None

    recent = conn.execute(
        f"SELECT price, area {base} AND deal_date >= DATE(?, '-90 days') ORDER BY deal_date DESC",
        params + (today.isoformat(),),
    ).fetchall()
    pyeong = None
    if recent:
        vals = [r["price"] / (r["area"] / PYEONG) for r in recent if r["area"]]
        pyeong = int(sum(vals) / len(vals)) if vals else None

    older = conn.execute(
        f"""SELECT price, area {base}
             AND deal_date < DATE(?, '-90 days') AND deal_date >= DATE(?, '-180 days')""",
        params + (today.isoformat(), today.isoformat()),
    ).fetchall()
    trend = None
    if pyeong and older:
        vals = [r["price"] / (r["area"] / PYEONG) for r in older if r["area"]]
        if vals:
            prev = sum(vals) / len(vals)
            trend = round((pyeong - prev) / prev * 100, 1)

    high_row = conn.execute(
        f"SELECT MAX(price) AS p {base} AND deal_date >= DATE(?, '-365 days')",
        params + (today.isoformat(),),
    ).fetchone()
    last_row = conn.execute(
        f"SELECT price {base} ORDER BY deal_date DESC LIMIT 1", params
    ).fetchone()
    from_high = None
    if high_row and high_row["p"] and last_row:
        from_high = round((last_row["price"] - high_row["p"]) / high_row["p"] * 100, 1)

    return Thermo(target.name, counts, yoy, pyeong, trend, from_high)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_judge_thermo.py -v`
Expected: PASS (6개)

- [ ] **Step 5: run_daily.py에서 지방 온도계를 조립**

렌더 직전에 넣는다:

```python
from src.judge import thermometer

    thermo_rows = []
    for tgt in [t for t in targets if t.region == "지방"]:
        th = thermometer(conn, tgt, today)
        thermo_rows.append({
            "name": th.name,
            "recent_counts": " · ".join(f"{ym[5:]}월 {n}건" for ym, n in th.recent_counts),
            "yoy_text": (f"{th.yoy:+d}건" if th.yoy is not None else "―"),
            "pyeong_text": (f"{th.pyeong // 10_000:,}만" if th.pyeong else "―"),
            "trend_text": (f"{th.trend_pct:+.1f}%" if th.trend_pct is not None else "―"),
            "from_high_text": (f"{th.from_high_pct:+.1f}%" if th.from_high_pct is not None else "―"),
        })
```

`build_context` 호출에 `thermometer=thermo_rows`를 넘긴다.

- [ ] **Step 6: 전체 테스트 통과 확인**

Run: `python -m pytest tests/ -v`
Expected: PASS (전체)

- [ ] **Step 7: 커밋**

```bash
git add src/judge.py src/run_daily.py tests/test_judge_thermo.py
git commit -m "feat: 지방 온도계 (월별 거래량, 전년 동월, 평당가 추세, 전고점 대비)"
```

---

## Task 14: 실패 처리 마감

**Files:**
- Modify: `src/run_daily.py`
- Test: `tests/test_failures.py`

**Interfaces:**
- Consumes: 앞선 전부
- Produces: 없음 (동작 보강)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_failures.py`:
```python
from datetime import date
from pathlib import Path
import textwrap
import pytest
from src.collect_kor import KorApiError
from src.run_daily import run
from src.store import init_db


@pytest.fixture
def wl(tmp_path) -> Path:
    p = tmp_path / "w.yml"
    p.write_text(textwrap.dedent("""
        수도권:
          - 이름: 장위 재개발 일대
            유형: 구역
            lawd_cd: "11290"
            법정동: ["장위동"]
            좌표: {lat: 37.6130, lon: 127.0480, zoom: 17}
            자산: "VL"
    """), encoding="utf-8")
    return p


def test_둘다_실패해도_요약은_나온다(tmp_path, wl, monkeypatch):
    def kor_boom(*a, **k):
        raise KorApiError("인증키 미승인")
    monkeypatch.setattr("src.run_daily.collect_all", kor_boom)
    monkeypatch.setattr("src.run_daily.collect_naver_all", lambda t: ({}, ["호가 미수집"]))
    s = run(date(2026, 9, 29), tmp_path / "d.db", wl, {"KOR_API_KEY": "x"}, dry_run=True)
    assert len(s.warnings) == 2
    assert s.headline


def test_렌더가_터져도_Slack은_시도된다(tmp_path, wl, monkeypatch):
    sent = {}
    monkeypatch.setattr("src.run_daily.collect_all", lambda *a, **k: [])
    monkeypatch.setattr("src.run_daily.collect_naver_all", lambda t: ({}, []))
    monkeypatch.setattr("src.run_daily.html_to_png",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("chromium 없음")))
    monkeypatch.setattr("src.run_daily.post",
                        lambda *a, **k: sent.update({"called": True, "png": k.get("png_path")}))
    s = run(date(2026, 9, 29), tmp_path / "d.db", wl,
            {"KOR_API_KEY": "x", "SLACK_BOT_TOKEN": "t", "SLACK_CHANNEL": "#c"})
    assert sent["called"] is True
    assert sent["png"] is None
    assert any("렌더" in w for w in s.warnings)


def test_Slack이_터져도_예외가_안_새고_runs에_남는다(tmp_path, wl, monkeypatch):
    monkeypatch.setattr("src.run_daily.collect_all", lambda *a, **k: [])
    monkeypatch.setattr("src.run_daily.collect_naver_all", lambda t: ({}, []))
    monkeypatch.setattr("src.run_daily.html_to_png", lambda h, p, **k: p)
    monkeypatch.setattr("src.run_daily.post",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("채널 없음")))
    db = tmp_path / "d.db"
    run(date(2026, 9, 29), db, wl, {"KOR_API_KEY": "x", "SLACK_BOT_TOKEN": "t", "SLACK_CHANNEL": "#c"})
    conn = init_db(db)
    row = conn.execute("SELECT ok, err FROM runs WHERE stage = 'slack'").fetchone()
    assert row["ok"] == 0
    assert "채널 없음" in row["err"]
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `python -m pytest tests/test_failures.py -v`
Expected: FAIL — 렌더 실패 시 `png_path`가 정리되지 않거나 경고가 안 붙는다

- [ ] **Step 3: run_daily.py의 렌더 예외 처리를 보강**

렌더 `except` 블록에서 `png_path = None`을 명시적으로 세팅하고, `warnings`에 붙인 뒤 `build_blocks`를 **렌더 이후에** 만들도록 순서를 확인한다(경고가 블록에 반영되어야 한다).

```python
    except Exception as e:
        log.exception("렌더 실패")
        png_path = None
        warnings.append("이미지 렌더 실패 — 텍스트만 보냅니다")
        log_run(conn, "render", False, 0, time.monotonic() - t2, str(e))

    summary = Summary(headline=headline, counts=counts, lines=lines, warnings=warnings)
```

`Summary` 생성을 렌더 블록 **뒤로** 옮긴다.

- [ ] **Step 4: 전체 테스트 통과 확인**

Run: `python -m pytest tests/ -v`
Expected: PASS (전체)

- [ ] **Step 5: 커밋**

```bash
git add src/run_daily.py tests/test_failures.py
git commit -m "feat: 실패 처리 마감 (렌더 실패 시 텍스트 발송, Slack 실패 기록)"
```

---

## Task 15: 작업 스케줄러 등록 — 4주차 완료

**Files:**
- Create: `scripts/run_daily.bat`, `scripts/register_task.ps1`, `README.md`
- Test: 수동 검증

**Interfaces:**
- Consumes: `run_daily.main` (T5)
- Produces: 없음 (운영 스크립트)

- [ ] **Step 1: 배치 래퍼 작성**

`scripts/run_daily.bat`:
```bat
@echo off
cd /d "%~dp0.."
call .venv\Scripts\activate.bat 2>nul
python -m src.run_daily >> "briefings\run.log" 2>&1
exit /b %errorlevel%
```

- [ ] **Step 2: 등록 스크립트 작성**

`scripts/register_task.ps1`:
```powershell
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$bat  = Join-Path $root "scripts\run_daily.bat"

$action   = New-ScheduledTaskAction -Execute $bat
$trigger  = New-ScheduledTaskTrigger -Daily -At 8:00am
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopOnIdleEnd `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName "부동산일일브리핑" `
    -Action $action -Trigger $trigger -Settings $settings -Force

Write-Host "등록 완료. 즉시 실행으로 확인하려면:"
Write-Host "  Start-ScheduledTask -TaskName '부동산일일브리핑'"
```

`-StartWhenAvailable`이 스펙 6절의 "PC가 꺼져 있었으면 다음 부팅 시 밀린 분 실행"을 담당한다. `-MultipleInstances IgnoreNew`가 중복 실행을 막는다.

- [ ] **Step 3: 등록하고 즉시 실행으로 확인**

```powershell
powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1
Start-ScheduledTask -TaskName "부동산일일브리핑"
Get-ScheduledTaskInfo -TaskName "부동산일일브리핑" | Select LastRunTime, LastTaskResult
```
Expected: `LastTaskResult`가 `0`이고 Slack에 메시지가 도착한다

- [ ] **Step 4: README 작성**

`README.md`에 다음을 적는다 — 설치 순서(`pip install -r requirements.txt`, `playwright install chromium`, `vendor/anti_bot_scraper` 클론), `.env` 항목, `watchlist.yml` 채우는 법과 좌표 얻는 법, 수동 실행(`python -m src.run_daily --dry-run`), 과거 날짜 재렌더, 작업 스케줄러 등록·해제(`Unregister-ScheduledTask -TaskName "부동산일일브리핑"`), 그리고 스펙 10절의 개인정보·약관 취급 방침 요약.

- [ ] **Step 5: 전체 테스트 통과 확인**

Run: `python -m pytest tests/ -v`
Expected: PASS (전체)

- [ ] **Step 6: 커밋**

```bash
git add scripts/ README.md
git commit -m "feat: 작업 스케줄러 등록과 운영 문서 (4주차 완료)"
```

---

## 자체 점검 결과

**스펙 커버리지** — 15절 전부 태스크에 매핑됨. 3절 watchlist→T1, 4절A→T3, 4절B→T11, 5절→T2·T10, 6절→T5·T12·T13, 7절→T6·T7·T13·T10, 8절→T8·T12·T13, 9절→T14, 10절→T11(PII 제거)·T15(README), 11절→T3·T4 수동 단계, 12절→T5·T9·T12·T15 완료 지점, 13절→T1, 14절→File Structure, 15절→각 태스크 테스트.

**Review Focus 배치** — 5개 항목 모두 테스트로 고정됨: 에러 본문(T3 `test_에러_본문은_거래0건이_아니라_예외다`), 멱등성(T2 `test_같은_날_두번_실행해도...`, T5 `test_같은_날_두번_돌리면...`), 연 경계(T3 `test_연_경계를_넘어간다`), 금액 파싱(T3 `test_금액이_지저분하면_ValueError`·`test_금액이_깨진_항목은_건너뛰고...`), xlsx 부재(T11 `test_xlsx가_없으면_None을_돌려준다`).

**미검증 전제 하나** — Task 3 Step 1에서 실제 API 응답의 태그명을 확인하기 전까지 `collect_kor.py`의 `_text()` 태그명은 추정이다. Step 1이 이 추정을 사실로 바꾸는 단계이며, Step 5에 태그명이 다를 경우의 처리(테스트가 아니라 코드를 고친다)를 명시해뒀다.
