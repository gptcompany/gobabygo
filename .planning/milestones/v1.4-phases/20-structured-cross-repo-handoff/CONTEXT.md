# Phase 20: Structured Cross-Repo Handoff — Context

## Phase Goal

Replace copy/paste with a structured handoff object while reusing the existing thread model.

## Source

- `.planning/milestones/v1.4-ROADMAP.md` — Phase 20 section
- `.planning/milestones/v1.4-REQUIREMENTS.md` — HANDOFF-01 through HANDOFF-05

## Requirements

- **HANDOFF-01**: Cross-repo handoff packet includes `source_repo`, `target_repo`, `summary`, and `question`
- **HANDOFF-02**: Cross-repo handoff packet includes `decisions`, `artifacts`, `open_risks`, and `related_session_ids`
- **HANDOFF-03**: `PRESIDENT_GLOBAL` is the only cross-repo handoff producer/consumer in the initial rollout
- **HANDOFF-04**: First iteration reuses existing task payload/event structures
- **HANDOFF-05**: `thread_context` remains available and is not replaced by the handoff packet

## Current State Analysis

### Thread Model (v1.3)

- Thread steps are normal `Task` rows with `thread_id` and `step_index`
- `thread_context` is runtime enrichment computed from completed steps — not persisted separately
- Steps have `depends_on` for automatic chaining
- `Task.payload` is `dict[str, Any]` — extensible

### Task Payload

`Task.payload` is the natural carrier for structured handoff data. The handoff packet can live inside `payload` as a well-defined sub-structure without changing the Task model or DB schema.

### Cross-repo routing (from Phase 17)

- `Task.repo` identifies the target repo
- Topology defines `require_president_handoff=true`
- `PRESIDENT_GLOBAL` is the only entity allowed to produce cross-repo work

## Design Decisions (Resolved)

1. **Handoff shape in payload**: Nested `payload.handoff` object. Keeps handoff data separate from other payload keys (e.g. `payload.prompt`). Workers can check `if "handoff" in task.payload` to detect handoff steps. Aligns with HANDOFF-04 (reuse existing payload structure).

2. **Handoff as step type**: A property of any step, not a special step type. Any step whose `payload` contains a `handoff` key is a handoff step. No new `TaskPhase` or `ExecutionMode` values needed. This is the simplest approach.

3. **meshctl visibility**: Extend `meshctl thread status` to show a `[HANDOFF]` marker on steps that carry handoff data. Add `meshctl thread handoff <thread_id> <step_index>` subcommand for full handoff details (source_repo, target_repo, summary, decisions, etc.).

4. **Validation**: Light structural validation in the router. When a step is added via `POST /threads/<id>/steps` and `payload.handoff` is present, validate that required fields (`source_repo`, `target_repo`, `summary`) exist. Optional fields (`question`, `decisions`, `artifacts`, `open_risks`, `related_session_ids`) are not required. Malformed handoff returns 400. This prevents garbage handoffs while keeping the system extensible.

## Gaps to Close

| # | Gap | Where to Fix |
|---|-----|-------------|
| G1 | No structured handoff shape | Define schema (Pydantic model or documented dict contract) |
| G2 | No handoff production path | Server/API: accept handoff in step creation |
| G3 | No handoff consumption path | thread_context or explicit read endpoint |
| G4 | No operator visibility | meshctl: display handoff data in thread status |

## Architectural Constraints

1. **No second orchestration stack**: handoff reuses existing thread/task model
2. **PRESIDENT_GLOBAL only**: no REPO_LEAD-to-REPO_LEAD handoff in initial rollout
3. **thread_context preserved**: handoff augments, does not replace
4. **Existing payload structure**: no new DB columns — handoff lives in `Task.payload`
5. **Depends on Phase 19**: notification bridge can display handoff context in alerts

## Handoff Schema (Resolved)

```python
# Pydantic model for validation (not persisted — lives inside Task.payload)
HANDOFF_SUMMARY_MAX = 4096
HANDOFF_LIST_ITEM_MAX = 512
HANDOFF_LIST_MAX_ITEMS = 20

class HandoffPacket(BaseModel):
    source_repo: str                            # HANDOFF-01 (required)
    target_repo: str                            # HANDOFF-01 (required)
    summary: str = Field(max_length=HANDOFF_SUMMARY_MAX)  # HANDOFF-01 (required)
    question: str = ""                          # HANDOFF-01 (optional)
    decisions: list[str] = []                   # HANDOFF-02 (max 20 items)
    artifacts: list[str] = []                   # HANDOFF-02 (max 20 items)
    open_risks: list[str] = []                  # HANDOFF-02 (max 20 items)
    related_session_ids: list[str] = []         # HANDOFF-02 (max 20 items)
```

Usage: `payload = {"handoff": {...}, "prompt": "..."}`

## Files to Modify

| File | Changes |
|------|---------|
| src/router/models.py | Add `HandoffPacket` Pydantic model for validation |
| src/router/thread.py | Validate handoff in `add_step()` when `payload.handoff` present |
| src/router/server.py | No changes needed — validation in thread.py |
| src/meshctl.py | `[HANDOFF]` marker in thread status + `thread handoff` subcommand |
| tests/router/test_thread.py | Handoff creation, validation, context propagation |
| tests/router/test_handoff_e2e.py | Cross-repo handoff E2E flow |

## Data Flow Model (HANDOFF-05)

The handoff packet flows through two distinct paths:

1. **Direct delivery (payload)**: The assigned worker receives `payload.handoff` directly in the task it polls via `/tasks/next`. This is how the consuming worker gets the handoff context — from its own task payload, not from thread_context.

2. **Downstream propagation (thread_context)**: When the handoff step completes, its `result` (not payload) is included in `thread_context` for subsequent steps. Downstream steps see the RESULT of the handoff step, not the handoff packet itself.

This distinction is critical:
- **Handoff consumer** = the worker assigned to the step with `payload.handoff` → reads from `task.payload.handoff`
- **Downstream steps** = later steps that see the handoff step's result via `thread_context`

`thread_context` remains unchanged (HANDOFF-05): it continues to aggregate completed step results.

### 32KB Cap Mitigation

Handoff packets can be large (summary + decisions + artifacts). Mitigations:
- `HandoffPacket.summary` max length: 4096 chars (enforced by validation)
- `HandoffPacket.decisions` / `artifacts` / `open_risks`: max 20 items, each max 512 chars
- Total handoff packet size stays well under 32KB
- If future handoffs need larger payloads: store external reference (task_id + read endpoint)

## PRESIDENT_GLOBAL Enforcement (HANDOFF-03)

Cross-repo handoff requires `role=PRESIDENT_GLOBAL`. The router enforces with a **hard block**:
- When `payload.handoff` is present AND `handoff.source_repo != handoff.target_repo`
- The step's `role` field must be `PRESIDENT_GLOBAL`
- If not: return **403 Forbidden** with message "cross-repo handoff requires PRESIDENT_GLOBAL role"
- Same-repo handoffs (source_repo == target_repo) are allowed without role restriction

## Topology Validation

When `payload.handoff` is present, validate repos against loaded topology:
- `handoff.source_repo` must exist in topology repos (or be empty string for "current")
- `handoff.target_repo` must exist in topology repos
- If topology is not loaded (no topology file): skip validation (backward compatibility)
- Invalid repo: return **400 Bad Request** with message "unknown repo in handoff"
