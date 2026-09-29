from dataclasses import dataclass
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
        "INSERT INTO runs (ran_at, stage, ok, count, secs, err) "
        "VALUES (datetime('now','localtime'),?,?,?,?,?)",
        (stage, int(ok), count, secs, err),
    )
    conn.commit()
