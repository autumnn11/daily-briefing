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


def test_최고가와_같으면_보합(conn):
    seed(conn, [t(1_000_000_000, "2026-05-01"), t(1_050_000_000, "2026-06-01"),
                t(1_080_000_000, "2026-07-01")])
    v = judge_price(conn, t(1_080_000_000, "2026-09-14"))
    assert v.kind == "보합"


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


def test_자기_자신은_표본에_안_들어간다(conn):
    """오늘 적재된 신규 건을 그대로 판정해도 자기 가격과 비교하지 않는다."""
    today_trade = t(1_120_000_000, "2026-09-14")
    seed(conn, [t(1_000_000_000, "2026-05-01"), t(1_050_000_000, "2026-06-01"),
                t(1_080_000_000, "2026-07-01"), today_trade])
    v = judge_price(conn, today_trade)
    assert v.kind == "신고가"
    assert v.sample == 3
