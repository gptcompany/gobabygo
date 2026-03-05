import time
from unittest.mock import MagicMock, patch

import pytest
from src.router.session_worker import MeshSessionWorker, SessionWorkerConfig
from src.router.models import Task

@pytest.fixture
def worker():
    config = SessionWorkerConfig(
        worker_id="test-worker",
        router_url="http://localhost",
        cli_type="claude",
        account_profile="work",
        task_timeout=0.1,  # Short timeout for testing
        session_poll_interval_s=0.01
    )
    w = MeshSessionWorker(config)
    w._send_session_message = MagicMock()
    w._report_failure = MagicMock()
    w._report_complete = MagicMock()
    w._close_session = MagicMock()
    w._tmux_has_session = MagicMock(return_value=True)
    w._tmux_kill_session = MagicMock()
    w._tmux_send_text = MagicMock()
    w._tmux_capture_pane = MagicMock(return_value="test output")
    w._list_session_messages = MagicMock(return_value=[])
    w._deliver_inbound_messages = MagicMock(return_value=0)
    w._ack_task = MagicMock(return_value=True)
    w._api = MagicMock()
    w._running = True
    return w

def test_interactive_task_timeout(worker):
    task = {"task_id": "t1", "title": "test", "phase": "implement", "target_cli": "claude", "target_account": "work", "execution_mode": "session", "payload": {"prompt": "prompt"}}
    worker._open_session = MagicMock(return_value="sess-123")
    worker._tmux_session_name = MagicMock(return_value="tmux-sess")
    worker._tmux_new_session = MagicMock()
    
    # We patch monotonic to simulate time passing
    times = [0.0, 0.05, 0.2]  # start, first check (pass), second check (timeout)
    with patch("time.monotonic", side_effect=times):
        worker._execute_task(task)

    # Assert timeout handling
    worker._report_failure.assert_called_once()
    assert "timeout" in worker._report_failure.call_args[0][1]
    worker._close_session.assert_called_with("sess-123", state="errored")
    worker._tmux_kill_session.assert_called_with("tmux-sess")

def test_interactive_task_exception(worker):
    task = {"task_id": "t2", "title": "test", "phase": "implement", "target_cli": "claude", "target_account": "work", "execution_mode": "session", "payload": {"prompt": "prompt"}}
    worker._open_session = MagicMock(return_value="sess-456")
    worker._tmux_session_name = MagicMock(return_value="tmux-sess")
    worker._tmux_new_session = MagicMock()
    
    # Make something raise an exception to trigger the try/except block
    worker._tmux_capture_pane.side_effect = Exception("Simulated tmux error")
    
    worker._execute_task(task)
    
    worker._report_failure.assert_called_once()
    assert "Simulated tmux error" in worker._report_failure.call_args[0][1]
    worker._close_session.assert_called_with("sess-456", state="errored")
