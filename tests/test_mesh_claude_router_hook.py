from __future__ import annotations

import json

from scripts import mesh_claude_router_hook as hook


def test_extract_summary_from_transcript_reads_last_assistant_entry(tmp_path) -> None:
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(
        "\n".join(
            [
                json.dumps({"type": "user", "message": "hello"}),
                json.dumps({"type": "assistant", "message": {"content": [{"text": "First"}]}}),
                json.dumps({"role": "assistant", "content": [{"text": "Final answer"}]}),
            ]
        ),
        encoding="utf-8",
    )

    assert hook._extract_summary_from_transcript(str(transcript)) == "Final answer"


def test_clean_summary_strips_terminal_noise() -> None:
    text = "❯ hi\n✻ Thinking...\n● Ready\n⎿ trace\nStop says: noop\n[mesh:boss] repo=x\n"
    assert hook._clean_summary(text) == "● Ready"


def test_handle_stop_emits_relay_and_idle(monkeypatch: object) -> None:
    calls: list[tuple[str, str, dict]] = []
    monkeypatch.setenv("MESH_RELAY_MODE", "claude_hooks")
    monkeypatch.setenv("MESH_UI_ROLE", "boss")
    monkeypatch.setenv("MESH_UI_GROUP_ID", "snake-ui-1")
    monkeypatch.setenv("MESH_ROUTER_SESSION_ID", "11111111-1111-4111-8111-111111111111")
    monkeypatch.setenv("MESH_RELAY_TARGET_ROLE", "president")

    monkeypatch.setattr(hook, "_extract_summary_from_transcript", lambda path: "boss summary")
    monkeypatch.setattr(hook, "_capture_tmux_summary", lambda: "")
    monkeypatch.setattr(hook, "_router_post", lambda path, body: calls.append((path, body.get("role", ""), body)))

    hook._handle_stop({"transcript_path": "/tmp/fake.jsonl"})

    relay_calls = [body for path, _, body in calls if path == "/sessions/send" and body["metadata"]["envelope"]["msg_type"] == "relay"]
    state_calls = [body for path, _, body in calls if path == "/sessions/send" and body["metadata"]["envelope"]["msg_type"] == "state_change"]
    turn_release = [body for path, _, body in calls if path == "/sessions/turn/release"]
    assert relay_calls and relay_calls[0]["content"] == "boss summary"
    assert state_calls and state_calls[-1]["content"] == "idle"
    assert turn_release and turn_release[0]["role"] == "boss"
