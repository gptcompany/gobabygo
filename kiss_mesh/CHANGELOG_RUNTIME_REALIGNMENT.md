# Runtime Realignment Changelog (GSD-Tracked)

Purpose:
- provide an audit trail for the 2026-02 session-first runtime correction
- map `milestone/workstream -> commits -> touched files -> validation`
- keep runtime fixes explicitly inside GSD roadmap/governance tracking

Scope rules (important):
- Runtime execution truth lives in router DB + session workers (`src/router/*`)
- GSD remains the tracking/integration framework (milestones, status, later semantic mapping)
- Do not encode roadmap milestones in runtime `TaskPhase` (`plan|implement|test|integrate|release`)

## Milestone Mapping

### S0 — Runtime Realignment (session-first foundations)

Status:
- `in progress` (foundations landed)

Commits:
- `64877d9` — `Add tmux session worker and persisted session bus`
  - Router/runtime:
    - `src/router/models.py`
    - `src/router/db.py`
    - `src/router/server.py`
    - `src/router/scheduler.py`
    - `src/router/session_worker.py`
    - `src/router/worker_client.py`
    - `src/router/worker_manager.py`
  - Deploy/runtime ops:
    - `deploy/mesh-session-worker@.service`
    - `deploy/mesh-session-claude-work.env`
    - `deploy/mesh-session-codex-work.env`
    - `deploy/deploy-workers.sh`
    - `deploy/install.sh`
    - `deploy/BOOT-ORDER.md`
    - `deploy/MAC-112-ITERM2-CLI-SETUP.md`
    - `deploy/check-mac-112-cli.sh`
    - `deploy/mesh-worker-claude-work.env` (Claude Agent Teams flag template)
  - Tests/docs:
    - `tests/router/test_db.py`
    - `tests/router/test_scheduler.py`
    - `tests/router/test_server.py`
    - `tests/router/test_session_worker.py`
    - `tests/router/test_worker_client.py`
    - `QUICKSTART.md`

### S0.1 — Session output streaming + roadmap realignment docs

Status:
- `landed`

Commits:
- `64f7043` — `Stream session output and realign runtime milestones`
  - Runtime:
    - `src/router/session_worker.py` (incremental tmux output emission)
    - `tests/router/test_session_worker.py`
  - Canonical docs:
    - `kiss_mesh/README.md`
    - `kiss_mesh/KISS_IMPLEMENTATION_SPEC_V1.md`
    - `kiss_mesh/CONSOLIDATED_TEAM_ORCHESTRATION_GUIDE.md`
  - Ops docs:
    - `QUICKSTART.md`

### GSD Governance Traceability Clarification (for S0/S1)

Status:
- `landed`

Commits:
- `c57bc2e` — `docs(kiss): align session-first fixes with GSD tracking`
  - Canonical docs:
    - `kiss_mesh/README.md`
    - `kiss_mesh/KISS_IMPLEMENTATION_SPEC_V1.md`
    - `kiss_mesh/CONSOLIDATED_TEAM_ORCHESTRATION_GUIDE.md`
  - Outcome:
    - clarifies that runtime realignment milestones are *inside* GSD tracking/governance
    - avoids false interpretation that GSD is “out of scope”

## Validation Notes (cross-checkable)

Implementation validation executed during S0/S0.1:
- router/unit/API tests passed (including session bus/session worker coverage)
- deploy script tests passed
- `py_compile` on `src/router/session_worker.py` passed

Operational verification performed on Mac `.112` (outside repo, operator machine):
- enabled `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` in `~/.claude/settings.json`
- verified/updated `claude`, `codex`, `gemini` CLIs
- fixed local Gemini config schema incompatibility (backup created before edit)

Network assumptions (documented SSOT):
- `network-infra` repo is the network SSOT for LAN/VPN/WireGuard topology
- Mac `.112` should be treated `VPN-first` with LAN fallback unless WG IP is explicitly documented there

## Next Tracked Milestones

### S1 — Interactive Worker Stabilization
- richer live output streaming / cursor semantics
- operator completion/fail controls for long-lived interactive tasks
- attach/recover behavior hardening across worker restart

### G1 — GSD Tracking Integration (post-runtime stabilization)
- auto event emitter for command lifecycle
- YAML mapping/override integration without changing runtime execution truth
- milestone/status dashboards aligned with router event stream

