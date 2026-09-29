from pathlib import Path
import textwrap
import pytest
from src.config import load_watchlist


def write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "watchlist.yml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


def test_수도권_항목을_읽는다(tmp_path):
    p = write(tmp_path, """
        수도권:
          - 이름: 장위 재개발 일대
            유형: 구역
            lawd_cd: "11290"
            법정동: ["장위동"]
            좌표: {lat: 37.6130, lon: 127.0480, zoom: 17}
            자산: "VL"
    """)
    targets = load_watchlist(p)
    assert len(targets) == 1
    t = targets[0]
    assert t.name == "장위 재개발 일대"
    assert t.region == "수도권"
    assert t.lawd_cd == "11290"
    assert t.dongs == ["장위동"]
    assert t.coord.zoom == 17
    assert t.asset == "VL"
    assert t.complex_name is None


def test_지방_항목은_좌표와_자산이_없다(tmp_path):
    p = write(tmp_path, """
        지방:
          - 이름: 대전 둔산 크로바
            lawd_cd: "30170"
            법정동: ["둔산동"]
            단지명: "크로바"
    """)
    t = load_watchlist(p)[0]
    assert t.region == "지방"
    assert t.coord is None
    assert t.asset is None
    assert t.complex_name == "크로바"


def test_빈_파일이면_빈_리스트를_돌려준다(tmp_path):
    p = write(tmp_path, "")
    assert load_watchlist(p) == []


def test_파일이_없으면_빈_리스트를_돌려준다(tmp_path):
    assert load_watchlist(tmp_path / "없음.yml") == []


def test_lawd_cd가_5자리가_아니면_거부한다(tmp_path):
    p = write(tmp_path, """
        수도권:
          - 이름: 잘못된 항목
            lawd_cd: "112901"
            법정동: ["장위동"]
    """)
    with pytest.raises(ValueError, match="lawd_cd"):
        load_watchlist(p)
