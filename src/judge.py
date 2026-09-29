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
