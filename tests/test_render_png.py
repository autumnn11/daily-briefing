import pytest
from src.render import html_to_png

pytest.importorskip("playwright")


def test_html을_png로_찍는다(tmp_path):
    html = tmp_path / "x.html"
    html.write_text("<body style='width:1200px'><h1>테스트 브리핑</h1></body>",
                    encoding="utf-8")
    png = html_to_png(html, tmp_path / "x.png")
    assert png.exists()
    assert png.stat().st_size > 1000
    assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_한글이_깨지지_않고_찍힌다(tmp_path):
    """폰트가 없으면 PNG 는 나오지만 내용이 두부로 찍힌다. 크기로 간접 확인한다."""
    html = tmp_path / "k.html"
    html.write_text(
        "<body style='width:1200px;font-family:\"Malgun Gothic\"'>"
        "<h1>장위동 신규 신고 7건</h1><p>래미안장위 전용 84.9 15층 11억 2,000</p></body>",
        encoding="utf-8")
    png = html_to_png(html, tmp_path / "k.png")
    assert png.stat().st_size > 3000
