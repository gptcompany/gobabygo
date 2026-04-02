#!/usr/bin/env python3
"""PTY relay proxy for interactive CLI panes.

This keeps the wrapped CLI fully interactive while mirroring submitted user
prompts into the mesh session bus. It is provider-agnostic and works at the
terminal layer, so the same mechanism can wrap CCS Gemini, Claude Code, Codex,
or any other CLI launched inside the pane.
"""

from __future__ import annotations

import argparse
import codecs
import os
import pty
import select
import shutil
import subprocess
import sys
import termios
import tty
from dataclasses import dataclass, field


_BRACKETED_PASTE_START = b"\x1b[200~"
_BRACKETED_PASTE_END = b"\x1b[201~"
_INBOUND_PREFIX = "__mesh_inbound__:"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mirror submitted pane prompts to a mesh role.")
    parser.add_argument("--mode", default="prompt_submit")
    parser.add_argument("--target-role", required=True)
    parser.add_argument("--ui-group-id", required=True)
    parser.add_argument("--mesh-script", required=True)
    parser.add_argument("--source-role", default="")
    parser.add_argument("--message-prefix", default="")
    parser.add_argument("--ignore-slash-commands", action="store_true")
    parser.add_argument("--no-child-passthrough", action="store_true")
    parser.add_argument("--local-ack", default="")
    parser.add_argument("--summary-max-chars", type=int, default=400)
    parser.add_argument("--child-command", required=True)
    return parser.parse_args()


def _clean_submitted_line(text: str, *, ignore_slash_commands: bool) -> str | None:
    cleaned = " ".join(str(text or "").replace("\r", "\n").split()).strip()
    if not cleaned:
        return None
    if ignore_slash_commands and cleaned.startswith("/"):
        return None
    return cleaned


def _encode_inbound_line(source_role: str, content: str) -> str:
    role = "peer"
    candidate = str(source_role or "").strip()
    if candidate:
        role = candidate
    return f"{_INBOUND_PREFIX}{role}:{content}"


def _decode_inbound_line(text: str) -> tuple[str, str] | None:
    raw = str(text or "")
    if not raw.startswith(_INBOUND_PREFIX):
        return None
    payload = raw[len(_INBOUND_PREFIX):]
    role, sep, content = payload.partition(":")
    if not sep:
        return None
    return (role.strip() or "peer", content.strip())


@dataclass
class _InputTracker:
    ignore_slash_commands: bool = False
    _buffer: list[str] = field(default_factory=list)
    _decoder: codecs.IncrementalDecoder = field(
        default_factory=lambda: codecs.getincrementaldecoder("utf-8")("ignore")
    )
    _escape: bytearray = field(default_factory=bytearray)
    _in_bracketed_paste: bool = False

    def feed(self, data: bytes) -> list[str]:
        submitted: list[str] = []
        for byte in data:
            maybe_line = self._feed_byte(byte)
            if maybe_line is not None:
                submitted.append(maybe_line)
        return submitted

    def _feed_byte(self, byte: int) -> str | None:
        if self._escape:
            self._escape.append(byte)
            if 0x40 <= byte <= 0x7E:
                seq = bytes(self._escape)
                self._in_bracketed_paste = seq == _BRACKETED_PASTE_START or (
                    self._in_bracketed_paste and seq != _BRACKETED_PASTE_END
                )
                if seq == _BRACKETED_PASTE_END:
                    self._in_bracketed_paste = False
                self._escape.clear()
            return None

        if byte == 0x1B:
            self._escape = bytearray([byte])
            return None
        if byte in (0x08, 0x7F):
            if self._buffer:
                self._buffer.pop()
            return None
        if byte == 0x15:
            self._buffer.clear()
            return None
        if byte in (0x0A, 0x0D):
            submitted = "".join(self._buffer)
            self._buffer.clear()
            self._decoder.reset()
            return submitted
        if byte < 0x20 and byte != 0x09:
            return None

        decoded = self._decoder.decode(bytes([byte]), final=False)
        if decoded:
            self._buffer.append(decoded)
        return None


def _relay_prompt(args: argparse.Namespace, prompt: str) -> None:
    mesh_script = args.mesh_script
    if not mesh_script or not os.path.exists(mesh_script):
        return
    if not shutil.which(mesh_script) and not os.access(mesh_script, os.X_OK):
        return

    command = [mesh_script, "send", args.target_role, "--ui-group-id", args.ui_group_id]
    message = f"{args.message_prefix}{prompt}" if args.message_prefix else prompt
    command.append(message)
    try:
        subprocess.run(
            command,
            cwd=os.getcwd(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return


def _format_local_ack(template: str, *, target_role: str, prompt: str) -> str:
    text = str(template or "").strip()
    if not text:
        return ""
    try:
        rendered = text.format(target_role=target_role, prompt=prompt)
    except (KeyError, ValueError):
        rendered = text
    return f"\r\n● {rendered}\r\n"


def _format_inbound_message(source_role: str, content: str) -> str:
    role = str(source_role or "").strip() or "peer"
    body = str(content or "").strip()
    if not body:
        return ""
    return f"\r\n[{role}] {body}\r\n"


def _looks_ready_prompt(text: str) -> bool:
    lines = str(text or "").splitlines()
    for raw in reversed(lines):
        line = raw.strip()
        if not line:
            continue
        return line == "❯"
    return False


def _extract_response_summary(text: str, *, max_chars: int = 400) -> str:
    lines: list[str] = []
    for raw in str(text or "").splitlines():
        line = raw.replace("\xa0", " ").strip()
        if not line:
            continue
        if line.startswith("❯"):
            continue
        if line.startswith("✻"):
            continue
        if line.startswith("⎿"):
            continue
        if line.startswith("Stop says:"):
            continue
        lines.append(line)
    summary = " ".join(lines).strip()
    if not summary:
        return ""
    return summary[-max(1, int(max_chars)) :]


def _run_proxy(args: argparse.Namespace) -> int:
    stdin_fd = sys.stdin.fileno()
    stdout_fd = sys.stdout.fileno()
    old_tty = None
    if os.isatty(stdin_fd):
        old_tty = termios.tcgetattr(stdin_fd)

    tracker = _InputTracker(ignore_slash_commands=args.ignore_slash_commands)
    mode = str(args.mode or "prompt_submit").strip()
    capture_reply = mode == "response_summary"
    router_relay = mode == "router_relay"
    awaiting_summary = False
    output_decoder = codecs.getincrementaldecoder("utf-8")("ignore")
    output_buffer = ""
    pid, master_fd = pty.fork()
    if pid == 0:
        os.execvp("/bin/bash", ["/bin/bash", "-lc", args.child_command])

    exit_code = 0
    try:
        if old_tty is not None:
            tty.setraw(stdin_fd)
        while True:
            ready, _, _ = select.select([stdin_fd, master_fd], [], [])
            if master_fd in ready:
                try:
                    output = os.read(master_fd, 4096)
                except OSError:
                    output = b""
                if not output:
                    break
                os.write(stdout_fd, output)
                if capture_reply and awaiting_summary:
                    output_buffer += output_decoder.decode(output, final=False)
                    if _looks_ready_prompt(output_buffer):
                        summary = _extract_response_summary(
                            output_buffer,
                            max_chars=args.summary_max_chars,
                        )
                        if summary:
                            _relay_prompt(args, summary)
                        awaiting_summary = False
                        output_buffer = ""
                        output_decoder.reset()
            if stdin_fd in ready:
                try:
                    data = os.read(stdin_fd, 1024)
                except OSError:
                    data = b""
                if not data:
                    break
                raw_lines = tracker.feed(data)
                inbound_only = False
                if raw_lines:
                    decoded_lines = []
                    for raw_prompt in raw_lines:
                        cleaned = _clean_submitted_line(
                            raw_prompt,
                            ignore_slash_commands=args.ignore_slash_commands,
                        )
                        if not cleaned:
                            decoded_lines.append(("passthrough", raw_prompt))
                            continue
                        inbound = _decode_inbound_line(cleaned)
                        if inbound is not None:
                            decoded_lines.append(("inbound", inbound))
                            continue
                        decoded_lines.append(("relay", cleaned))
                    inbound_only = bool(decoded_lines) and all(kind == "inbound" for kind, _ in decoded_lines)
                    for kind, payload in decoded_lines:
                        if kind == "relay":
                            if mode == "prompt_submit":
                                _relay_prompt(args, str(payload))
                            elif capture_reply:
                                awaiting_summary = True
                                output_buffer = ""
                                output_decoder.reset()
                        elif kind == "inbound":
                            source_role, content = payload
                            if router_relay:
                                if content:
                                    os.write(master_fd, content.encode("utf-8", "ignore"))
                                    os.write(master_fd, b"\r")
                            else:
                                rendered = _format_inbound_message(source_role, content)
                                if rendered:
                                    os.write(stdout_fd, rendered.encode("utf-8", "ignore"))
                if not args.no_child_passthrough:
                    if not inbound_only:
                        os.write(master_fd, data)
                    continue

                os.write(stdout_fd, data)
                if not raw_lines:
                    continue
                for raw_prompt in raw_lines:
                    cleaned = _clean_submitted_line(
                        raw_prompt,
                        ignore_slash_commands=args.ignore_slash_commands,
                    )
                    inbound = _decode_inbound_line(cleaned or "")
                    if inbound is not None:
                        source_role, content = inbound
                        if router_relay:
                            if content:
                                os.write(master_fd, content.encode("utf-8", "ignore"))
                                os.write(master_fd, b"\r")
                        else:
                            rendered = _format_inbound_message(source_role, content)
                            if rendered:
                                os.write(stdout_fd, rendered.encode("utf-8", "ignore"))
                        continue
                    if cleaned:
                        if mode == "prompt_submit":
                            _relay_prompt(args, cleaned)
                        elif capture_reply:
                            awaiting_summary = True
                            output_buffer = ""
                            output_decoder.reset()
                        ack = _format_local_ack(
                            args.local_ack,
                            target_role=args.target_role,
                            prompt=cleaned,
                        )
                        if ack:
                            os.write(stdout_fd, ack.encode("utf-8", "ignore"))
                        continue
                    os.write(master_fd, raw_prompt.encode("utf-8", "ignore") + b"\r")
        _, status = os.waitpid(pid, 0)
        exit_code = os.waitstatus_to_exitcode(status)
    finally:
        if old_tty is not None:
            termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_tty)
    return exit_code


def main() -> int:
    return _run_proxy(_parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
