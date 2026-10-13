import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest  # noqa: E402

from hydrowitness.demo import DEMO_SECRETS, demo_profile  # noqa: E402


@pytest.fixture
def profile():
    return demo_profile()


@pytest.fixture
def secrets():
    return dict(DEMO_SECRETS)
