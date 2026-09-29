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
