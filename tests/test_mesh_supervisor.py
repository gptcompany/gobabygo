from __future__ import annotations

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
