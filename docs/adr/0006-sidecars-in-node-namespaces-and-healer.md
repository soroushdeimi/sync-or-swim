# 6. Sidecars in Node Namespaces and Sidecar Healer

Date: 2026-10-10

## Status

Accepted

## Context

Each lab node requires multiple ancillary services:
- Exporters: `node_exporter`, `mysqld_exporter`, `frr_exporter`, `wireguard_exporter`, and `blackbox_exporter`.
- Log collectors: `route-monitor` (streaming `ip monitor route link`) and `wg-watch` (monitoring WireGuard handshakes).
- Database service: `mysql:8.4` server container.

Two architectural models were considered:
1. "Fat containers": Packing MySQL, exporters, monitoring scripts, and routing daemons into a single monolithic Docker image running systemd or supervisord.
2. Sidecar containers running inside each node's network namespace (`network_mode: "container:<node>"`).

## Decision

We chose **sidecar containers joined to the node's network namespace** accompanied by an automated **sidecar healer daemon**.

Key design choices:
1. **Namespace sharing (`network_mode: "container:<node>"`):** Exporters and monitoring sidecars attach directly to the node's network namespace. This allows:
   - Exporters to bind to the node's loopback (`127.0.0.1`) and management (`mgmt0`) interfaces.
   - `route-monitor` and `wg-watch` to directly observe kernel route updates and WireGuard device states without host network privileges.
   - MySQL to bind directly to the node's loopback (`10.255.0.x`).
2. **The Docker Netns Orphan Problem:**
   As discovered during lab development (see `docs/notes.md`), when a Docker container is restarted (`docker restart <node>`), Docker creates a completely new network namespace for it. However, sidecar containers attached via `container:<node>` remain trapped in the dead/old network namespace. Their processes appear "Up" in `docker ps`, but they become unreachable on the node's management IP.
3. **Automated Sidecar Healer (`sidecar-healer`):**
   To resolve this without manual intervention, we deploy a lightweight sidecar healer container (`sync-or-swim-sidecar-healer`) running alongside Docker:
   - It listens to Docker socket events (`docker events --filter type=container --filter event=start`).
   - When a node container starts, it immediately discovers all sidecars labeled with `sos.node=<name>` and restarts them.
   - The restarted sidecars cleanly attach to the node's newly created network namespace.

## Consequences

### Positive
- Clean separation of concerns: Standard, official container images (`prom/node-exporter`, `mysql:8.4`, `grafana/alloy`) are used without modification.
- Complete observability: Sidecars inspect kernel network tables, WireGuard sockets, and local sockets without privilege leakage.
- Self-healing: Full host or container reboots recover automatically without orphaned network namespaces.

### Negative
- Requires Docker socket access for the sidecar healer container (`/var/run/docker.sock`).
- Compose startup sequencing requires creating node containers before sidecar containers.
