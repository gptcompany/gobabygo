# Phase 21: Shared Memory Layer (OpenMemory via MCP) — Context

## Phase Goal

Add organization-wide shared memory without changing operational authority.

## Source

- `.planning/milestones/v1.4-ROADMAP.md` — Phase 21 section
- `.planning/milestones/v1.4-REQUIREMENTS.md` — MEM-01 through MEM-04

## Requirements

- **MEM-01**: OpenMemory runs as a separate self-hosted memory service
- **MEM-02**: OpenMemory is consumed through its native MCP server as a shared memory layer
- **MEM-03**: OpenMemory stores context and recall only; it is not a message bus or source of operational truth
- **MEM-04**: Router operations remain correct when OpenMemory is unavailable

## Current State Analysis

### Topology Config (deploy/topology.v1.4.example.yml)

Memory section already defined:
```yaml
memory:
  provider: openmemory_mcp
  mcp_server_name: openmemory
  write_policy: best_effort
  required: false
```

### Router Independence

Router has no dependency on any external memory service. All operational state is in SQLite. This must remain true after Phase 21.

### MCP Ecosystem

OpenMemory provides a native MCP server. Claude Code and other MCP-aware CLI clients can connect to it directly via their MCP configuration. The router does not need to proxy MCP calls.

## Open Design Questions (for PLAN.md)

1. **Deployment host**: muletto? workstation? separate container?
2. **MCP client scope**: PRESIDENT_GLOBAL only? Every worker? Only session workers?
3. **Write triggers**: who writes to OpenMemory and when? (thread completion, handoff, decisions)
4. **Memory categories**: decisions, closed-thread summaries, recurring issues, repo dependency context — how structured?
5. **Read patterns**: on-demand query or proactive context injection?

## Gaps to Close

| # | Gap | Where to Fix |
|---|-----|-------------|
| G1 | No OpenMemory deployment | Deploy as self-hosted service (Docker or systemd) |
| G2 | No MCP server config | Add to `.claude.json` or per-worker MCP config |
| G3 | No write policy implementation | Define when/what gets written to memory |
| G4 | No graceful degradation test | Verify router operates correctly when OpenMemory is down |

## Architectural Constraints

1. **Sidecar only**: OpenMemory is not a message bus, scheduler, or task authority
2. **Best-effort writes**: memory writes never block router dispatch/ack/complete
3. **Required: false**: system operates correctly without OpenMemory
4. **No router code changes for MCP**: MCP connectivity is a client-side concern (CLI tools), not a router concern
5. **Depends on Phase 20**: memory can store handoff summaries and cross-repo decisions

## Files Likely to Modify

| File | Changes |
|------|---------|
| deploy/ | OpenMemory deployment config, docker-compose or systemd unit |
| MCP config | Add openmemory MCP server to relevant client configs |
| docs/ | Memory write/read policy documentation |

## Needs Breakdown

This phase requires a detailed breakdown document before planning can proceed. Key decisions about deployment location, MCP client scope, and write triggers need to be resolved first.
