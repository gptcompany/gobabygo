# Phase 06: Monitoring & Hardening — Context

## Goal

Mesh-specific Prometheus metrics export and Grafana Cloud alert rules. Closes the last gap identified in cross-validation: "monitoring alerts assenti".

## Requirement

**MONITOR-01**: Alert rules for MeshRouterDown, MeshWorkerStale, MeshQueueDepthHigh + Grafana Cloud "no data" alert. Metrics export: queue depth, task success rate, p95 duration, stale workers, retry rate.

## Architecture Decisions

### Prometheus Client Library
- **Decision**: Use `prometheus-client` (standard Python library)
- **Rationale**: OpenMetrics compliant, standard `/metrics` endpoint, native Gauge/Counter/Summary types
- **Impact**: New dependency in `pyproject.toml`

### Metrics Endpoint
- **Decision**: Add `GET /metrics` to existing HTTP server (server.py)
- **No auth** on `/metrics` (same as `/health` — WireGuard-only network)
- **Format**: Prometheus text exposition format (text/plain; version=0.004)

### P95 Duration
- **Decision**: Use `prometheus-client` Summary with quantiles (p50, p95, p99)
- **Rationale**: Standard Prometheus pattern; call `observe(duration)` at task completion time in the server. Efficient O(1) per observation, no SQL query at scrape time.
- **Trade-off**: Quantile data resets on restart, but this is acceptable for a monitoring metric (not billing-critical). Summary accumulates in-memory.
- **Implementation**: In server.py task completion handler, calculate `duration = updated_at - created_at` and call `summary.observe(duration_seconds)`

### Alert Target
- **Decision**: Grafana Cloud alerting (provisioning YAML format)
- **Rationale**: Existing 7 alert rules already on Grafana Cloud → Discord. Keep consistency.
- **Delivery**: Alert rules as Grafana provisioning YAML + VictoriaMetrics scrape job config

### Scrape Configuration
- **Decision**: VictoriaMetrics scrape config snippet (Prometheus-compatible)
- **Target**: VPS mesh-router via WireGuard IP (e.g., 10.x.x.1:8780)
- **Interval**: 15s (default Prometheus interval)

## Metrics Inventory

### Gauges (point-in-time values)
| Metric | Description | Source |
|--------|-------------|--------|
| `mesh_router_up` | 1 if router healthy | Always 1 when serving |
| `mesh_tasks_queued` | Tasks in queued state | `COUNT(status='queued')` |
| `mesh_tasks_running` | Tasks in running state | `COUNT(status='running')` |
| `mesh_tasks_review` | Tasks in review state | `COUNT(status='review')` |
| `mesh_queue_depth` | Total pending tasks (queued + assigned + blocked) | SQL COUNT |
| `mesh_workers_total` | Total registered workers | `COUNT(*)` on workers |
| `mesh_workers_idle` | Idle workers | `COUNT(status='idle')` |
| `mesh_workers_busy` | Busy workers | `COUNT(status='busy')` |
| `mesh_workers_stale` | Stale workers | `COUNT(stale_since IS NOT NULL)` |
| `mesh_task_retries_total` | Total retry attempts | `SUM(attempt - 1)` on tasks |
| `mesh_uptime_seconds` | Router uptime | `time.time() - start_time` |

### Counters (monotonically increasing totals)
| Metric | Description | Source |
|--------|-------------|--------|
| `mesh_tasks_completed_total` | Total completed tasks | `COUNT(status='completed')` |
| `mesh_tasks_failed_total` | Total failed tasks | `COUNT(status='failed')` |
| `mesh_tasks_timeout_total` | Total timed out tasks | `COUNT(status='timeout')` |
| `mesh_dead_letters_total` | Dead letter events | `COUNT(*)` on dead_letter_events |
| `mesh_events_total` | Total task events | `COUNT(*)` on task_events |

### Summary (quantile distribution)
| Metric | Description | Source |
|--------|-------------|--------|
| `mesh_task_duration_seconds` | Task duration distribution (p50, p95, p99) | `Summary.observe()` at task completion |

**Note on success_rate**: Removed as pre-calculated Gauge (anti-pattern). Use PromQL in Grafana: `rate(mesh_tasks_completed_total[5m]) / (rate(mesh_tasks_completed_total[5m]) + rate(mesh_tasks_failed_total[5m]))` for windowed analysis.

## Alert Rules

### MeshRouterDown
- **Condition**: `mesh_router_up == 0` OR `absent(mesh_router_up)` for > 1m
- **Severity**: critical
- **Action**: Discord notification

### MeshWorkerStale
- **Condition**: `mesh_workers_stale > 0` for > 2m
- **Severity**: warning
- **Action**: Discord notification

### MeshQueueDepthHigh
- **Condition**: `mesh_queue_depth > 50` for > 5m
- **Severity**: warning
- **Action**: Discord notification

### MeshNoData
- **Condition**: `absent(mesh_router_up)` for > 5m
- **Severity**: critical
- **Action**: Discord notification (Grafana Cloud "no data" alert)

### MeshTaskFailureRateHigh
- **Condition**: `rate(mesh_tasks_failed_total[5m]) / (rate(mesh_tasks_completed_total[5m]) + rate(mesh_tasks_failed_total[5m])) > 0.05` for > 10m
- **Severity**: warning
- **Action**: Discord notification (SLO violation: success rate < 95% over 5m window)

## Deliverables

### Plan 06-01: Alert Rules + Grafana Cloud Config
- Grafana alert rule provisioning YAML (5 rules)
- VictoriaMetrics scrape job config snippet
- Documentation for adding to existing monitoring stack

### Plan 06-02: Metrics Export
- `src/router/metrics.py` — MetricsCollector class
- Integration into `server.py` `/metrics` endpoint
- `prometheus-client` dependency
- Tests (unit + integration)

## Existing Infrastructure Reference

- **VictoriaMetrics**: Workstation (192.168.1.111), Prometheus-compatible
- **Grafana**: Workstation, connected to Grafana Cloud
- **Alerting**: 7 existing rules (CPU/Memory/Disk/NodeDown/ProcessDown) → Discord
- **Netdata**: System-level monitoring (not relevant for mesh metrics)
- **Router**: VPS (WireGuard IP), port 8780

## Test Strategy

- Unit tests: MetricsCollector queries return expected values
- Integration tests: `/metrics` endpoint returns valid Prometheus format
- Alert rule tests: Validate YAML structure and expressions
- No E2E with real Grafana (infrastructure test, not code test)
