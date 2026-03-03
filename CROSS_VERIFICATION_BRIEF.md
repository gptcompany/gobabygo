# GoBabyGo v1.3 — Cross-Verification & Implementation Brief

**Data**: 2026-03-03 (aggiornato con cross-verifica Codex)
**Preparato da**: Claude (Opus 4.6) + Codex (cross-verifica)
**Repo**: /media/sam/1TB/gobabygo (branch: master, tag: v1.0, HEAD: b897022)
**Stato**: v5 — decisione architetturale finale, piano consolidato

---

## 0. Changelog

| Versione | Data | Autore | Modifiche |
|----------|------|--------|-----------|
| v1 | 2026-03-03 | Claude | Draft iniziale con 11 claim |
| v2 | 2026-03-03 | Claude + Codex | Corretto: Task ha 24 campi (non 25), src/ totale 6,149 LOC (non 4,438), 455 test function (non ~441). **Scoperta critica**: i worker GIA' inviano result payload — il gap e' solo server-side persistence. Piano rivisto di conseguenza. |
| v3 | 2026-03-03 | Claude + Codex (round 2) | 5 incoerenze architetturali corrette: handler review inesistente rimosso, result persistence spostata in scheduler.py/db.py (non server.py), filtro thread rimosso da Fase 14, comms.py rimosso da Fase 15, decisione task_results risolta (colonna inline + pre-condizione chiusa). |
| v4 | 2026-03-03 | Claude | Decisioni architetturali: router spostato su WS (no VPS), batch worker declassato a fallback, deploy semplificato a single-machine. Tag v1.1 e v1.2 creati nel repo. |
| v5 | 2026-03-03 | Claude | **Decisione finale**: GoBabyGo unico orchestratore (Opzione A=C). Agent Teams (Opzione B) scartata: non puo' spawnare CLI diverse (solo Claude↔Claude). Spawn dinamico (+180 LOC) integrato in Fase 15. Opzioni ridondanti A/B/C eliminate. |

---

## 2. Problema dell'utente (contesto operativo)

### Setup attuale

```
Mac (.112) ──SSH via VSCode──> Workstation (.111)
  │
  ├── Per ogni repo aperta:
  │     ├── Terminal 1: Claude Code CLI
  │     ├── Terminal 2: Codex CLI (ChatGPT)
  │     └── Terminal 3: Gemini CLI
  │
  └── Spesso 2+ repo contemporanee (es. monitoring-stack + hyperliquid-node)
```

### Problemi concreti

| # | Problema | Impatto |
|---|---------|---------|
| P1 | Orchestrazione manuale via copia-incolla tra CLI | Errori di contesto, token sprecati, lavoro mal eseguito |
| P2 | Connessione SSH via VSCode instabile | Drop → resume manuale su TUTTE le CLI per OGNI repo |
| P3 | Risorse esaurite: ogni repo = 1 VSCode server + 3 CLI | RAM/CPU insufficienti, crash |
| P4 | Cross-repo coordination manuale | "Thread" mentale tra repo diverse = fragile, errori di sequenza |
| P5 | Solo Claude Code ha accesso a GSD/SpecKit framework | Codex e Gemini non possono usare /gsd:* o /speckit.* |
| P6 | Nessun audit trail del lavoro cross-repo | Impossibile ricostruire cosa e' stato fatto e perche' |

### Workflow target

L'utente vuole:
- **Eliminare** il copia-incolla manuale tra sessioni CLI
- **Tracciare** thread di lavoro cross-repo con contesto persistente
- **Orchestrare** Claude, Codex, Gemini da un singolo punto di controllo
- **Sopravvivere** ai drop di connessione SSH senza perdere stato
- **Usare** GSD/SpecKit (skill interattive Claude Code) attraverso l'orchestrazione

---

## 3. Stato attuale del progetto GoBabyGo

### 3.1 Milestone completate

| Milestone | Fasi | Test | Shipped |
|-----------|------|------|---------|
| v1.0 MVP | Phase 1-6 (15 piani) | 291 | 2026-02-19 |
| v1.1 Production Readiness | Phase 7-10 (9 piani) | 404 | 2026-02-21 |
| v1.2 Operational Readiness | Phase 11-13 (3 piani) | 436 | 2026-02-23 |

**Nessuna milestone o fase aperta.** Tutto il lavoro pianificato e' completo.

**CLAIM-01**: Il git tag `v1.2` NON esiste. Ultimo tag: `v1.0`. `git describe`: `v1.0-69-gb897022`.
> Verificare: `git tag --list` e `git describe --tags --long`

### 3.2 Architettura esistente — file esatti

| File | LOC | Ruolo |
|------|-----|-------|
| `src/router/server.py` | 889 | HTTP server: tutti gli endpoint |
| `src/router/db.py` | 811 | SQLite persistence, WAL mode |
| `src/router/session_worker.py` | 557 | **Session worker: tmux interactive sessions** |
| `src/router/worker_client.py` | 342 | Batch worker: subprocess --print |
| `src/router/scheduler.py` | 335 | Deterministic task scheduler |
| `src/router/worker_manager.py` | 251 | Worker registry, FSM, stale detection |
| `src/router/verifier.py` | 212 | VERIFIER gate per task critici |
| `src/router/models.py` | 164 | Pydantic schemas: Task, Session, etc. |
| `src/router/fsm.py` | 164 | FSM 9 stati con transizioni validate |
| `src/router/comms.py` | 74 | Communication policy: BOSS/PRESIDENT/WORKER |
| `src/router/bridge/` | 639 | Event bridge (6 file): CloudEvents, buffer, mapping |
| **Subtotale file elencati** | **4,438** | Solo i file nella tabella sopra |
| **TOTALE reale src/** | **6,149** | Include tutti i moduli Python sotto src/ |
| **TOTALE tests/** | **7,664** | 25 file, **455** funzioni test |

**CLAIM-02** (CORRETTO dopo cross-verifica): I LOC per-file sono corretti. Il totale src/ originale (4,438) contava solo i file elencati; il totale reale e' **6,149**. I test function sono **455**, non ~441.
> Moduli aggiuntivi non elencati: `src/router/dependency.py`, `src/meshctl.py`, `src/__init__.py`, etc.

### 3.3 Due tipi di worker

**CLAIM-03**: GoBabyGo ha DUE worker type distinti.

#### Batch Worker (`worker_client.py`)

```python
# Line 243-249 di worker_client.py
proc = subprocess.run(
    full_cmd,   # ["claude", "--print", "-p", "<prompt>"]
    capture_output=True,
    text=True,
    timeout=self.config.task_timeout,
    cwd=work_dir,
)
```

- Invocazione: `subprocess.run()` con flag `--print` (non-interattivo)
- Output: stdout catturato, max 4KB
- Timeout: 30 min default
- Nessun message bus

#### Session Worker (`session_worker.py`)

```python
# Line 337-344 di session_worker.py
subprocess.run(
    [self.config.tmux_bin, "new-session", "-d", "-s", session_name,
     "-c", work_dir, "bash", "-lc", cli_command],
    check=True, capture_output=True, text=True,
)
```

- Invocazione: tmux session interattiva (NO --print flag)
- Input: `tmux send-keys` (line 367-374) — **puo' inviare QUALSIASI testo, incluso `/gsd:execute-phase`**
- Output: `tmux capture-pane -S -200` (line 385-397) — snapshot continuo con delta detection
- Message bus: bidirezionale via `POST /sessions/send` (line 479-499)
- Timeout: 2 ore default
- Poll interval: 1s configurabile

**CLAIM-04**: Il session worker puo' inviare qualsiasi testo alla CLI interattiva, inclusi comandi GSD/SpecKit, perche' usa `tmux send-keys` senza alcun filtro.
> Verificare: leggere `_tmux_send_text()` in session_worker.py e confermare assenza di filtering.

### 3.4 Session message bus

**CLAIM-05**: Esiste un message bus bidirezionale per le sessioni.

- Endpoint: `POST /sessions/send` (server.py)
- Campi: `session_id`, `direction` (in/out/system), `role` (cli/operator/system/president), `content`, `seq` (monotonic), `metadata`
- Persistenza: tutti i messaggi salvati in DB con audit trail
- Il session worker posta output delta come messaggi `direction=out`
- L'operatore o il BOSS puo' postare messaggi `direction=in` che il worker recapita via `tmux send-keys`

> Verificare: endpoint in server.py e `_send_session_message()` in session_worker.py lines 479-499.

### 3.5 Task Model — il gap critico

**CLAIM-06** (CORRETTO): Il model Task ha **24** campi (non 25) e NESSUN campo `result` o `output`.

Campi presenti in `models.py` lines 63-87:
```
task_id, parent_task_id, phase, title, payload, target_cli, target_account,
execution_mode, priority, deadline_ts, depends_on, status, assigned_worker,
session_id, lease_expires_at, attempt, not_before, created_by, critical,
rejection_count, review_timeout_at, idempotency_key, created_at, updated_at
```

`payload` e' solo INPUT (parametri del task). Non esiste un campo per l'OUTPUT del worker.
La tabella DB (`db.py:28`) conferma: nessuna colonna result.

**CLAIM-07** (PARZIALMENTE CORRETTO): L'endpoint `/tasks/complete` (server.py lines 325-358) legge solo `task_id` e `worker_id`:
```json
{ "task_id": "string", "worker_id": "string" }
```
**PERO'** (scoperta Codex): il server non RIFIUTA campi extra. E i worker GIA' inviano result:
- Batch worker: `{"output": "...", "exit_code": N}` a `worker_client.py:253`
- Session worker: `{"interactive_session": true, "session_id": "...", "tmux_session": "...", "final_snapshot": "..."}` a `session_worker.py:309`

**Il gap reale non e' "i worker non inviano result" ma "il server ignora e non persiste il result che riceve."**

> Verificare: leggere `_handle_task_complete()` in server.py e i payload di completamento nei worker.

### 3.6 BOSS non legge output

**CLAIM-08**: `comms.py` (74 LOC) implementa CommunicationPolicy con gerarchia BOSS > PRESIDENT > WORKER, ma non ha metodi per leggere output dei task completati.

> Verificare: leggere tutto comms.py (74 righe) e confermare.

### 3.7 Componenti inesistenti

**CLAIM-09**: I seguenti path NON esistono:
- `src/router/aggregator.py` — fan-in aggregation
- `src/router/templates/` — directory task templates
- `src/router/bridges/` — directory bridge to external services (nota: `bridge/` singolare ESISTE ed e' l'event bridge CloudEvents)

> Verificare: `ls -la` su ogni path.

### 3.8 Nessuna integrazione iTerm2/AppleScript/MCP nel codice

**CLAIM-10**: Non esiste alcun riferimento a `it2`, `applescript`, `osascript`, `iterm`, o `mcp` nel codice sorgente (`src/`). L'unico riferimento a iTerm2 e' nel doc `deploy/MAC-112-ITERM2-CLI-SETUP.md`.

> Verificare: `grep -ri "it2\|applescript\|osascript\|iterm\|mcp" src/`

### 3.9 CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS

**CLAIM-11**: Il flag e' configurato in due file .env di deploy:
- `deploy/mesh-session-claude-work.env` (line 9)
- `deploy/mesh-worker-claude-work.env` (line 9)

Ma NON e' referenziato nel codice Python — viene solo passato come variabile d'ambiente al processo Claude Code.

> Verificare: grep nel codice Python e nei file .env.

---

## 4. Ecosistema esterno — documentazione ufficiale

### 4.1 Claude Code Agent Teams (Anthropic)

**Fonte**: https://code.claude.com/docs/en/agent-teams

- Flag: `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` in settings.json o env
- `teammateMode`: `"auto"` | `"tmux"` | `"in-process"`
- Architettura: Team Lead + Teammates + Shared Task List + Mailbox
- Split panes: tmux o iTerm2 (via `it2` CLI + Python API)
- `tmux -CC`: control mode, iTerm2 mappa pane tmux a pane nativi
- Comunicazione: file JSON su disco (`~/.claude/<teamName>/inboxes/<agentName>.json`)
- Storage: `~/.claude/teams/{team-name}/config.json`, `~/.claude/tasks/{team-name}/`
- **Limitazioni**: no session resumption con in-process, no VS Code terminal, no nested teams

**Bug noti iTerm2**:
- [#24301](https://github.com/anthropics/claude-code/issues/24301): fallback silenzioso a in-process
- [#24292](https://github.com/anthropics/claude-code/issues/24292): teammateMode tmux non crea iTerm2 panes
- [#24385](https://github.com/anthropics/claude-code/issues/24385): pane orfani al shutdown

### 4.2 Codex CLI Multi-Agent (OpenAI)

**Fonte**: https://developers.openai.com/codex/multi-agent/

- Config: `config.toml` con `[agents]` section
- Ruoli pre-configurati: default, worker, explorer, monitor + custom
- `max_threads=6`, `max_depth=1` configurabili
- Spawn: automatico / `/agent` CLI / `spawn_agents_on_csv`
- MCP server mode: `codex()` + `codex-reply()` tools
- Per-agent MCP servers
- Traces dashboard con timeline audit
- **Feature chiave**: Codex puo' girare come MCP server, permettendo orchestrazione esterna

### 4.3 Complementarita'

```
Claude Code Agent Teams     Codex CLI Multi-Agent       GoBabyGo
─────────────────────────   ────────────────────────   ──────────────────────
Multi-Claude parallelo      Multi-Codex parallelo       Multi-CLI (Claude+Codex+Gemini)
Intra-repo                  Intra-repo                  Cross-repo
Shared task list nativa     Thread con routing           Thread (da implementare)
Mailbox file-based          MCP server mode              Session message bus
No persistent storage       Traces dashboard             SQLite + CloudEvents
Solo Claude                 Solo Codex                   Qualsiasi CLI
```

**GoBabyGo copre uno spazio che ne' Agent Teams ne' Codex Multi-Agent coprono**: orchestrazione cross-CLI e cross-repo con persistent tracking.

---

## 5. Gap analysis per v1.3

### 5.1 Cosa esiste e funziona

| Componente | File | Status |
|-----------|------|--------|
| Session worker con tmux interattivo | session_worker.py (557 LOC) | Production-ready |
| Session message bus bidirezionale | server.py + session_worker.py | Production-ready |
| Session persistence in DB | db.py | Production-ready |
| Batch worker | worker_client.py (342 LOC) | Production-ready |
| Multi-CLI routing (claude/codex/gemini) | scheduler.py + worker_manager.py | Production-ready |
| Worker deploy (systemd) | deploy/ | Validato E2E |
| Agent Teams flag | .env files | Configurato |
| Router + FSM + scheduler | core | Production-ready, 436 test |

### 5.2 Cosa manca (gap per il workflow dell'utente)

| # | Gap | Impatto | Risolve problema |
|---|-----|---------|------------------|
| G1 | Campo `result` su Task model | Worker completa ma output non strutturato | P1, P6 |
| G2 | Endpoint `/tasks/complete` non accetta output | Impossibile riportare risultati | P1, P6 |
| G3 | Concetto di Thread (gruppo ordinato di task cross-repo) | Nessuna coordinazione cross-repo | P4, P6 |
| G4 | Step chaining (output step N → input step N+1) | Nessun passaggio automatico di contesto | P1, P4 |
| G5 | `meshctl thread` CLI | Nessuna interfaccia per gestire thread | P1, P4 |
| G6 | BOSS legge result dei task completati | Nessuna aggregazione/decisione | P1 |
| G7 | Aggregator base | BOSS non puo' decidere next step | P1 |

### 5.3 NON servono (gia' coperti o fuori scope)

| Componente | Perche' non serve |
|-----------|-------------------|
| iTerm2/AppleScript nel codice GoBabyGo | tmux e' sufficiente; iTerm2 e' un layer UI del Mac |
| MCP client per Codex | Prematura; tmux send-keys funziona per tutti i CLI |
| Agent Teams integration nel router | Agent Teams e' intra-sessione Claude; GoBabyGo e' il layer sopra |
| PePeRS bridge | Opzionale, non blocca il workflow dell'utente |

---

## 6. Step di verifica per Codex

Esegui ogni verifica nell'ordine indicato. Per ogni claim, riporta il verdetto con evidenza.

### Step 1: Verifica struttura e metriche

```bash
cd /media/sam/1TB/gobabygo

# Versione
git tag --list
git describe --tags --long

# LOC esatti
wc -l src/router/session_worker.py src/router/worker_client.py \
      src/router/comms.py src/router/models.py src/router/server.py \
      src/router/scheduler.py src/router/fsm.py src/router/verifier.py \
      src/router/db.py src/router/worker_manager.py

# Bridge
wc -l src/router/bridge/*.py

# Test count
grep -rh "def test_" tests/ | wc -l

# Path inesistenti
ls -la src/router/aggregator.py 2>&1
ls -la src/router/templates/ 2>&1
ls -la src/router/bridges/ 2>&1
```

**Verifica CLAIM-01, CLAIM-02, CLAIM-09**

### Step 2: Verifica session worker (interattivita')

```bash
# tmux new-session (creazione sessione interattiva)
grep -n "new-session" src/router/session_worker.py

# tmux send-keys (invio input — NESSUN filtro?)
grep -n "send-keys" src/router/session_worker.py

# tmux capture-pane (cattura output)
grep -n "capture-pane" src/router/session_worker.py

# Verifica ASSENZA di --print flag nel session worker
grep -n "\-\-print" src/router/session_worker.py

# Verifica PRESENZA di --print flag nel batch worker
grep -n "\-\-print" src/router/worker_client.py
```

**Verifica CLAIM-03, CLAIM-04**

### Step 3: Verifica message bus sessioni

```bash
# Endpoint /sessions/send
grep -n "sessions/send\|_send_session_message\|SessionMessage" src/router/server.py

# Message model
grep -n "class SessionMessage\|direction\|role\|content\|seq" src/router/models.py

# Worker chiama endpoint
grep -n "sessions/send\|_send_session_message" src/router/session_worker.py
```

**Verifica CLAIM-05**

### Step 4: Verifica gap Task model

```bash
# Tutti i campi del Task model
grep -A 30 "class Task\b\|class TaskCreate" src/router/models.py

# Verifica ASSENZA campo result/output
grep -n "result\|output" src/router/models.py

# Endpoint /tasks/complete — cosa accetta
grep -A 20 "_handle_task_complete\|tasks/complete" src/router/server.py
```

**Verifica CLAIM-06, CLAIM-07**

### Step 5: Verifica comms.py e BOSS

```bash
# Intero file (solo 74 righe)
cat -n src/router/comms.py

# Cerca qualsiasi accesso a result/output
grep -n "result\|output\|payload" src/router/comms.py
```

**Verifica CLAIM-08**

### Step 6: Verifica assenza integrazione esterna

```bash
# Nessun riferimento a iTerm2/AppleScript/MCP nel codice
grep -ri "it2\|applescript\|osascript\|iterm\|mcp" src/

# CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS nel codice vs env
grep -rn "AGENT_TEAMS" src/
grep -rn "AGENT_TEAMS" deploy/
```

**Verifica CLAIM-10, CLAIM-11**

### Step 7: Verifica domanda critica

Leggi il metodo `_tmux_send_text()` in `session_worker.py` e rispondi:

> **Esiste QUALSIASI filtro, validazione, o restrizione su cosa viene inviato alla sessione tmux?**
> Se no, conferma che il session worker puo' inviare `/gsd:execute-phase`, `/speckit.implement`, o qualsiasi altro comando interattivo.

---

## 7. Piano di implementazione — v1.3 "Cross-Repo Orchestration" (RIVISTO)

> Piano aggiornato dopo cross-verifica Codex. Incorpora 4 correzioni architetturali chiave.

### 7.0 Correzioni architetturali da cross-verifica

| # | Errore originale | Correzione Codex | Impatto sul piano |
|---|-----------------|------------------|-------------------|
| C1 | "Worker non inviano result" | **Worker GIA' inviano result** (`worker_client.py:253`, `session_worker.py:309`). Il gap e' solo server-side persistence. | Fase 14 ridotta: nessuna modifica ai worker |
| C2 | "Servono nuove tabelle thread + engine" | **dependency.py** (`dependency.py:55`, `:112`) ha gia' unblocking automatico via `depends_on`. Riutilizzare. | Fase 15 semplificata: thread come wrapper su Task esistenti |
| C3 | "meshctl in `src/router/meshctl.py`" | CLI e' a `src/meshctl.py` (root, non sotto router) | Path corretto |
| C4 | "GET /tasks/{id} esiste" | **NON esiste.** Solo health, workers, sessions hanno GET. | Aggiunto come deliverable Fase 14 |

### 7.1 Obiettivo milestone

> Permettere all'utente di orchestrare thread di lavoro cross-repo e multi-CLI da un singolo punto di controllo, eliminando il copia-incolla manuale e tracciando contesto e risultati in modo persistente.

### 7.2 Fasi proposte (riviste)

#### Fase 14: Result Persistence + Read Path

**Goal**: Il router persiste i result che i worker gia' inviano, e li rende leggibili.

**Prerequisiti verificati**:
- Batch worker GIA' invia `{"output": "...", "exit_code": N}` a `worker_client.py:253`
- Session worker GIA' invia `{"interactive_session": true, "session_id": "...", "final_snapshot": "..."}` a `session_worker.py:309`
- Il server ignora questi payload — servono solo modifiche router-side

**Modifiche**:

| File | Modifica | LOC stimate |
|------|----------|-------------|
| `src/router/models.py` | Campo `result: dict[str, Any] \| None = None` su Task | ~5 |
| `src/router/db.py` | Colonna `result_json TEXT` in tasks + migration backward-compatible | ~30 |
| `src/router/db.py` | Metodo `persist_result(task_id, result_json)` richiamabile in transazione | ~15 |
| `src/router/server.py` | `_handle_task_complete()`: estrarre `result` dal body, passarlo allo scheduler | ~15 |
| `src/router/scheduler.py` | Persistere result nella **stessa transazione** che cambia stato (complete O review) | ~25 |
| `src/router/server.py` | **Nuovo: `GET /tasks/{id}`** — ritorna task con result | ~30 |
| `src/router/server.py` | **Nuovo: `GET /tasks?status=...`** — lista filtrata per status (NO filtro thread — introdotto in Fase 15) | ~20 |
| `tests/test_result_capture.py` | Test: result persist su complete + review, read path, truncation | ~120 |

**LOC totali**: ~260
**Breaking changes**: 0 (campo result opzionale, colonna aggiunta con DEFAULT NULL)

**Architettura della persistence (corretto dopo cross-verifica round 2)**:
Il path di completamento oggi e': `server.py:_handle_task_complete()` → `scheduler.py` → `db.py`. La transizione di stato (completed o review) avviene nello scheduler (`scheduler.py:218`, `:233`), NON nell'handler HTTP. Quindi `result` va:
1. Estratto dal body in `server.py` (handler HTTP)
2. Passato allo `scheduler` come parametro
3. Persistito in `db.py` nella **stessa transazione** che cambia stato
Questo copre sia il path `completed` che il path `review` (task critici) senza bisogno di handler separati.

**Success criteria**:
1. `POST /tasks/complete {"task_id":"...","worker_id":"...","result":{...}}` persiste result in DB
2. `POST /tasks/complete` senza result continua a funzionare (backward compatible)
3. Result persistito anche su transition a `review` (task critici, `scheduler.py:233`) — stessa transazione
4. `GET /tasks/{id}` ritorna task completo con result
5. `GET /tasks?status=completed` lista task filtrati per status
6. Result > 32KB viene troncato con flag `_truncated: true`; se ancora fuori limite, fallback compatto con `_hard_truncated: true`

#### Fase 15: Thread Model + Cross-Repo Context

**Goal**: Thread come gruppo di task con contesto condiviso cross-repo. Costruito sopra Task + dependency.py esistenti.

**Design decision (raccomandazione Codex)**: Ogni step del thread e' un **normale Task row** con campi aggiuntivi `thread_id` + `step_index` + `repo`. NON creare un engine parallelo — riutilizzare scheduler, FSM, leases, e dependency resolver (`dependency.py:55`).

**Nuovi file**:

| File | Descrizione | LOC stimate |
|------|-------------|-------------|
| `src/router/thread.py` | Thread CRUD: create, get, list, status, context aggregation | ~120 |
| `src/router/session_spawner.py` | **Spawn dinamico**: crea sessioni tmux on-demand per thread step. `POST /sessions/spawn` → `tmux new-window -t mesh -n "{role}" "{cli}"` | ~100 |
| `tests/test_thread.py` | Test thread model, step creation, context propagation | ~150 |
| `tests/test_session_spawner.py` | Test spawn dinamico: creazione, naming, cleanup | ~80 |

**Modifiche a file esistenti**:

| File | Modifica | LOC stimate |
|------|----------|-------------|
| `src/router/models.py` | `ThreadCreate`, `ThreadStep` schemas; `thread_id`, `step_index`, `repo`, `role` su Task | ~35 |
| `src/router/db.py` | Tabella `threads` (id, name, status, created_at) + colonne thread su tasks | ~50 |
| `src/router/server.py` | `POST /threads`, `GET /threads/{id}`, `GET /threads/{id}/context`, `GET /tasks?thread_id=...`, `POST /sessions/spawn` | ~80 |
| `src/router/scheduler.py` | Al complete di uno step: iniettare result come contesto nello step successivo + trigger spawn dello step successivo | ~40 |
| `src/router/thread.py` | `get_aggregated_context(thread_id)`: legge result di tutti gli step completati dal DB | ~30 |
| `src/meshctl.py` | `meshctl thread create/add-step/status/context` comandi | ~60 |

**LOC totali**: ~745

**Decisioni architetturali (v5)**:
- `comms.py` resta policy engine stateless. Lettura result in `thread.py`.
- Filtro `GET /tasks?thread_id=...` introdotto in questa fase (non Fase 14).
- **Spawn dinamico**: il router crea sessioni tmux on-demand quando un thread step viene attivato. I worker systemd statici restano come fallback per batch/unattended. Il campo `role` (president/boss/worker) determina l'ordine di spawn e il contesto iniziale.
- **iTerm2 visibilita'**: con `tmux -CC`, ogni sessione spawnata appare come tab/pane nativo in iTerm2.

**Success criteria**:
1. `meshctl thread create --name "monitoring-hl"` crea thread
2. `meshctl thread add-step --thread "monitoring-hl" --repo hyperliquid-node --cli claude --role president --prompt "..."` aggiunge step come Task
3. Quando lo step diventa attivo, il router spawna una sessione tmux interattiva per la CLI specificata
4. Step usano `depends_on` esistente — dependency.py li sblocca automaticamente
5. Al complete di step N, `result` di step N viene iniettato come contesto in `payload` di step N+1
6. `meshctl thread context {name}` mostra result aggregati di tutti gli step completati
7. `meshctl thread status {name}` mostra tabella con stato per step
8. In iTerm2 con `tmux -CC`, ogni sessione attiva e' visibile come pane/tab nativo

#### Fase 16: Aggregator + Error Handling

**Goal**: Aggregazione automatica dei risultati e gestione errori nei thread.

**Nuovi file**:

| File | Descrizione | LOC stimate |
|------|-------------|-------------|
| `src/router/aggregator.py` | Fan-in: legge result del thread, produce summary, confidence | ~120 |
| `tests/test_aggregator.py` | Test aggregator | ~80 |
| `tests/test_thread_e2e.py` | E2E: thread 3 step cross-repo end-to-end | ~120 |

**Modifiche a file esistenti**:

| File | Modifica | LOC stimate |
|------|----------|-------------|
| `src/router/thread.py` | `on_failure` per step (skip/retry/abort), thread-level error handling | ~40 |
| `src/meshctl.py` | `meshctl thread update`, output formattato | ~30 |

**LOC totali**: ~390
**Success criteria**:
1. Thread 3-step cross-repo esegue E2E senza copia-incolla
2. Step fallito con `on_failure: retry` viene ri-eseguito (max 3 tentativi)
3. Step fallito con `on_failure: skip` non blocca thread
4. `meshctl thread status` mostra tabella leggibile con risultati per step
5. Audit trail completo in DB: ogni step ha input, output, timestamps, worker, repo

### 7.3 Riepilogo implementazione (rivisto)

| Fase | LOC | Dipende da | Risolve gap | Note |
|------|-----|-----------|-------------|------|
| 14: Result Persistence + Read Path | ~260 | nessuna | G1, G2 | Worker non toccati — solo modifiche router-side |
| 15: Thread + Cross-Repo + Spawn Dinamico | ~745 | Fase 14 | G3, G4, G5, G6 | Riusa dependency.py + spawn tmux on-demand (+180 LOC per session_spawner) |
| 16: Aggregator + Error | ~390 | Fase 15 | G7, UX completa | |
| **TOTALE** | **~1,395** | | **Tutti i gap** | GoBabyGo unico orchestratore, spawn dinamico incluso |

### 7.4 Rischi e mitigazioni (aggiornati con findings Codex)

| Rischio | Severita' | Mitigazione |
|---------|-----------|-------------|
| Result non persistito su transition `review` | **ALTA** | Persistere in scheduler.py nella stessa transazione che cambia stato — copre SIA completed CHE review |
| Result bloat nella tabella tasks | MEDIA | Truncation a 32KB con flag `_truncated`. Se insufficiente, migrazione a tabella separata (non bloccante) |
| Data sensitivity (secrets in output) | **ALTA** | Filtro regex su patterns noti (sk-, ghp_, xoxb-) prima di persistere |
| `depends_on` come JSON text con LIKE | BASSA | OK a scala corrente; se cresce, migrare a tabella join dedicata |
| Schema migration su deploy attivo | MEDIA | `ALTER TABLE ADD COLUMN ... DEFAULT NULL` e' non-blocking in SQLite WAL |
| Thread con step fallito | MEDIA | `on_failure: skip\|retry\|abort` configurabile per step |

---

## 8. Risultati cross-verifica Codex (2026-03-03)

### 8.1 Verdetti

| Claim | Verdetto | Evidenza |
|-------|----------|----------|
| CLAIM-01 (tag v1.2 non esiste) | **VERIFIED** | `git tag --list` → solo v1.0; `git describe` → v1.0-69-gb897022 |
| CLAIM-02 (LOC per file) | **PARTIALLY_CORRECT** | Per-file LOC corretti. Totale src/ = 6,149 (non 4,438). Test = 455 funzioni (non ~441) |
| CLAIM-03 (due tipi worker) | **VERIFIED** | Batch: `--print` a `worker_client.py:229`. Session: tmux a `session_worker.py:337` |
| CLAIM-04 (nessun filtro send-keys) | **VERIFIED** | `_tmux_send_text()` a `:361` invia verbatim. `_deliver_inbound_messages()` a `:529` skip solo empty |
| CLAIM-05 (message bus bidirezionale) | **VERIFIED** | POST `/sessions/send` a `server.py:80/416`. Autoincrement seq a `db.py:128`. Worker emette a `:414`, poll a `:509` |
| CLAIM-06 (Task model no result) | **PARTIALLY_CORRECT** | 24 campi (non 25). Nessun campo result confermato. DB conferma no colonna result a `db.py:28` |
| CLAIM-07 (/tasks/complete solo task_id+worker_id) | **PARTIALLY_CORRECT** | Server legge solo quei 2 campi. **MA worker inviano gia' result**: batch a `:253`, session a `:309` |
| CLAIM-08 (comms.py no result access) | **VERIFIED** | Solo policy checks (can_*, validate_communication) a `comms.py:38` |
| CLAIM-09 (path inesistenti) | **VERIFIED** | aggregator.py, templates/, bridges/ non esistono. bridge/ (singolare) esiste |
| CLAIM-10 (no iterm/mcp in src/) | **PARTIALLY_CORRECT** | Corretto per src/. iTerm2 referenziato in piu' deploy docs (non solo uno) |
| CLAIM-11 (AGENT_TEAMS solo in .env) | **PARTIALLY_CORRECT** | Anche in deploy docs/scripts: `BOOT-ORDER.md:27`, `check-mac-112-cli.sh:58` |

### 8.2 Errori chiave trovati da Codex

1. **Worker gia' inviano result** — il gap e' solo server-side persistence, non end-to-end
2. **Task ha 24 campi**, non 25
3. **GET /tasks/{id} non esiste** — era nel piano come se esistesse gia'
4. **dependency.py esiste** (`:55`, `:112`) — sequencing automatico gia' implementato
5. **meshctl e' a `src/meshctl.py`**, non `src/router/meshctl.py`
6. **Totale src/ e' 6,149 LOC** — il brief contava solo i file elencati

### 8.3 Raccomandazioni Codex incorporate nel piano

1. Persistere result nella stessa transazione che cambia stato (evita completed senza output)
2. Coprire ENTRAMBI i path: completed E review (task critici passano per review, `scheduler.py:233`)
3. Riutilizzare `dependency.py` per step sequencing — non creare engine parallelo
4. Thread step = Task row normale con `thread_id` aggiuntivo
5. Considerare tabella separata `task_results` per evitare bloat
6. Attenzione a data sensitivity: output puo' contenere secrets

### 8.4 Cross-verifica round 2 — incoerenze architetturali (2026-03-03)

5 incoerenze trovate e corrette:

| # | Incoerenza | Correzione |
|---|-----------|------------|
| 1 | `_handle_task_report_review()` citato ma non esiste | Rimosso. Il path review usa lo stesso flusso `/tasks/complete` via scheduler |
| 2 | Result persistence assegnata a server.py ma la transizione stato e' in scheduler.py | Corretto: server estrae result, lo passa a scheduler, scheduler persiste in stessa transazione via db.py |
| 3 | `GET /tasks?thread_id=...` in Fase 14 ma thread_id introdotto in Fase 15 | Spostato: Fase 14 ha solo `GET /tasks?status=...`, filtro thread aggiunto in Fase 15 |
| 4 | comms.py usato per lettura result aggregati | Corretto: result aggregation va in `thread.py`, comms.py resta policy engine stateless |
| 5 | Pre-condizione "decidere task_results separata" incoerente con Fase 14 che gia' sceglie colonna inline | Risolto: decisione chiusa — colonna `result_json TEXT` inline, YAGNI per tabella separata |

---

## 9. Decisioni architetturali v1.3

### 9.1 Router su WS (no VPS)

**Decisione**: Il router gira sulla WS (.111), stessa macchina dei worker.

**Razionale**: La WS e' always-on (Hyperliquid, N8N, Grafana). Il VPS aggiungeva latenza VPN e un punto di failure senza valore pratico per single-operator. Il path multi-machine resta aperto: basta cambiare `MESH_ROUTER_URL` nei `.env` dei worker e fare bind su `0.0.0.0`.

**Impatto deploy**:
- `MESH_ROUTER_URL=http://localhost:8780` in tutti i `.env`
- `mesh-router.service` installato sulla WS (non piu' VPS)
- UFW rules sul VPS non piu' necessarie per porta 8780

### 9.2 Session worker come default (batch = fallback)

**Decisione**: I session worker (tmux interattivi) sono il tipo primario. I batch worker (`--print`) restano come fallback disponibile ma non attivato di default.

**Razionale**: Il caso d'uso dell'utente richiede sessioni interattive (GSD/SpecKit, approval gates, contesto conversazionale). Il batch mode serve solo per task fire-and-forget (test, build, lint).

**Impatto deploy**:
- `mesh-session-worker@{cli}.service`: enabled e started
- `mesh-worker@{cli}.service`: available ma stopped (avviabile on demand)

### 9.3 GoBabyGo unico orchestratore (decisione finale)

**Decisione**: GoBabyGo e' l'unico orchestratore. Claude Code Agent Teams NON viene usato per orchestrazione.

**Razionale — perche' Agent Teams (Opzione B) non funziona**:

1. Agent Teams spawna **solo Claude ↔ Claude**. Non puo' spawnare Codex o Gemini come teammate.
2. Se PRESIDENT = Codex e BOSS = Claude, Agent Teams non puo' gestire questa configurazione.
3. Con due orchestratori (Agent Teams + GoBabyGo) nessuno dei due ha visibilita' completa.
4. Il cross-repo tracking vivrebbe in due posti diversi.

**Come funziona con GoBabyGo unico orchestratore**:

```
Mac iTerm2 + tmux -CC → ssh WS → tmux session "mesh"

Router riceve thread:
  Step 1: role=president, cli=claude  → router spawna tmux window "claude-president"
  Step 2: role=boss, cli=codex        → router spawna tmux window "codex-boss"
  Step 3: role=worker, cli=claude     → router spawna tmux window "claude-worker-1"
  Step 4: role=worker, cli=gemini     → router spawna tmux window "gemini-worker-1"

iTerm2 in tmux -CC mostra tutto come tab/pane nativi.
L'operatore vede tutte le sessioni e puo' intervenire su qualsiasi pane.
PRESIDENT/BOSS/WORKER sono ruoli REALI con comportamento, non solo policy.
```

**Spawn dinamico**: il router crea sessioni tmux on-demand quando un thread step lo richiede. Non servono worker systemd pre-registrati (restano come fallback per batch/unattended).

**Impatto su Fase 15**: +180 LOC per `POST /sessions/spawn` e session spawner logic.

### 9.4 Configurabilita' garantita

Tutte le decisioni sopra sono configurabili on-the-fly via env var, senza modifiche al codice:

| Parametro | Default v1.3 | Per tornare a VPS |
|-----------|-------------|-------------------|
| `MESH_ROUTER_URL` | `http://localhost:8780` | `http://10.0.0.1:8780` |
| `MESH_BIND_HOST` | `127.0.0.1` | `0.0.0.0` |
| `MESH_EXECUTION_MODES` | `session` | `batch` o `session,batch` |

---

## 10. Step parallelo: iTerm2 + tmux setup (indipendente dalla v1.3)

Mentre la v1.3 viene implementata, l'utente puo' migrare da VSCode SSH a iTerm2 + tmux per stabilita' immediata:

1. **SSH config** sul Mac per connessione rapida alla WS
2. **tmux** sulla WS per sessioni persistenti
3. **iTerm2 profiles** con `tmux -CC` per pane nativi
4. **Niente piu' VSCode server overhead**

Questo e' indipendente da GoBabyGo e da' valore immediato.

---

## 10. Fonti

### Documentazione ufficiale
- [Claude Code Agent Teams](https://code.claude.com/docs/en/agent-teams)
- [Claude Code Subagents](https://code.claude.com/docs/en/sub-agents)
- [Codex CLI Multi-Agent](https://developers.openai.com/codex/multi-agent/)
- [Codex CLI Reference](https://developers.openai.com/codex/cli/reference/)
- [Codex as MCP Server (Agents SDK)](https://developers.openai.com/codex/guides/agents-sdk/)
- [Building a C compiler with agent teams](https://www.anthropic.com/engineering/building-c-compiler)

### Bug report rilevanti
- [iTerm2 silent fallback #24301](https://github.com/anthropics/claude-code/issues/24301)
- [teammateMode tmux #24292](https://github.com/anthropics/claude-code/issues/24292)
- [Orphaned panes #24385](https://github.com/anthropics/claude-code/issues/24385)

### File di progetto chiave
- `.planning/PROJECT.md` — value proposition e feature list
- `.planning/ROADMAP.md` — milestone e fasi con stato
- `.planning/STATE.md` — posizione corrente e decisioni
- `TODO.md` — gap analysis originale (2026-02-27)
- `deploy/MAC-112-ITERM2-CLI-SETUP.md` — setup operatore iTerm2
- `deploy/SESSION-FIRST-E2E-RUNBOOK.md` — procedura E2E validation

---

## 11. Prossimi passi

Il brief e' completo e cross-verificato. Le opzioni sono:

1. **Implementare v1.3** con `/gsd:new-milestone` partendo dalla Fase 14 (Result Persistence + Read Path)
2. **Setup iTerm2 + tmux** in parallelo per valore immediato
3. **Entrambi** — iTerm2 setup e' indipendente e non blocca la v1.3

### Pre-condizioni per avvio implementazione

- [x] ~~Decisione su `task_results` tabella separata vs colonna su tasks~~ → **DECISO: colonna `result_json TEXT` inline su tasks** (YAGNI)
- [x] ~~Tag `v1.2` nel repo~~ → **FATTO: `v1.1` @ e52cfd4, `v1.2` @ 768c399** (2026-03-03)
- [x] ~~Architettura deploy~~ → **DECISO: router su WS, session worker default, batch fallback** (sezione 9)
- [ ] Verificare che WS possa eseguire `mesh-router.service` (installare se necessario)
- [ ] Spostare o copiare i file deploy dalla config VPS alla WS

---

*Generato da sessione Claude Code (Opus 4.6), 2026-03-03*
*Cross-verificato da Codex (ChatGPT), 2026-03-03*
*Repo: /media/sam/1TB/gobabygo @ b897022 (master)*
