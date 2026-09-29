from datetime import date
from pathlib import Path
import textwrap
import pytest
from src.models import ListingRow
from src.run_daily import run
from src.store import init_db


@pytest.fixture
def wl(tmp_path) -> Path:
    p = tmp_path / "w.yml"
    p.write_text(textwrap.dedent("""
        수도권:
          - 이름: 장위 재개발 일대
            유형: 구역
            lawd_cd: "11290"
            법정동: ["장위동"]
            좌표: {lat: 37.6130, lon: 127.0480, zoom: 17}
            자산: "VL"
    """), encoding="utf-8")
    return p


def L(**kw) -> ListingRow:
    base = dict(listing_no="1", name="장위빌라", deal_type="매매", price=380_000_000,
                floor="2/4", area=52.1, exclusive_area=42.1, direction="남동",
                features="", listed_date="2026-09-01", prev_jeonse=None, gap=None,
                gap_ratio=None, area_name="장위 재개발 일대")
    base.update(kw)
    return ListingRow(**base)


@pytest.fixture(autouse=True)
def no_kor(monkeypatch):
    monkeypatch.setattr("src.run_daily.collect_all", lambda *a, **k: [])


def test_신규매물이_카운트에_들어간다(tmp_path, wl, monkeypatch):
    monkeypatch.setattr("src.run_daily.collect_naver_all",
                        lambda t: ({"장위 재개발 일대": [L()]}, []))
    s = run(date(2026, 9, 29), tmp_path / "d.db", wl, {"KOR_API_KEY": "x"}, dry_run=True)
    assert s.counts["신규매물"] == 1


def test_네이버_실패시_경고가_붙고_파이프라인은_산다(tmp_path, wl, monkeypatch):
    monkeypatch.setattr("src.run_daily.collect_naver_all",
                        lambda t: ({}, ["호가 미수집 — 네이버 차단 (장위 재개발 일대)"]))
    s = run(date(2026, 9, 29), tmp_path / "d.db", wl, {"KOR_API_KEY": "x"}, dry_run=True)
    assert any("호가 미수집" in w for w in s.warnings)
    assert s.counts["신규매물"] == 0


def test_네이버_실패한_날은_소멸로_찍히지_않는다(tmp_path, wl, monkeypatch):
    db = tmp_path / "d.db"
    monkeypatch.setattr("src.run_daily.collect_naver_all",
                        lambda t: ({"장위 재개발 일대": [L()]}, []))
    run(date(2026, 9, 29), db, wl, {"KOR_API_KEY": "x"}, dry_run=True)

    monkeypatch.setattr("src.run_daily.collect_naver_all", lambda t: ({}, ["호가 미수집"]))
    s = run(date(2026, 9, 30), db, wl, {"KOR_API_KEY": "x"}, dry_run=True)
    assert s.counts["소멸매물"] == 0
    conn = init_db(db)
    row = conn.execute("SELECT last_seen FROM listings WHERE listing_no='1'").fetchone()
    assert row["last_seen"] == "2026-09-29"


def test_가격조정이_카운트에_들어간다(tmp_path, wl, monkeypatch):
    db = tmp_path / "d.db"
    monkeypatch.setattr("src.run_daily.collect_naver_all",
                        lambda t: ({"장위 재개발 일대": [L()]}, []))
    run(date(2026, 9, 29), db, wl, {"KOR_API_KEY": "x"}, dry_run=True)

    monkeypatch.setattr("src.run_daily.collect_naver_all",
                        lambda t: ({"장위 재개발 일대": [L(price=360_000_000)]}, []))
    s = run(date(2026, 9, 30), db, wl, {"KOR_API_KEY": "x"}, dry_run=True)
    assert s.counts["가격조정"] == 1


def test_카운트_키_순서가_스펙_8절과_같다(tmp_path, wl, monkeypatch):
    monkeypatch.setattr("src.run_daily.collect_naver_all", lambda t: ({}, []))
    s = run(date(2026, 9, 29), tmp_path / "d.db", wl, {"KOR_API_KEY": "x"}, dry_run=True)
    assert list(s.counts) == ["신규신고", "신규매물", "소멸매물", "가격조정", "계약파기"]


def test_거래유형별로_행이_나뉘고_증감이_계산된다(tmp_path):
    from src.run_daily import build_listing_rows
    from src.store import listing_counts, upsert_listings

    conn = init_db(tmp_path / "b.db")
    day1 = [L(), L(listing_no="2", deal_type="전세", price=250_000_000),
            L(listing_no="3", price=400_000_000)]
    upsert_listings(conn, day1, "장위 재개발 일대", "2026-09-29")

    day2 = [L(), L(listing_no="2", deal_type="전세", price=250_000_000)]  # 3번 사라짐
    prev = listing_counts(conn, "장위 재개발 일대", "2026-09-29")
    diff = upsert_listings(conn, day2, "장위 재개발 일대", "2026-09-30")
    sections, changes = build_listing_rows(
        conn, "장위 재개발 일대", day2, diff, date(2026, 9, 30), prev)

    by_type = {r["deal_type"]: r for r in sections}
    assert by_type["매매"]["count"] == 1
    assert by_type["매매"]["prev"] == 2
    assert by_type["매매"]["delta_text"] == "-1"
    assert by_type["매매"]["gone"] == 1
    assert by_type["전세"]["count"] == 1
    assert by_type["전세"]["delta_text"] == "―"
    assert changes == []


def test_가격조정_행에_방향과_누적이_담긴다(tmp_path):
    from src.run_daily import build_listing_rows
    from src.store import listing_counts, upsert_listings

    conn = init_db(tmp_path / "c.db")
    upsert_listings(conn, [L()], "장위 재개발 일대", "2026-09-29")
    day2 = [L(price=360_000_000)]
    prev = listing_counts(conn, "장위 재개발 일대", "2026-10-01")
    diff = upsert_listings(conn, day2, "장위 재개발 일대", "2026-10-02")
    _, changes = build_listing_rows(
        conn, "장위 재개발 일대", day2, diff, date(2026, 10, 2), prev)

    assert len(changes) == 1
    c = changes[0]
    assert c["down"] is True
    assert c["delta_text"] == "-5.3%"
    assert c["days"] == 3
    assert c["times"] == 1
