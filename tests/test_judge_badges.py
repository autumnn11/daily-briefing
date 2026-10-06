from datetime import date
import pytest
from src.judge import badges
from src.models import TradeRow
from src.store import init_db, max_floor, upsert_trades


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


def test_층을_모르면_저층도_최상층도_아니다():
    assert badges(t(floor=None), top_floor=25, today=TODAY) == []


def test_직거래_뱃지():
    assert "직거래" in badges(t(deal_kind="직거래"), top_floor=25, today=TODAY)


def test_해제_뱃지():
    assert "해제" in badges(t(released=True, released_date="2026-09-20"),
                           top_floor=25, today=TODAY)


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


@pytest.fixture
def conn(tmp_path):
    return init_db(tmp_path / "mf.db")


def test_max_floor는_단지의_최고층을_돌려준다(conn):
    # 표본이 MIN_FLOOR_SAMPLE 이상이어야 최고층을 단정한다 (리뷰 I8)
    upsert_trades(conn, [t(floor=f, price=10_000_000 * f) for f in (3, 10, 14, 20, 25)],
                  today="2026-09-29")
    assert max_floor(conn, "11290", "장위동", "래미안장위") == 25


def test_표본이_적으면_max_floor가_None(conn):
    upsert_trades(conn, [t(floor=3), t(floor=25, price=1)], today="2026-09-29")
    assert max_floor(conn, "11290", "장위동", "래미안장위") is None


def test_거래가_없는_단지는_max_floor가_None(conn):
    assert max_floor(conn, "11290", "장위동", "없는단지") is None


def test_다른_시군구의_같은_이름은_안_섞인다(conn):
    upsert_trades(conn, [t(floor=f, price=10_000_000 * f) for f in (3, 10, 14, 20, 25)],
                  today="2026-09-29")
    assert max_floor(conn, "30170", "둔산동", "래미안장위") is None
