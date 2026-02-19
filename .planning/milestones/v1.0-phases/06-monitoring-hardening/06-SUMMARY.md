# Phase 06 Summary: Monitoring & Hardening

## Completed: 2026-02-19

### Plans Executed
| Plan | Description | Tests | LOC (prod) | LOC (test) |
|------|-------------|-------|-----------|-----------|
| 06-01 | Prometheus metrics export | 14 | ~170 | ~270 |
| 06-02 | Alert rules + scrape config | 13 | config files | ~120 |
| **Total** | | **27** | **~170** | **~390** |

### New Files
- `src/router/metrics.py` — MeshMetrics collector (15 metric families)
- `deploy/monitoring/mesh-alerts.yaml` — 5 Grafana Cloud alert rules
- `deploy/monitoring/scrape-mesh.yaml` — VictoriaMetrics scrape config
- `deploy/monitoring/notification-policy.md` — Discord notification routing
- `tests/router/test_metrics.py` — Metrics collector + endpoint tests (14 tests)
- `tests/test_monitoring_config.py` — Alert rules + config validation (13 tests)

### Modified Files
- `src/router/db.py` — Added count_all_task_statuses (GROUP BY), count_dead_letters
- `src/router/server.py` — Added /metrics endpoint, task duration observation
- `pyproject.toml` — Added prometheus-client>=0.20.0
- `tests/router/test_server.py` — Updated fixture with MeshMetrics

### Metrics Inventory (15 families)
- Gauges: router_up, tasks_queued, tasks_running, tasks_review, queue_depth, workers_total, workers_idle, workers_busy, workers_stale, uptime_seconds
- Totals: tasks_completed_total, tasks_failed_total, tasks_timeout_total, dead_letters_total
- Summary: task_duration_seconds (p50, p95, p99)

### Alert Rules
| Rule | Severity | For |
|------|----------|-----|
| MeshRouterDown | critical | 1m |
| MeshNoData | critical | 5m |
| MeshWorkerStale | warning | 2m |
| MeshQueueDepthHigh | warning | 5m |
| MeshTaskFailureRateHigh | warning | 10m |

### Architecture Decisions Made
1. **prometheus-client library** (not manual text format) — OpenMetrics compliant
2. **Summary for task duration** (not SQLite on-the-fly percentile) — efficient, standard
3. **No pre-calculated success_rate Gauge** — anti-pattern; use PromQL increase() in Grafana
4. **Single GROUP BY query** for task status counts — efficient scrape (3 DB queries total)
5. **Grafana Cloud alerting** with Discord notifications — consistent with existing 7 rules
6. **No auth on /metrics** — WireGuard-only network, same as /health

### Confidence Gates
- Context gate: 81/100 (iterate: fixed P95 approach, removed success_rate Gauge)
- Plan gate: 92/100 (auto-approve after fixing redundant queries)
- Implementation gate: 92/100 (auto-approve)

### Test Suite
- Phase 06 new tests: 27
- Total test suite: 291 (zero regressions)

### MONITOR-01 Requirement
- [x] MeshRouterDown alert
- [x] MeshWorkerStale alert
- [x] MeshQueueDepthHigh alert
- [x] Grafana Cloud "no data" alert
- [x] Metrics export (15 families via /metrics)
