# Postmortem: Alerts on metrics that never had data

- **Date:** 2026-10-12
- **Status:** Resolved
- **Impact:** `ReplicationIOStopped`, `ReplicationSQLStopped` and `WireGuardHandshakeStale` could never fire, and the matching Grafana panels were empty.

## Summary

Three alert rules and three dashboard panels read metrics that had no samples in Prometheus. An alert on a missing metric does not error, it just never fires, so everything stayed green. Found while answering "is everything really implemented?" by querying Prometheus for each metric the rules use.

## Impact

- A broken replica would not have paged anyone.
- A tunnel with no handshake would not have paged anyone.
- The promtool unit tests passed because they fed the same wrong metric name.
- The exporter test passed because it only looked for the metric name in the response, and the `# HELP` line contains it.

## Timeline

- Earlier: the exporters worker listed the replication metric names as "unverified" in its report. Nobody went back to verify them.
- 2026-10-12: queried `/api/v1/label/__name__/values` for every metric used by the rules. Two names and one whole exporter had no data.

## Detection

`count(mysql_slave_status_slave_io_running)` returned no series, while the real names were `mysql_slave_status_replica_io_running` and `..._replica_sql_running`. For WireGuard, the exporter answered 200 with only `HELP`/`TYPE` lines.

## Root cause

1. **Metric names:** the rules used the pre-8.0.22 `slave` names. `mysqld_exporter` follows MySQL 8.4 and exposes `replica` names.
2. **WireGuard exporter:** the image runs as an unprivileged user. `cap_add: NET_ADMIN` gives that capability to root only, so `wg show` failed with "Operation not permitted" and the exporter exported nothing.
3. **Tests:** every check was about the shape of the data (names, 200 OK), none asked whether a rule can actually fire.

## Resolution

- Correct names in the rules, dashboard, promtool tests and runbooks. WireGuard exporter runs as root.
- New test: every metric referenced by a rule or dashboard panel must have samples in Prometheus (red with the old names, green now).
- New test: stop the replica's IO or SQL thread, assert the alert fires, restart it, assert it clears. Proven to fail with the old metric name.
- The exporter test now requires real handshake samples with a value above zero.

## What went well

- The fix was small once the cause was clear, and the lab itself was never affected.

## What went wrong

- A report line saying "unverified" was not tracked to closure.
- Tests asserted that something exists, not that it works.

## Action items

- [x] Metric-existence test for rules and dashboard.
- [x] Behaviour tests for the replication alerts.
- [ ] Do the same for the other alerts that are hard to trigger (`WireGuardHandshakeStale` needs a stale handshake of over 180 s).
