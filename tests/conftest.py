import pytest


@pytest.fixture(autouse=True)
def _stub_external_collectors(monkeypatch):
    """테스트가 실수로 외부 네트워크를 때리지 못하게 기본 차단한다.

    run() 은 국토부 API 와 네이버 스크레이퍼를 둘 다 부른다. 스텁하지 않은
    테스트가 하나라도 있으면 pytest 가 실제로 네이버 지도를 긁으러 가고,
    (1) 테스트가 수십 초씩 걸리며 (2) 남의 서비스에 실트래픽을 보낸다.
    실제로 Task 12 에서 이 일이 벌어졌다 — test_run_daily.py 가 파일 하나에
    192초를 쓰고 있었다.

    개별 테스트가 monkeypatch.setattr 로 다시 덮어쓰면 그쪽이 이긴다.
    """
    monkeypatch.setattr("src.run_daily.collect_all",
                        lambda *a, **k: [], raising=False)
    monkeypatch.setattr("src.run_daily.collect_naver_all",
                        lambda *a, **k: ({}, []), raising=False)
    monkeypatch.setattr("src.collect_naver.scrape_target",
                        _blocked, raising=False)


def _blocked(*args, **kwargs):
    raise AssertionError(
        "테스트에서 네이버 스크레이퍼를 실제로 호출했습니다. "
        "collect_naver_all 을 스텁하세요.")
