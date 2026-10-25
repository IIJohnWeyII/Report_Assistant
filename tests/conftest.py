import os

import pytest

from app import db  # Load the same local configuration as the app.


@pytest.fixture(autouse=True)
def require_demo():
    assert os.getenv("DB_NAME") == "report_assistant_demo", "Run tests against the local demo only"
    assert os.getenv("DB_HOST") == "127.0.0.1"
    assert os.getenv("DB_PORT") == "13306"
