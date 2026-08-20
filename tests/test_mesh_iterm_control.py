from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import pytest


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "mesh_iterm_control.py"
    spec = importlib.util.spec_from_file_location("mesh_iterm_control", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _FakeLine:
    def __init__(self, string: str):
        self.string = string


class _FakeScreen:
    def __init__(self, lines: list[str]):
        self._lines = [_FakeLine(line) for line in lines]

    @property
    def number_of_lines(self) -> int:
        return len(self._lines)

    def line(self, index: int):
        return self._lines[index]


class _FakeSession:
    def __init__(self, *, role: str = "", repo: str = "", marked: bool = False, ui_group_id: str = ""):
        self.variables = {
            "user.mesh_ui_tab": "1" if marked else "",
            "user.mesh_repo": repo,
            "user.mesh_role": role,
            "user.mesh_ui_group_id": ui_group_id,
        }
        self.sent: list[str] = []
        self.activated = False
        self.screen = _FakeScreen([])

    async def async_get_variable(self, name: str):
        return self.variables.get(name, "")

    async def async_set_variable(self, name: str, value: str):
        self.variables[name] = value

    async def async_send_text(self, text: str):
        self.sent.append(text)

    async def async_activate(self):
        self.activated = True

    async def async_get_screen_contents(self):
        return self.screen


class _FakeTab:
    def __init__(self, sessions):
        self.sessions = sessions


class _FakeWindow:
    def __init__(self, tabs):
        self.tabs = tabs


def test_key_text_maps_common_keys():
    module = _load_module()

    assert module._key_text("enter") == "\r"
    assert module._key_text("up") == "\x1b[A"
    assert module._key_text("ctrl-c") == "\x03"


def test_iterm_retry_enabled_reads_env(monkeypatch):
    module = _load_module()

    monkeypatch.delenv("MESH_ITERM_RETRY", raising=False)
    assert module._iterm_retry_enabled() is False

    monkeypatch.setenv("MESH_ITERM_RETRY", "yes")
    assert module._iterm_retry_enabled() is True


def test_emit_writes_output_file(tmp_path):
    module = _load_module()
    target = tmp_path / "term.txt"

    module._emit("one", str(target))

    assert target.read_text(encoding="utf-8") == "one\n"


def test_ui_command_env_key_normalizes_role():
    module = _load_module()

    assert module._ui_command_env_key("worker-gemini") == "MESH_UI_CMD_WORKER_GEMINI"


def test_command_name_extracts_first_token():
    module = _load_module()

    assert module._command_name("gemini --model auto") == "gemini"
    assert module._command_name(" codex ") == "codex"
    assert module._command_name("") == ""


def test_role_launch_command_quotes_repo_path(monkeypatch):
    module = _load_module()
    monkeypatch.setenv("PATH", "/nonexistent")

    command = module._role_launch_command("/tmp/demo repo", "gemini")

    assert command.startswith("exec /bin/zsh -lc ")
    assert "/tmp/demo repo" in command
    assert "source ~/.zshrc" in command
    assert "gemini; status=$?" in command
    assert "leaving shell open for diagnostics" in command


def test_role_launch_command_uses_zsh_from_path(tmp_path, monkeypatch):
    module = _load_module()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    zsh = bin_dir / "zsh"
    zsh.write_text("#!/bin/sh\n", encoding="utf-8")
    zsh.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))

    command = module._role_launch_command("/tmp/demo", "codex --model test")

    assert command.startswith(f"exec {zsh} -lc ")
    assert "cd /tmp/demo || exit $?" in command
    assert "codex --model test; status=$?" in command
    assert "exec zsh -l" in command


def test_restart_mesh_role_pane_interrupts_and_restarts_command():
    module = _load_module()
    session = _FakeSession(role="boss", repo="/media/sam/1TB/demo", marked=True)
    pane = module.MeshPane(
        window_index=0,
        tab_index=0,
        session_index=0,
        repo="/media/sam/1TB/demo",
        role="boss",
        ui_group_id="group-1",
        tab=object(),
        session=session,
    )

    asyncio.run(module._restart_mesh_role_pane(pane, "/media/sam/1TB/demo", "codex"))

    assert session.activated is True
    assert session.sent[0] == module._key_text("ctrl-c")
    assert session.sent[-1] == "\r"
    assert "source ~/.zshrc" in "".join(session.sent[1:-1])
    assert "cd /media/sam/1TB/demo || return $?" in "".join(session.sent[1:-1])
    assert "codex" in "".join(session.sent[1:-1])


def test_launch_single_role_tab_in_group_marks_and_starts_new_tab():
    module = _load_module()
    existing = _FakeSession(role="president", repo="/media/sam/1TB/demo", marked=True, ui_group_id="group-1")
    created = _FakeSession()
    tab_existing = _FakeTab([existing])
    tab_created = _FakeTab([created])

    class _Window:
        def __init__(self):
            self.tabs = [tab_existing]

        async def async_create_tab(self):
            self.tabs.append(tab_created)
            return tab_created

    window = _Window()
    app = type("App", (), {"windows": [window]})()

    launched = asyncio.run(
        module._launch_single_role_tab_in_group(
            app,
            repo="/media/sam/1TB/demo",
            role="boss",
            command_text="codex",
            ui_group_id="group-1",
        )
    )

    assert launched is True
    assert created.variables["user.mesh_ui_tab"] == "1"
    assert created.variables["user.mesh_repo"] == "/media/sam/1TB/demo"
    assert created.variables["user.mesh_role"] == "boss"
    assert created.variables["user.mesh_ui_group_id"] == "group-1"
    assert created.activated is True
    assert created.sent[-1].endswith("\r")
    assert "[mesh:boss] repo=demo" in created.sent[-1]
    assert "codex; status=$?" in created.sent[-1]


def test_format_mesh_msg_is_single_line_and_quoted():
    module = _load_module()

    message = module._format_mesh_msg(
        id="m1",
        from_role="boss",
        task="line one\nline two",
        write_allowed="false",
    )

    assert "\n" not in message
    assert message.startswith("MESH_MSG ")
    assert "task='line one line two'" in message
    assert message.endswith(" END_MESH_MSG")


def test_worker_prompt_allows_scoped_edits_when_write_enabled():
    module = _load_module()

    text = module._worker_prompt_text(
        task="Create demo/ragdoll-physics/index.html with gravity",
        allow_write=True,
        allowed_edit_paths=("demo/ragdoll-physics/index.html",),
    )

    assert "Edit only these path(s): demo/ragdoll-physics/index.html." in text
    assert "Implement: Create demo/ragdoll-physics/index.html with gravity." in text
    assert "No edits" not in text


def test_worker_prompt_keeps_no_edits_when_write_disabled():
    module = _load_module()

    text = module._worker_prompt_text(
        task="Create demo",
        allow_write=False,
        allowed_edit_paths=("demo/index.html",),
    )

    assert "No edits" in text
    assert "Draft implementation plan" in text


def test_parse_product_review_contract():
    module = _load_module()

    review = module._parse_product_review(
        "\n".join(
            [
                "DELIVERY_ACK S1 E1",
                "PRODUCT_REVIEW status=retry score=4 visual=3 interaction=5 clarity=4 technical=7",
                "FEEDBACK: Dolls are hard to read.",
                "SPECKIT_RUN_REVIEWER_DONE_ABC",
            ]
        )
    )

    assert review.parsed is True
    assert review.status == "retry"
    assert review.score == 4
    assert review.visual == 3
    assert review.interaction == 5
    assert review.clarity == 4
    assert review.technical == 7
    assert review.feedback == "Dolls are hard to read."


def test_parse_product_review_handles_wrapped_scores():
    module = _load_module()

    review = module._parse_product_review(
        "\n".join(
            [
                "PRODUCT_REVIEW status=retry score=4 visual=3 interaction=5 clarity=4",
                "technical=7",
                "FEEDBACK: Needs better scene.",
            ]
        )
    )

    assert review.parsed is True
    assert review.technical == 7
    assert review.feedback == "Needs better scene."


def test_product_review_pass_requires_pass_status_and_min_score():
    module = _load_module()

    passing = module._parse_product_review("PRODUCT_REVIEW status=pass score=8 visual=8 interaction=7 clarity=8 technical=9")
    low_score = module._parse_product_review("PRODUCT_REVIEW status=pass score=6 visual=8 interaction=7 clarity=8 technical=9")
    retry = module._parse_product_review("PRODUCT_REVIEW status=retry score=9 visual=9 interaction=9 clarity=9 technical=9")

    assert module._product_review_passed(passing, min_score=7) is True
    assert module._product_review_passed(low_score, min_score=7) is False
    assert module._product_review_passed(retry, min_score=7) is False


def test_product_review_payload_reports_controller_status():
    module = _load_module()

    review = module._parse_product_review("PRODUCT_REVIEW status=pass score=8 visual=8 interaction=7 clarity=8 technical=9")
    payload = module._product_review_payload(review, min_score=7, retry_count=1)

    assert payload["controller_status"] == "passed"
    assert payload["score"] == 8
    assert payload["retry_count"] == 1


def test_product_retry_worker_prompt_scopes_edits_and_feedback():
    module = _load_module()

    text = module._product_retry_worker_prompt_text(
        task="Improve ragdoll",
        feedback="Scene is plain",
        allowed_edit_paths=("demo/ragdoll-physics/index.html",),
    )

    assert "Edit only these path(s): demo/ragdoll-physics/index.html." in text
    assert "Reviewer feedback: Scene is plain." in text
    assert "No nested CLI" in text


def test_reviewer_product_prompt_includes_artifact_path_and_read_only_access():
    module = _load_module()

    text = module._reviewer_product_prompt_text(
        test_status="passed",
        quality_status="passed",
        min_score=7,
        artifact_paths=("demo/ragdoll-physics/index.html",),
    )

    assert "Artifact path(s): demo/ragdoll-physics/index.html." in text
    assert "Read-only inspection is allowed" in text
    assert "do not score 0 solely for lack of browser access" in text


def test_classify_controller_failure_recognizes_model_fallback_needed():
    module = _load_module()

    assessment = module._classify_controller_failure(
        screen_text="We are currently experiencing high demand.\n2. Switch to gemini-2.5-flash",
        marker="GBG_BOSS_ABC123",
        delivery_ack=None,
    )

    assert assessment.failure_class == "model_fallback_needed"


def test_classify_controller_failure_recognizes_queued_prompt_issue():
    module = _load_module()

    assessment = module._classify_controller_failure(
        screen_text="Queued (press ↑ to edit): Reply exactly 2 lines...\nType your message or @path/to/file",
        marker="GBG_BOSS_ABC123",
        delivery_ack=None,
    )

    assert assessment.failure_class == "queued_prompt_issue"


def test_classify_controller_failure_recognizes_provider_not_ready():
    module = _load_module()

    assessment = module._classify_controller_failure(
        screen_text="Waiting for MCP servers to initialize... prompts will be queued.",
        marker="GBG_BOSS_ABC123",
        delivery_ack=None,
    )

    assert assessment.failure_class == "provider_not_ready"


def test_classify_controller_failure_recognizes_review_context_missing():
    module = _load_module()

    assessment = module._classify_controller_failure(
        screen_text=(
            "PRODUCT_REVIEW status=retry score=0 visual=0 interaction=0 clarity=0 technical=0\n"
            "FEEDBACK: Unable to playtest the artifact as a product due to lack of browser access."
        ),
        marker="GBG_REVIEW_ABC123",
        delivery_ack=None,
    )

    assert assessment.failure_class == "review_context_missing"


def test_classify_controller_failure_recognizes_marker_format_issue():
    module = _load_module()

    assessment = module._classify_controller_failure(
        screen_text="some output with GBG_BOSS_ABC123 inline but no exact final line",
        marker="GBG_BOSS_ABC123",
        delivery_ack=None,
    )

    assert assessment.failure_class == "marker_format_issue"


def test_classify_controller_failure_recognizes_stalled_run_from_timeout_telemetry():
    module = _load_module()

    assessment = module._classify_controller_failure(
        screen_text="worker still thinking",
        marker="GBG_WORKER_ABC123",
        delivery_ack=None,
        timeout_telemetry=module.TimeoutTelemetry(
            timeout_s=30.0,
            elapsed_s=30.2,
            poll_interval_s=1.0,
            poll_count=30,
            last_progress_s_ago=12.0,
            screen_changed_recently=False,
            marker_seen_without_ack=False,
        ),
    )

    assert assessment.failure_class == "stalled_run"


def test_supervisor_remediation_registry_returns_known_action():
    module = _load_module()

    remediation = module._supervisor_remediation_for("review_context_missing")

    assert remediation.action == "re_prompt_reviewer_with_artifact_context"
    assert remediation.retryable is True
    assert remediation.max_attempts == 1


def test_supervisor_remediation_registry_resumes_queued_prompt_twice_max():
    module = _load_module()

    remediation = module._supervisor_remediation_for("queued_prompt_issue")

    assert remediation.action == "resume_queued_prompt"
    assert remediation.retryable is True
    assert remediation.max_attempts == 2


def test_supervisor_remediation_registry_stops_provider_not_ready():
    module = _load_module()

    remediation = module._supervisor_remediation_for("provider_not_ready")

    assert remediation.action == "stop_run"
    assert remediation.retryable is False
    assert remediation.max_attempts == 0


def test_supervisor_remediation_registry_stops_stalled_run():
    module = _load_module()

    remediation = module._supervisor_remediation_for("stalled_run")

    assert remediation.action == "stop_run"
    assert remediation.retryable is False
    assert remediation.max_attempts == 0


def test_supervisor_report_payload_contains_reason_and_budget():
    module = _load_module()

    assessment = module.SupervisorAssessment(
        failure_class="delivery_ack_issue",
        remediation="normalize delivery-ack formatting variants for this provider",
    )
    payload = module._supervisor_report_payload(
        role="boss",
        marker="GBG_BOSS_ABC123",
        assessment=assessment,
        attempts=1,
    )

    assert payload["schema"] == "mesh.controller.supervisor.v1"
    assert payload["failure_class"] == "delivery_ack_issue"
    assert payload["action"] == "normalize_delivery_ack_variant"
    assert payload["retryable"] is True
    assert payload["max_attempts"] == 1
    assert payload["attempts"] == 1


def test_supervisor_finalize_assessment_promotes_queued_issue_to_provider_not_ready():
    module = _load_module()

    finalized = module._supervisor_finalize_assessment(
        screen_text="Waiting for MCP servers to initialize... prompts will be queued.\nQueued (press ↑ to edit): hi",
        assessment=module.SupervisorAssessment(
            failure_class="queued_prompt_issue",
            remediation="resume the queued Gemini composer and continue waiting",
        ),
        attempts=2,
    )

    assert finalized.failure_class == "provider_not_ready"


def test_supervisor_finalize_assessment_keeps_queued_issue_before_budget_exhaustion():
    module = _load_module()

    finalized = module._supervisor_finalize_assessment(
        screen_text="Waiting for MCP servers to initialize... prompts will be queued.\nQueued (press ↑ to edit): hi",
        assessment=module.SupervisorAssessment(
            failure_class="queued_prompt_issue",
            remediation="resume the queued Gemini composer and continue waiting",
        ),
        attempts=1,
    )

    assert finalized.failure_class == "queued_prompt_issue"


def test_supervisor_finalize_assessment_promotes_model_fallback_to_provider_not_ready():
    module = _load_module()

    finalized = module._supervisor_finalize_assessment(
        screen_text=(
            "We are currently experiencing high demand.\n"
            "2. Switch to gemini-2.5-flash"
        ),
        assessment=module.SupervisorAssessment(
            failure_class="model_fallback_needed",
            remediation="select fallback model once and resume waiting",
        ),
        attempts=1,
    )

    assert finalized.failure_class == "provider_not_ready"


def test_supervisor_error_role_extracts_role_from_runtime_error():
    module = _load_module()

    role = module._supervisor_error_role(
        "timed out waiting for marker 'GBG_BOSS_123' in role=boss\nsupervisor_failure_class=provider_not_ready"
    )

    assert role == "boss"


def test_provider_fallback_command_switches_gemini_role_to_codex_once():
    module = _load_module()
    commands = {"boss": "gemini", "worker-gemini": "gemini", "president": "codex"}
    error_text = (
        "timed out waiting for marker 'GBG_BOSS_123' in role=boss\n"
        "supervisor_failure_class=provider_not_ready"
    )

    assert module._provider_fallback_command(
        role="boss",
        commands=commands,
        error_text=error_text,
        used_roles=set(),
    ) == "codex"
    assert module._provider_fallback_command(
        role="boss",
        commands=commands,
        error_text=error_text,
        used_roles={"boss"},
    ) == ""
    assert module._provider_fallback_command(
        role="president",
        commands=commands,
        error_text=error_text,
        used_roles=set(),
    ) == ""


def test_supervisor_outcome_fields_defaults_to_clean_success():
    module = _load_module()

    payload = module._supervisor_outcome_fields(status="passed")

    assert payload == {
        "supervisor_status": "passed",
        "supervisor_failure_class": "",
        "supervisor_remediation": "",
        "supervisor_attempts": 0,
    }


def test_supervisor_failure_handoff_payload_parses_runtime_error_fields():
    module = _load_module()

    payload = module._supervisor_failure_handoff_payload(
        feature="Supervisor smoke",
        task="Inspect file",
        error_text=(
            "timed out waiting for marker 'GBG_WORKER_123' in role=worker-gemini\n"
            "supervisor_failure_class=stalled_run\n"
            "supervisor_remediation=stop the run after timeout budget is exhausted without fresh pane output\n"
            "supervisor_action=stop_run\n"
            "supervisor_retryable=False\n"
            "supervisor_max_attempts=0\n"
            "supervisor_attempts=1\n"
            "timeout_elapsed_s=46.246\n"
            "timeout_poll_count=23\n"
            "timeout_last_progress_s_ago=28.103"
        ),
    )

    assert payload["phase"] == "speckit.report"
    assert payload["run_status"] == "failed"
    assert payload["supervisor_status"] == "failed"
    assert payload["supervisor_failure_class"] == "stalled_run"
    assert payload["supervisor_attempts"] == 1
    assert payload["supervisor_action"] == "stop_run"
    assert payload["supervisor_retryable"] is False
    assert payload["supervisor_max_attempts"] == 0
    assert payload["timeout"]["elapsed_s"] == pytest.approx(46.246)
    assert payload["timeout"]["poll_count"] == 23


def test_wait_for_screen_marker_reports_supervisor_failure_class():
    module = _load_module()
    session = _FakeSession(role="boss", repo="/media/sam/1TB/demo", marked=True)
    session.screen = _FakeScreen(
        [
            "We are currently experiencing high demand.",
            "2. Switch to gemini-2.5-flash",
        ]
    )

    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(
            module._wait_for_screen_marker(
                session,
                role="boss",
                marker="GBG_BOSS_ABC123",
                timeout=0.2,
                poll_interval=0.1,
            )
        )

    message = str(excinfo.value)
    assert "supervisor_failure_class=provider_not_ready" in message
    assert "supervisor_action=stop_run" in message
    assert "supervisor_retryable=False" in message


def _make_claude_config_root(base: Path) -> Path:
    root = base / "claude-config"
    (root / "commands").mkdir(parents=True)
    (root / "scripts").mkdir()
    return root


def _write_claude_config_file(root: Path, relative_path: str, content: str = "x") -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_resolve_claude_config_root_uses_explicit_path(tmp_path):
    module = _load_module()
    root = _make_claude_config_root(tmp_path)

    result = module._resolve_claude_config_root(str(root), candidates=())

    assert result.available is True
    assert result.root == str(root.resolve())
    assert result.source == "explicit"
    assert result.markers == ("commands", "scripts")


def test_resolve_claude_config_root_uses_env_path(tmp_path):
    module = _load_module()
    root = _make_claude_config_root(tmp_path)

    result = module._resolve_claude_config_root("", env={"MESH_CLAUDE_CONFIG": str(root)}, candidates=())

    assert result.available is True
    assert result.root == str(root.resolve())
    assert result.source == "env"


def test_resolve_claude_config_root_rejects_invalid_explicit_path(tmp_path):
    module = _load_module()
    invalid = tmp_path / "not-claude-config"
    invalid.mkdir()

    with pytest.raises(RuntimeError, match="invalid claude-config root"):
        module._resolve_claude_config_root(str(invalid), candidates=())


def test_resolve_claude_config_root_default_missing_is_nonfatal():
    module = _load_module()

    result = module._resolve_claude_config_root("", env={}, candidates=())

    assert result.available is False
    assert result.source == "none"
    assert "no claude-config root found" in result.reason


def test_claude_config_payload_is_json_serializable(tmp_path):
    module = _load_module()
    root = _make_claude_config_root(tmp_path)
    result = module._resolve_claude_config_root(str(root), candidates=())

    payload = module._claude_config_payload(result)

    assert payload == {
        "available": True,
        "root": str(root.resolve()),
        "source": "explicit",
        "markers": ["commands", "scripts"],
        "reason": "",
    }
    json.dumps(payload)


def test_claude_config_contract_inventory_maps_known_files(tmp_path):
    module = _load_module()
    root = _make_claude_config_root(tmp_path)
    pipeline = _write_claude_config_file(root, "commands/pipeline.speckit.md")
    gate = _write_claude_config_file(root, "scripts/confidence_gate.py")
    result = module._resolve_claude_config_root(str(root), candidates=())

    inventory = module._claude_config_contract_inventory(
        result,
        names=("pipeline.speckit", "confidence-gate", "validate"),
    )

    assert inventory["pipeline.speckit"] == {
        "name": "pipeline.speckit",
        "kind": "command",
        "relative_path": "commands/pipeline.speckit.md",
        "path": str(pipeline.resolve()),
        "exists": True,
    }
    assert inventory["confidence-gate"]["path"] == str(gate.resolve())
    assert inventory["confidence-gate"]["kind"] == "script"
    assert inventory["confidence-gate"]["exists"] is True
    assert inventory["validate"]["relative_path"] == "skills/validate/SKILL.md"
    assert inventory["validate"]["exists"] is False


def test_claude_config_contract_inventory_reports_unknown_name(tmp_path):
    module = _load_module()
    root = _make_claude_config_root(tmp_path)
    result = module._resolve_claude_config_root(str(root), candidates=())

    inventory = module._claude_config_contract_inventory(result, names=("unknown.contract",))

    assert inventory["unknown.contract"] == {
        "name": "unknown.contract",
        "kind": "unknown",
        "relative_path": "",
        "path": "",
        "exists": False,
        "error": "unknown contract",
    }


def test_claude_config_payload_can_include_contract_metadata(tmp_path):
    module = _load_module()
    root = _make_claude_config_root(tmp_path)
    _write_claude_config_file(root, "commands/pipeline.speckit.md")
    result = module._resolve_claude_config_root(str(root), candidates=())

    payload = module._claude_config_payload(result, include_contracts=True)

    assert payload["contracts"]["pipeline.speckit"]["exists"] is True
    assert "speckit.analyze" in payload["missing_contracts"]
    json.dumps(payload)


def test_role_contract_names_reads_optional_args():
    module = _load_module()

    args = type(
        "Args",
        (),
        {
            "boss_contract": "pipeline.speckit",
            "president_contract": "speckit.analyze",
            "worker_contract": "speckit.implement",
            "reviewer_contract": "verify.quick",
        },
    )()

    assert module._role_contract_names(args) == {
        "boss": "pipeline.speckit",
        "president": "speckit.analyze",
        "worker": "speckit.implement",
        "reviewer": "verify.quick",
    }


def test_role_contract_names_defaults_missing_args_to_empty():
    module = _load_module()

    assert module._role_contract_names(type("Args", (), {})()) == {
        "boss": "",
        "president": "",
        "worker": "",
        "reviewer": "",
    }


def test_role_contract_names_uses_defaults_when_claude_config_is_explicit():
    module = _load_module()

    args = type("Args", (), {"claude_config": "/tmp/claude-config"})()

    assert module._role_contract_names(args) == {
        "boss": "pipeline.speckit",
        "president": "speckit.analyze",
        "worker": "speckit.implement",
        "reviewer": "verify.quick",
    }


def test_role_contract_names_explicit_values_override_defaults_when_enabled():
    module = _load_module()

    args = type(
        "Args",
        (),
        {
            "claude_config": "/tmp/claude-config",
            "worker_contract": "custom.worker",
        },
    )()

    assert module._role_contract_names(args)["worker"] == "custom.worker"
    assert module._role_contract_names(args)["boss"] == "pipeline.speckit"


def test_compact_contract_excerpt_extracts_frontmatter_heading_and_objective():
    module = _load_module()
    text = """---
name: speckit:analyze
description: Analyze artifacts
allowed-tools:
  - Read
---
# Speckit Analyze

Intro that should not win over objective.

<objective>
Validate spec, plan, and tasks without editing files.
</objective>

## Later
Ignore this.
"""

    excerpt, truncated = module._compact_contract_excerpt_from_text(text, max_chars=500)

    assert truncated is False
    assert "name: speckit:analyze" in excerpt
    assert "description: Analyze artifacts" in excerpt
    assert "# Speckit Analyze" in excerpt
    assert "<objective>" in excerpt
    assert "Validate spec, plan, and tasks without editing files." in excerpt
    assert "allowed-tools" not in excerpt
    assert "## Later" not in excerpt


def test_compact_contract_excerpt_handles_plain_markdown_intro():
    module = _load_module()
    text = """# Quick Verification

Run fast verification loop.

## Phases

This section should not be included in fallback intro.
"""

    excerpt, truncated = module._compact_contract_excerpt_from_text(text, max_chars=500)

    assert truncated is False
    assert "# Quick Verification" in excerpt
    assert "Run fast verification loop." in excerpt
    assert "## Phases" not in excerpt


def test_compact_contract_excerpt_is_bounded():
    module = _load_module()
    text = "# Big\n\n" + ("abcdef " * 100)

    excerpt, truncated = module._compact_contract_excerpt_from_text(text, max_chars=80)

    assert truncated is True
    assert len(excerpt) <= 80
    assert excerpt.endswith("...[truncated]")


def test_claude_config_contract_excerpt_reads_file_with_metadata(tmp_path):
    module = _load_module()
    root = _make_claude_config_root(tmp_path)
    target = _write_claude_config_file(
        root,
        "commands/speckit.implement.md",
        """---
name: speckit:implement
description: Implement tasks
---
# Speckit Implement

<process>
Execute tasks with tests and no hidden delegation.
</process>
""",
    )
    result = module._resolve_claude_config_root(str(root), candidates=())

    payload = module._claude_config_contract_excerpt(result, "speckit.implement", max_chars=500)

    assert payload["name"] == "speckit.implement"
    assert payload["kind"] == "command"
    assert payload["relative_path"] == "commands/speckit.implement.md"
    assert payload["path"] == str(target.resolve())
    assert payload["exists"] is True
    assert payload["excerpt_truncated"] is False
    assert payload["excerpt_max_chars"] == 500
    assert "description: Implement tasks" in payload["excerpt"]
    assert "<process>" in payload["excerpt"]


def test_claude_config_contract_excerpt_reports_missing_file(tmp_path):
    module = _load_module()
    root = _make_claude_config_root(tmp_path)
    result = module._resolve_claude_config_root(str(root), candidates=())

    payload = module._claude_config_contract_excerpt(result, "verify.quick")

    assert payload["name"] == "verify.quick"
    assert payload["exists"] is False
    assert payload["excerpt"] == ""


def test_role_contract_context_includes_one_line_excerpt(tmp_path):
    module = _load_module()
    root = _make_claude_config_root(tmp_path)
    _write_claude_config_file(
        root,
        "commands/pipeline.speckit.md",
        """---
name: pipeline:speckit
description: Full pipeline
---
# Pipeline

<objective>
Coordinate phases without bypassing Mesh.
</objective>
""",
    )
    result = module._resolve_claude_config_root(str(root), candidates=())

    context = module._role_contract_context(result, "boss", "pipeline.speckit", max_chars=500)

    assert "\n" not in context
    assert "CLAUDE_CONFIG_CONTRACT role=boss name=pipeline.speckit" in context
    assert "source=commands/pipeline.speckit.md" in context
    assert "description: Full pipeline" not in context
    assert "Coordinate phases without bypassing Mesh." not in context


def test_role_contract_context_reports_unavailable_contract(tmp_path):
    module = _load_module()
    root = _make_claude_config_root(tmp_path)
    result = module._resolve_claude_config_root(str(root), candidates=())

    context = module._role_contract_context(result, "reviewer", "verify.quick")

    assert context == "CLAUDE_CONFIG_CONTRACT role=reviewer name=verify.quick unavailable: missing contract file."


def test_prompt_contract_context_appends_space_only_when_present():
    module = _load_module()

    assert module._prompt_contract_context("") == ""
    assert module._prompt_contract_context("hello\nworld") == "hello world "


def test_role_output_policy_scanner_blocks_non_worker_cli_launch():
    module = _load_module()

    findings = module._scan_role_output_policy(
        role="boss",
        role_class="non_worker",
        phase="speckit.discuss",
        text="I will run codex --ask worker now.",
    )

    assert findings[0]["severity"] == "block"
    assert findings[0]["role"] == "boss"
    assert findings[0]["rule_id"] == "codex-cli"
    assert json.dumps(findings)


def test_role_output_policy_scanner_blocks_reviewer_task_tool():
    module = _load_module()

    findings = module._scan_role_output_policy(
        role="reviewer",
        role_class="non_worker",
        phase="speckit.review",
        text="Task({subagent_type: 'general-purpose', prompt: 'audit this'})",
    )

    assert [item["rule_id"] for item in findings] == ["task-tool"]


def test_role_output_policy_scanner_blocks_worker_commit_and_nested_run():
    module = _load_module()

    findings = module._scan_role_output_policy(
        role="worker-gemini",
        role_class="worker",
        phase="speckit.implement",
        text="$ mesh speckit run . --feature demo\nThen git commit -m done",
    )

    assert [item["rule_id"] for item in findings] == ["mesh-speckit-run", "git-commit"]


def test_role_output_policy_scanner_allows_role_name_mentions():
    module = _load_module()

    findings = module._scan_role_output_policy(
        role="president",
        role_class="non_worker",
        phase="speckit.analyze",
        text="Assign exactly one scoped task to worker-gemini and wait for the handoff marker.",
    )

    assert findings == []


def test_role_output_policy_scanner_ignores_cli_update_banner():
    module = _load_module()

    findings = module._scan_role_output_policy(
        role="president",
        role_class="non_worker",
        phase="speckit.verify-work",
        text="ℹ Gemini CLI update available! 0.29.7 -> 0.38.2",
    )

    assert findings == []


def test_role_output_policy_scanner_ignores_model_status_line():
    module = _load_module()

    findings = module._scan_role_output_policy(
        role="boss",
        role_class="non_worker",
        phase="speckit.discuss",
        text="~/gobabygo (master*) no sandbox /model Auto (Gemini 2.5)",
    )

    assert findings == []


def test_role_output_policy_scanner_blocks_command_prefixed_ai_cli():
    module = _load_module()

    findings = module._scan_role_output_policy(
        role="president",
        role_class="non_worker",
        phase="speckit.analyze",
        text="$ gemini --prompt 'do hidden work'",
    )

    assert [item["rule_id"] for item in findings] == ["gemini-cli"]


def test_role_output_policy_scanner_ignores_controller_artifacts():
    module = _load_module()

    text = (
        "CLAUDE_CONFIG_CONTRACT role=boss name=pipeline.speckit excerpt: "
        "Run scripts/confidence_gate.py before release. END_CLAUDE_CONFIG_CONTRACT.\n"
        "Input: MESH_MSG phase=speckit.discuss handoff_out='./scripts/mesh speckit run' END_MESH_MSG\n"
        "Decision: delegate normally."
    )

    findings = module._scan_role_output_policy(
        role="boss",
        role_class="non_worker",
        phase="speckit.discuss",
        text=text,
    )

    assert findings == []


def test_enforce_role_output_policy_raises_on_blocking_violation():
    module = _load_module()

    with pytest.raises(RuntimeError, match="git-push"):
        module._enforce_role_output_policy(
            role="worker-gemini",
            role_class="worker",
            phase="speckit.implement",
            text="git push origin main",
        )


def test_write_policy_violations_json_returns_repo_relative_path(tmp_path):
    module = _load_module()
    repo = tmp_path / "demo"
    repo.mkdir()
    findings = [
        {
            "severity": "block",
            "role": "worker-gemini",
            "role_class": "worker",
            "phase": "speckit.implement",
            "rule_id": "git-commit",
            "reason": "git commit attempt",
            "line": 1,
            "match": "git commit",
            "excerpt": "git commit -m done",
        }
    ]

    rel_path = module._write_policy_violations_json(str(repo), ".mesh/runs", "ABC123", findings)

    assert rel_path == ".mesh/runs/ABC123/policy-violations.json"
    data = json.loads((repo / rel_path).read_text(encoding="utf-8"))
    assert data["schema"] == "mesh.speckit.policy_violations.v1"
    assert data["run_id"] == "ABC123"
    assert data["status"] == "blocked"
    assert data["blocking_count"] == 1
    assert data["finding_count"] == 1
    assert data["findings"][0]["rule_id"] == "git-commit"


def test_write_policy_violations_json_skips_empty_findings(tmp_path):
    module = _load_module()
    repo = tmp_path / "demo"
    repo.mkdir()

    rel_path = module._write_policy_violations_json(str(repo), ".mesh/runs", "ABC123", [])

    assert rel_path == ""
    assert not (repo / ".mesh").exists()


def test_enforce_role_output_policy_writes_sidecar_on_block(tmp_path):
    module = _load_module()
    repo = tmp_path / "demo"
    repo.mkdir()

    with pytest.raises(RuntimeError, match="policy-violations.json"):
        module._enforce_role_output_policy(
            role="worker-gemini",
            role_class="worker",
            phase="speckit.implement",
            text="git push origin main",
            repo=str(repo),
            handoff_dir=".mesh/runs",
            run_id="ABC123",
            write_sidecar=True,
        )

    sidecar = repo / ".mesh/runs/ABC123/policy-violations.json"
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    assert data["status"] == "blocked"
    assert data["findings"][0]["rule_id"] == "git-push"


def test_quality_quick_payload_passes_with_deterministic_evidence():
    module = _load_module()

    payload = module._quality_quick_payload(
        run_id="ABC123",
        git_status="M index.html",
        diff_stat="index.html | 2 +-",
        test_status="passed",
        allow_test_failure=False,
        operator_allowed_edit_paths=("index.html",),
        president_allowed_edit_paths=("index.html", "style.css"),
        effective_allowed_edit_paths=("index.html",),
    )

    assert payload["schema"] == "mesh.speckit.quality_quick.v1"
    assert payload["run_id"] == "ABC123"
    assert payload["mode"] == "quick"
    assert payload["status"] == "passed"
    assert payload["reasons"] == []
    assert payload["evidence"]["git_status"] == "M index.html"
    assert payload["evidence"]["effective_allowed_edit_paths"] == ["index.html"]
    assert payload["evidence"]["policy_status"] == "clean"
    json.dumps(payload)


def test_quality_quick_payload_fails_on_test_failure_unless_allowed():
    module = _load_module()

    failed = module._quality_quick_payload(
        run_id="ABC123",
        git_status="",
        diff_stat="",
        test_status="failed",
        allow_test_failure=False,
        operator_allowed_edit_paths=(),
        president_allowed_edit_paths=(),
        effective_allowed_edit_paths=(),
    )
    allowed = module._quality_quick_payload(
        run_id="ABC123",
        git_status="",
        diff_stat="",
        test_status="failed",
        allow_test_failure=True,
        operator_allowed_edit_paths=(),
        president_allowed_edit_paths=(),
        effective_allowed_edit_paths=(),
    )

    assert failed["status"] == "failed"
    assert failed["reasons"][0]["code"] == "test_failed"
    assert allowed["status"] == "passed"
    assert allowed["warnings"][0]["code"] == "test_failed"


def test_write_quality_quick_json_returns_repo_relative_path(tmp_path):
    module = _load_module()
    repo = tmp_path / "demo"
    repo.mkdir()
    payload = module._quality_quick_payload(
        run_id="ABC123",
        git_status="",
        diff_stat="",
        test_status="skipped",
        allow_test_failure=False,
        operator_allowed_edit_paths=(),
        president_allowed_edit_paths=(),
        effective_allowed_edit_paths=(),
    )

    rel_path = module._write_quality_quick_json(str(repo), ".mesh/runs", "ABC123", payload)

    assert rel_path == ".mesh/runs/ABC123/quality-quick.json"
    data = json.loads((repo / rel_path).read_text(encoding="utf-8"))
    assert data["schema"] == "mesh.speckit.quality_quick.v1"
    assert data["status"] == "passed"


def test_write_quality_quick_json_can_be_disabled(tmp_path):
    module = _load_module()
    repo = tmp_path / "demo"
    repo.mkdir()

    rel_path = module._write_quality_quick_json(str(repo), ".mesh/runs", "ABC123", {}, enabled=False)

    assert rel_path == ""
    assert not (repo / ".mesh").exists()


def test_delivery_prompt_does_not_satisfy_its_own_ack():
    module = _load_module()
    delivery = module._delivery_tokens("ABC123", "speckit.discuss", "boss")

    prompt = module._delivery_prompt("Do the task.", delivery)

    assert delivery["start"] in prompt
    assert delivery["end"] in prompt
    assert "DELIVERY_ACK" in prompt
    assert module._screen_has_delivery_ack(prompt, delivery) is False
    assert module._screen_has_delivery_ack(
        f"DELIVERY_ACK {delivery['start']} {delivery['end']}",
        delivery,
    ) is True
    assert module._screen_has_delivery_ack(
        f"✦ DELIVERY_ACK {delivery['start']} {delivery['end']};",
        delivery,
    ) is True
    assert module._screen_has_delivery_ack(
        f"✦ line1 DELIVERY_ACK {delivery['start']} {delivery['end']}",
        delivery,
    ) is True
    assert module._screen_has_delivery_ack(
        f"✦ DELIVERY_ACK {delivery['start']} {delivery['end']}; GBG_BOSS_ABC123.",
        delivery,
    ) is True
    assert module._screen_has_delivery_ack(
        f"• DELIVERY_ACK {delivery['start']} {delivery['end']}",
        delivery,
    ) is True


def test_screen_has_marker_requires_own_line():
    module = _load_module()

    assert module._screen_has_marker("prompt mentions DONE_MARKER inline", "DONE_MARKER") is False
    assert module._screen_has_marker("summary\nDONE_MARKER\n", "DONE_MARKER") is True
    assert module._screen_has_marker("✦ line2 DONE_MARKER", "DONE_MARKER") is True
    assert module._screen_has_marker("> DONE_MARKER", "DONE_MARKER") is True
    assert module._screen_has_marker("• DONE_MARKER", "DONE_MARKER") is True
    assert module._screen_has_marker("✦ DELIVERY_ACK S1 E1; DONE_MARKER. Role boss.", "DONE_MARKER") is True
    assert module._screen_has_marker("✦ DELIVERY_ACK S1 E1\n  DONE_MARKER. Role boss.", "DONE_MARKER") is True


def test_wait_for_screen_marker_accepts_delivery_ack():
    module = _load_module()
    session = _FakeSession(role="boss", repo="/media/sam/1TB/demo", marked=True)
    delivery = module._delivery_tokens("ABC123", "speckit.discuss", "boss")
    session.screen = _FakeScreen(["ready", f"DELIVERY_ACK {delivery['start']} {delivery['end']}", "DONE_MARKER"])

    asyncio.run(
        module._wait_for_screen_marker(
            session,
            role="boss",
            marker="DONE_MARKER",
            timeout=1.0,
            poll_interval=0.1,
            delivery_ack=delivery,
        )
    )


def test_wait_for_screen_marker_rejects_marker_without_delivery_ack():
    module = _load_module()
    session = _FakeSession(role="boss", repo="/media/sam/1TB/demo", marked=True)
    delivery = module._delivery_tokens("ABC123", "speckit.discuss", "boss")
    session.screen = _FakeScreen(["DONE_MARKER"])

    with pytest.raises(RuntimeError, match="delivery ack"):
        asyncio.run(
            module._wait_for_screen_marker(
                session,
                role="boss",
                marker="DONE_MARKER",
                timeout=1.0,
                poll_interval=0.1,
                delivery_ack=delivery,
            )
        )


def test_write_handoff_json_creates_repo_relative_artifact(tmp_path):
    module = _load_module()
    repo = tmp_path / "demo"
    repo.mkdir()

    rel_path = module._write_handoff_json(
        str(repo),
        ".mesh/runs",
        "ABC123",
        "01-discuss.json",
        {"phase": "speckit.discuss", "marker": "DONE"},
    )

    assert rel_path == ".mesh/runs/ABC123/01-discuss.json"
    data = json.loads((repo / rel_path).read_text(encoding="utf-8"))
    assert data["schema"] == "mesh.speckit.handoff.v1"
    assert data["run_id"] == "ABC123"
    assert data["phase"] == "speckit.discuss"
    assert data["marker"] == "DONE"


def test_write_handoff_json_preserves_supervisor_fields(tmp_path):
    module = _load_module()
    repo = tmp_path / "demo"
    repo.mkdir()

    rel_path = module._write_handoff_json(
        str(repo),
        ".mesh/runs",
        "ABC123",
        "06-report.json",
        {
            "phase": "speckit.report",
            "marker": "DONE",
            **module._supervisor_outcome_fields(status="passed"),
        },
    )

    data = json.loads((repo / rel_path).read_text(encoding="utf-8"))
    assert data["supervisor_status"] == "passed"
    assert data["supervisor_failure_class"] == ""
    assert data["supervisor_attempts"] == 0


def test_write_handoff_json_can_be_disabled(tmp_path):
    module = _load_module()
    repo = tmp_path / "demo"
    repo.mkdir()

    rel_path = module._write_handoff_json(
        str(repo),
        ".mesh/runs",
        "ABC123",
        "01-discuss.json",
        {"phase": "speckit.discuss"},
        enabled=False,
    )

    assert rel_path == ""
    assert not (repo / ".mesh").exists()


def test_turn_limit_text_uses_minimum_one_turn():
    module = _load_module()

    assert "maximum 1 response" in module._turn_limit_text(0)
    assert "Do not ask questions" in module._turn_limit_text(0)


def test_auto_approval_choice_handles_known_prompts():
    module = _load_module()

    assert module._auto_approval_choice("Apply this change?\n1. Yes, allow once") == ("1", "apply change once")
    assert module._auto_approval_choice("Allow execution of 'ls'?\n2. Allow for this session") == (
        "2",
        "allow command for session",
    )
    assert module._auto_approval_choice("Do you trust the files in this folder?\n1. Yes\n2. No") == (
        "1",
        "trust folder",
    )
    assert module._auto_approval_choice("Doyoutrustthecontentsofthisdirectory?\n› 1. Yes, continue") == (
        "1",
        "trust folder",
    )


def test_auto_approval_choice_ignores_plain_screen():
    module = _load_module()

    assert module._auto_approval_choice("Type your message") == ("", "")


def test_codex_needs_submit_retry_when_prompt_is_still_pending():
    module = _load_module()
    screen = "› Reply with exactly PRESIDENT_ACK.\n  gpt-5.4 high · /tmp/demo"

    assert module._codex_needs_submit_retry(screen, "Reply with exactly PRESIDENT_ACK.") is True


def test_codex_needs_submit_retry_skips_when_activity_is_visible():
    module = _load_module()
    screen = "› Reply with exactly PRESIDENT_ACK.\n• PRESIDENT_ACK\n  gpt-5.4 high · /tmp/demo"

    assert module._codex_needs_submit_retry(screen, "Reply with exactly PRESIDENT_ACK.") is False


def test_gemini_screen_ready_requires_no_queue_warning():
    module = _load_module()

    assert module._gemini_screen_ready("Type your message or @path/to/file") is True
    assert (
        module._gemini_screen_ready(
            "Waiting for MCP servers to initialize... prompts will be queued.\nType your message"
        )
        is False
    )
    assert module._gemini_screen_ready("Queued (press ↑ to edit): hello\nType your message") is False


def test_gemini_screen_has_queued_prompt_detects_queued_composer():
    module = _load_module()

    assert module._gemini_screen_has_queued_prompt("Queued (press ↑ to edit): hi\nType your message") is True
    assert module._gemini_screen_has_queued_prompt("Type your message or @path/to/file") is False


def test_maybe_resume_gemini_queued_prompt_sends_up_then_enter_once():
    module = _load_module()
    session = _FakeSession(role="boss", repo="/media/sam/1TB/demo", marked=True)
    seen: set[str] = set()
    screen_text = "Queued (press ↑ to edit): hi\nReply exactly 2 lines...\nType your message"

    changed = asyncio.run(
        module._maybe_resume_gemini_queued_prompt(
            session,
            screen_text,
            role="boss",
            seen=seen,
        )
    )

    assert changed is True
    assert session.sent == [module._key_text("up"), "\r"]
    assert asyncio.run(
        module._maybe_resume_gemini_queued_prompt(
            session,
            screen_text,
            role="boss",
            seen=seen,
        )
    ) is False


def test_auto_approval_choice_switches_gemini_high_demand_to_flash():
    module = _load_module()

    prompt = (
        "We are currently experiencing high demand.\n"
        "/model to switch models.\n"
        "1. Keep trying\n"
        "2. Switch to gemini-2.5-flash\n"
        "3. Stop"
    )

    assert module._auto_approval_choice(prompt) == ("2", "switch gemini flash")


def test_supervisor_can_auto_remediate_safe_reasons_without_write_mode():
    module = _load_module()

    assert module._supervisor_can_auto_remediate_reason("switch gemini flash", auto_approve_prompts=False) is True
    assert module._supervisor_can_auto_remediate_reason("allow command for session", auto_approve_prompts=False) is True
    assert module._supervisor_can_auto_remediate_reason("trust folder", auto_approve_prompts=False) is True
    assert module._supervisor_can_auto_remediate_reason("apply change once", auto_approve_prompts=False) is False


def test_auto_approval_signature_distinguishes_edit_files():
    module = _load_module()

    index_prompt = "Action Required\n?  Edit index.html: <p> => <p>\nApply this change?\n1. Allow once"
    style_prompt = "Action Required\n?  Edit style.css: * { => * {\nApply this change?\n1. Allow once"
    snake_reset_prompt = "Action Required\n?  Edit snake.js: function reset() {... => function reset() {...\nApply this change?\n1. Allow once"
    snake_apple_prompt = "Action Required\n?  Edit snake.js: if (head.x === apple.x) => if (head.x === apple.x)\nApply this change?\n1. Allow once"

    assert module._auto_approval_signature(index_prompt, "1", "apply change once") != module._auto_approval_signature(
        style_prompt,
        "1",
        "apply change once",
    )
    assert module._auto_approval_signature(
        snake_reset_prompt,
        "1",
        "apply change once",
    ) != module._auto_approval_signature(snake_apple_prompt, "1", "apply change once")


def test_auto_approval_edit_path_allowlist():
    module = _load_module()
    prompt = "Action Required\n?  Edit style.css: * { => * {\nApply this change?\n1. Allow once"

    assert module._auto_approval_edit_path(prompt) == "style.css"
    assert module._edit_path_allowed("style.css", ("style.css",)) is True
    assert module._edit_path_allowed("style.css", ("index.html", "snake.js")) is False


def test_auto_approval_edit_path_supports_gemini_writefile_prompt():
    module = _load_module()
    prompt = (
        "Action Required\n"
        "?  WriteFile Writing to demo/ragdoll-physics/index.html\n"
        "Apply this change?\n"
        "1. Allow once"
    )

    assert module._auto_approval_edit_path(prompt) == "demo/ragdoll-physics/index.html"


def test_parse_allowed_edit_paths_from_president_output():
    module = _load_module()

    text = (
        "Include one line exactly like ALLOWED_EDIT_PATHS: path1, path2 for repo-relative files.\n"
        "Plan:\nALLOWED_EDIT_PATHS: index.html, ./snake.js\nSPECKIT_RUN_PRESIDENT_ASSIGNED_ABC"
    )

    assert module._parse_allowed_edit_paths(text) == ("index.html", "snake.js")


def test_parse_allowed_edit_paths_treats_any_sentence_as_unrestricted():
    module = _load_module()

    assert module._parse_allowed_edit_paths("ALLOWED_EDIT_PATHS: ANY. No edits/tools/questions.") == ()


def test_effective_edit_allowlist_intersects_operator_and_president_paths():
    module = _load_module()

    assert module._effective_edit_allowlist(("index.html", "snake.js"), ("snake.js", "style.css")) == ("snake.js",)
    assert module._effective_edit_allowlist((), ("index.html",)) == ("index.html",)
    assert module._effective_edit_allowlist(("index.html",), ()) == ("index.html",)


def test_maybe_auto_approve_rejects_edit_outside_allowlist():
    module = _load_module()
    session = _FakeSession(role="worker-gemini", repo="/media/sam/1TB/demo", marked=True)
    prompt = "Action Required\n?  Edit style.css: * { => * {\nApply this change?\n1. Allow once\n4. No, suggest changes"

    changed = asyncio.run(
        module._maybe_auto_approve_prompt(
            session,
            prompt,
            role="worker-gemini",
            enabled=True,
            seen=set(),
            allowed_edit_paths=("index.html", "snake.js"),
        )
    )

    assert changed is True
    assert session.sent == ["4", "\r"]


def test_maybe_auto_approve_rejects_edit_in_non_worker_role():
    module = _load_module()
    session = _FakeSession(role="boss", repo="/media/sam/1TB/demo", marked=True)
    prompt = "Action Required\n?  Edit index.html: <p> => <p>\nApply this change?\n1. Allow once\n4. No, suggest changes"

    changed = asyncio.run(
        module._maybe_auto_approve_prompt(
            session,
            prompt,
            role="boss",
            enabled=True,
            seen=set(),
            allowed_edit_paths=module.NO_AUTO_EDIT_PATHS,
        )
    )

    assert changed is True
    assert session.sent == ["4", "\r"]


def test_wait_for_screen_any_auto_approves_before_broad_marker():
    module = _load_module()
    session = _FakeSession(role="president", repo="/media/sam/1TB/demo", marked=True)
    session.screen = _FakeScreen(["Doyoutrustthecontentsofthisdirectory?", "› 1. Yes, continue"])

    async def _send_text(text: str):
        session.sent.append(text)
        if text == "\r":
            session.screen = _FakeScreen(["Codex ready", "›"])

    session.async_send_text = _send_text

    marker = asyncio.run(
        module._wait_for_screen_any(
            session,
            role="president",
            markers=("›",),
            timeout=3.0,
            poll_interval=0.1,
            description="Codex prompt",
            auto_approve_prompts=True,
        )
    )

    assert marker == "›"
    assert session.sent == ["1", "\r"]


def test_wait_for_gemini_ready_waits_until_queue_warning_clears():
    module = _load_module()
    session = _FakeSession(role="boss", repo="/media/sam/1TB/demo", marked=True)
    screens = [
        _FakeScreen(
            [
                "Waiting for MCP servers to initialize... prompts will be queued.",
                "Type your message or @path/to/file",
            ]
        ),
        _FakeScreen(["Type your message or @path/to/file"]),
    ]

    async def _get_screen_contents():
        if len(screens) > 1:
            session.screen = screens.pop(0)
        else:
            session.screen = screens[0]
        return session.screen

    session.async_get_screen_contents = _get_screen_contents

    asyncio.run(
        module._wait_for_gemini_ready(
            session,
            role="boss",
            timeout=2.0,
            poll_interval=0.1,
        )
    )


def test_wait_for_gemini_ready_reports_provider_not_ready_when_bootstrap_never_clears():
    module = _load_module()
    session = _FakeSession(role="boss", repo="/media/sam/1TB/demo", marked=True)
    session.screen = _FakeScreen(
        [
            "Waiting for MCP servers to initialize... prompts will be queued.",
            "Type your message or @path/to/file",
        ]
    )

    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(
            module._wait_for_gemini_ready(
                session,
                role="boss",
                timeout=0.3,
                poll_interval=0.1,
            )
        )

    message = str(excinfo.value)
    assert "supervisor_failure_class=provider_not_ready" in message
    assert "supervisor_action=stop_run" in message


def test_wait_for_screen_marker_supervisor_remediates_model_fallback_once():
    module = _load_module()
    session = _FakeSession(role="worker-gemini", repo="/media/sam/1TB/demo", marked=True)
    delivery = module._delivery_tokens("ABC123", "speckit.implement", "worker-gemini")
    session.screen = _FakeScreen(
        [
            "We are currently experiencing high demand.",
            "2. Switch to gemini-2.5-flash",
        ]
    )

    async def _send_text(text: str):
        session.sent.append(text)
        if text == "\r":
            session.screen = _FakeScreen(
                [
                    f"DELIVERY_ACK {delivery['start']} {delivery['end']}",
                    "GBG_WORKER_ABC123",
                ]
            )

    session.async_send_text = _send_text

    asyncio.run(
        module._wait_for_screen_marker(
            session,
            role="worker-gemini",
            marker="GBG_WORKER_ABC123",
            timeout=3.0,
            poll_interval=0.1,
            auto_approve_prompts=False,
            delivery_ack=delivery,
        )
    )

    assert session.sent == ["2", "\r"]


def test_run_speckit_team_run_reopens_role_on_provider_fallback(monkeypatch, tmp_path):
    module = _load_module()
    repo = tmp_path / "demo"
    repo.mkdir()
    app = object()
    launch_calls: list[list[str]] = []
    wait_calls: list[tuple[str, str]] = []
    reopen_calls: list[tuple[str, str, str]] = []
    retire_calls: list[tuple[str, str]] = []

    boss_session = _FakeSession(role="boss", repo=str(repo), marked=True, ui_group_id="group-1")
    president_session = _FakeSession(role="president", repo=str(repo), marked=True, ui_group_id="group-1")
    worker_session = _FakeSession(role="worker", repo=str(repo), marked=True, ui_group_id="group-1")
    panes = {
        "boss": module.MeshPane(0, 0, 0, str(repo), "boss", "group-1", object(), boss_session),
        "president": module.MeshPane(0, 0, 1, str(repo), "president", "group-1", object(), president_session),
        "worker": module.MeshPane(0, 0, 2, str(repo), "worker", "group-1", object(), worker_session),
    }
    boss_attempts = {"count": 0}

    monkeypatch.setattr(module, "_ensure_command", lambda _command: None)
    monkeypatch.setattr(module, "_git_status_short", lambda _repo: "")
    monkeypatch.setattr(module, "_resolve_claude_config_root", lambda _root: None)

    async def _fake_launch_role_layout(_connection, *, repo, roles, commands, ui_group_id):
        launch_calls.append(list(roles))

    async def _fake_find_mesh_panes_ready(_app, repo, roles, ui_group_id, *, timeout=12.0, poll_interval=1.0):
        return {str(role): panes[str(role)] for role in roles}

    async def _fake_wait_for_cli_ready(session, *, role, command_text, **kwargs):
        wait_calls.append((role, command_text))
        if role == "boss" and command_text == "gemini":
            boss_attempts["count"] += 1
            if boss_attempts["count"] == 1:
                raise RuntimeError(
                    "timed out waiting for Gemini ready state\n"
                    "supervisor_failure_class=provider_not_ready\n"
                    "supervisor_action=stop_run"
                )

    async def _fake_close_or_retire_mesh_role_panes(_app, repo, role, ui_group_id=""):
        retire_calls.append((role, ui_group_id))
        return 1

    async def _fake_launch_single_role_tab_in_group(_app, *, repo, role, command_text, ui_group_id):
        reopen_calls.append((role, repo, command_text))
        return True

    async def _fake_run_speckit_team_cycle(_app, cycle_args):
        assert cycle_args.role_commands["boss"] == "codex"
        return 0

    monkeypatch.setattr(module, "_launch_role_layout", _fake_launch_role_layout)
    monkeypatch.setattr(module, "_find_mesh_panes_ready", _fake_find_mesh_panes_ready)
    monkeypatch.setattr(module, "_wait_for_cli_ready", _fake_wait_for_cli_ready)
    monkeypatch.setattr(module, "_provider_fallback_command", lambda **kwargs: "codex" if kwargs["role"] == "boss" else "")
    monkeypatch.setattr(module, "_close_or_retire_mesh_role_panes", _fake_close_or_retire_mesh_role_panes)
    monkeypatch.setattr(module, "_launch_single_role_tab_in_group", _fake_launch_single_role_tab_in_group)
    monkeypatch.setattr(module, "_run_speckit_team_cycle", _fake_run_speckit_team_cycle)
    monkeypatch.setattr(module, "_close_mesh_tabs", lambda *args, **kwargs: 0)

    args = argparse.Namespace(
        boss_cmd="gemini",
        president_cmd="codex",
        worker_cmd="codex",
        reviewer_cmd="codex",
        with_reviewer=False,
        auto_approve_prompts=False,
        allow_write=False,
        product_quality=False,
        repo=str(repo),
        allow_dirty=False,
        claude_config="",
        ui_group_id="group-1",
        boss_role="boss",
        president_role="president",
        worker_role="worker",
        reviewer_role="reviewer",
        run_id="RUN1",
        handoff_dir=".mesh/runs",
        no_handoff=True,
        startup_wait=0.0,
        poll_interval=0.01,
        startup_timeout=1.0,
        feature="demo",
        task="test fallback",
        test_command="",
        test_timeout=1.0,
        allow_test_failure=False,
        quality="quick",
        min_product_score=7,
        max_quality_retries=1,
        max_turns=3,
        response_timeout=1.0,
        auto_approve_edit_path=[],
        keep_open=True,
        boss_contract="",
        president_contract="",
        worker_contract="",
        reviewer_contract="",
    )

    result = asyncio.run(module._run_speckit_team_run(None, app, args))

    assert result == 0
    assert launch_calls == [["boss", "president", "worker"]]
    assert reopen_calls == [("boss", str(repo), "codex")]
    assert retire_calls == [("boss", "group-1")]
    assert wait_calls == [
        ("boss", "gemini"),
        ("boss", "codex"),
        ("president", "codex"),
        ("worker", "codex"),
    ]


def test_run_speckit_team_run_relaunches_missing_roles_once(monkeypatch, tmp_path):
    module = _load_module()
    repo = tmp_path / "demo"
    repo.mkdir()
    app = object()
    launch_calls: list[list[str]] = []
    repair_calls: list[str] = []
    wait_calls: list[tuple[str, str]] = []
    ready_attempts = {"count": 0}

    boss_session = _FakeSession(role="boss", repo=str(repo), marked=True, ui_group_id="group-1")
    president_session = _FakeSession(role="president", repo=str(repo), marked=True, ui_group_id="group-1")
    worker_session = _FakeSession(role="worker", repo=str(repo), marked=True, ui_group_id="group-1")
    panes = {
        "boss": module.MeshPane(0, 0, 0, str(repo), "boss", "group-1", object(), boss_session),
        "president": module.MeshPane(0, 0, 1, str(repo), "president", "group-1", object(), president_session),
        "worker": module.MeshPane(0, 0, 2, str(repo), "worker", "group-1", object(), worker_session),
    }

    monkeypatch.setattr(module, "_ensure_command", lambda _command: None)
    monkeypatch.setattr(module, "_git_status_short", lambda _repo: "")
    monkeypatch.setattr(module, "_resolve_claude_config_root", lambda _root: None)

    async def _fake_launch_role_layout(_connection, *, repo, roles, commands, ui_group_id):
        launch_calls.append(list(roles))

    async def _fake_find_mesh_panes_ready(_app, repo, roles, ui_group_id, *, timeout=12.0, poll_interval=1.0):
        ready_attempts["count"] += 1
        if ready_attempts["count"] == 1:
            raise RuntimeError(
                "no pane matched repo="
                f"{repo!r} role='president' ui_group_id={ui_group_id!r}"
            )
        return {str(role): panes[str(role)] for role in roles}

    async def _fake_mesh_sessions(_app, repo, ui_group_id=""):
        return [panes["boss"], panes["worker"]]

    async def _fake_launch_single_role_tab_in_group(_app, *, repo, role, command_text, ui_group_id):
        repair_calls.append(role)
        return True

    async def _fake_wait_for_cli_ready(session, *, role, command_text, **kwargs):
        wait_calls.append((role, command_text))

    async def _fake_run_speckit_team_cycle(_app, cycle_args):
        return 0

    monkeypatch.setattr(module, "_launch_role_layout", _fake_launch_role_layout)
    monkeypatch.setattr(module, "_find_mesh_panes_ready", _fake_find_mesh_panes_ready)
    monkeypatch.setattr(module, "_mesh_sessions", _fake_mesh_sessions)
    monkeypatch.setattr(module, "_launch_single_role_tab_in_group", _fake_launch_single_role_tab_in_group)
    monkeypatch.setattr(module, "_wait_for_cli_ready", _fake_wait_for_cli_ready)
    monkeypatch.setattr(module, "_provider_fallback_command", lambda **kwargs: "")
    monkeypatch.setattr(module, "_run_speckit_team_cycle", _fake_run_speckit_team_cycle)
    monkeypatch.setattr(module, "_close_mesh_tabs", lambda *args, **kwargs: 0)

    args = argparse.Namespace(
        boss_cmd="codex",
        president_cmd="codex",
        worker_cmd="gemini",
        reviewer_cmd="gemini",
        with_reviewer=False,
        auto_approve_prompts=False,
        allow_write=False,
        product_quality=False,
        repo=str(repo),
        allow_dirty=False,
        claude_config="",
        ui_group_id="group-1",
        boss_role="boss",
        president_role="president",
        worker_role="worker",
        reviewer_role="reviewer",
        run_id="RUN2",
        handoff_dir=".mesh/runs",
        no_handoff=True,
        startup_wait=0.0,
        poll_interval=0.01,
        startup_timeout=1.0,
        feature="demo",
        task="repair launch",
        test_command="",
        test_timeout=1.0,
        allow_test_failure=False,
        quality="quick",
        min_product_score=7,
        max_quality_retries=1,
        max_turns=3,
        response_timeout=1.0,
        auto_approve_edit_path=[],
        keep_open=True,
        boss_contract="",
        president_contract="",
        worker_contract="",
        reviewer_contract="",
    )

    result = asyncio.run(module._run_speckit_team_run(None, app, args))

    assert result == 0
    assert launch_calls == [["boss", "president", "worker"]]
    assert repair_calls == ["president"]
    assert wait_calls == [
        ("boss", "codex"),
        ("president", "codex"),
        ("worker", "gemini"),
    ]


def test_wait_for_screen_marker_resumes_queued_gemini_prompt_once():
    module = _load_module()
    session = _FakeSession(role="boss", repo="/media/sam/1TB/demo", marked=True)
    delivery = module._delivery_tokens("ABC123", "speckit.discuss", "boss")
    session.screen = _FakeScreen(
        [
            "Queued (press ↑ to edit): Reply exactly 2 lines...",
            "Type your message or @path/to/file",
        ]
    )

    async def _send_text(text: str):
        session.sent.append(text)
        if text == "\r":
            session.screen = _FakeScreen(
                [
                    f"DELIVERY_ACK {delivery['start']} {delivery['end']}",
                    "GBG_BOSS_ABC123",
                ]
            )

    session.async_send_text = _send_text

    asyncio.run(
        module._wait_for_screen_marker(
            session,
            role="boss",
            marker="GBG_BOSS_ABC123",
            timeout=3.0,
            poll_interval=0.1,
            auto_approve_prompts=False,
            delivery_ack=delivery,
        )
    )

    assert session.sent == [module._key_text("up"), "\r"]


def test_supervisor_can_resume_same_queued_prompt_twice_with_attempt_budget():
    module = _load_module()
    session = _FakeSession(role="boss", repo="/media/sam/1TB/demo", marked=True)
    attempts: dict[str, int] = {}
    seen: set[str] = set()
    assessment = module.SupervisorAssessment(
        failure_class="queued_prompt_issue",
        remediation="resume the queued Gemini composer and continue waiting",
    )
    screen_text = "Queued (press ↑ to edit): hi\nType your message"

    first = asyncio.run(
        module._maybe_supervisor_remediate(
            session,
            screen_text,
            role="boss",
            assessment=assessment,
            auto_approve_prompts=False,
            seen=seen,
            attempts=attempts,
        )
    )
    second = asyncio.run(
        module._maybe_supervisor_remediate(
            session,
            screen_text,
            role="boss",
            assessment=assessment,
            auto_approve_prompts=False,
            seen=seen,
            attempts=attempts,
        )
    )
    third = asyncio.run(
        module._maybe_supervisor_remediate(
            session,
            screen_text,
            role="boss",
            assessment=assessment,
            auto_approve_prompts=False,
            seen=seen,
            attempts=attempts,
        )
    )

    assert first is True
    assert second is True
    assert third is False
    assert attempts["queued_prompt_issue"] == 2


def test_mesh_sessions_filters_marked_repo_and_role():
    module = _load_module()
    target = _FakeSession(role="boss", repo="/media/sam/1TB/demo", marked=True)
    other_role = _FakeSession(role="president", repo="/media/sam/1TB/demo", marked=True)
    other_repo = _FakeSession(role="boss", repo="/media/sam/1TB/other", marked=True)
    plain = _FakeSession()
    app = type(
        "App",
        (),
        {"windows": [type("Window", (), {"tabs": [_FakeTab([target, other_role, other_repo, plain])]})()]},
    )()

    panes = asyncio.run(module._mesh_sessions(app, "/media/sam/1TB/demo"))

    assert [pane.role for pane in panes] == ["boss", "president"]


def test_mesh_sessions_uses_current_session_when_tab_sessions_missing():
    module = _load_module()
    target = _FakeSession(role="boss", repo="/media/sam/1TB/demo", marked=True)
    tab = type("Tab", (), {"sessions": [], "current_session": target})()
    app = type(
        "App",
        (),
        {"windows": [type("Window", (), {"tabs": [tab]})()]},
    )()

    panes = asyncio.run(module._mesh_sessions(app, "/media/sam/1TB/demo"))

    assert [pane.role for pane in panes] == ["boss"]


def test_mesh_sessions_filters_ui_group_id():
    module = _load_module()
    target = _FakeSession(role="boss", repo="/media/sam/1TB/demo", marked=True, ui_group_id="group-1")
    other_group = _FakeSession(role="boss", repo="/media/sam/1TB/demo", marked=True, ui_group_id="group-2")
    app = type(
        "App",
        (),
        {"windows": [type("Window", (), {"tabs": [_FakeTab([target, other_group])]})()]},
    )()

    panes = asyncio.run(module._mesh_sessions(app, "/media/sam/1TB/demo", "group-1"))

    assert [pane.ui_group_id for pane in panes] == ["group-1"]


def test_find_mesh_pane_returns_unique_match():
    module = _load_module()
    boss = _FakeSession(role="boss", repo="/media/sam/1TB/demo", marked=True)
    president = _FakeSession(role="president", repo="/media/sam/1TB/demo", marked=True)
    app = type(
        "App",
        (),
        {"windows": [type("Window", (), {"tabs": [_FakeTab([boss, president])]})()]},
    )()

    pane = asyncio.run(module._find_mesh_pane(app, "/media/sam/1TB/demo", "president"))

    assert pane.role == "president"
    assert pane.session is president


def test_find_mesh_panes_ready_retries_until_all_roles_appear(monkeypatch):
    module = _load_module()
    boss = type("Pane", (), {"role": "boss"})()
    president = type("Pane", (), {"role": "president"})()
    app = type("App", (), {"windows": []})()
    calls = {"count": 0}

    async def _fake_find_mesh_pane(app_obj, repo, role, ui_group_id=""):
        calls["count"] += 1
        if role == "president" and calls["count"] < 3:
            raise RuntimeError("no pane matched repo='/media/sam/1TB/demo' role='president' ui_group_id='group-1'")
        return boss if role == "boss" else president

    monkeypatch.setattr(module, "_find_mesh_pane", _fake_find_mesh_pane)

    panes = asyncio.run(
        module._find_mesh_panes_ready(
            app,
            "/media/sam/1TB/demo",
            ("boss", "president"),
            "group-1",
            timeout=1.0,
            poll_interval=0.01,
        )
    )

    assert panes["boss"].role == "boss"
    assert panes["president"].role == "president"


def test_screen_tail_keeps_recent_non_empty_lines():
    module = _load_module()
    session = _FakeSession(role="boss", repo="/media/sam/1TB/demo", marked=True)
    session.screen = _FakeScreen(["", "one", "two\x00", "   ", "three"])

    text = asyncio.run(module._screen_tail(session, lines=2))

    assert text == "two\nthree"


def test_run_send_text_activates_pane_before_sending():
    module = _load_module()
    boss = _FakeSession(role="boss", repo="/media/sam/1TB/demo", marked=True)
    app = type(
        "App",
        (),
        {"windows": [type("Window", (), {"tabs": [_FakeTab([boss])]})()]},
    )()
    args = type("Args", (), {"cmd": "send-text", "repo": "/media/sam/1TB/demo", "role": "boss", "text": "ciao"})()

    async def _wrapped():
        import types

        async def _async_get_app(_conn):
            return app

        fake_iterm2 = types.SimpleNamespace(async_get_app=_async_get_app)
        previous = sys.modules.get("iterm2")
        try:
            sys.modules["iterm2"] = fake_iterm2
            return await module._run(None, args)
        finally:
            if previous is None:
                sys.modules.pop("iterm2", None)
            else:
                sys.modules["iterm2"] = previous

    assert asyncio.run(_wrapped()) == 0
    assert boss.activated is True
    assert boss.sent == ["ciao"]


def test_run_send_key_activates_pane_before_sending():
    module = _load_module()
    president = _FakeSession(role="president", repo="/media/sam/1TB/demo", marked=True)
    app = type(
        "App",
        (),
        {"windows": [type("Window", (), {"tabs": [_FakeTab([president])]})()]},
    )()
    args = type("Args", (), {"cmd": "send-key", "repo": "/media/sam/1TB/demo", "role": "president", "key": "enter"})()

    async def _wrapped():
        import types

        async def _async_get_app(_conn):
            return app

        fake_iterm2 = types.SimpleNamespace(async_get_app=_async_get_app)
        previous = sys.modules.get("iterm2")
        try:
            sys.modules["iterm2"] = fake_iterm2
            return await module._run(None, args)
        finally:
            if previous is None:
                sys.modules.pop("iterm2", None)
            else:
                sys.modules["iterm2"] = previous

    assert asyncio.run(_wrapped()) == 0
    assert president.activated is True
    assert president.sent == ["\r"]


def test_run_send_line_appends_newline_and_activates():
    module = _load_module()
    boss = _FakeSession(role="boss", repo="/media/sam/1TB/demo", marked=True)
    app = type(
        "App",
        (),
        {"windows": [type("Window", (), {"tabs": [_FakeTab([boss])]})()]},
    )()
    args = type("Args", (), {"cmd": "send-line", "repo": "/media/sam/1TB/demo", "role": "boss", "text": "/GBG status"})()

    async def _wrapped():
        import types

        async def _async_get_app(_conn):
            return app

        fake_iterm2 = types.SimpleNamespace(async_get_app=_async_get_app)
        previous = sys.modules.get("iterm2")
        try:
            sys.modules["iterm2"] = fake_iterm2
            return await module._run(None, args)
        finally:
            if previous is None:
                sys.modules.pop("iterm2", None)
            else:
                sys.modules["iterm2"] = previous

    assert asyncio.run(_wrapped()) == 0
    assert boss.activated is True
    assert boss.sent == ["/GBG status", "\r"]


def test_send_line_chunks_long_text_before_enter():
    module = _load_module()
    session = _FakeSession(role="boss", repo="/media/sam/1TB/demo", marked=True)
    text = "x" * (module.SEND_TEXT_CHUNK_CHARS * 2 + 7)

    asyncio.run(module._send_line(session, text))

    assert session.activated is True
    assert session.sent[-1] == "\r"
    assert "".join(session.sent[:-1]) == text
    assert [len(chunk) for chunk in session.sent[:-1]] == [
        module.SEND_TEXT_CHUNK_CHARS,
        module.SEND_TEXT_CHUNK_CHARS,
        7,
    ]


def test_send_line_retries_enter_once_for_pending_codex_prompt():
    module = _load_module()
    session = _FakeSession(role="president", repo="/media/sam/1TB/demo", marked=True)
    session.variables["session.badge"] = "mesh:president (spawn:codex) | demo"
    session.screen = _FakeScreen(["› Reply with exactly PRESIDENT_ACK.", "  gpt-5.4 high · /tmp/demo"])

    asyncio.run(module._send_line(session, "Reply with exactly PRESIDENT_ACK."))

    assert session.sent == ["Reply with exactly PRESIDENT_ACK.", "\r", "\r"]
