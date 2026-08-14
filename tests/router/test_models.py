from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.router.models import (
    CLIType,
    CrossRoleMessageType,
    MessageEnvelope,
    RoleState,
    Task,
    TaskCreateRequest,
    ThreadStepRequest,
    is_runnable_cli_type,
)


def test_message_envelope_accepts_valid_payload() -> None:
    envelope = MessageEnvelope(
        sender_role="boss",
        sender_session_id="11111111-1111-4111-8111-111111111111",
        target_role="president",
        msg_type=CrossRoleMessageType.relay,
        ui_group_id="snake-ui-1",
    )

    assert envelope.target_role == "president"
    assert envelope.msg_type == CrossRoleMessageType.relay


def test_message_envelope_rejects_invalid_session_uuid() -> None:
    with pytest.raises(ValidationError):
        MessageEnvelope(
            sender_role="boss",
            sender_session_id="not-a-uuid",
            target_role="president",
            msg_type=CrossRoleMessageType.relay,
            ui_group_id="snake-ui-1",
        )


def test_role_state_enum_values_are_minimal_first_release() -> None:
    assert {state.value for state in RoleState} == {"idle", "responding", "awaiting_input"}


def test_gemini_remains_readable_for_historical_rows() -> None:
    task = Task(target_cli=CLIType.gemini)

    assert task.target_cli == CLIType.gemini
    assert is_runnable_cli_type(task.target_cli) is False


@pytest.mark.parametrize("request_type", [TaskCreateRequest, ThreadStepRequest])
def test_new_gemini_work_is_rejected(request_type) -> None:
    kwargs = {"title": "legacy", "target_cli": "gemini"}
    if request_type is ThreadStepRequest:
        kwargs["step_index"] = 0

    with pytest.raises(ValidationError, match="deprecated"):
        request_type(**kwargs)
