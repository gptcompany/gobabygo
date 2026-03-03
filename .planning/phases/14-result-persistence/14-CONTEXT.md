# Phase 14: Result Persistence + Read Path — Context

## Phase Goal

Il router persiste i result che i worker gia' inviano, e li rende leggibili via API.

## Source

`CROSS_VERIFICATION_BRIEF.md` sezioni 3.5, 3.7, 5.2, 7.2 (Fase 14)

## Current State Analysis

### Task Model (src/router/models.py:63-87)

24 campi. **Nessun campo `result` o `output`**.
Campi rilevanti: `payload` (solo input), `status`, `critical`.

### DB Schema (src/router/db.py:28-53)

Tabella `tasks` con 24 colonne. **Nessuna colonna `result_json`**.
Metodo `update_task_fields()` (line 696-715) accetta dict arbitrario — riutilizzabile.
Metodo `get_task()` (line 318-324) ritorna Task.

### Task Completion Flow

```
server._handle_task_complete()  (line 325-358)
  ├── Estrae solo task_id + worker_id dal body
  ├── Chiama scheduler.complete_task(task_id, worker_id)
  │     ├── Se critical: _route_to_review() (line 234-273) → running → review
  │     │     └── Transazione: update_task_status + insert_event + update_task_fields(review_timeout) + expire_lease
  │     └── Se non-critical: _route_to_completed() (line 275-301) → running → completed
  │           └── Transazione: update_task_status + insert_event + expire_lease + on_task_terminal()
  └── Risponde 200/409
```

**Gap critico**: Il server ignora il campo `result` nel body. I worker lo inviano, il server non lo legge.

### Worker Result Payloads (gia' inviati)

**Batch worker** (worker_client.py:253-258, 288-302):
```json
{"output": "<last 4096 chars stdout>", "exit_code": 0}
```
Inviato via `_report_complete(task_id, result)` a `POST /tasks/complete` con campo `result`.

**Session worker** (session_worker.py:309-315):
```json
{"interactive_session": true, "session_id": "...", "tmux_session": "...", "final_snapshot": "<last 4000 chars>"}
```

### Existing HTTP Endpoints

**GET**: /health, /metrics, /tasks/next, /workers, /workers/{id}, /sessions, /sessions/{id}, /sessions/messages
**POST**: /tasks, /events, /heartbeat, /register, /sessions/open, /sessions/send, /sessions/close, /tasks/ack, /tasks/complete, /tasks/fail, /workers/{id}/drain

**Missing**: GET /tasks/{id}, GET /tasks?status=...

## Gaps to Close

| # | Gap | Where to Fix |
|---|-----|-------------|
| G1 | No `result` field on Task model | models.py: add `result: dict[str, Any] | None = None` |
| G2 | No `result_json` column in DB | db.py: ALTER TABLE ADD COLUMN in init_schema() |
| G3 | Server ignores result in /tasks/complete | server.py: extract `data.get("result")`, pass to scheduler |
| G4 | Scheduler doesn't persist result | scheduler.py: add result param, persist in SAME transaction |
| G5 | No GET /tasks/{id} | server.py: new handler |
| G6 | No GET /tasks?status=... | server.py: new handler |

## Architectural Constraints

1. **Same transaction**: Result DEVE essere persistito nella stessa transazione che cambia stato (copre sia completed che review)
2. **Backward compatible**: `POST /tasks/complete` senza result deve continuare a funzionare
3. **DB migration**: `ALTER TABLE ADD COLUMN ... DEFAULT NULL` non-blocking in SQLite WAL
4. **Worker non toccati**: Zero modifiche ai worker — il gap e' solo server-side
5. **Size limit**: Result > 32KB troncato con `_truncated: true`; se ancora fuori limite, fallback compatto `_hard_truncated: true`
6. **Secret filtering**: Pattern sk-, ghp_, xoxb- filtrati prima di persistere

## Files to Modify

| File | Changes | Est. LOC |
|------|---------|----------|
| src/router/models.py | Add `result` field to Task | ~5 |
| src/router/db.py | Add `result_json` column, migration, persist method | ~45 |
| src/router/scheduler.py | Accept + persist result in both _route_to_review and _route_to_completed | ~25 |
| src/router/server.py | Extract result in handler, add GET /tasks/{id}, GET /tasks?status=... | ~65 |
| tests/test_result_capture.py | New test file | ~120 |

**Total estimated**: ~260 LOC

## Decisions (pre-resolved from cross-verification)

- [x] Colonna inline `result_json TEXT` su tasks (no tabella separata — YAGNI)
- [x] Result persistence in scheduler.py (non server.py) — allineato con flusso transazionale
- [x] Filtro thread_id rimandato a Fase 15
- [x] comms.py non toccato (resta policy engine stateless)
