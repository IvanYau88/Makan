import pytest

from makan.trace import ListSink


@pytest.fixture
def sink() -> ListSink:
    return ListSink()
