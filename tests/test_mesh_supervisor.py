from __future__ import annotations

import json

import pytest

from scripts import mesh_supervisor as module


def test_unknown_remediation_fails_closed() -> None:
    remediation = module.remediation_for("new-untrusted-failure")

    assert remediation.action == "stop_run"
    assert remediation.retryable is False
    assert remediation.max_attempts == 0


def test_registry_is_immutable() -> None:
    with pytest.raises(TypeError):
        module.SUPERVISOR_REMEDIATION_REGISTRY["unsafe"] = (  # type: ignore[index]
            module.SupervisorRemediation("send_input", True, 99)
        )


def test_report_contains_bounded_policy_and_telemetry() -> None:
    payload = module.report_payload(
        role="reviewer",
        marker="REVIEW_DONE_123",
        assessment=module.SupervisorAssessment(
            failure_class="stalled_run",
            remediation="stop after the timeout budget",
        ),
        attempts=-1,
        timeout_telemetry=module.TimeoutTelemetry(
            timeout_s=30,
            elapsed_s=31.25,
            poll_interval_s=1,
            poll_count=30,
            last_progress_s_ago=12.5,
            screen_changed_recently=False,
        ),
    )

    assert payload["schema"] == "mesh.controller.supervisor.v1"
    assert payload["action"] == "stop_run"
    assert payload["retryable"] is False
    assert payload["attempts"] == 0
    assert payload["timeout"] == {
        "timeout_s": 30.0,
        "elapsed_s": 31.25,
        "poll_interval_s": 1.0,
        "poll_count": 30,
        "last_progress_s_ago": 12.5,
        "screen_changed_recently": False,
        "marker_seen_without_ack": False,
    }


def test_transitions_require_confirmation_and_recover() -> None:
    state: dict[str, object] = {"version": 1, "sessions": {}}
    warning = module.SupervisorSignal(
        key="fleet/workers",
        state="workers_missing",
        severity="warning",
        reason="no workers",
    )

    events, _changed = module.record_transitions(
        [warning], state, observed_at=10, confirmations=2
    )
    assert events == []
    events, _changed = module.record_transitions(
        [warning], state, observed_at=20, confirmations=2
    )
    assert [item.state for item in events] == ["workers_missing"]

    healthy = module.SupervisorSignal(
        key="fleet/workers",
        state="healthy",
        severity="info",
        reason="one worker",
    )
    module.record_transitions([healthy], state, observed_at=30, confirmations=2)
    events, _changed = module.record_transitions(
        [healthy], state, observed_at=40, confirmations=2
    )
    assert events[0].previous_state == "workers_missing"
    assert events[0].state == "healthy"

    module.record_transitions(
        [healthy], state, observed_at=50, confirmations=2
    )
    assert state["supervisor"]["signals"]["fleet/workers"]["candidate_count"] == 2


def test_transition_history_is_bounded_and_contains_no_capture() -> None:
    state: dict[str, object] = {"version": 1, "sessions": {}}
    for index in range(5):
        signal = module.SupervisorSignal(
            key="session/sam/coordinator",
            state=f"state-{index}",
            severity="warning",
            reason="metadata only",
        )
        module.record_transitions(
            [signal], state, observed_at=float(index), confirmations=1, max_events=2
        )

    supervisor = state["supervisor"]
    assert isinstance(supervisor, dict)
    events = supervisor["events"]
    assert isinstance(events, list)
    assert len(events) == 2
    assert "output" not in json.dumps(state).lower()
