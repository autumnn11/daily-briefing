from datetime import date
from pathlib import Path
import textwrap
import pytest
from src.collect_kor import KorApiError
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


ENV = {"KOR_API_KEY": "x", "SLACK_BOT_TOKEN": "t", "SLACK_CHANNEL": "#c"}


def test_둘다_실패해도_요약은_나온다(tmp_path, wl, monkeypatch):
    def kor_boom(*a, **k):
        raise KorApiError("인증키 미승인")
    monkeypatch.setattr("src.run_daily.collect_all", kor_boom)
    monkeypatch.setattr("src.run_daily.collect_naver_all", lambda t: ({}, ["호가 미수집"]))
    s = run(date(2026, 9, 29), tmp_path / "d.db", wl, ENV, dry_run=True)
    assert len(s.warnings) == 2
    assert s.headline


def test_렌더가_터져도_Slack은_시도된다(tmp_path, wl, monkeypatch):
    sent = {}
    monkeypatch.setattr("src.run_daily.html_to_png",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("chromium 없음")))
    monkeypatch.setattr("src.run_daily.post",
                        lambda *a, **k: sent.update({"called": True,
                                                     "png": k.get("png_path")}))
    s = run(date(2026, 9, 29), tmp_path / "d.db", wl, ENV)
    assert sent["called"] is True
    assert sent["png"] is None
    assert any("렌더" in w for w in s.warnings)


def test_렌더_실패_경고가_Slack_블록까지_간다(tmp_path, wl, monkeypatch):
    seen = {}
    monkeypatch.setattr("src.run_daily.html_to_png",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr("src.run_daily.post",
                        lambda tok, ch, blocks, text, **k: seen.update({"blocks": blocks}))
    run(date(2026, 9, 29), tmp_path / "d.db", wl, ENV)
    import json
    assert "렌더" in json.dumps(seen["blocks"], ensure_ascii=False)


def test_Slack이_터져도_예외가_안_새고_runs에_남는다(tmp_path, wl, monkeypatch):
    monkeypatch.setattr("src.run_daily.html_to_png", lambda h, p, **k: p)
    monkeypatch.setattr("src.run_daily.post",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("채널 없음")))
    db = tmp_path / "d.db"
    run(date(2026, 9, 29), db, wl, ENV)
    conn = init_db(db)
    row = conn.execute("SELECT ok, err FROM runs WHERE stage = 'slack'").fetchone()
    assert row["ok"] == 0
    assert "채널 없음" in row["err"]


def test_국토부_실패가_runs에_남는다(tmp_path, wl, monkeypatch):
    def kor_boom(*a, **k):
        raise KorApiError("트래픽 초과")
    monkeypatch.setattr("src.run_daily.collect_all", kor_boom)
    db = tmp_path / "d.db"
    run(date(2026, 9, 29), db, wl, ENV, dry_run=True)
    conn = init_db(db)
    row = conn.execute("SELECT ok, err FROM runs WHERE stage = 'kor_api'").fetchone()
    assert row["ok"] == 0
    assert "트래픽 초과" in row["err"]


def test_네이버_실패가_runs에_남는다(tmp_path, wl, monkeypatch):
    monkeypatch.setattr("src.run_daily.collect_naver_all",
                        lambda t: ({}, ["호가 미수집 — 네이버 차단"]))
    db = tmp_path / "d.db"
    run(date(2026, 9, 29), db, wl, ENV, dry_run=True)
    conn = init_db(db)
    row = conn.execute("SELECT ok, err FROM runs WHERE stage = 'naver'").fetchone()
    assert row["ok"] == 0
    assert "차단" in row["err"]
