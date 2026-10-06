from datetime import date
import pytest
from src.judge import thermometer
from src.models import Target, TradeRow
from src.store import init_db, upsert_trades


def target(**kw) -> Target:
    base = dict(name="대전 둔산 크로바", kind="단지", lawd_cd="30170",
                dongs=["둔산동"], complex_name="크로바", coord=None,
                asset=None, region="지방")
    base.update(kw)
    return Target(**base)


def t(price: int, deal_date: str, area: float = 84.9, complex_name="크로바") -> TradeRow:
    return TradeRow(lawd_cd="30170", dong="둔산동", complex_name=complex_name,
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
    assert th.yoy is None


def test_평당가를_계산한다(conn):
    upsert_trades(conn, [t(700_000_000, "2026-09-05", area=84.9)], today="2026-09-29")
    th = thermometer(conn, target(), TODAY)
    assert th.pyeong == pytest.approx(27_257_000, rel=0.01)


def test_1년_최고가_대비를_계산한다(conn):
    # 표본이 MIN_SAMPLE 이상일 때만 전고점 대비를 말한다 (리뷰 I7)
    upsert_trades(conn, [t(800_000_000, "2026-02-01"), t(750_000_000, "2026-04-01"),
                         t(700_000_000, "2026-09-05")],
                  today="2026-09-29")
    th = thermometer(conn, target(), TODAY)
    assert th.from_high_pct == pytest.approx(-12.5, abs=0.1)


def test_해제된_거래는_빠진다(conn):
    upsert_trades(conn, [t(700_000_000, "2026-09-05")], today="2026-09-29")
    conn.execute("UPDATE trades SET released = 1")
    conn.commit()
    th = thermometer(conn, target(), TODAY)
    assert th.recent_counts[-1][1] == 0


def test_다른_단지_거래는_섞이지_않는다(conn):
    upsert_trades(conn, [t(700_000_000, "2026-09-05"),
                         t(900_000_000, "2026-09-06", complex_name="다른단지")],
                  today="2026-09-29")
    th = thermometer(conn, target(), TODAY)
    assert th.recent_counts[-1][1] == 1


def test_3개월_추세는_직전_3개월과_비교한다(conn):
    # 최근 90일 평당가 vs 그 이전 90일 평당가
    upsert_trades(conn, [t(600_000_000, "2026-04-15"),   # 이전 구간
                         t(700_000_000, "2026-09-05")],  # 최근 구간
                  today="2026-09-29")
    th = thermometer(conn, target(), TODAY)
    assert th.trend_pct == pytest.approx(16.7, abs=0.2)


def test_연_경계에서_월_목록이_맞다(conn):
    th = thermometer(conn, target(), date(2026, 1, 15))
    assert [ym for ym, _ in th.recent_counts] == ["2025-11", "2025-12", "2026-01"]
