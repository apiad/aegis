import time

import pytest
from fake_server import FakeServer

# The real gateway takes this long to accept traffic after a deploy; the fixture
# waits the same so the suite catches clients that assume it is up at once.
WARMUP_S = 20


@pytest.fixture
def server():
    with FakeServer() as s:
        time.sleep(WARMUP_S)
        yield s
