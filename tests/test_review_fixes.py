"""전체-브랜치 리뷰에서 나온 결함들의 회귀 테스트."""
from datetime import date
from pathlib import Path
import json
import textwrap

import pytest

from src.collect_kor import KorApiError
from src.models import ListingRow, Target, TradeRow
from src.run_daily import run
from src.store import init_db, upsert_listings, upsert_trades

ENV = {"KOR_API_KEY": "x", "SLACK_BOT_TOKEN": "t", "SLACK_CHANNEL": "#c"}


def wl_file(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "w.yml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


GOOD_WL = """
    수도권:
      - 이름: 장위 재개발 일대
        유형: 구역
        lawd_cd: "11290"
        법정동: ["장위동"]
        좌표: {lat: 37.6130, lon: 127.0480, zoom: 17}
        자산: "VL"
"""


def L(**kw) -> ListingRow:
    base = dict(listing_no="1", name="장위빌라", deal_type="매매", price=380_000_000,
                floor="2/4", area=52.1, exclusive_area=42.1, direction="남동",
                features="", listed_date="2026-09-01", prev_jeonse=None, gap=None,
                gap_ratio=None, area_name="장위 재개발 일대")
    base.update(kw)
    return ListingRow(**base)


def T(**kw) -> TradeRow:
    base = dict(lawd_cd="11290", dong="장위동", complex_name="래미안",
                deal_date="2026-09-14", area=84.93, floor=15, price=1_120_000_000,
                deal_kind="중개거래", released=False, released_date=None,
                register_date=None, asset="APT")
    base.update(kw)
    return TradeRow(**base)


# ---------------------------------------------------------------- C3
def test_C3_watchlist가_깨져도_Slack은_나간다(tmp_path, monkeypatch):
    """lawd_cd 오타 하나로 아침이 통째로 조용해지면 안 된다."""
    bad = wl_file(tmp_path, """
        수도권:
          - 이름: 오타
            lawd_cd: "1129"
            법정동: ["장위동"]
    """)
    sent = {}
    monkeypatch.setattr("src.run_daily.html_to_png", lambda h, p, **k: p)
    monkeypatch.setattr("src.run_daily.post", lambda *a, **k: sent.update(called=True))
    s = run(date(2026, 9, 29), tmp_path / "d.db", bad, ENV)
    assert sent.get("called") is True
    assert any("watchlist" in w for w in s.warnings)


def test_C3_가격0인_매물이_있어도_죽지_않는다(tmp_path, monkeypatch):
    """vendor 스크레이퍼는 월세 매물의 매매금액을 0으로 쓴다."""
    db = tmp_path / "d.db"
    wl = wl_file(tmp_path, GOOD_WL)
    monkeypatch.setattr("src.run_daily.collect_naver_all",
                        lambda t: ({"장위 재개발 일대": [L(price=0, deal_type="월세")]}, []))
    monkeypatch.setattr("src.run_daily.html_to_png", lambda h, p, **k: p)
    sent = {}
    monkeypatch.setattr("src.run_daily.post", lambda *a, **k: sent.update(called=True))
    run(date(2026, 9, 29), db, wl, ENV)
    monkeypatch.setattr("src.run_daily.collect_naver_all",
                        lambda t: ({"장위 재개발 일대": [L(price=50_000_000,
                                                          deal_type="월세")]}, []))
    run(date(2026, 9, 30), db, wl, ENV)
    assert sent.get("called") is True


# ---------------------------------------------------------------- I12
def test_I12_수집실패면_조용하다고_말하지_않는다(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise KorApiError("인증키 미승인")
    seen = {}
    monkeypatch.setattr("src.run_daily.collect_all", boom)
    monkeypatch.setattr("src.run_daily.html_to_png", lambda h, p, **k: p)
    monkeypatch.setattr("src.run_daily.post",
                        lambda tok, ch, blocks, text, **k: seen.update(blocks=blocks))
    s = run(date(2026, 9, 29), tmp_path / "d.db", wl_file(tmp_path, GOOD_WL), ENV)
    body = json.dumps(seen["blocks"], ensure_ascii=False)
    assert "조용" not in body
    assert "수집" in body
    assert s.warnings


def test_I12_진짜_조용한_날은_조용하다고_말한다(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr("src.run_daily.html_to_png", lambda h, p, **k: p)
    monkeypatch.setattr("src.run_daily.post",
                        lambda tok, ch, blocks, text, **k: seen.update(blocks=blocks))
    run(date(2026, 9, 29), tmp_path / "d.db", wl_file(tmp_path, GOOD_WL), ENV)
    assert "조용" in json.dumps(seen["blocks"], ensure_ascii=False)


# ---------------------------------------------------------------- C4
def test_C4_다른_시군구_동명이_단지와_비교하지_않는다(tmp_path):
    from src.judge import judge_price
    conn = init_db(tmp_path / "c4.db")
    seoul = [T(price=1_100_000_000, deal_date="2026-03-01"),
             T(price=1_150_000_000, deal_date="2026-05-01"),
             T(price=1_200_000_000, deal_date="2026-07-01")]
    upsert_trades(conn, seoul, "2026-09-28")
    daejeon = T(lawd_cd="30170", dong="둔산동", price=700_000_000,
                deal_date="2026-09-14")
    v = judge_price(conn, daejeon)
    assert v.kind == "표본부족", f"서울 래미안과 비교했다: {v}"
    assert v.sample == 0


# ---------------------------------------------------------------- C5
def test_C5_관심_법정동_밖의_거래는_걸러진다():
    from src.collect_kor import filter_by_watchlist
    targets = [Target("장위 재개발 일대", "구역", "11290", ["장위동"], None,
                      None, "VL", "수도권")]
    rows = [T(dong="장위동", complex_name="장위빌라"),
            T(dong="석관동", complex_name="석관아파트")]
    kept = filter_by_watchlist(rows, targets)
    assert [r.dong for r in kept] == ["장위동"]


def test_C5_단지명이_지정되면_그_단지만_남는다():
    from src.collect_kor import filter_by_watchlist
    targets = [Target("래미안장위", "단지", "11290", ["장위동"], "래미안",
                      None, "APT", "수도권")]
    rows = [T(dong="장위동", complex_name="래미안"),
            T(dong="장위동", complex_name="장위현대")]
    kept = filter_by_watchlist(rows, targets)
    assert [r.complex_name for r in kept] == ["래미안"]


def test_C5_지방_거래는_수도권_상세에_안_들어간다(tmp_path, monkeypatch):
    wl = wl_file(tmp_path, """
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
    """)
    rows = [T(dong="장위동", complex_name="장위빌라", price=400_000_000),
            T(lawd_cd="30170", dong="둔산동", complex_name="크로바",
              price=900_000_000)]
    monkeypatch.setattr("src.run_daily.collect_all", lambda *a, **k: (rows, []))
    monkeypatch.setattr("src.run_daily.html_to_png", lambda h, p, **k: p)
    s = run(date(2026, 9, 29), tmp_path / "d.db", wl, ENV, dry_run=True)
    assert s.counts["신규신고"] == 1, "지방 거래가 수도권 상세로 샜다"
    assert "크로바" not in s.headline


# ---------------------------------------------------------------- I4
def test_I4_resultCode_000은_성공이다():
    from src.collect_kor import parse_trades
    xml = """<response><header><resultCode>000</resultCode><resultMsg>OK</resultMsg></header>
      <body><items><item><거래금액>11,200</거래금액><전용면적>84.93</전용면적><층>15</층>
      <년>2026</년><월>9</월><일>14</일><아파트>래미안</아파트><법정동>장위동</법정동>
      </item></items></body></response>"""
    rows = parse_trades(xml, lawd_cd="11290", asset="APT")
    assert len(rows) == 1


# ---------------------------------------------------------------- I1
def test_I1_소멸은_하루만_보고된다(tmp_path):
    conn = init_db(tmp_path / "i1.db")
    upsert_listings(conn, [L(), L(listing_no="999")], "A", "2026-09-29")
    d1 = upsert_listings(conn, [L()], "A", "2026-09-30")
    assert [r["listing_no"] for r in d1.gone] == ["999"]
    d2 = upsert_listings(conn, [L()], "A", "2026-10-01")
    assert d2.gone == [], "같은 소멸을 이틀째 보고했다"


# ---------------------------------------------------------------- I2
def test_I2_같은날_두번_돌려도_전일_매물수가_맞다(tmp_path, monkeypatch):
    from src.run_daily import build_listing_rows
    from src.store import listing_counts
    conn = init_db(tmp_path / "i2.db")
    rows = [L(), L(listing_no="2"), L(listing_no="3")]
    upsert_listings(conn, rows, "A", "2026-09-28")

    prev1 = listing_counts(conn, "A", "2026-09-28")
    d1 = upsert_listings(conn, rows, "A", "2026-09-29")
    s1, _ = build_listing_rows(conn, "A", rows, d1, date(2026, 9, 29), prev1)

    prev2 = listing_counts(conn, "A", "2026-09-28")
    d2 = upsert_listings(conn, rows, "A", "2026-09-29")
    s2, _ = build_listing_rows(conn, "A", rows, d2, date(2026, 9, 29), prev2)

    assert s1[0]["prev"] == 3
    assert s2[0]["prev"] == 3, f"두 번째 실행에서 전일 매물수가 틀렸다: {s2[0]}"
    assert s2[0]["delta_text"] == "―"


# ---------------------------------------------------------------- I3
def test_I3_전부_사라진_거래유형도_행으로_남는다(tmp_path):
    from src.run_daily import build_listing_rows
    from src.store import listing_counts
    conn = init_db(tmp_path / "i3.db")
    day1 = [L(), L(listing_no="2", deal_type="전세", price=300_000_000)]
    upsert_listings(conn, day1, "A", "2026-09-29")

    day2 = [L(listing_no="2", deal_type="전세", price=300_000_000)]  # 매매 전멸
    prev = listing_counts(conn, "A", "2026-09-29")
    d = upsert_listings(conn, day2, "A", "2026-09-30")
    sections, _ = build_listing_rows(conn, "A", day2, d, date(2026, 9, 30), prev)

    by_type = {r["deal_type"]: r for r in sections}
    assert "매매" in by_type, "전멸한 거래유형 행이 통째로 사라졌다"
    assert by_type["매매"]["count"] == 0
    assert by_type["매매"]["prev"] == 1
    assert by_type["매매"]["delta_text"] == "-1"


# ---------------------------------------------------------------- I9
def test_I9_경과일은_등록후_경과일이다(tmp_path):
    conn = init_db(tmp_path / "i9.db")
    upsert_listings(conn, [L(listed_date="2026-09-01")], "A", "2026-09-20")
    upsert_listings(conn, [L(listed_date="2026-09-01")], "A", "2026-09-29")
    d = upsert_listings(conn, [L(listed_date="2026-09-01", price=360_000_000)],
                        "A", "2026-09-30")
    assert d.price_changes[0].days == 29, "관측 간격을 경과일로 썼다"


# ---------------------------------------------------------------- I8
def test_I8_표본이_적으면_최상층_뱃지를_안_단다(tmp_path):
    from src.judge import badges
    from src.store import max_floor
    conn = init_db(tmp_path / "i8.db")
    upsert_trades(conn, [T(floor=15)], "2026-09-29")
    top = max_floor(conn, "11290", "장위동", "래미안")
    assert top is None, "거래 1건으로 단지 최고층을 단정했다"
    assert "최상층" not in badges(T(floor=15), top, date(2026, 9, 29))


def test_I8_표본이_충분하면_최상층을_판정한다(tmp_path):
    from src.store import max_floor
    conn = init_db(tmp_path / "i8b.db")
    upsert_trades(conn, [T(floor=f, price=10_000_000 * f) for f in (3, 7, 11, 18, 25)],
                  "2026-09-29")
    assert max_floor(conn, "11290", "장위동", "래미안") == 25


# ---------------------------------------------------------------- I7
def test_I7_이력이_없으면_전년동월을_만들어내지_않는다(tmp_path):
    from src.judge import thermometer
    conn = init_db(tmp_path / "i7.db")
    upsert_trades(conn, [T(lawd_cd="30170", dong="둔산동", complex_name="크로바",
                           deal_date="2026-09-05", price=700_000_000)], "2026-09-29")
    tgt = Target("대전 둔산 크로바", "단지", "30170", ["둔산동"], "크로바",
                 None, None, "지방")
    th = thermometer(conn, tgt, date(2026, 9, 29))
    assert th.yoy is None, "1년치 이력도 없이 전년 동월 대비를 만들었다"
    assert th.from_high_pct is None, "1건으로 전고점 대비를 만들었다"


# ---------------------------------------------------------------- C2
@pytest.mark.allow_scrape_target
def test_C2_수집0건은_소멸이_아니라_경고다(tmp_path, monkeypatch):
    db = tmp_path / "c2.db"
    wl = wl_file(tmp_path, GOOD_WL)
    monkeypatch.setattr("src.run_daily.html_to_png", lambda h, p, **k: p)
    monkeypatch.setattr("src.run_daily.collect_naver_all",
                        lambda t: ({"장위 재개발 일대": [L(), L(listing_no="2"),
                                                        L(listing_no="3")]}, []))
    run(date(2026, 9, 29), db, wl, ENV, dry_run=True)

    from src.collect_naver import collect_all as real_collect
    monkeypatch.setattr("src.collect_naver.scrape_target", lambda t: [])
    monkeypatch.setattr("src.run_daily.collect_naver_all", real_collect)
    s = run(date(2026, 9, 30), db, wl, ENV, dry_run=True)

    assert s.counts["소멸매물"] == 0, "수집 0건을 전량 소멸로 보고했다"
    assert any("호가" in w for w in s.warnings), "0건 수집에 경고가 없다"


# ---------------------------------------------------------------- I6
def test_I6_일부_호출이_실패해도_성공분은_살린다(monkeypatch):
    from src import collect_kor
    calls = {"n": 0}

    def flaky(api_key, lawd_cd, deal_ymd, asset, session=None):
        calls["n"] += 1
        if calls["n"] == 3:
            raise KorApiError("일시 오류")
        return [T(deal_date=f"2026-0{min(9, calls['n'])}-01", price=calls["n"] * 10**8)]

    monkeypatch.setattr(collect_kor, "fetch_trades", flaky)
    targets = [Target("장위", "구역", "11290", ["장위동"], None, None, "VL", "수도권")]
    rows, warnings = collect_kor.collect_all("k", targets, date(2026, 9, 29))
    assert len(rows) == 5, f"성공분 5건이 남아야 하는데 {len(rows)}건"
    assert warnings


# ---------------------------------------------------------------- I5
def test_I5_totalCount가_크면_다음_페이지를_가져온다(monkeypatch):
    from src import collect_kor
    pages = []

    class FakeResp:
        def __init__(self, body):
            self.text = body
            self.status_code = 200

        def raise_for_status(self):
            pass

    def item(no):
        return (f"<item><거래금액>{no},000</거래금액><전용면적>84.93</전용면적>"
                f"<층>{no}</층><년>2026</년><월>9</월><일>14</일>"
                f"<아파트>래미안</아파트><법정동>장위동</법정동></item>")

    class FakeSession:
        def get(self, url, params=None, timeout=None):
            page = params["pageNo"]
            pages.append(page)
            body = item(page)
            return FakeResp(
                "<response><header><resultCode>00</resultCode></header><body>"
                f"<items>{body}</items><totalCount>3</totalCount>"
                f"<numOfRows>1</numOfRows><pageNo>{page}</pageNo></body></response>")

    monkeypatch.setattr(collect_kor, "PAGE_SIZE", 1)
    rows = collect_kor.fetch_trades("k", "11290", "202609", "APT", FakeSession())
    assert pages == [1, 2, 3], f"페이지를 다 안 돌았다: {pages}"
    assert len(rows) == 3


# ---------------------------------------------------------------- I13
@pytest.mark.allow_scrape_target
def test_I13_스크레이퍼가_터져도_xlsx가_있으면_살린다(tmp_path, monkeypatch):
    """vendor 는 to_excel 뒤 nsmallest 에서 죽는다. 그 결과를 버리면 안 된다."""
    import pandas as pd
    from src import collect_naver

    xlsx = tmp_path / "매물정보_확장_20260929_080000.xlsx"

    def fake_crawl(lat, lon, zoom):
        pd.DataFrame([{"매물번호": "1", "매물명": "장위빌라", "거래유형": "매매",
                       "매매 금액(원)": 380000000}]).to_excel(
            Path.cwd() / xlsx.name, index=False, engine="openpyxl")
        raise TypeError("Column '갭비율' has dtype object")

    monkeypatch.setattr(collect_naver, "_run_scraper", fake_crawl)
    tgt = Target("장위", "구역", "11290", ["장위동"], None,
                 __import__("src.models", fromlist=["Coord"]).Coord(37.6, 127.0, 17),
                 "VL", "수도권")
    rows = collect_naver.scrape_target(tgt)
    assert len(rows) == 1, "xlsx 가 있는데도 결과를 버렸다"


@pytest.mark.allow_scrape_target
def test_I13_xlsx가_아예_없으면_예외가_그대로_올라간다(tmp_path, monkeypatch):
    from src import collect_naver

    def fake_crawl(lat, lon, zoom):
        raise RuntimeError("브라우저 기동 실패")

    monkeypatch.setattr(collect_naver, "_run_scraper", fake_crawl)
    tgt = Target("장위", "구역", "11290", ["장위동"], None,
                 __import__("src.models", fromlist=["Coord"]).Coord(37.6, 127.0, 17),
                 "VL", "수도권")
    with pytest.raises(RuntimeError):
        collect_naver.scrape_target(tgt)


# ---------------------------------------------------------------- I10
def test_I10_과거날짜는_수집하지_않고_다시_렌더한다(tmp_path, monkeypatch):
    called = {"collect": False}

    def spy(*a, **k):
        called["collect"] = True
        return []

    monkeypatch.setattr("src.run_daily.collect_all", spy)
    monkeypatch.setattr("src.run_daily.html_to_png", lambda h, p, **k: p)
    run(date(2020, 1, 1), tmp_path / "i10.db", wl_file(tmp_path, GOOD_WL),
        ENV, dry_run=True, today_is=date(2026, 9, 29))
    assert called["collect"] is False, "과거 날짜인데 실수집을 했다"


# ---------------------------------------------------------------- I10 (재렌더 CLI)
def test_I10_render_CLI가_DB에서_과거_브리핑을_다시_만든다(tmp_path, monkeypatch):
    """스펙 6절: 네트워크 없이 과거 날짜 브리핑을 다시 뽑을 수 있어야 한다."""
    from src.render import rerender

    conn = init_db(tmp_path / "r.db")
    upsert_trades(conn, [T(deal_date="2026-09-14")], "2026-09-28")
    conn.close()

    monkeypatch.setattr("src.render.html_to_png", lambda h, p, **k: p)
    html = rerender(date(2026, 9, 28), tmp_path / "r.db", tmp_path / "out",
                    wl_file(tmp_path, GOOD_WL))
    assert html.exists()
    body = html.read_text(encoding="utf-8")
    assert "2026-09-28" in body
    assert "래미안" in body


# ---------------------------------------------------------------- I11 (Slack 백로그)
def test_I11_Slack이_실패하면_다음_실행에_밀린_건을_보낸다(tmp_path, monkeypatch):
    db = tmp_path / "b.db"
    wl = wl_file(tmp_path, GOOD_WL)
    monkeypatch.setattr("src.run_daily.html_to_png", lambda h, p, **k: p)

    def boom(*a, **k):
        raise RuntimeError("채널 없음")

    monkeypatch.setattr("src.run_daily.post", boom)
    run(date(2026, 9, 29), db, wl, ENV)

    sent = []
    monkeypatch.setattr("src.run_daily.post",
                        lambda tok, ch, blocks, text, **k: sent.append(text))
    run(date(2026, 9, 30), db, wl, ENV)

    assert len(sent) == 2, f"밀린 건을 같이 안 보냈다: {sent}"
    assert any("2026-09-29" in t for t in sent)


def test_I11_이미_보낸_날은_다시_보내지_않는다(tmp_path, monkeypatch):
    db = tmp_path / "b2.db"
    wl = wl_file(tmp_path, GOOD_WL)
    monkeypatch.setattr("src.run_daily.html_to_png", lambda h, p, **k: p)
    sent = []
    monkeypatch.setattr("src.run_daily.post",
                        lambda tok, ch, blocks, text, **k: sent.append(text))
    run(date(2026, 9, 29), db, wl, ENV)
    run(date(2026, 9, 30), db, wl, ENV)
    assert len(sent) == 2
