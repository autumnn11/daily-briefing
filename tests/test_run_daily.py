from datetime import date
from pathlib import Path
import textwrap
import pytest
from src.collect_kor import KorApiError
from src.models import TradeRow
from src.run_daily import run
from src.store import init_db


@pytest.fixture
def wl(tmp_path) -> Path:
    p = tmp_path / "watchlist.yml"
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


def a_trade(**kw) -> TradeRow:
    base = dict(lawd_cd="11290", dong="장위동", complex_name="래미안장위",
                deal_date="2026-09-14", area=84.93, floor=15, price=1_120_000_000,
                deal_kind="중개거래", released=False, released_date=None,
                register_date=None, asset="APT")
    base.update(kw)
    return TradeRow(**base)


def test_신규건이_요약에_들어간다(tmp_path, wl, monkeypatch):
    monkeypatch.setattr("src.run_daily.collect_all", lambda *a, **k: [a_trade()])
    s = run(date(2026, 9, 29), tmp_path / "d.db", wl, {"KOR_API_KEY": "x"}, dry_run=True)
    assert s.counts["신규신고"] == 1
    assert s.warnings == []


def test_국토부_실패해도_브리핑은_나간다(tmp_path, wl, monkeypatch):
    def boom(*a, **k):
        raise KorApiError("인증키 미승인")
    monkeypatch.setattr("src.run_daily.collect_all", boom)
    s = run(date(2026, 9, 29), tmp_path / "d.db", wl, {"KOR_API_KEY": "x"}, dry_run=True)
    assert any("국토부" in w for w in s.warnings)
    assert s.counts["신규신고"] == 0


def test_감시대상이_없으면_그렇게_말한다(tmp_path, monkeypatch):
    empty = tmp_path / "empty.yml"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setattr("src.run_daily.collect_all", lambda *a, **k: [])
    s = run(date(2026, 9, 29), tmp_path / "d.db", empty, {"KOR_API_KEY": "x"}, dry_run=True)
    assert "감시 대상" in s.headline


def test_실행이_runs에_기록된다(tmp_path, wl, monkeypatch):
    monkeypatch.setattr("src.run_daily.collect_all", lambda *a, **k: [a_trade()])
    db = tmp_path / "d.db"
    run(date(2026, 9, 29), db, wl, {"KOR_API_KEY": "x"}, dry_run=True)
    conn = init_db(db)
    rows = conn.execute("SELECT stage, ok FROM runs").fetchall()
    assert ("kor_api", 1) in [(r["stage"], r["ok"]) for r in rows]


def test_같은_날_두번_돌리면_두번째는_신규가_0(tmp_path, wl, monkeypatch):
    monkeypatch.setattr("src.run_daily.collect_all", lambda *a, **k: [a_trade()])
    db = tmp_path / "d.db"
    run(date(2026, 9, 29), db, wl, {"KOR_API_KEY": "x"}, dry_run=True)
    s = run(date(2026, 9, 29), db, wl, {"KOR_API_KEY": "x"}, dry_run=True)
    assert s.counts["신규신고"] == 0


def test_파기건이_요약에_들어간다(tmp_path, wl, monkeypatch):
    db = tmp_path / "d.db"
    monkeypatch.setattr("src.run_daily.collect_all", lambda *a, **k: [a_trade()])
    run(date(2026, 9, 29), db, wl, {"KOR_API_KEY": "x"}, dry_run=True)
    monkeypatch.setattr("src.run_daily.collect_all",
                        lambda *a, **k: [a_trade(released=True, released_date="2026-09-28")])
    s = run(date(2026, 9, 30), db, wl, {"KOR_API_KEY": "x"}, dry_run=True)
    assert s.counts["계약파기"] == 1


def test_cp949_스트림을_utf8로_바꾼다():
    """Windows 콘솔(cp949)에 em dash 를 찍어도 죽지 않아야 한다."""
    import io
    from src.run_daily import _configure_streams

    stream = io.TextIOWrapper(io.BytesIO(), encoding="cp949")
    _configure_streams(stream)
    assert stream.encoding.lower().replace("-", "") == "utf8"
    stream.write("국토부 수집 실패 — 인증키 미승인")
    stream.flush()


def test_reconfigure가_없는_스트림도_견딘다():
    from src.run_daily import _configure_streams

    class Dumb:
        pass

    _configure_streams(Dumb())
