# Phase 21: Shared Memory Layer — Decisions

**Decided:** 2026-03-05
**Method:** Codex discussion → KISS v1

| # | Question | Decision | Rationale |
|---|----------|----------|-----------|
| D1 | Deployment host | Muletto standalone Docker | Independent lifecycle from router. Always-on host. |
| D2 | MCP client scope | Only BOSS/PRESIDENT | Controlled writes, clean signal. Expand later if needed. |
| D3 | Write triggers | Manual only | No automation in Phase 21 v1. Operator decides what's worth storing. |
| D4 | Read patterns | On-demand query | Agent explicitly calls `openmemory_query`. No proactive injection. |
| D5 | Embedding provider | Ollama locale (primary), OpenAI fallback | Modello embeddings già disponibile su muletto. Zero cost. OpenAI come backup. |
| D6 | Network/security | LAN/VPN only | No public exposure. Network isolation is sufficient for v1. |
| D7 | MEM-04 acceptance | Smoke test: OpenMemory down, router operates normally | MCP is client-side only, so degradation is architectural. Test confirms. |
