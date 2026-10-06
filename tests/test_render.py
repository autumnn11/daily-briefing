from src.render import render_html, write_html


def ctx(**kw) -> dict:
    base = dict(
        target_date="2026-09-29",
        headline="장위동 신규 신고 7건",
        counts={"신규신고": 7, "신규매물": 8, "소멸매물": 5, "가격조정": 2, "계약파기": 1},
        cards=[{"complex_name": "래미안장위", "area": 84.93, "floor": 15,
                "price_text": "11억 2,000", "verdict": "신고가", "pct": 3.7,
                "badges": ["직거래"], "deal_date": "2026-09-14"}],
        warnings=[],
        listing_sections=[],
        price_changes=[],
        thermometer=[],
    )
    base.update(kw)
    return base


def test_헤드라인과_날짜가_들어간다():
    html = render_html(ctx())
    assert "장위동 신규 신고 7건" in html
    assert "2026-09-29" in html


def test_카드_내용이_들어간다():
    html = render_html(ctx())
    assert "래미안장위" in html
    assert "11억 2,000" in html
    assert "직거래" in html


def test_경고가_있으면_배너가_나온다():
    html = render_html(ctx(warnings=["호가 미수집 — 네이버 차단"]))
    assert "호가 미수집" in html


def test_카드가_없어도_렌더된다():
    html = render_html(ctx(cards=[], counts={"신규신고": 0}))
    assert "조용" in html


def test_HTML_특수문자가_이스케이프된다():
    html = render_html(ctx(headline="<script>alert(1)</script>"))
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_빈_섹션은_통째로_빠진다():
    html = render_html(ctx())
    assert "호가 동향" not in html
    assert "가격 조정" not in html
    assert "지방 온도계" not in html


def test_섹션이_있으면_나온다():
    html = render_html(ctx(
        listing_sections=[{"area_name": "장위4구역", "deal_type": "매매", "count": 34,
                           "prev": 31, "delta_text": "+3", "new": 4, "gone": 1,
                           "low_text": "3억 6,000"}],
        price_changes=[{"name": "장위빌라", "down": True, "delta_text": "-5.3%",
                        "prev_text": "3억 8,000", "now_text": "3억 6,000",
                        "days": 3, "times": 2}],
        thermometer=[{"name": "대전 둔산 크로바", "recent_counts": "07월 1건",
                      "yoy_text": "+1건", "pyeong_text": "2,180만",
                      "trend_text": "+4.2%", "from_high_text": "-8.0%"}],
    ))
    assert "장위4구역" in html
    assert "장위빌라" in html
    assert "대전 둔산 크로바" in html


def test_파일로_저장된다(tmp_path):
    p = write_html(ctx(), tmp_path)
    assert p.exists()
    assert p.name == "2026-09-29.html"
    assert "래미안장위" in p.read_text(encoding="utf-8")
