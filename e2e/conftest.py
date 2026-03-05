"""E2E test fixtures — spin up a real FastAPI server with a temp database."""

from __future__ import annotations

import multiprocessing
import socket
import time

import pytest

# ---------------------------------------------------------------------------
# Port allocation
# ---------------------------------------------------------------------------


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# Server process
# ---------------------------------------------------------------------------


def _run_server(port: int, db_path: str, secret_key: str):
    """Target for the server subprocess."""
    import os

    os.environ["ORTOBAHN_DB_PATH"] = db_path
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test"
    os.environ["SECRET_KEY"] = secret_key

    import uvicorn

    from ortobahn.config import Settings
    from ortobahn.db import Database
    from ortobahn.web.app import create_app

    settings = Settings(
        anthropic_api_key="sk-ant-test",
        db_path=db_path,
        secret_key=secret_key,
    )
    app = create_app.__wrapped__() if hasattr(create_app, "__wrapped__") else create_app()
    db = Database(db_path)
    app.state.db = db
    app.state.settings = settings
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


@pytest.fixture(scope="session")
def e2e_server(tmp_path_factory):
    """Start a real Ortobahn server on a random port. Yields (base_url, secret_key, db_path)."""
    tmp = tmp_path_factory.mktemp("e2e")
    db_path = str(tmp / "e2e.db")
    secret_key = "e2e-test-secret-key-1234567890"
    port = _find_free_port()

    proc = multiprocessing.Process(
        target=_run_server,
        args=(port, db_path, secret_key),
        daemon=True,
    )
    proc.start()

    # Wait for server to be ready (max 10s)
    base_url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            import urllib.request

            urllib.request.urlopen(f"{base_url}/health", timeout=1)
            break
        except Exception:
            time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail("E2E server failed to start within 10 seconds")

    yield base_url, secret_key, db_path

    proc.kill()
    proc.join(timeout=5)


@pytest.fixture(scope="session")
def e2e_client_session(e2e_server):
    """Create a test client in the DB and return session cookie + client_id."""
    base_url, secret_key, db_path = e2e_server

    from ortobahn.auth import create_session_token
    from ortobahn.db import Database

    db = Database(db_path)
    client_id = db.create_client({"name": "E2E Test Co"})
    token = create_session_token(client_id, secret_key)
    db.close()

    return {
        "base_url": base_url,
        "client_id": client_id,
        "session_token": token,
        "secret_key": secret_key,
    }
