import pytest

from mirror.extract import MockExtractor
from mirror.store import MirrorStore


@pytest.fixture
def store(tmp_path):
    st = MirrorStore(str(tmp_path / "test.kuzu"))
    yield st
    st.close()


@pytest.fixture
def mock():
    return MockExtractor()


DINNER_TEXT = (
    "10월 16일 목요일 저녁 8시. 야근으로 피곤해서 새로운 식당을 찾아보기 "
    "귀찮았다. 결국 항상 시키는 익숙한 집에 배달을 주문했다."
)
