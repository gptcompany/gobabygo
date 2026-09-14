from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "mesh_probity_hook.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("mesh_probity_hook", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git_repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


def _payload(cwd: Path) -> bytes:
    return json.dumps(
        {
            "session_id": "test-session",
            "transcript_path": None,
            "cwd": str(cwd),
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_use_id": "call-1",
            "tool_input": {"command": "pytest -q"},
        }
    ).encode()


def _dispatch(module, raw: bytes, agent: str = "codex") -> str:
    return module.dispatch(raw, agent=agent)


def test_missing_config_is_a_noop(monkeypatch, tmp_path) -> None:
    module = _load_module()
    repo = _git_repo(tmp_path / "repo")
    monkeypatch.setattr(module, "_probity_executable", lambda: (_ for _ in ()).throw(AssertionError()))

    assert json.loads(_dispatch(module, _payload(repo))) == {}


def test_opted_in_repo_forwards_exact_payload(monkeypatch, tmp_path) -> None:
    module = _load_module()
    repo = _git_repo(tmp_path / "repo")
    nested = repo / "src"
    nested.mkdir()
    config = repo / "probity.config.ts"
    config.write_text("export default {}\n", encoding="utf-8")
    capture = tmp_path / "capture"
    fake = tmp_path / "probity"
    fake.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$PWD\" \"$@\" > \"$CAPTURE\"\ncat >/dev/null\n"
        "printf '{\"decision\":\"block\",\"reason\":\"synthetic\"}\\n'\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    monkeypatch.setenv("CAPTURE", str(capture))
    monkeypatch.setattr(module, "_probity_executable", lambda: str(fake))
    raw = _payload(nested)

    response = json.loads(_dispatch(module, raw))

    assert response == {"decision": "block", "reason": "synthetic"}
    assert capture.read_text(encoding="utf-8").splitlines() == [
        str(repo),
        "--agent",
        "codex",
        "--config",
        str(config),
    ]


def test_ambiguous_config_denies_without_running_probity(monkeypatch, tmp_path) -> None:
    module = _load_module()
    repo = _git_repo(tmp_path / "repo")
    (repo / "probity.config.ts").write_text("export default {}\n", encoding="utf-8")
    (repo / "probity.config.js").write_text("export default {}\n", encoding="utf-8")
    monkeypatch.setattr(module, "_probity_executable", lambda: (_ for _ in ()).throw(AssertionError()))

    response = json.loads(_dispatch(module, _payload(repo)))

    assert response["decision"] == "block"
    assert "multiple" in response["reason"]


def test_symlink_config_denies_without_running_probity(monkeypatch, tmp_path) -> None:
    module = _load_module()
    repo = _git_repo(tmp_path / "repo")
    external = tmp_path / "external.mjs"
    external.write_text("export default {}\n", encoding="utf-8")
    (repo / "probity.config.mjs").symlink_to(external)
    monkeypatch.setattr(module, "_probity_executable", lambda: (_ for _ in ()).throw(AssertionError()))

    response = json.loads(_dispatch(module, _payload(repo), "claude-code"))

    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "regular file" in response["hookSpecificOutput"]["permissionDecisionReason"]


def test_missing_runtime_denies_only_an_opted_in_repo(monkeypatch, tmp_path) -> None:
    module = _load_module()
    repo = _git_repo(tmp_path / "repo")
    (repo / "probity.config.ts").write_text("export default {}\n", encoding="utf-8")
    monkeypatch.setattr(module, "_probity_executable", lambda: None)

    response = json.loads(_dispatch(module, _payload(repo)))

    assert response["decision"] == "block"
    assert "unavailable" in response["reason"]


def test_pinned_version_mismatch_denies_before_execution(monkeypatch, tmp_path) -> None:
    module = _load_module()
    repo = _git_repo(tmp_path / "repo")
    (repo / "probity.config.ts").write_text("export default {}\n", encoding="utf-8")
    fake = tmp_path / "probity"
    fake.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("MESH_PROBITY_EXPECTED_VERSION", "1.10.0")
    monkeypatch.setattr(module, "_probity_executable", lambda: str(fake))

    response = json.loads(_dispatch(module, _payload(repo)))

    assert response["decision"] == "block"
    assert "pinned version" in response["reason"]


def test_invalid_probity_output_fails_closed(monkeypatch, tmp_path) -> None:
    module = _load_module()
    repo = _git_repo(tmp_path / "repo")
    (repo / "probity.config.ts").write_text("export default {}\n", encoding="utf-8")
    fake = tmp_path / "probity"
    fake.write_text("#!/bin/sh\ncat >/dev/null\nprintf not-json\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setattr(module, "_probity_executable", lambda: str(fake))

    response = json.loads(_dispatch(module, _payload(repo)))

    assert response["decision"] == "block"
    assert "invalid JSON" in response["reason"]


def test_empty_probity_stdout_is_codex_allow(monkeypatch, tmp_path) -> None:
    module = _load_module()
    repo = _git_repo(tmp_path / "repo")
    (repo / "probity.config.ts").write_text("export default {}\n", encoding="utf-8")
    fake = tmp_path / "probity"
    fake.write_text("#!/bin/sh\ncat >/dev/null\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setattr(module, "_probity_executable", lambda: str(fake))

    assert json.loads(_dispatch(module, _payload(repo))) == {}


def test_invalid_or_non_pretool_payload_is_a_noop() -> None:
    module = _load_module()

    assert json.loads(_dispatch(module, b"not-json")) == {}
    assert json.loads(_dispatch(module, b'{"hook_event_name":"Stop"}')) == {}


def test_claude_forwards_vendor_and_preserves_vendor_response(monkeypatch, tmp_path) -> None:
    module = _load_module()
    repo = _git_repo(tmp_path / "repo")
    config = repo / "probity.config.mjs"
    config.write_text("export default {}\n", encoding="utf-8")
    capture = tmp_path / "capture"
    fake = tmp_path / "probity"
    fake.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$CAPTURE\"\ncat >/dev/null\n"
        "printf '%s\\n' '{\"hookSpecificOutput\":{\"hookEventName\":\"PreToolUse\","
        "\"permissionDecision\":\"deny\",\"permissionDecisionReason\":\"synthetic\"}}'\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    monkeypatch.setenv("CAPTURE", str(capture))
    monkeypatch.setattr(module, "_probity_executable", lambda: str(fake))

    response = json.loads(_dispatch(module, _payload(repo), "claude-code"))

    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert capture.read_text(encoding="utf-8").splitlines() == [
        "--agent",
        "claude-code",
        "--config",
        str(config),
    ]


def test_wrong_vendor_response_fails_closed(monkeypatch, tmp_path) -> None:
    module = _load_module()
    repo = _git_repo(tmp_path / "repo")
    (repo / "probity.config.ts").write_text("export default {}\n", encoding="utf-8")
    fake = tmp_path / "probity"
    fake.write_text("#!/bin/sh\ncat >/dev/null\nprintf '{\"decision\":\"allow\"}\\n'\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setattr(module, "_probity_executable", lambda: str(fake))

    response = json.loads(_dispatch(module, _payload(repo)))

    assert response["decision"] == "block"
    assert "invalid vendor response" in response["reason"]


def test_cli_never_exposes_probity_stderr(monkeypatch, tmp_path) -> None:
    repo = _git_repo(tmp_path / "repo")
    (repo / "probity.config.ts").write_text("export default {}\n", encoding="utf-8")
    fake = tmp_path / "probity"
    fake.write_text("#!/bin/sh\ncat >/dev/null\necho SECRET >&2\nexit 7\n", encoding="utf-8")
    fake.chmod(0o755)

    proc = subprocess.run(
        ["python3", str(MODULE_PATH), "--agent", "codex"],
        input=_payload(repo),
        env={**os.environ, "MESH_PROBITY_BIN": str(fake)},
        check=False,
        capture_output=True,
    )

    assert proc.returncode == 0
    assert b"SECRET" not in proc.stdout + proc.stderr
    assert json.loads(proc.stdout)["decision"] == "block"


def test_cli_rejects_missing_or_unknown_agent() -> None:
    for args in ([], ["--agent", "agy"]):
        proc = subprocess.run(
            ["python3", str(MODULE_PATH), *args],
            input=b"{}",
            check=False,
            capture_output=True,
        )
        assert proc.returncode == 2
        assert b"codex|claude-code" in proc.stderr


def _transcript_dispatch(monkeypatch, tmp_path, transcript, run):
    module = _load_module()
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    (repo / "probity.config.ts").write_text("export default {}\n")
    monkeypatch.setattr(module, "_git_root", lambda cwd: repo)
    monkeypatch.setattr(module, "_probity_executable", lambda: "/fake/probity")
    monkeypatch.setattr(module, "_runtime_matches_expected", lambda *args: True)
    monkeypatch.setattr(module.subprocess, "run", run)
    monkeypatch.setattr(module, "MAX_TRANSCRIPT_BYTES", 256, raising=False)
    monkeypatch.setattr(module, "MAX_TRANSCRIPT_TAIL_BYTES", 100, raising=False)
    payload = json.loads(_payload(repo))
    payload["transcript_path"] = str(transcript) if transcript is not None else None
    raw = (" \n" + json.dumps(payload, indent=2) + "\n").encode()
    return module, raw


def test_normal_transcript_forwarding_is_byte_exact(monkeypatch, tmp_path):
    transcript = tmp_path / "session.jsonl"
    transcript.write_bytes(b'{"message":"hello"}\n')
    seen = []

    def run(*args, **kwargs):
        seen.append(kwargs["input"])
        return subprocess.CompletedProcess(args, 0, b"", b"")

    for path in (None, transcript):
        module, raw = _transcript_dispatch(monkeypatch, tmp_path, path, run)
        assert json.loads(_dispatch(module, raw)) == {}
        assert seen[-1] == raw


def test_oversized_transcript_tail_and_cleanup(monkeypatch, tmp_path):
    transcript = tmp_path / "session.jsonl"
    original = b''.join(json.dumps({"i": i, "text": "caf\u00e9"}, ensure_ascii=False).encode() + b'\n' for i in range(40))
    transcript.write_bytes(original + b'{"incomplete":')
    paths = []

    def run(*args, **kwargs):
        forwarded = json.loads(kwargs["input"])
        path = Path(forwarded.pop("transcript_path"))
        expected = json.loads(raw)
        expected.pop("transcript_path")
        assert forwarded == expected
        assert path != transcript
        assert path.stat().st_mode & 0o777 == 0o600
        assert path.parent.stat().st_mode & 0o777 == 0o700
        tail = path.read_bytes()
        assert 0 < len(tail) <= 100
        assert original.endswith(tail)
        assert [json.loads(line)["i"] for line in tail.splitlines()][-1] == 39
        paths.append(path)
        return subprocess.CompletedProcess(args, 0, b'{"decision":"block","reason":"TDD"}', b"")

    module, raw = _transcript_dispatch(monkeypatch, tmp_path, transcript, run)
    assert json.loads(_dispatch(module, raw)) == {"decision": "block", "reason": "TDD"}
    assert paths and not paths[0].exists() and not paths[0].parent.exists()
    assert transcript.read_bytes() == original + b'{"incomplete":'


def test_unsafe_transcripts_fail_closed(monkeypatch, tmp_path):
    target = tmp_path / "large.jsonl"
    target.write_bytes(b'{}\n' * 200)
    link = tmp_path / "link.jsonl"
    link.symlink_to(target)
    directory = tmp_path / "directory"
    directory.mkdir()
    parent_link = tmp_path / "parent-link"
    parent_link.symlink_to(directory, target_is_directory=True)
    (directory / "large.jsonl").write_bytes(target.read_bytes())
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)

    def run(*args, **kwargs):
        raise AssertionError("unsafe transcript reached Probity")

    for path in (link, directory, parent_link / "large.jsonl", fifo, tmp_path / "missing"):
        module, raw = _transcript_dispatch(monkeypatch, tmp_path, path, run)
        assert json.loads(_dispatch(module, raw))["decision"] == "block"


@pytest.mark.parametrize("failure", ["create", "write", "timeout", "execute"])
def test_transcript_failures_block_and_clean_up(monkeypatch, tmp_path, failure):
    transcript = tmp_path / "large.jsonl"
    transcript.write_bytes(b'{}\n' * 200)
    paths = []

    def run(*args, **kwargs):
        paths.append(Path(json.loads(kwargs["input"])["transcript_path"]).parent)
        if failure == "timeout":
            raise subprocess.TimeoutExpired(args[0], 120)
        if failure == "execute":
            raise OSError("execution failed")
        raise AssertionError("failed preparation reached Probity")

    module, raw = _transcript_dispatch(monkeypatch, tmp_path, transcript, run)
    original = module.tempfile.NamedTemporaryFile

    def temporary_file(**kwargs):
        paths.append(Path(kwargs["dir"]))
        if failure == "create":
            raise OSError("creation failed")
        target = original(**kwargs)

        def failed_write(data):
            target.file.write(data[:1])
            raise OSError("disk full")

        target.write = failed_write
        return target

    if failure in ("create", "write"):
        monkeypatch.setattr(module.tempfile, "NamedTemporaryFile", temporary_file)
    assert json.loads(_dispatch(module, raw))["decision"] == "block"
    assert paths and all(not path.exists() for path in paths)


@pytest.mark.parametrize("ending", [b'x' * 200, b'not-json\n', b'\xff\n'])
def test_unusable_transcript_tail_fails_closed(monkeypatch, tmp_path, ending):
    transcript = tmp_path / "large.jsonl"
    transcript.write_bytes(b'{}\n' * 200 + ending)

    def run(*args, **kwargs):
        raise AssertionError("invalid tail reached Probity")

    module, raw = _transcript_dispatch(monkeypatch, tmp_path, transcript, run)
    assert json.loads(_dispatch(module, raw))["decision"] == "block"
