#!/usr/bin/env python3
"""Provider-neutral supervisor policy and report primitives."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class SupervisorAssessment:
    failure_class: str
    remediation: str


@dataclass(frozen=True)
class SupervisorRemediation:
    action: str
    retryable: bool
    max_attempts: int


@dataclass(frozen=True)
class TimeoutTelemetry:
    timeout_s: float
    elapsed_s: float
    poll_interval_s: float
    poll_count: int
    last_progress_s_ago: float
    screen_changed_recently: bool
    marker_seen_without_ack: bool = False


@dataclass(frozen=True)
class SupervisorSignal:
    key: str
    state: str
    severity: str
    reason: str
    provider: str = ""
    schedule_source: str = ""
    not_before: float = 0.0


@dataclass(frozen=True)
class SupervisorEvent:
    key: str
    previous_state: str
    state: str
    severity: str
    reason: str
    observed_at: float


_UNKNOWN_REMEDIATION = SupervisorRemediation(
    action="stop_run",
    retryable=False,
    max_attempts=0,
)

SUPERVISOR_REMEDIATION_REGISTRY: Mapping[str, SupervisorRemediation] = MappingProxyType(
    {
        "marker_format_issue": SupervisorRemediation(
            action="normalize_marker_variant", retryable=True, max_attempts=1
        ),
        "delivery_ack_issue": SupervisorRemediation(
            action="normalize_delivery_ack_variant", retryable=True, max_attempts=1
        ),
        "approval_pattern_missing": SupervisorRemediation(
            action="extend_approval_profile", retryable=True, max_attempts=1
        ),
        "model_fallback_needed": SupervisorRemediation(
            action="switch_fallback_model", retryable=True, max_attempts=1
        ),
        "review_context_missing": SupervisorRemediation(
            action="re_prompt_reviewer_with_artifact_context",
            retryable=True,
            max_attempts=1,
        ),
        "queued_prompt_issue": SupervisorRemediation(
            action="resume_queued_prompt", retryable=True, max_attempts=2
        ),
        "provider_not_ready": _UNKNOWN_REMEDIATION,
        "stalled_run": _UNKNOWN_REMEDIATION,
        "unknown_controller_failure": _UNKNOWN_REMEDIATION,
    }
)


def remediation_for(failure_class: str) -> SupervisorRemediation:
    """Return a bounded policy; unknown classes always fail closed."""
    return SUPERVISOR_REMEDIATION_REGISTRY.get(
        str(failure_class or "").strip(), _UNKNOWN_REMEDIATION
    )


def report_payload(
    *,
    role: str,
    marker: str,
    assessment: SupervisorAssessment,
    attempts: int = 0,
    timeout_telemetry: TimeoutTelemetry | None = None,
) -> dict[str, object]:
    remediation = remediation_for(assessment.failure_class)
    payload: dict[str, object] = {
        "schema": "mesh.controller.supervisor.v1",
        "role": str(role or ""),
        "marker": str(marker or ""),
        "failure_class": assessment.failure_class,
        "failure_summary": assessment.remediation,
        "action": remediation.action,
        "retryable": remediation.retryable,
        "max_attempts": remediation.max_attempts,
        "attempts": max(0, int(attempts)),
    }
    if timeout_telemetry is not None:
        payload["timeout"] = {
            "timeout_s": round(float(timeout_telemetry.timeout_s), 3),
            "elapsed_s": round(float(timeout_telemetry.elapsed_s), 3),
            "poll_interval_s": round(float(timeout_telemetry.poll_interval_s), 3),
            "poll_count": int(timeout_telemetry.poll_count),
            "last_progress_s_ago": round(
                float(timeout_telemetry.last_progress_s_ago), 3
            ),
            "screen_changed_recently": bool(
                timeout_telemetry.screen_changed_recently
            ),
            "marker_seen_without_ack": bool(
                timeout_telemetry.marker_seen_without_ack
            ),
        }
    return payload


def outcome_fields(
    *,
    status: str,
    assessment: SupervisorAssessment | None = None,
    attempts: int = 0,
) -> dict[str, object]:
    item = assessment or SupervisorAssessment(failure_class="", remediation="")
    return {
        "supervisor_status": str(status or "").strip() or "unknown",
        "supervisor_failure_class": str(item.failure_class or "").strip(),
        "supervisor_remediation": str(item.remediation or "").strip(),
        "supervisor_attempts": max(0, int(attempts)),
    }


def record_transitions(
    signals: Sequence[SupervisorSignal],
    state: dict[str, Any],
    *,
    observed_at: float,
    confirmations: int = 2,
    max_events: int = 100,
) -> tuple[list[SupervisorEvent], bool]:
    """Debounce metadata-only signals and retain a bounded transition history."""
    if confirmations < 1:
        raise ValueError("supervisor confirmations must be positive")
    if max_events < 1:
        raise ValueError("supervisor max_events must be positive")
    supervisor = state.setdefault("supervisor", {})
    if not isinstance(supervisor, dict):
        raise ValueError("supervisor state must be an object")
    saved_signals = supervisor.setdefault("signals", {})
    saved_events = supervisor.setdefault("events", [])
    if not isinstance(saved_signals, dict) or not isinstance(saved_events, list):
        raise ValueError("invalid supervisor transition state")

    emitted: list[SupervisorEvent] = []
    changed = False
    active_keys: set[str] = set()
    for signal in signals:
        key = str(signal.key or "").strip()
        if not key:
            continue
        active_keys.add(key)
        saved = saved_signals.setdefault(key, {})
        if not isinstance(saved, dict):
            saved = {}
            saved_signals[key] = saved
        candidate = str(saved.get("candidate_state") or "")
        if candidate == signal.state:
            count = min(int(saved.get("candidate_count") or 0) + 1, confirmations)
        else:
            candidate = signal.state
            count = 1
        previous = str(saved.get("stable_state") or "")
        saved.update(
            {
                "candidate_state": candidate,
                "candidate_count": count,
                "severity": signal.severity,
                "reason": signal.reason,
                "observed_at": observed_at,
            }
        )
        changed = True
        if count < confirmations or candidate == previous:
            continue
        saved["stable_state"] = candidate
        event = SupervisorEvent(
            key=key,
            previous_state=previous,
            state=candidate,
            severity=signal.severity,
            reason=signal.reason,
            observed_at=observed_at,
        )
        if previous or candidate != "healthy":
            emitted.append(event)
            saved_events.append(
                {
                    "key": event.key,
                    "previous_state": event.previous_state,
                    "state": event.state,
                    "severity": event.severity,
                    "reason": event.reason,
                    "observed_at": event.observed_at,
                }
            )

    for key in list(saved_signals):
        if key not in active_keys:
            del saved_signals[key]
            changed = True
    if len(saved_events) > max_events:
        del saved_events[:-max_events]
        changed = True
    return emitted, changed
