import pytest
from src.models import ListingRow
from src.store import init_db, listing_counts, upsert_listings


def L(**kw) -> ListingRow:
    base = dict(listing_no="2444012345", name="장위빌라", deal_type="매매",
                price=380_000_000, floor="2/4", area=52.1, exclusive_area=42.1,
                direction="남동", features="역세권", listed_date="2026-09-01",
                prev_jeonse=None, gap=None, gap_ratio=None,
                area_name="장위 재개발 일대")
    base.update(kw)
    return ListingRow(**base)


@pytest.fixture
def conn(tmp_path):
    return init_db(tmp_path / "l.db")


def test_처음_본_매물은_신규다(conn):
    d = upsert_listings(conn, [L()], "장위 재개발 일대", "2026-09-29")
    assert len(d.new) == 1


def test_같은_매물은_다음날_신규가_아니다(conn):
    upsert_listings(conn, [L()], "장위 재개발 일대", "2026-09-29")
    d = upsert_listings(conn, [L()], "장위 재개발 일대", "2026-09-30")
    assert d.new == []


def test_오늘_안_보이면_소멸이다(conn):
    upsert_listings(conn, [L(), L(listing_no="999")], "장위 재개발 일대", "2026-09-29")
    d = upsert_listings(conn, [L()], "장위 재개발 일대", "2026-09-30")
    assert [r["listing_no"] for r in d.gone] == ["999"]


def test_가격이_내리면_조정으로_잡힌다(conn):
    upsert_listings(conn, [L()], "장위 재개발 일대", "2026-09-29")
    d = upsert_listings(conn, [L(price=360_000_000)], "장위 재개발 일대", "2026-10-02")
    assert len(d.price_changes) == 1
    c = d.price_changes[0]
    assert c.prev == 380_000_000 and c.now == 360_000_000
    assert c.days == 3
    assert c.times == 1


def test_두번_내리면_누적_횟수가_2다(conn):
    upsert_listings(conn, [L()], "장위 재개발 일대", "2026-09-29")
    upsert_listings(conn, [L(price=370_000_000)], "장위 재개발 일대", "2026-09-30")
    d = upsert_listings(conn, [L(price=360_000_000)], "장위 재개발 일대", "2026-10-01")
    assert d.price_changes[0].times == 2


def test_가격이_오르면_조정에_잡히되_방향이_다르다(conn):
    upsert_listings(conn, [L()], "장위 재개발 일대", "2026-09-29")
    d = upsert_listings(conn, [L(price=400_000_000)], "장위 재개발 일대", "2026-09-30")
    c = d.price_changes[0]
    assert c.now > c.prev


def test_가격이_그대로면_이력이_안_쌓인다(conn):
    upsert_listings(conn, [L()], "장위 재개발 일대", "2026-09-29")
    upsert_listings(conn, [L()], "장위 재개발 일대", "2026-09-30")
    n = conn.execute("SELECT COUNT(*) FROM listing_price_history").fetchone()[0]
    assert n == 1


def test_거래유형별로_센다(conn):
    rows = [L(), L(listing_no="2", deal_type="전세", price=300_000_000)]
    upsert_listings(conn, rows, "장위 재개발 일대", "2026-09-29")
    d = upsert_listings(conn, rows, "장위 재개발 일대", "2026-09-30")
    assert d.counts[("장위 재개발 일대", "매매")] == 1
    assert d.counts[("장위 재개발 일대", "전세")] == 1


def test_다른_구역의_매물은_소멸로_안_잡힌다(conn):
    upsert_listings(conn, [L(listing_no="A", area_name="구역A")], "구역A", "2026-09-29")
    d = upsert_listings(conn, [L(listing_no="B", area_name="구역B")], "구역B", "2026-09-29")
    assert d.gone == []


def test_listing_counts는_그날_본_것만_센다(conn):
    upsert_listings(conn, [L(), L(listing_no="2", deal_type="전세")],
                    "장위 재개발 일대", "2026-09-29")
    assert listing_counts(conn, "장위 재개발 일대", "2026-09-29") == {"매매": 1, "전세": 1}
    assert listing_counts(conn, "장위 재개발 일대", "2026-09-28") == {}


def test_같은_날_두번_돌려도_중복되지_않는다(conn):
    upsert_listings(conn, [L()], "장위 재개발 일대", "2026-09-29")
    d = upsert_listings(conn, [L()], "장위 재개발 일대", "2026-09-29")
    assert d.new == []
    assert d.price_changes == []
    assert conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0] == 1
