from dataclasses import dataclass
from datetime import date

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


def _avg_pyeong(rows) -> float | None:
    vals = [r["price"] / (r["area"] / PYEONG) for r in rows if r["area"]]
    return sum(vals) / len(vals) if vals else None


def thermometer(conn, target, today: date) -> Thermo:
    base, params = _where(target)
    months = _months_back(today, 3)

    counts = []
    for ym in months:
        n = conn.execute(
            f"SELECT COUNT(*) {base} AND substr(deal_date, 1, 7) = ?", params + (ym,)
        ).fetchone()[0]
        counts.append((ym, n))

    last_year_ym = f"{today.year - 1:04d}-{today.month:02d}"
    yoy_prev = conn.execute(
        f"SELECT COUNT(*) {base} AND substr(deal_date, 1, 7) = ?",
        params + (last_year_ym,),
    ).fetchone()[0]
    yoy = counts[-1][1] - yoy_prev if (counts[-1][1] or yoy_prev) else None

    iso = today.isoformat()
    recent = conn.execute(
        f"SELECT price, area {base} AND deal_date >= DATE(?, '-90 days')",
        params + (iso,),
    ).fetchall()
    pyeong_f = _avg_pyeong(recent)
    pyeong = int(pyeong_f) if pyeong_f else None

    older = conn.execute(
        f"""SELECT price, area {base}
             AND deal_date < DATE(?, '-90 days') AND deal_date >= DATE(?, '-180 days')""",
        params + (iso, iso),
    ).fetchall()
    prev_pyeong = _avg_pyeong(older)
    trend = (round((pyeong_f - prev_pyeong) / prev_pyeong * 100, 1)
             if pyeong_f and prev_pyeong else None)

    high_row = conn.execute(
        f"SELECT MAX(price) AS p {base} AND deal_date >= DATE(?, '-365 days')",
        params + (iso,),
    ).fetchone()
    last_row = conn.execute(
        f"SELECT price {base} ORDER BY deal_date DESC LIMIT 1", params
    ).fetchone()
    from_high = None
    if high_row and high_row["p"] and last_row:
        from_high = round((last_row["price"] - high_row["p"]) / high_row["p"] * 100, 1)

    return Thermo(target.name, counts, yoy, pyeong, trend, from_high)
