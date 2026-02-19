# Phase 04 Context: Event Bridge

## Vision
Build the client-side GSD→Router event bridge that wraps commands into CloudEvent envelopes, maps them to semantic steps via YAML rules, validates with JSON Schema, and includes a fallback buffer for offline resilience.

## Scope

### In Scope
- `EventEmitter`: CloudEvent creation, schema validation, transport dispatch
- `MappingEngine`: YAML rule-based semantic mapping (3 layers: auto, rules, overrides)
- `FallbackBuffer`: NDJSON file buffer with replay on reconnect
- `EventTransport` protocol with 2 implementations:
  - `InProcessTransport`: direct DB write (tests + local dev)
  - `HttpTransport`: HTTP POST (production VPN Mac→VPS)
- CommunicationPolicy integration at event bridge boundary (validate sender roles)
- JSON Schema for event envelope validation
- YAML config files for mapping rules and overrides

### Out of Scope
- HTTP server endpoint on router side (Phase 5 — deployment)
- systemd integration (Phase 5)
- Metrics export (Phase 6)

## Architecture Decisions

| Decision | Rationale |
|----------|-----------|
| CloudEvents SDK (`cloudevents` v1.12) | CNCF standard, Python SDK well-maintained |
| stdlib `json` for NDJSON (not orjson) | Low volume events, no need for speed optimization |
| `jsonschema` Draft202012Validator | Standard compliance, already in ecosystem |
| `pyyaml` for mapping config | Simple, proven, versioned-in-git |
| Transport adapter pattern | Enables in-process testing + HTTP production |
| Configurable buffer path (default ~/.mesh/) | Supports per-machine deployment |
| CommPolicy validation at bridge boundary | Event bridge IS the API boundary from Phase 3 todo |

## Technical Context

### Existing Modules (from Phase 1-3)
- `src/router/models.py` — Task, TaskEvent, Worker, Lease, TaskStatus, CLIType, CommunicationRole
- `src/router/fsm.py` — apply_transition(), ALLOWED_TRANSITIONS
- `src/router/db.py` — SQLite persistence, insert_event()
- `src/router/comms.py` — CommunicationPolicy (allowed_edges, validate_message)
- `src/router/dead_letter.py` — dead-letter stream for rejected transitions
- Total: 141 tests passing across 9 test files

### Event Envelope Format (from KISS spec)
```json
{
  "specversion": "1.0",
  "type": "com.mesh.command.{event}",
  "source": "mesh/gsd-bridge/{machine}",
  "id": "uuid",
  "data": {
    "run_id": "uuid",
    "task_id": "uuid|null",
    "phase": "04",
    "gsd_command": "gsd:plan-phase",
    "step": "plan",
    "event": "started|completed|failed",
    "target_cli": "claude|codex|gemini|null",
    "target_account": "default",
    "status": "running",
    "attempt": 1,
    "idempotency_key": "hash(run_id,command,step,event,attempt)",
    "artifact_paths": [],
    "ts": "ISO-8601",
    "duration_ms": null
  }
}
```

### Mapping Layers
1. **Auto (Layer A)**: Every command → `command.started`, `command.completed`, `command.failed`
2. **Rules (Layer B)**: Pattern match command name → semantic step
3. **Overrides (Layer C)**: Critical commands with explicit mapping

### New Files
- `src/router/bridge/__init__.py`
- `src/router/bridge/emitter.py` — EventEmitter class
- `src/router/bridge/mapping.py` — MappingEngine class
- `src/router/bridge/buffer.py` — FallbackBuffer class
- `src/router/bridge/transport.py` — EventTransport protocol + implementations
- `src/router/bridge/schema.py` — JSON Schema validation
- `mapping/command_rules.yaml`
- `mapping/command_overrides.yaml`
- `schemas/command_event.schema.json`
- `tests/router/test_emitter.py`
- `tests/router/test_mapping.py`
- `tests/router/test_buffer.py`
- `tests/router/test_transport.py`

### Dependencies to Add
- `cloudevents>=1.12.0`
- `jsonschema>=4.20.0`
- `pyyaml>=6.0`

## Boundaries
- Bridge is client-side only (emitter sends, router receives)
- Fallback buffer is per-machine, path configurable
- CommPolicy validates sender role on event acceptance
- Invalid events → dead-letter (reuse existing dead_letter.py pattern)
- Idempotency via deterministic key: `hash(run_id, command_name, step, event_kind, attempt)`

## User Preferences
- Buffer path: configurable (default ~/.mesh/)
- CommPolicy: included in Phase 4
- Transport: adapter pattern with in-process + HTTP
