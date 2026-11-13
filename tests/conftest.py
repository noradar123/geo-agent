import os

import pytest


@pytest.fixture(autouse=True)
def isolated_model_settings(monkeypatch):
    # CLI tests call load_dotenv(), which copies the developer's real .env into os.environ.
    for name in list(os.environ):
        if name.startswith("GEOAGENT_") or name.endswith("_MODEL") or name == "ANTHROPIC_EFFORT":
            monkeypatch.delenv(name)
