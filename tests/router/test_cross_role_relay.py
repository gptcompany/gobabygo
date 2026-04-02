from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.router.models import CrossRoleMessageType, RoleState
from src.router.session_worker import (
    MeshSessionWorker,
    SessionWorkerConfig,
    _detect_role_state,
    _extract_clean_response,
)


def _make_worker() -> MeshSessionWorker:
    worker = MeshSessionWorker(SessionWorkerConfig())
    worker._http = MagicMock()
    return worker


def test_detect_role_state_minimal_states() -> None:
    assert _detect_role_state("Working...\n● Running command\n") == RoleState.responding
    assert _detect_role_state("Some prompt text\n❯ continue the task") == RoleState.awaiting_input
    assert _detect_role_state("Done\n❯ ") == RoleState.idle


def test_detect_role_state_treats_final_bullet_answer_as_idle() -> None:
    captured = "❯ saluta il president\n\n● Ciao Presidente!\n\n❯ "
    assert _detect_role_state(captured) == RoleState.idle


def test_detect_role_state_keeps_tool_activity_as_responding() -> None:
    captured = "❯ saluta il president\n\n● Write(summary.txt)\n· Flowing…\n\n❯ "
    assert _detect_role_state(captured) == RoleState.responding


def test_extract_clean_response_strips_terminal_noise() -> None:
    text = """✻ Thinking...\n● Result ready\n⎿ note\nStop says: noop\n❯ \n"""
    assert _extract_clean_response(text) == "● Result ready"


def test_emit_response_relay_sends_structured_envelope() -> None:
    worker = _make_worker()

    with patch.object(worker, "_send_session_message") as mock_send:
        worker._emit_response_relay(
            session_id="11111111-1111-4111-8111-111111111111",
            ui_role="boss",
            ui_group_id="snake-ui-1",
            target_role="president",
            baseline_capture="old output\n",
            current_capture="old output\n● Final answer\n❯ ",
        )

    mock_send.assert_called_once()
    metadata = mock_send.call_args.kwargs["metadata"]
    envelope = metadata["envelope"]
    assert envelope["msg_type"] == CrossRoleMessageType.relay.value
    assert envelope["sender_role"] == "boss"
    assert envelope["target_role"] == "president"


def test_deliver_group_messages_targets_tmux_with_proxy_prefix() -> None:
    worker = _make_worker()
    with (
        patch.object(worker, "_list_group_messages", return_value=[
            {
                "seq": 3,
                "session_id": "22222222-2222-4222-8222-222222222222",
                "content": "president summary",
                "metadata": {
                    "envelope": {
                        "msg_type": "relay",
                        "sender_role": "president",
                    }
                },
            }
        ]),
        patch.object(worker, "_tmux_send_text") as mock_send_text,
    ):
        after = worker._deliver_group_messages(
            session_id="11111111-1111-4111-8111-111111111111",
            tmux_session="mesh-boss",
            ui_group_id="snake-ui-1",
            after_seq=0,
            ui_role="boss",
        )

    assert after == 3
    sent = mock_send_text.call_args.args[1]
    assert sent.startswith("__mesh_inbound__:president:")
