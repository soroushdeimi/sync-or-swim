# Postmortem: Alerts that could not work

- **Date:** 2026-10-12
- **Status:** Resolved
- **Impact:** `ReplicationIOStopped`, `ReplicationSQLStopped` and `WireGuardHandshakeStale` could never fire, and the matching Grafana panels were empty. `BGPSessionDown` did the opposite: it fired for every healthy session.

## Summary

Three alert rules and three dashboard panels read metrics that had no samples in Prometheus. An alert on a missing metric does not error, it just never fires, so everything stayed green. Found while answering "is everything really implemented?" by querying Prometheus for each metric the rules use.

Firing every alert with a real fault then showed a fourth problem: `BGPSessionDown` was already firing for all eight healthy sessions.

## Impact

- A broken replica would not have paged anyone.
- A tunnel with no handshake would not have paged anyone.
- The promtool unit tests passed because they fed the same wrong metric name.
- The exporter test passed because it only looked for the metric name in the response, and the `# HELP` line contains it.

## Timeline

- Earlier: the exporters worker listed the replication metric names as "unverified" in its report. Nobody went back to verify them.
- 2026-10-12: queried `/api/v1/label/__name__/values` for every metric used by the rules. Two names and one whole exporter had no data.
- 2026-10-12: wrote a chaos test per alert. The relay-crash test failed waiting for `BGPSessionDown` to clear, and Prometheus showed it firing on all eight peers while `vtysh` reported every session Established.

## Detection

`count(mysql_slave_status_slave_io_running)` returned no series, while the real names were `mysql_slave_status_replica_io_running` and `..._replica_sql_running`. For WireGuard, the exporter answered 200 with only `HELP`/`TYPE` lines.

## Root cause

0. **Inverted state value:** `frr_exporter` reports `1` for Established and `2` for administratively down. The rule used `!= 2` and the dashboard mapped 2 to "Established", so healthy sessions alerted and showed as "Connect/Active". The task spec that produced the rule said "2 = established" and listed it as provisional.

1. **Metric names:** the rules used the pre-8.0.22 `slave` names. `mysqld_exporter` follows MySQL 8.4 and exposes `replica` names.
2. **WireGuard exporter:** the image runs as an unprivileged user. `cap_add: NET_ADMIN` gives that capability to root only, so `wg show` failed with "Operation not permitted" and the exporter exported nothing.
3. **Tests:** every check was about the shape of the data (names, 200 OK), none asked whether a rule can actually fire.

## Resolution

- Correct names in the rules, dashboard, promtool tests and runbooks. WireGuard exporter runs as root.
- New test: every metric referenced by a rule or dashboard panel must have samples in Prometheus (red with the old names, green now).
- New test: a healthy lab must have no active alerts, pending included. Pending matters: the first red check passed because the wrong rule was still inside its `for:` window. With pending counted, the old rule fails it.
- promtool tests for BGP and BFD now cover both the healthy and the failing value (they had none).
- Chaos scenarios 9 and 10 fire every other alert with a real fault and require it to clear after recovery.
- New test: stop the replica's IO or SQL thread, assert the alert fires, restart it, assert it clears. Proven to fail with the old metric name.
- The exporter test now requires real handshake samples with a value above zero.

## What went well

- The fix was small once the cause was clear, and the lab itself was never affected.

## What went wrong

- A report line saying "unverified" was not tracked to closure.
- Tests asserted that something exists, not that it works.
- A "provisional" value in a task spec became a production rule without being checked.

## Action items

- [x] Metric-existence test for rules and dashboard.
- [x] Behaviour tests for the replication alerts.
- [x] Fire every alert with a real fault. `RouteFlapping` is the exception: dampening prevents the flapping it detects, so only its rule logic is covered by promtool.
- [ ] Verify a metric's meaning from the exporter's `# HELP` text before writing a rule on it.
