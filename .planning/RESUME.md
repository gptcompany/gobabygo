# Resume Notes (2026-03-04)

## Stato attuale
- Branch: `master`
- Ultimi commit chiave:
  - `e4ce673` fix upterm socket + piano fase 19
  - `158b810` revert chirurgico della falsa concurrency scheduler
- Test mirati verdi:
  - `tests/router/test_topology.py`
  - `tests/router/test_scheduler.py`
  - `tests/router/test_session_worker.py`
  - `tests/router/test_thread.py`
  - Esito: `124 passed`

## Decisioni già prese
- Niente concurrency nel router/scheduler.
- Capacity in produzione: multi-worker Docker (1 worker process = 1 task).
- Session bus `/sessions/*` resta control-plane, non terminale primario.

## Blocker/attenzioni aperte
1. `account_in_use` blocca repliche con stesso `account_profile`:
   - `src/router/worker_manager.py` (`register_worker`)
   - Se servono repliche con stesso account, serve micro-patch controllata.
2. `load_topology()` non normalizza tutti gli errori filesystem:
   - `src/router/topology.py`
   - Possibile hardening: convertire `OSError` in `TopologyError`.

## Prossimo passo raccomandato
1. Deploy Docker multi-worker (senza nuovo refactor).
2. Decidere policy account:
   - account distinti per replica (zero code), oppure
   - stesso account con micro-patch in `WorkerManager`.
3. Solo dopo: riprendere `Phase 19` (`.planning/phases/19-notification-bridge/19-01-PLAN.md`).
