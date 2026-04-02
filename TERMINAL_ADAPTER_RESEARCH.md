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

## Research Questions

### Claude Code

- Which hook events are reliable for:
  - response finished
  - waiting input
  - approval prompt
- Is transcript parsing enough to extract the final assistant summary safely?
- Can a wrapper emit a custom iTerm2 escape marker at `Stop`?

### Gemini / CCS Gemini

- Is there any native event, transcript, or log file that indicates:
  - reply finished
  - waiting input
  - permission/choice prompt
- Is there any structured output mode we can enable?
- Can we wrap Gemini to emit explicit control markers when it reaches known states?

### Shared

- Can a thin wrapper emit iTerm2 custom escape sequences like:
  - `mesh:response_finished`
  - `mesh:waiting_input`
  - `mesh:needs_choice`
  - `mesh:summary:<payload>`
- Can the same wrapper also emit plain router-side state if iTerm2 is not available?

## Recommended Research Sequence

1. Claude Code:
   - confirm the strongest hook + transcript combination for `stop/summary/waiting_input`
2. Gemini via CCS:
   - search for logs, transcripts, structured mode, prompt-state hints
3. shared wrapper design:
   - define the smallest cross-CLI event contract
4. iTerm2 integration:
   - optionally map wrapper events to custom escape sequences and local attention signals

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
