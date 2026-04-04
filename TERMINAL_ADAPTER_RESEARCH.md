# Terminal Adapter Research Brief

## Goal

Close the current gap between:

- mechanical terminal control on the Mac (`focus`, `send text`, `send key`, `dump/capture`)
- semantic runtime events from AI CLIs (`response finished`, `waiting for input`, `choice required`, `summary ready`)

This brief is intentionally narrow:

- canonical UI/layout stays `boss,president`
- `mesh sessions` remains the router-backed helper
- `mesh term` is the mechanical iTerm helper
- research focuses on `Gemini` and `Claude Code` first

## Current State

Canonical commands already aligned:

- `mesh sessions`
- `mesh ui <repo>`
- `mesh ui attach <repo>`
- `mesh ui close <repo>`
- `mesh term list|focus|send|key|dump`

Mechanical iTerm control now exists locally:

- [mesh_iterm_control.py](/Users/sam/gobabygo/scripts/mesh_iterm_control.py)

What it already does:

- list mesh-marked panes
- focus a pane by exact `repo + role`
- send text
- send keys like `enter`, arrows, `ctrl-c`
- dump recent screen contents

What is still not closed:

- detect `response finished` reliably for Gemini and Claude Code
- detect `waiting for input` / `permission prompt` / `press p to continue`
- extract or emit a trustworthy summary to route across roles

## Target State

For `boss,president` we want one stable chain:

1. operator talks to `boss`
2. runtime detects when `boss` has finished a meaningful reply
3. runtime emits or extracts a concise summary
4. summary is delivered to `president`
5. if `president` needs human/role input, that state is detectable without scraping by hand

## What We Already Verified

### 1. `it2ag`

Verified local clone:

- `/tmp/it2ag`

Useful code:

- process/status detection: [`detector.py`](/tmp/it2ag/src/it2ag/detector.py)
- iTerm2 Toolbelt/focus: [`server.py`](/tmp/it2ag/src/it2ag/server.py)
- monitor UI: [`ui.py`](/tmp/it2ag/src/it2ag/ui.py)

What it is good for:

- detect Claude/Codex processes
- detect `running/idle`
- group by repo
- focus iTerm2 session

What it does **not** solve:

- summary extraction
- final response detection for Gemini/Codex/CCS
- choice prompts like `press p`

Repo:

- https://github.com/mkusaka/it2ag

### 2. `claude-code-monitor`

Verified local clone:

- `/tmp/claude-code-monitor`

Useful code:

- focus by TTY: [`focus.ts`](/tmp/claude-code-monitor/src/utils/focus.ts)
- send text and direct keys: [`send-text.ts`](/tmp/claude-code-monitor/src/utils/send-text.ts)
- screen capture: [`screen-capture.ts`](/tmp/claude-code-monitor/src/utils/screen-capture.ts)
- hook event state store: [`file-store.ts`](/tmp/claude-code-monitor/src/store/file-store.ts)
- Claude hooks: [`handler.ts`](/tmp/claude-code-monitor/src/hook/handler.ts)
- transcript parsing: [`transcript.ts`](/tmp/claude-code-monitor/src/utils/transcript.ts)

What it is good for:

- mechanical Mac terminal control
- `waiting_input` / `running` / `stopped` for Claude via hooks
- transcript-backed assistant message extraction for Claude

What it does **not** solve generically:

- Gemini/Codex/CCS semantic events
- cross-CLI summary extraction

Repo:

- https://github.com/onikan27/claude-code-monitor

## External References

### iTerm2 official docs

Python API intro:

- https://iterm2.com/python-api/tutorial/index.html

Notifications:

- https://iterm2.com/python-api/notifications.html

Prompt API:

- https://iterm2.com/python-api/prompt.html

Custom escape sequence example:

- https://iterm2.com/python-api/examples/ccs.html

Escape code reference:

- https://iterm2.com/documentation-escape-codes.html

### Other references

`automate-terminal`:

- https://pypi.org/project/automate-terminal/

Why it matters:

- it is a good reference for `create/list/focus/paste/run` terminal automation
- it does **not** solve semantic agent events

`maniple` / `maniple-mcp`:

- https://pypi.org/project/maniple-mcp/

Why it matters:

- useful as a reference for worker state, screen-based observation, and iTerm/tmux orchestration
- not a drop-in answer for our `boss,president` relay problem

## Working Hypotheses

### Hypothesis A: iTerm2 should handle only mechanical control

Keep iTerm2 limited to:

- focus
- send text
- send key
- screen dump/capture
- prompt notifications where shell integration really applies
- optional custom escape sequence notifications

Do **not** make iTerm2 the semantic source of truth for `boss -> president`.

### Hypothesis B: semantic detection should come from adapters

For each CLI, prefer in this order:

1. native hooks/events
2. transcript/log files
3. custom escape markers emitted by a wrapper
4. terminal parser/screen parser only as fallback

### Hypothesis C: `bell`/tab badge is a secondary signal

Useful for:

- visual attention
- human intervention

Not enough as the primary automation contract.

## Research Findings

### Claude Code

- **Native Hooks (Confirmed)**:
  - `Stop`: deterministic signal for "response finished". Anthropic documents that it runs when the main Claude Code agent has finished responding, except for user interrupts.
  - `UserPromptSubmit`: runs when the user submits a prompt, before Claude processes it.
  - `PreToolUse`: runs before a tool call; can return `permissionDecision: "ask"` for approval flow.
  - `PostToolUse`: runs after a tool completes successfully.
  - `Notification`: runs for permission notifications and "Claude is waiting for your input".
  - `SessionStart`, `SessionEnd`, `SubagentStop`, `PreCompact` also exist and may be useful, but they are secondary for this adapter.
- **Transcript/Logs**:
  - Found in `~/.claude/transcripts/` (session-specific JSON files).
  - Contains full tool-use history and assistant responses.
- **Deterministic Signals**:
  - `Stop` event is the primary signal for "summary extraction".
  - Hook logic can write a specific marker (e.g., `mesh:summary`) to a file or stdout.
- **Recommended Adapter**:
  - **Primary**: Use a dedicated `mesh-claude-adapter` that wires into `Stop` and `PreToolUse` hooks via `.claude/settings.json` or `hooks.json`.
  - **Secondary**: Screen-scraping for prompts and `❯` markers as a universal fallback.
- **Primary Sources**:
  - Anthropic hooks reference: `https://docs.anthropic.com/en/docs/claude-code/hooks`
  - Anthropic settings reference: `https://docs.anthropic.com/en/docs/claude-code/settings`

### Gemini / CCS Gemini

- **What Is Confirmed From CCS Docs/Repo**:
  - CCS launches Claude CLI with alternative provider settings for `gemini`, `codex`, and other profiles.
  - CCS documents shared `settings.json` behavior via `~/.ccs/shared/` symlinked to `~/.claude/`.
  - CCS documents CLIProxy provider settings that point Claude-compatible traffic at provider-specific `ANTHROPIC_BASE_URL` endpoints.
- **What Is Still Inference, Not Yet Proven Locally**:
  - If `ccs gemini` / `ccs codex` really execute through the same Claude Code frontend and shared settings path, Claude hooks should still fire.
  - We have not yet completed a local end-to-end proof that `Stop`, `Notification`, or `UserPromptSubmit` fire exactly the same way under `ccs gemini` and `ccs codex`.
- **Structured Output**:
  - No verified global interactive `--json` mode yet for CCS Gemini/Codex in this project.
  - Hook payloads remain the strongest candidate if the Claude hook path is confirmed live.
- **Recommended Adapter**:
  - Preferred path: treat CCS Gemini/Codex as Claude-hook-capable, but verify with a local smoke before making it the canonical contract.
  - Fallback path: PTY proxy or terminal parser if the hook path is missing or inconsistent under CCS.
- **Primary Sources**:
  - CCS README/repo: `https://github.com/kaitranntt/ccs`
  - Especially the sections describing CLIProxy providers, shared `settings.json`, and `CCS_CLAUDE_PATH`

### Failure Modes and Ambiguity

- **Hook Latency**: Hooks may have slight latency between tool completion and trigger.
- **Concurrent Prompts**: If an operator types while a hook is running, the PTY proxy might capture it twice or cause race conditions.
- **Transcripts**: Large sessions can lead to very big transcript files, making real-time parsing slow.
- **Provider Disconnect**: `ccs` provider-side errors (like `500 auth_unavailable`) may not always trigger a clean `Stop` event.
- **Ambiguity**: Distinguishing between an "interactive sub-shell" (e.g., inside `bash`) and the "main Claude agent" when relying solely on screen scraping.


## Deliverables The Research Should Produce

For each CLI (`Claude Code`, `Gemini/CCS`):

- native events available
- transcript/log availability
- deterministic signals for `done`
- deterministic signals for `waiting_input`
- deterministic signals for `choice required`
- exact recommended adapter approach
- failure modes / ambiguity

## Prompt For Gemini / Claude / Codex Research

Use this prompt as-is or with small repo-specific edits:

```text
We are hardening a local multi-CLI operator cockpit on macOS using iTerm2.

Current state:
- canonical UI is minimal: boss + president
- `mesh sessions` is the canonical router-backed helper
- `mesh term` is the mechanical iTerm2 helper for focus/send-key/send-text/dump
- iTerm2 should remain only the mechanical control layer, not the semantic source of truth
- current unresolved blocker is semantic detection:
  - response finished
  - waiting for input
  - choice required (example: "press p to continue")
  - summary extraction for relay from boss to president

Target state:
- operator talks to boss
- when boss finishes a meaningful response, runtime can detect that deterministically
- runtime extracts or emits a concise summary
- summary is routed to president
- if the CLI is waiting for input or a choice, that state is detectable without manual visual inspection

Research task:
1. For the target CLI, find native hooks/events/logs/transcripts or structured outputs that can detect:
   - response finished
   - waiting input
   - approval/choice prompt
   - final assistant message / summary
2. Prefer:
   - native hooks/events
   - transcript/log files
   - explicit machine-readable modes
   - wrapper-emitted markers
   - terminal parsing only as fallback
3. Return:
   - exact files, commands, flags, env vars, or APIs
   - links to primary sources
   - confidence level for each signal
   - what is deterministic vs heuristic
   - recommended adapter design for this CLI
4. If nothing robust exists, propose the smallest wrapper contract that emits explicit markers such as:
   - response_finished
   - waiting_input
   - needs_choice
   - summary_ready

Constraints:
- macOS operator host
- iTerm2 Python API available
- CCS may wrap the CLI
- avoid vague suggestions; prefer exact paths, commands, APIs, or code references
- separate facts from inference
```

## Immediate Next Implementation Steps

1. expose `mesh term` as the canonical mechanical helper
2. complete targeted research for:
   - Claude Code
   - Gemini / CCS Gemini
3. choose one adapter per CLI:
   - native hook/transcript
   - or wrapper marker
   - terminal parsing only if forced
