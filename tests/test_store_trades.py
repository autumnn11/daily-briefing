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
