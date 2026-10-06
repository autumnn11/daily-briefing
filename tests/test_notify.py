import json
from src.notify import Summary, build_blocks


def s(**kw) -> Summary:
    base = dict(headline="장위동 신규 신고 7건", counts={"신규신고": 7, "계약파기": 1},
                lines=["래미안장위 84.9 11억2000 ▲신고가"], warnings=[])
    base.update(kw)
    return Summary(**base)


def test_헤더에_날짜가_들어간다():
    blocks = build_blocks(s(), "2026-09-29")
    assert "2026-09-29" in json.dumps(blocks, ensure_ascii=False)


def test_카운트가_모두_0이면_조용하다고_표시한다():
    blocks = build_blocks(s(counts={"신규신고": 0, "계약파기": 0}, lines=[]), "2026-09-29")
    assert "조용" in json.dumps(blocks, ensure_ascii=False)


def test_경고가_있으면_블록에_들어간다():
    blocks = build_blocks(s(warnings=["호가 미수집 — 네이버 차단"]), "2026-09-29")
    assert "호가 미수집" in json.dumps(blocks, ensure_ascii=False)


def test_경고가_있는_0건은_조용한_날이_아니다():
    """수집 실패를 '조용합니다'로 덮으면 3초 읽는 사람이 그냥 넘어간다 (리뷰 I12)."""
    blocks = build_blocks(
        s(counts={"신규신고": 0}, lines=[], warnings=["국토부 수집 실패"]), "2026-09-29")
    body = json.dumps(blocks, ensure_ascii=False)
    assert "국토부 수집 실패" in body
    assert "조용" not in body
    assert "확인하지 못했습니다" in body


def test_블록이_슬랙_제한인_50개를_넘지_않는다():
    blocks = build_blocks(s(lines=[f"줄 {i}" for i in range(500)]), "2026-09-29")
    assert len(blocks) <= 50


def test_모든_블록에_type_키가_있다():
    for b in build_blocks(s(), "2026-09-29"):
        assert "type" in b
