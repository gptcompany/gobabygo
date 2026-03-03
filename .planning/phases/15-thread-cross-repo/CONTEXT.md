# Phase 15: Thread Model + Cross-Repo Context

## Phase Goal

Thread come gruppo ordinato di task con contesto condiviso cross-repo. Costruito sopra Task + dependency.py esistenti.

## Success Criteria (from ROADMAP.md)

1. `meshctl thread create --name "..."` crea thread
2. `meshctl thread add-step` aggiunge step come Task con thread_id, step_index, repo
3. Quando step diventa attivo, il router spawna sessione tmux interattiva per la CLI specificata
4. Step usano `depends_on` esistente -- dependency.py li sblocca automaticamente
5. Al complete di step N, `result` di step N viene iniettato come contesto in `payload` di step N+1
6. `meshctl thread context {name}` mostra result aggregati
7. `meshctl thread status {name}` mostra tabella con stato per step

## Architectural Decisions

### DB Design: Ibrido (threads table + colonne nullable su tasks)

- `threads(thread_id, name, status, created_at, updated_at)` — metadata aggregati
- `tasks(..., thread_id NULL, step_index NULL, repo NULL, role NULL)` — esecuzione
- 1:N pulito, backward-compatible (tutti i campi nullable)
- `threads.status` persistito e aggiornato nello scheduler path transazionale

**Rationale**: tasks esegue, threads descrive/aggrega. Evita derivazione costosa dello stato globale per ogni GET /threads.

### threads.status Lifecycle (Full)

- Update in `_route_to_completed()` e `_route_to_review()` dello scheduler (dentro transazione con `conn`)
- NON in `on_task_terminal()` che resta dedicato a dependency unblocking

**Status values e transizioni:**
- `pending` — thread creato, nessuno step ancora dispatched
- `active` — almeno uno step in running/queued (transizione in `_try_dispatch()` quando primo step viene assegnato)
- `completed` — tutti gli step in stato terminale completato (check in `_route_to_completed()`)
- `failed` — almeno uno step failed/timeout/canceled (check in `_route_to_completed()`, `_handle_task_timeout()`, cancel path)

**Edge cases gestiti:**
- Task timeout → scheduler aggiorna threads.status a `failed` nel path di timeout
- Cancellazione manuale → se task ha thread_id, aggiorna threads.status
- Il check "tutti completed?" usa: `SELECT COUNT(*) FROM tasks WHERE thread_id = ? AND status NOT IN ('completed')` nella stessa transazione

### tmux Spawn: Reale (subprocess)

- `subprocess.run(['tmux', 'new-session', '-d', '-s', session_name, ...])` quando step diventa attivo
- Nuovo modulo `src/router/session_spawner.py` (~100 LOC)
- Integrazione con la tabella `sessions` esistente per tracking

**Security: sanitizzazione nomi sessione**
- Session name format: `mesh-{thread_id[:8]}-s{step_index}` (alfanumerico + trattini, no shell metachar)
- Validazione regex `^[a-zA-Z0-9_-]+$` prima di passare a subprocess
- subprocess senza shell=True (coerente con pattern CLI invocation esistente)

**Lifecycle management / cleanup**
- `tmux kill-session -t {name}` su step completion o failure
- Cleanup idempotente: se sessione non esiste, ignora (tmux kill-session fallisce silenziosamente)
- Conflitti nomi: il formato `mesh-{thread_id[:8]}-s{step_index}` e' unico per design (thread_id e' UUID)
- Router restart: al boot, non tenta di ripristinare sessioni tmux orfane (le sessioni sono transienti, i task hanno recovery via FSM)

**Topologia**: router e worker operano sulla stessa macchina (Workstation) via VPN. tmux spawning locale e' corretto per questa architettura. Deploy distribuito multi-host e' out of scope (vedi PROJECT.md Constraints).

### Context Propagation: Lazy Read + Runtime Enrichment

- `dependency.py` sblocca step N+1 senza mutare payload (separation of concerns)
- Quando `/tasks/next` (long-poll) restituisce il task al worker, il server arricchisce la response on-the-fly
- `thread_context` come campo **top-level separato** da `payload`:
  - `payload` = intent originale (immutato)
  - `thread_context` = contesto derivato a runtime dai result degli step precedenti
- Nessuna mutazione in DB, nessuna duplicazione

**Guardrails**:
1. Cap 32KB su thread_context aggregato (stesso limite di result_json, `_MAX_RESULT_BYTES`). Se aggregato > 32KB, tronca i result piu' vecchi (step con step_index piu' basso) mantenendo gli ultimi.
2. Solo step con `step_index < current` AND `status = 'completed'` (review escluso)

**meshctl thread context aggregation**: query semplice `SELECT result_json FROM tasks WHERE thread_id = ? AND status = 'completed' ORDER BY step_index`. Thread tipici hanno 3-10 step, performance non e' un concern. Il cap 32KB previene memory issue anche con thread molto lunghi.

### on_failure: Deferred a Phase 16

- Phase 15 si concentra su thread model + context propagation
- Se uno step fallisce, il thread si blocca (behavior di default di `depends_on`)
- `on_failure` field (skip/retry/abort) implementato in Phase 16 (Aggregator + Error Handling)

## Scope

### In scope
- threads table + migration
- Colonne thread_id, step_index, repo, role su tasks + migration
- Thread CRUD module (`src/router/thread.py`)
- Session spawner module (`src/router/session_spawner.py`)
- Server endpoints: POST/GET /threads, GET /threads/{id}/context, GET /threads/{id}/status
- meshctl commands: thread create, thread add-step, thread status, thread context
- Runtime thread_context enrichment in server handler
- threads.status lifecycle management nello scheduler
- Tests

### Out of scope (Phase 16)
- on_failure per-step policy (skip/retry/abort)
- Fan-in aggregation con error handling
- E2E cross-repo test
- Audit trail completo per step

## Dependencies

- Phase 14 (Result Persistence) — COMPLETE
- Existing: Task model, dependency.py, scheduler.py, server.py, meshctl.py, sessions table

## Key Files to Modify

| File | Changes |
|------|---------|
| `src/router/models.py` | Thread model, ThreadCreate/ThreadStep schemas, Task fields |
| `src/router/db.py` | threads table, migration, CRUD queries |
| `src/router/thread.py` | NEW: Thread lifecycle, context aggregation |
| `src/router/session_spawner.py` | NEW: tmux session spawn/cleanup |
| `src/router/scheduler.py` | threads.status update in completion path |
| `src/router/server.py` | /threads endpoints, thread_context enrichment in /tasks/next |
| `src/meshctl.py` | thread create/add-step/status/context commands |
| `tests/router/test_thread.py` | NEW: Thread model tests |
| `tests/router/test_session_spawner.py` | NEW: Session spawner tests |
