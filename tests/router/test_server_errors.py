import threading
from http.server import ThreadingHTTPServer

import pytest
import requests
from unittest.mock import MagicMock

from src.router.server import MeshRouterHandler

@pytest.fixture
def mock_server_url():
    """Start a test HTTP server with mocked state for error testing."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), MeshRouterHandler)
    
    mock_db = MagicMock()
    mock_db.get_worker.side_effect = Exception("Simulated DB error")
    mock_db.insert_task.side_effect = Exception("Simulated DB error")

    server.router_state = {
        "db": mock_db,
        "worker_manager": MagicMock(),
        "heartbeat": MagicMock(),
        "scheduler": MagicMock(),
        "transport": MagicMock(),
        "metrics": MagicMock(),
        "longpoll_registry": MagicMock(),
        "longpoll_timeout": 0.1,
        "auth_token": None,
        "start_time": None,
    }

    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()

    port = server.server_address[1]
    yield f"http://127.0.0.1:{port}"

    server.shutdown()


def test_server_500_on_db_error_get(mock_server_url, caplog):
    """Test that a DB error during a GET request returns a 500 error."""
    resp = requests.get(f"{mock_server_url}/workers/w1")
    assert resp.status_code == 500
    assert "Simulated DB error" in resp.text
    assert "Unhandled GET /workers/w1" in caplog.text

def test_server_500_on_db_error_post(mock_server_url, caplog):
    """Test that a DB error during a POST request returns a 500 error."""
    resp = requests.post(f"{mock_server_url}/tasks", json={"title": "test", "phase": "implement"})
    assert resp.status_code == 500
    assert "Simulated DB error" in resp.text
    assert "Unhandled POST /tasks" in caplog.text
