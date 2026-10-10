# Postmortem: Sidecars Left in Dead Network Namespace After Node Restart

- **Date:** 2026-10-10
- **Status:** Resolved
- **Impact:** Prometheus exporters, Loki log collectors, and MySQL sidecars unreachable after node restart.

## Summary

Restarting a node container via `docker restart <node>` creates a new network namespace for the node, but dependent sidecars configured with `network_mode: container:<node>` remain trapped in the old, destroyed network namespace. Sidecars appeared running in Docker process listings, yet could not communicate on the node management IP.

## Impact

Prometheus scraping failed for all node exporters (`node_exporter`, `mysqld_exporter`, `frr_exporter`, `wireguard_exporter`). Route monitoring and WireGuard log tailing stopped forwarding events to Loki.

## Timeline

- 2026-10-10: Observed exporter timeouts and dead management listener endpoints after testing `docker restart mysql-1`.
- 2026-10-10 10:40:02 +0330: Implemented sidecar healer and `sidecars` role in commit `ab0cd8f`.
- 2026-10-10 10:40:11 +0330: Documented network namespace failure dynamics in `docs/notes.md` (`825a50d`).

## Detection

After running `docker restart mysql-1`, `docker ps` reported node and sidecars with status `Up`. However, metrics endpoints returned connection timeouts:
```bash
curl -s -m 2 -o /dev/null -w '%{http_code}' http://172.31.100.11:9100/metrics
# 000: nothing listening on the node's mgmt IP
```
The exporters only answer inside the node's namespace, so a namespace mismatch was the obvious suspect. Stopping the healer and restarting the node reproduced it every time.

## Root cause

Docker creates a fresh network namespace upon container restart, allocating new virtual network interface pairs. Sidecar containers attached via `container:<name>` hold open file descriptors to the previous network namespace. The sidecars do not automatically restart on parent container restarts, and their container IDs do not change, rendering naive ID checks ineffective.

## Resolution

1. Deployed an automated sidecar healer container (`sidecar-healer`) running alongside Docker:
   - Listens to Docker engine socket events (`docker events --filter type=container --filter event=start`).
   - On node startup, queries all containers labeled `sos.node=<name>` and executes `docker restart`.
2. Created an Ansible `sidecars` role that inspects `.HostConfig.NetworkMode` against the actual node container IDs and recreates stale sidecar compose projects when nodes are recreated.
3. Added automated regression assertions in integration tests (`tests/integration/test_lab.py`).

## What went well

- The full integration run caught it: every exporter on mysql-1 went down right after the restart test.
- The healer service recovered sidecars automatically without requiring container recreation or playbook runs.

## What went wrong

- Initial exporter recreation logic relied on building custom JSON strings in Ansible shell tasks, which failed whenever a stale container was detected.

## Action items

- [x] Create lightweight event-driven sidecar healer daemon (`ab0cd8f`).
- [x] Implement generic `sidecars` role replacing bespoke role tasks (`ab0cd8f`).
- [x] Document network namespace architecture in ADR 0006 (`docs/adr/0006-sidecars-in-node-namespaces-and-healer.md`).
