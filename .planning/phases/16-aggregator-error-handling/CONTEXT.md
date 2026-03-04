# Phase 16: Aggregator + Error Handling — Context

## Phase Goal

Aggregazione automatica dei risultati e gestione errori nei thread. Ultima fase di v1.3.

## Success Criteria (from ROADMAP.md)

1. Thread 3-step cross-repo esegue E2E senza copia-incolla
2. Step fallito con `on_failure: retry` viene ri-eseguito (max 3 tentativi)
3. Step fallito con `on_failure: skip` non blocca thread
4. `meshctl thread status` mostra tabella leggibile con risultati per step
5. Audit trail completo in DB: ogni step ha input, output, timestamps, worker, repo

## Existing Infrastructure

### Thread Model (src/router/thread.py)
- `create_thread(db, name)` — crea thread con UUID
- `add_step(db, thread_id, step_request)` — aggiunge step con auto-dependency su step precedente
- `get_thread_context(db, thread_id, up_to_step_index)` — aggregazione risultati (32KB cap)
- `compute_thread_status(db, thread_id)` — calcola stato thread (pending/active/completed/failed)

### Dependency System (src/router/dependency.py)
- `on_task_terminal(db, task_id)` — event-driven: sblocca blocked->queued quando deps complete
- `check_dependencies(db, task_id)` — verifica se tutte deps in terminal state
- TERMINAL_STATES = {completed, failed, timeout, canceled}
- Problema attuale: se uno step **fallisce**, il successivo resta **blocked** e il thread diventa **failed**

### Retry Policy (src/router/retry.py)
- `RetryPolicy(max_attempts=3, backoff_schedule=[15,60,180])`
- `should_retry(task)` / `requeue_with_backoff(task_id, reason)`
- EscalationCallback protocol per escalation a BOSS
- Esiste ma NON collegato a thread/step — opera solo a livello task

### Audit Trail (src/router/recovery.py + db.py)
- TaskEvent model: event_id, task_id, event_type, payload, ts
- `audit_timeline(db, task_id)` — lista eventi per task
- `insert_event(event)` — inserisce evento
- Evento types: state_transition, recovery_requeued, escalation_to_boss

### Scheduler (src/router/scheduler.py)
- `complete_task()` — persiste result_json, chiama on_task_terminal, aggiorna thread status
- `report_failure()` — chiama on_task_terminal, aggiorna thread status
- Result sanitization: secret patterns, 32KB cap, _truncated/_hard_truncated

### meshctl CLI (src/meshctl.py)
- `thread create --name`, `thread add-step`, `thread status`, `thread context`
- _resolve_thread_id: name -> thread_id resolution

### DB Schema (tasks table)
- thread_id TEXT, step_index INTEGER, repo TEXT, result_json TEXT
- Unique index: (thread_id, step_index)
- depends_on: JSON array of task_ids

## What Needs to Be Built

### 1. Per-Step on_failure Policy
- **Dove**: ThreadStepRequest model (add `on_failure` field)
- **Valori**: `abort` (default, current behavior), `skip`, `retry`
- **Dove agire**: `on_task_terminal()` in dependency.py — quando step fallisce, controllare policy
- **Skip**: sblocca step successivo anche se corrente failed (non solo completed)
- **Retry**: riusa RetryPolicy esistente, requeue step con backoff
- **Abort**: comportamento corrente (thread -> failed)

### 2. Policy Storage
- Task ha gia' tutti i campi. Serve aggiungere `on_failure TEXT DEFAULT 'abort'` alla tabella tasks
- ThreadStepRequest deve includere `on_failure` field
- `add_step()` deve propagare on_failure al Task

### 3. Enhanced Audit Trail per Step
- Ogni step deve avere: input (payload), output (result_json), timestamps (created_at, updated_at), worker (assigned_worker), repo
- La maggior parte esiste gia' sulla tabella tasks
- Manca: expose info completa in `meshctl thread status` e GET /threads/{id}/status

### 4. meshctl thread status Enhancement
- Mostrare tabella con: step_index, title, status, repo, worker, duration, result summary
- Duration = updated_at - created_at (per step completati)

### 5. E2E Cross-Repo Test
- Test che crea thread 3-step, simula esecuzione sequenziale, verifica context propagation
- Testa skip e retry policies
- Usa InProcess transport (no HTTP needed)

## Architecture Decisions

### AD-1: on_failure enforcement point — dual-site

**Primary site**: `report_failure()` in scheduler.py (early exit for retry)
**Secondary site**: `on_task_terminal()` in dependency.py (skip logic)

Flow per policy:
- **abort** (default): `report_failure()` segue path attuale -> `on_task_terminal()` -> dipendenti restano blocked -> thread failed
- **retry**: `report_failure()` controlla `on_failure == retry` E `task.attempt < max_attempts` **PRIMA** di chiamare `on_task_terminal()`. Se retry possibile: requeue via RetryPolicy, **non** chiamare on_task_terminal (il task non e' terminale). Se retry exhausted: procede come abort.
- **skip**: `report_failure()` procede normalmente (task raggiunge terminal state `failed`), poi `on_task_terminal()` in dependency.py sblocca i dependenti **anche se** il task e' failed, perche' controlla la policy skip sul task failed.

### AD-2: Thread status con step skipped

Un thread dove tutti gli step sono in terminal state, ma alcuni failed con `on_failure: skip`:
- Thread status = `completed` (non un nuovo stato)
- Rationale: l'utente ha esplicitamente dichiarato che il fallimento e' accettabile via skip
- La tabella `meshctl thread status` mostra chiaramente quali step sono "failed (skipped)" vs "completed"
- Nessun nuovo stato ThreadStatus — YAGNI

### AD-3: Context propagation con step skipped

Quando uno step fallisce con `on_failure: skip`:
- Il step successivo **NON** riceve result dallo step skipped (non c'e' result utile)
- `get_thread_context()` gia' filtra solo step `completed` — nessuna modifica necessaria
- Lo step successivo riceve un marker nel `thread_context`: `{"step_index": N, "status": "skipped", "repo": "...", "result": null}`
- Questo permette allo step successivo di sapere che uno step precedente e' stato skippato e adattarsi

### AD-4: Retry state visibility

Durante il retry backoff di uno step:
- Task status = `queued` (dopo requeue) con `not_before` set (backoff)
- Thread status = `active` (corretto: il thread sta ancora lavorando)
- `meshctl thread status` mostra step come "queued (retry N/3)" leggendo `task.attempt`
- Nessun nuovo stato FSM necessario — il retry usa gli stessi stati (failed -> queued via requeue)
- TaskEvent `retry_requeued` loggato con attempt number e reason

## Constraints

- Backward compatible: on_failure default = "abort" (current behavior)
- No new DB tables — solo nuova colonna su tasks
- No new FSM states — retry usa queued/running cycle esistente
- RetryPolicy gia' ha max_attempts=3 e backoff — riusare
- 32KB cap su result_json gia' enforced — no changes needed
- Thread status: completed anche con skip, failed solo con abort

## Files to Modify (estimated)

| File | Change |
|------|--------|
| src/router/models.py | Add on_failure to Task, ThreadStepRequest, OnFailurePolicy enum |
| src/router/db.py | Add on_failure column migration |
| src/router/thread.py | Propagate on_failure in add_step(), adjust compute_thread_status() for skip |
| src/router/dependency.py | Handle skip policy in on_task_terminal() |
| src/router/scheduler.py | Handle retry policy in report_failure() |
| src/router/server.py | Pass on_failure in add-step endpoint, enhance thread status response |
| src/meshctl.py | Add --on-failure flag, enhance status display |
| tests/ | E2E test, unit tests for each policy |
