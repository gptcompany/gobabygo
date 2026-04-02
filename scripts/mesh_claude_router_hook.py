#!/usr/bin/env python3
"""Claude Code hook adapter for mesh boss/president relay."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Any


def _env(name: str) -> str:
    return str(os.environ.get(name) or "").strip()


def _read_hook_input() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _router_post(path: str, body: dict[str, Any]) -> None:
    router_url = _env("MESH_ROUTER_URL")
    if not router_url:
        return
    headers = {"Content-Type": "application/json"}
    auth_token = _env("MESH_AUTH_TOKEN")
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    req = urllib.request.Request(
        f"{router_url.rstrip('/')}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5):
            return
    except (urllib.error.URLError, TimeoutError, ValueError):
        return


def _log(message: str) -> None:
    print(f"[mesh-hook] {message}", file=sys.stderr)


def _extract_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = [_extract_text(item) for item in value]
        return "\n".join(part for part in parts if part).strip()
    if isinstance(value, dict):
        for key in ("text", "message", "content", "output", "result", "response"):
            text = _extract_text(value.get(key))
            if text:
                return text
        parts = [_extract_text(item) for item in value.values()]
        return "\n".join(part for part in parts if part).strip()
    return ""


def _looks_assistant_entry(entry: dict[str, Any]) -> bool:
    values = [
        str(entry.get("type") or "").strip().lower(),
        str(entry.get("role") or "").strip().lower(),
        str(entry.get("speaker") or "").strip().lower(),
        str(entry.get("sender") or "").strip().lower(),
    ]
    return any(value == "assistant" for value in values)


def _looks_user_entry(entry: dict[str, Any]) -> bool:
    values = [
        str(entry.get("type") or "").strip().lower(),
        str(entry.get("role") or "").strip().lower(),
        str(entry.get("speaker") or "").strip().lower(),
        str(entry.get("sender") or "").strip().lower(),
    ]
    return any(value == "user" for value in values)


def _clean_summary(text: str, *, max_chars: int = 1600) -> str:
    raw_lines = [raw.replace("\xa0", " ") for raw in str(text or "").splitlines()]

    assistant_blocks: list[list[str]] = []
    current_block: list[str] = []
    in_assistant_block = False
    for raw in raw_lines:
        stripped = raw.strip()
        if not stripped:
            if in_assistant_block and current_block:
                assistant_blocks.append(current_block)
            current_block = []
            in_assistant_block = False
            continue
        if stripped.startswith("●"):
            if in_assistant_block and current_block:
                assistant_blocks.append(current_block)
            current_block = [stripped[1:].strip()]
            in_assistant_block = True
            continue
        if in_assistant_block:
            if stripped.startswith(("⎿", "Stop says:", "❯", "✻", "[mesh:", "/model")):
                assistant_blocks.append(current_block)
                current_block = []
                in_assistant_block = False
                continue
            if raw.startswith("  ") or raw.startswith("\t"):
                current_block.append(stripped)
                continue
            assistant_blocks.append(current_block)
            current_block = []
            in_assistant_block = False
        if stripped.startswith(("❯", "✻", "⎿", "Stop says:", "[mesh:", "/model")):
            continue
    if in_assistant_block and current_block:
        assistant_blocks.append(current_block)

    if assistant_blocks:
        clean = "\n".join(line for line in assistant_blocks[-1] if line).strip()
        return clean[-max(1, int(max_chars)) :]

    lines: list[str] = []
    for raw in raw_lines:
        line = raw.strip()
        if not line:
            continue
        if line.startswith(("❯", "✻", "⎿", "Stop says:", "[mesh:", "/model")):
            continue
        lines.append(line)
    clean = "\n".join(lines).strip()
    return clean[-max(1, int(max_chars)) :]


def _read_transcript_entries(path: str) -> list[dict[str, Any]]:
    if not path or not os.path.isfile(path):
        return []
    entries: list[dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    entry = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(entry, dict):
                    entries.append(entry)
    except OSError:
        return []
    return entries


def _extract_gbg_payload(text: str) -> dict[str, Any]:
    body = str(text or "")
    candidates: list[tuple[int, str]] = []
    for match in re.finditer(r"<GBG>\s*(\{.*?\})\s*</GBG>", body, flags=re.DOTALL):
        candidates.append((match.end(), match.group(1).strip()))
    for match in re.finditer(r"^\s*GBG:\s*(\{.*\})\s*$", body, flags=re.MULTILINE):
        candidates.append((match.end(), match.group(1).strip()))
    if candidates:
        _, raw = max(candidates, key=lambda item: item[0])
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {}
        if isinstance(payload, dict):
            return payload

    lines = [line.strip() for line in body.splitlines() if line.strip()]
    if not lines:
        return {}
    raw = lines[-1]
    if not (raw.startswith("{") and raw.endswith("}")):
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _normalize_gbg_relay(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    message = _clean_summary(str(payload.get("message") or ""))
    use_last_response = bool(payload.get("use_last_response"))
    if not message and not use_last_response:
        return {}
    normalized: dict[str, Any] = {}
    if message:
        normalized["message"] = message
    if use_last_response:
        normalized["use_last_response"] = True
    target = str(payload.get("target") or payload.get("target_role") or "").strip()
    if target:
        normalized["target"] = target
    kind = str(payload.get("kind") or "").strip()
    if kind:
        normalized["kind"] = kind
    return normalized


def _extract_summary_from_transcript(path: str) -> str:
    if not path or not os.path.isfile(path):
        return ""
    last = ""
    try:
        with open(path, encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    entry = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(entry, dict):
                    continue
                if not _looks_assistant_entry(entry):
                    continue
                text = _extract_text(entry)
                if text:
                    last = text
    except OSError:
        return ""
    return _clean_summary(last)


def _capture_tmux_summary() -> str:
    tmux_session = _env("MESH_TMUX_SESSION")
    if not tmux_session:
        return ""
    try:
        result = subprocess.run(
            ["tmux", "capture-pane", "-pt", tmux_session, "-S", "-120"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return _clean_summary(result.stdout)


def _extract_gbg_relay_from_transcript(path: str) -> dict[str, Any]:
    entries = _read_transcript_entries(path)
    if not entries:
        return {}

    last_gbg_user_index = -1
    last_gbg_user_text = ""
    for index in range(len(entries) - 1, -1, -1):
        entry = entries[index]
        if not _looks_user_entry(entry):
            continue
        text = _extract_text(entry)
        stripped = text.lstrip()
        if re.match(r"(?i)^/gbg(?:\s|$)", stripped):
            last_gbg_user_index = index
            last_gbg_user_text = stripped
            break

    if last_gbg_user_index >= 0:
        command_text = re.sub(r"(?i)^/gbg", "", last_gbg_user_text, count=1).strip()
        tokens = command_text.split()
        known_roles = {
            "boss",
            "president",
            "lead",
            "worker",
            "worker-gemini",
            "worker-claude",
            "worker-codex",
            "reviewer",
            "verifier",
        }
        target = ""
        if tokens and tokens[0] in known_roles:
            target = tokens[0]
            command_text = command_text[len(tokens[0]) :].strip()
        if command_text:
            relay = {"message": command_text}
            if target:
                relay["target"] = target
            return relay
        for index in range(last_gbg_user_index - 1, -1, -1):
            entry = entries[index]
            if not _looks_assistant_entry(entry):
                continue
            text = _clean_summary(_extract_text(entry))
            if text:
                relay = {"message": text, "use_last_response": True}
                if target:
                    relay["target"] = target
                return relay
        _log("Ignoring /gbg without explicit text because no previous assistant response was found")
        return {}

    last = ""
    for entry in entries:
        if not _looks_assistant_entry(entry):
            continue
        text = _extract_text(entry)
        if text:
            last = text
    return _normalize_gbg_relay(_extract_gbg_payload(last))


def _extract_gbg_relay_from_tmux() -> dict[str, Any]:
    tmux_session = _env("MESH_TMUX_SESSION")
    if not tmux_session:
        return {}
    try:
        result = subprocess.run(
            ["tmux", "capture-pane", "-pt", tmux_session, "-S", "-120"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    return _normalize_gbg_relay(_extract_gbg_payload(result.stdout))


def _send_cross_role_message(*, msg_type: str, content: str, metadata: dict[str, Any] | None = None) -> None:
    session_id = _env("MESH_ROUTER_SESSION_ID")
    ui_group_id = _env("MESH_UI_GROUP_ID")
    ui_role = _env("MESH_UI_ROLE")
    target_role = str((metadata or {}).get("target_role") or _env("MESH_RELAY_TARGET_ROLE") or "*").strip() or "*"
    if not session_id or not ui_group_id or not ui_role or not content.strip():
        return
    body = {
        "session_id": session_id,
        "direction": "out",
        "role": ui_role,
        "content": content,
        "metadata": {
            **(metadata or {}),
            "ui_group_id": ui_group_id,
            "envelope": {
                "sender_role": ui_role,
                "sender_session_id": session_id,
                "target_role": target_role if msg_type == "relay" else "*",
                "msg_type": msg_type,
                "ui_group_id": ui_group_id,
            },
        },
    }
    _router_post("/sessions/send", body)


def _claim_turn() -> None:
    ui_group_id = _env("MESH_UI_GROUP_ID")
    ui_role = _env("MESH_UI_ROLE")
    if ui_group_id and ui_role:
        _router_post("/sessions/turn/claim", {"ui_group_id": ui_group_id, "role": ui_role})


def _release_turn() -> None:
    ui_group_id = _env("MESH_UI_GROUP_ID")
    ui_role = _env("MESH_UI_ROLE")
    if ui_group_id and ui_role:
        _router_post("/sessions/turn/release", {"ui_group_id": ui_group_id, "role": ui_role})


def _emit_state(state: str, *, extra: dict[str, Any] | None = None) -> None:
    _send_cross_role_message(
        msg_type="state_change",
        content=state,
        metadata={"state": state, **(extra or {})},
    )


def _handle_user_prompt_submit(_: dict[str, Any]) -> None:
    _claim_turn()
    _emit_state("responding")


def _handle_notification(hook_input: dict[str, Any]) -> None:
    _release_turn()
    _emit_state("awaiting_input", extra={"notification": _extract_text(hook_input)})


def _handle_stop(hook_input: dict[str, Any]) -> None:
    transcript_path = str(
        hook_input.get("transcript_path") or _env("CLAUDE_TRANSCRIPT_PATH") or ""
    ).strip()
    relay = _extract_gbg_relay_from_transcript(transcript_path)
    if not relay:
        relay = _extract_gbg_relay_from_tmux()
    if relay:
        _send_cross_role_message(
            msg_type="relay",
            content=str(relay.get("message") or ""),
            metadata={
                "source_role": _env("MESH_UI_ROLE"),
                "summary_source": "hook_stop",
                "gbg": relay,
                "target_role": str(relay.get("target") or "").strip(),
            },
        )
    _release_turn()
    _emit_state("idle")


def main() -> int:
    if _env("MESH_RELAY_MODE") != "claude_hooks":
        return 0
    if not _env("MESH_UI_ROLE") or not _env("MESH_UI_GROUP_ID") or not _env("MESH_ROUTER_SESSION_ID"):
        return 0
    hook_input = _read_hook_input()
    event_name = sys.argv[1].strip() if len(sys.argv) > 1 else str(hook_input.get("hook_event_name") or "").strip()
    if event_name == "UserPromptSubmit":
        _handle_user_prompt_submit(hook_input)
    elif event_name == "Notification":
        _handle_notification(hook_input)
    elif event_name == "Stop":
        _handle_stop(hook_input)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
