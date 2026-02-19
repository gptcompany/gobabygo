# Phase 04 Research: Event Bridge

## Research Topics

### 1. CloudEvents Python SDK

**Package**: `cloudevents` v1.12.0 (PyPI)
**Status**: CNCF Graduated project (Jan 2024), actively maintained

Key API:
```python
from cloudevents.http import CloudEvent
from cloudevents.conversion import to_json, to_structured

# Create event
event = CloudEvent({
    "type": "com.mesh.command.started",
    "source": "mesh/gsd-bridge",
    "subject": "task-{task_id}",
}, data={"run_id": "...", "command": "..."})

# Serialize to JSON (structured content mode)
body = to_json(event)
```

Features:
- Pydantic v2 model available (`cloudevents.pydantic.event`)
- Binary & structured content modes
- `specversion` defaults to `"1.0"`
- `id` auto-generated if omitted

**Decision**: Use `cloudevents` SDK for envelope creation. Pydantic model for validation.

### 2. NDJSON Transport

**Format**: Newline-Delimited JSON (one JSON object per line, `\n` separator)
**Library options**:
- `json` stdlib — sufficient for our use case (line-by-line write/read)
- `orjson` — 3-5x faster, but external dependency
- `ndjson` — inactive maintenance, avoid

**Decision**: Use stdlib `json` for NDJSON. Our volumes are low (command events, not streaming data). No need for orjson overhead.

Pattern:
```python
import json

# Write NDJSON
def append_ndjson(path: Path, event: dict) -> None:
    with open(path, "a") as f:
        f.write(json.dumps(event, default=str) + "\n")

# Read NDJSON
def read_ndjson(path: Path) -> list[dict]:
    events = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events
```

### 3. JSON Schema Draft 2020-12 Validation

**Package**: `jsonschema` v4.26.0, `Draft202012Validator`
**Usage**:
```python
from jsonschema import Draft202012Validator

schema = {...}
validator = Draft202012Validator(schema)
validator.validate(instance)  # raises ValidationError
```

**Decision**: Use `jsonschema` with `Draft202012Validator` for event schema validation. Define schema in `schemas/command_event.schema.json`.

## Architecture Reference

From KISS spec docs:
- `GSD_TRACKING_LAYER_MAPPING.md` — defines event envelope format, reliability rules
- `ROBUST_AUTO_MAPPING_STRATEGY.md` — 3-layer mapping (auto events, rule-based semantic, explicit overrides)

### Event Envelope (from spec)
```json
{
  "run_id": "uuid",
  "task_id": "uuid",
  "phase": "05",
  "gsd_command": "gsd:plan-phase",
  "step": "plan",
  "event": "started|completed|failed",
  "target_cli": "claude|codex|gemini|null",
  "target_account": "work|clientA|clientB|null",
  "status": "running",
  "attempt": 1,
  "idempotency_key": "string",
  "artifact_paths": [".planning/phases/.../PLAN.md"],
  "ts": "ISO-8601"
}
```

### Mapping Layers
1. **Layer A (auto)**: Every command emits `command.started`, `command.completed`, `command.failed`
2. **Layer B (rules)**: YAML patterns → semantic step (`*research*` → `step=research`)
3. **Layer C (overrides)**: Critical commands with explicit mapping

### Files to Create
- `src/router/bridge/event_emitter.py` — auto emit + validation + POST /events
- `src/router/bridge/mapping_engine.py` — YAML rule matching
- `src/router/bridge/fallback_buffer.py` — NDJSON buffer + replay
- `mapping/command_rules.yaml` — pattern rules
- `mapping/command_overrides.yaml` — critical exceptions
- `schemas/command_event.schema.json` — JSON Schema 2020-12

### CommunicationPolicy Integration
From Phase 3: CommunicationPolicy enforcement deferred to Phase 4 API layer. The event bridge is the API boundary where CommunicationPolicy should validate sender roles.

## Dependencies
- `cloudevents>=1.12.0`
- `jsonschema>=4.20.0` (already likely installed via pydantic)
- `pyyaml>=6.0` (for YAML rule configs)

## Sources
- [CloudEvents Python SDK](https://github.com/cloudevents/sdk-python)
- [CloudEvents Spec](https://cloudevents.io/)
- [jsonschema Python](https://python-jsonschema.readthedocs.io/en/stable/validate/)
- [JSON Schema Draft 2020-12](https://json-schema.org/draft/2020-12)
- [NDJSON.com](https://ndjson.com/)
