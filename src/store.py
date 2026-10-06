from dataclasses import dataclass
from datetime import date as _date
from pathlib import Path
import hashlib
import sqlite3

from src.models import ListingRow, TradeRow

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


def recent_trades(conn, lawd_cd: str, dong: str, complex_name: str,
                  area: float, before: str, days: int) -> list[sqlite3.Row]:
    """같은 단지·같은 전용면적의 과거 거래.

    단지 식별은 (lawd_cd, dong, complex_name) 셋 다 써야 한다. 이름만 쓰면
    성북구 래미안과 대전 래미안이 같은 단지로 묶여 신고가·하락이 엉뚱하게 나온다.
    """
    return conn.execute(
        """SELECT price, floor, deal_date FROM trades
            WHERE lawd_cd = ? AND dong = ? AND complex_name = ?
              AND ROUND(area, 2) = ROUND(?, 2)
              AND deal_date < ?
              AND deal_date >= DATE(?, ?)
              AND released = 0
            ORDER BY price DESC""",
        (lawd_cd, dong, complex_name, area, before, before, f"-{days} days"),
    ).fetchall()


MIN_FLOOR_SAMPLE = 5


def max_floor(conn, lawd_cd: str, dong: str, complex_name: str) -> int | None:
    """단지의 관측 최고층. 표본이 적으면 None.

    거래 1~2건으로 최고층을 단정하면 그 단지의 첫 거래마다 '최상층' 뱃지가
    잘못 붙는다. 뱃지가 가격 해석을 바꾸므로 모를 때는 말하지 않는다.
    """
    row = conn.execute(
        """SELECT MAX(floor) AS f, COUNT(DISTINCT floor) AS n FROM trades
            WHERE lawd_cd = ? AND dong = ? AND complex_name = ? AND floor IS NOT NULL""",
        (lawd_cd, dong, complex_name),
    ).fetchone()
    if not row or row["f"] is None or row["n"] < MIN_FLOOR_SAMPLE:
        return None
    return row["f"]


def log_run(conn, stage: str, ok: bool, count: int, secs: float, err: str | None) -> None:
    conn.execute(
        "INSERT INTO runs (ran_at, stage, ok, count, secs, err) "
        "VALUES (datetime('now','localtime'),?,?,?,?,?)",
        (stage, int(ok), count, secs, err),
    )
    conn.commit()


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
            "SELECT price, last_seen, first_seen, listed_date FROM listings "
            "WHERE listing_no = ?",
            (row.listing_no,),
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
            origin = prior["listed_date"] or prior["first_seen"]
            days = (_date.fromisoformat(today) - _date.fromisoformat(origin)).days
            changes.append(PriceChange(row.listing_no, row.name,
                                       prior["price"], row.price, days, times))

        conn.execute(
            """UPDATE listings SET price = ?, features = ?, prev_jeonse = ?, gap = ?,
                   gap_ratio = ?, last_seen = ? WHERE listing_no = ?""",
            (row.price, row.features, row.prev_jeonse, row.gap, row.gap_ratio,
             today, row.listing_no),
        )

    gone = conn.execute(
        "SELECT * FROM listings WHERE area_name = ? AND last_seen = DATE(?, '-1 day')",
        (area_name, today),
    ).fetchall()

    conn.commit()

    counts_rows = conn.execute(
        "SELECT area_name, deal_type, COUNT(*) AS n FROM listings "
        "WHERE last_seen = ? GROUP BY 1, 2",
        (today,),
    ).fetchall()
    counts = {(r["area_name"], r["deal_type"]): r["n"] for r in counts_rows}

    return ListingDiff(new=new, gone=gone, price_changes=changes, counts=counts)


def listing_counts(conn, area_name: str, on: str) -> dict[str, int]:
    """그 날짜에 살아 있던 매물 수를 거래유형별로.

    last_seen == on 으로 세면 안 된다. upsert 가 살아남은 매물의 last_seen 을
    오늘로 밀어버리므로, 같은 날 두 번 돌리면 어제 수가 0으로 쪼그라든다.
    생존 구간(first_seen <= on <= last_seen)으로 세면 그 영향을 받지 않는다.
    """
    rows = conn.execute(
        "SELECT deal_type, COUNT(*) AS n FROM listings "
        "WHERE area_name = ? AND first_seen <= ? AND last_seen >= ? GROUP BY 1",
        (area_name, on, on),
    ).fetchall()
    return {r["deal_type"]: r["n"] for r in rows}
