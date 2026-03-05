# Phase 21: Shared Memory Layer — Discussion Questions

**Phase Goal:** Add organization-wide shared memory without changing operational authority.

**Requirements:** MEM-01 (self-hosted), MEM-02 (MCP native), MEM-03 (context only, not message bus), MEM-04 (router works without it)

---

## Research Findings: CaviraOSS/OpenMemory

- **Repo**: https://github.com/CaviraOSS/OpenMemory
- **Backend**: Node.js, runs on port 8080
- **MCP endpoint**: HTTP transport at `http://HOST:8080/mcp`
- **MCP tools**: `openmemory_query`, `openmemory_store`, `openmemory_list`, `openmemory_get`, `openmemory_reinforce`
- **Storage**: SQLite default (zero-config), optional Postgres
- **Embeddings**: OpenAI, Gemini, Ollama, or synthetic fallback
- **Deploy**: `docker compose up --build -d` (official Dockerfile in repo)
- **Claude Code connection**: `claude mcp add --transport http openmemory http://localhost:8080/mcp`
- **Status**: Project in active rewrite, MCP interface stable. Apache 2.0 license.

---

## Q1: Deployment Host

Where should OpenMemory run?

| Option | Pros | Cons |
|--------|------|------|
| **A) Muletto** (192.168.1.100) | Already hosts Synapse, Gateway, CF Tunnel. Lightweight addition. Always-on. | Another service on the gateway box. |
| **B) Workstation** (192.168.1.111) | More resources. Co-located with N8N, Grafana, Docker fleet. | Already under load. Not always-on if desktop sleeps. |
| **C) Same compose as router** | Simplest deploy. Single `docker compose up`. Router and memory co-located. | Couples deployment lifecycle. If router redeploys, memory restarts too. |
| **D) Standalone Docker on muletto** | Separate compose file. Independent lifecycle from router. | Extra compose file to manage. |

**Topology config already defines**: `memory.required: false`, so any host works as long as MCP clients can reach it over the network.

**Key constraint**: Workers on mac-112 and ws-111 need to reach OpenMemory's MCP endpoint over the VPN/LAN.

---

## Q2: Embedding Provider

Which embedding model should OpenMemory use?

| Option | Pros | Cons |
|--------|------|------|
| **A) OpenAI** | OPENAI_API_KEY already in SSOT. Best quality embeddings. | Requires internet. Cost per embedding call. |
| **B) Ollama local** | Zero cost. Fully offline. Privacy. | Needs Ollama running + model downloaded. Extra resource usage. |
| **C) Synthetic fallback** | Zero config. No external deps. | Lower quality recall. May not be useful for semantic search. |

**Note**: OpenMemory supports hot-swapping providers via env config. Decision is not permanent.

---

## Q3: MCP Client Scope

Who connects to OpenMemory via MCP?

| Option | Pros | Cons |
|--------|------|------|
| **A) Only operator sessions** (BOSS/PRESIDENT) | Controlled writes. Operator decides what's worth remembering. Clean signal. | Workers don't benefit from shared memory directly. |
| **B) All MCP clients** (every worker + operator) | Maximum context sharing. Every agent can query/store. | Noisy writes. Potential garbage accumulation. Needs write discipline. |
| **C) Only PRESIDENT_GLOBAL** | Single writer. Maximum control. Consistent memory quality. | Only one agent benefits from read. Others need manual context. |
| **D) Tiered: operator writes, all read** | Operator curates memory. Workers can query for context. | More complex MCP config (read-only vs read-write). May not be supported natively. |

**Current MCP config model**: Each worker/session has its own `.claude.json` or env-based MCP config. Adding OpenMemory means adding one MCP server entry per client that needs access.

---

## Q4: Write Triggers

When does data get written to OpenMemory?

| Trigger | What gets stored | Who writes |
|---------|-----------------|------------|
| **Thread completion** | Thread summary, outcome, decisions made | Operator or PRESIDENT_GLOBAL |
| **Cross-repo handoff** | Handoff packet summary (from Phase 20) | PRESIDENT_GLOBAL |
| **Key decisions** | Architecture decisions, rejected alternatives | Operator manually |
| **Recurring issues** | Patterns, common failures, workarounds | Operator manually |
| **Repo dependency context** | Which repos depend on which, API contracts | Operator or automated |
| **Session end** | Session summary with key learnings | Session worker (if scope allows) |

**Sub-questions:**
- Should writes be **manual only** (operator explicitly calls `openmemory_store`) or **automated** (hooks/triggers after events)?
- If automated, should the matrix-bridge or a new script trigger writes?
- Should there be a **memory review** step before storing (like a staging area)?

---

## Q5: Memory Categories / Structure

How should memories be organized?

| Category | Example content | Access pattern |
|----------|----------------|----------------|
| `decisions` | "Chose SQLite over Postgres for router because single-writer" | Query when making similar decisions |
| `thread_summaries` | "Thread #42: migrated auth from JWT to API key, 3 steps" | Query when starting related work |
| `recurring_issues` | "Docker DNS resolution fails on first boot, needs restart" | Query when debugging |
| `repo_context` | "rektslug depends on ccxt-data-pipeline for price feeds" | Query during cross-repo handoff |
| `handoff_history` | "Handoff #5: rektslug->ccxt, topic: API schema change" | Query during future handoffs |

**Sub-questions:**
- Use OpenMemory's native categorization or custom metadata tags?
- Should categories map to MCP tool parameters (e.g., `openmemory_store` with `category` field)?
- Retention policy: keep everything forever or decay old memories?

---

## Q6: Read Patterns

How should agents consume shared memory?

| Pattern | Description | When |
|---------|-------------|------|
| **A) On-demand query** | Agent explicitly calls `openmemory_query` when it needs context | During planning, debugging, handoffs |
| **B) Proactive injection** | System pre-fetches relevant memories and injects into prompt | At session start, before each task |
| **C) Hybrid** | Auto-inject for cross-repo tasks, on-demand for everything else | Cross-repo handoffs get auto-context |

**Sub-questions:**
- Should `meshctl` have a `memory` subcommand for operators to query/browse?
- Should the matrix-bridge include memory context in notifications?

---

## Q7: Graceful Degradation

How to verify MEM-04 (router works without OpenMemory)?

| Test | What it verifies |
|------|-----------------|
| Stop OpenMemory container, run full task lifecycle | Router dispatch/ack/complete unaffected |
| MCP client timeout handling | Client doesn't hang if OpenMemory is down |
| Memory writes fail silently | No error propagation to task execution |

**Note**: Since OpenMemory is purely client-side (MCP), and the router has no dependency on it, MEM-04 is architecturally guaranteed. The test verifies the MCP client config handles unavailability gracefully.

---

## Q8: Network / Security

| Question | Options |
|----------|---------|
| Expose OpenMemory externally? | No (LAN/VPN only) vs. Yes (behind Cloudflare Access) |
| Authentication? | OpenMemory has no built-in auth. Rely on network isolation? Add reverse proxy? |
| Data sensitivity? | Memories may contain architectural decisions, repo names, patterns. Acceptable risk on LAN? |

---

## Summary: Decisions Needed Before Planning

| # | Question | Impact |
|---|----------|--------|
| Q1 | Deployment host | Determines compose file location, network config |
| Q2 | Embedding provider | Determines env vars, external dependencies |
| Q3 | MCP client scope | Determines how many MCP configs to update |
| Q4 | Write triggers | Determines if we need automation or manual-only |
| Q5 | Memory categories | Determines data structure and query patterns |
| Q6 | Read patterns | Determines integration depth |
| Q7 | Degradation testing | Determines test scope |
| Q8 | Network/security | Determines exposure and auth model |

**Recommendation for v1 (KISS):** Start with the simplest viable setup:
- Deploy on muletto (standalone Docker compose)
- OpenAI embeddings (key already available)
- Only operator MCP access (manual writes)
- On-demand reads only
- LAN-only, no auth (network isolation)
- Verify degradation with one smoke test
