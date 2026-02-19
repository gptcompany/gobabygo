# Mesh Alert Notification Policy

## Contact Points

Use the existing Discord webhook contact point already configured for infrastructure alerts.

## Routing

| Severity | Behavior | Group Wait | Group Interval |
|----------|----------|------------|----------------|
| critical | Immediate notification | 30s | 5m |
| warning | Grouped notifications | 5m | 10m |

## Alert → Discord Mapping

| Alert | Severity | Discord Channel |
|-------|----------|-----------------|
| MeshRouterDown | critical | #alerts |
| MeshNoData | critical | #alerts |
| MeshWorkerStale | warning | #alerts |
| MeshQueueDepthHigh | warning | #alerts |
| MeshTaskFailureRateHigh | warning | #alerts |

## Silence / Maintenance Windows

For planned maintenance (VPS reboot, deploy):

```bash
# Create silence via Grafana API (4 hour window)
curl -X POST "https://<GRAFANA_URL>/api/alertmanager/grafana/api/v2/silences" \
  -H "Authorization: Bearer <API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{
    "matchers": [{"name": "team", "value": "mesh", "isRegex": false}],
    "startsAt": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'",
    "endsAt": "'$(date -u -d "+4 hours" +%Y-%m-%dT%H:%M:%SZ)'",
    "comment": "Planned maintenance",
    "createdBy": "operator"
  }'
```

## Setup Checklist

1. [ ] Import `mesh-alerts.yaml` into Grafana (Alerting > Import)
2. [ ] Replace `<VICTORIAMETRICS_UID>` with actual datasource UID
3. [ ] Add `scrape-mesh.yaml` job to VictoriaMetrics scrape config
4. [ ] Replace `<VPS_WG_IP>` with actual WireGuard IP
5. [ ] Verify scrape: `curl http://<VPS_WG_IP>:8780/metrics`
6. [ ] Verify alerts appear in Grafana Alerting dashboard
7. [ ] Test Discord notification by temporarily lowering thresholds
