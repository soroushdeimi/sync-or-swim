# Central Logging (Loki + Alloy)

Centralized logging architecture for the sync-or-swim lab environment, collecting container stdout/stderr, routing daemon log files, and kernel network state change sidecars.

## Architecture and Log Flow

```
+-----------------------------------------------------------------------------------+
| Node Namespaces (mysql-1, mysql-2, wg-1, wg-2)                                    |
|                                                                                   |
|  [route-monitor sidecar]  ---> stdout  -\                                         |
|  (`ip -ts monitor`)                      \                                        |
|                                           +-> Docker Daemon Logging Engine        |
|  [wg-watch sidecar]       ---> stdout  --/     (container stdout/stderr json-file)|
|  (`wg show ...`)                         /              |                         |
|                                         /               |                         |
|  [node services]          ---> stdout -/                | /var/run/docker.sock:ro |
|                                                         v                         |
|  [FRR daemons]            ---> /var/run/frr/frr.log --> Alloy discovery.docker +  |
|                                 (lab_run_dir/<node>)    loki.source.docker        |
|                                      |                                            |
|                                      v                                            |
|                               Alloy local.file_match +                            |
|                               loki.source.file                                    |
+-----------------------------------------------------------------------------------+
                                       |
                                       | HTTP POST /loki/api/v1/push
                                       v
                             +-------------------+
                             | Loki (Single Bin) |
                             | Port 3100         |
                             | TSDB + Filesystem |
                             +-------------------+
```

1. **Docker Container Logs**:
   Grafana Alloy (`mgmt_services.alloy`, 172.31.100.34) discovers running containers via `discovery.docker` over `/var/run/docker.sock`. Containers matching the project name or lab nodes are selected and ingested via `loki.source.docker`.
2. **FRR Daemon Logs**:
   FRR logs directly to `/var/run/frr/frr.log` inside each node container, backed by the host's `{{ lab_run_dir }}/<node>/frr/frr.log`. Alloy mounts `{{ lab_run_dir }}` read-only, discovers these files via `local.file_match`, and ingests them with `loki.source.file`.
3. **Route Monitor Sidecars**:
   Each node has an associated `<node>-route-monitor` sidecar sharing the node's network namespace (`network_mode: "container:<node>"`). It runs `ip -ts monitor route link` to stream real-time routing table and link state transitions to stdout.
4. **WireGuard Watcher Sidecars**:
   Each node has a `<node>-wg-watch` sidecar in the node's network namespace (`cap_add: [NET_ADMIN]`). It polls `wg show all latest-handshakes` every 2 seconds, emitting a log line on interface startup and only on subsequent handshake state transitions (`up` vs `stale`).

## Label Set and Cardinality

Labels are strictly normalized to maintain low cardinality:

| Label | Source | Example Values | Description |
|---|---|---|---|
| `container` | Docker container name | `mysql-1`, `mysql-1-route-monitor`, `sync-or-swim-loki` | Name of the producing container |
| `service` | `sos.service` label / fallback | `route-monitor`, `wg-watch`, `frr`, `mysql`, `loki`, `alloy` | Logical service classification |
| `node` | `sos.node` / container name | `mysql-1`, `mysql-2`, `wg-1`, `wg-2` | Host or namespace identifier |
| `site` | `sos.site` label | `site1`, `site2`, `path-a`, `path-b` | Geographic / topological site |
| `role` | `sos.role` label | `mysql`, `relay`, `monitoring` | Node role assignment |

No dynamic parameters, timestamps, IP addresses, or log message contents are used as stream labels.

## Saved LogQL Queries

### Network Failure (BFD Down)
Detect BFD session down events logged by FRR daemons:
```logql
{service="frr"} |= "BFD" |~ "(DOWN|down)"
```

### Route Changes (Loopback and Transit Routing)
Track FIB modifications involving loopback addresses (10.255.0.0/24):
```logql
{service="route-monitor"} |= "10.255.0."
```

### Failover and Recovery (BGP Neighbors)
Observe BGP neighbor state transitions across all routing nodes:
```logql
{service="frr"} |~ "BGP.*neighbor.*(Up|Down)"
```

### MySQL Replication Issues
Identify replication IO thread or connection failures:
```logql
{service="mysql"} |~ "error (connecting|reconnecting) to source|Replica I/O"
```

### WireGuard Peer Health (Stale / Up Transitions)
Monitor tunnel handshake freshness and recovery:
```logql
{service="wg-watch"} |~ "state=(stale|up)"
```
Filter specifically for stale tunnels:
```logql
{service="wg-watch"} |= "state=stale"
```
Filter for tunnel recoveries:
```logql
{service="wg-watch"} |= "state=up"
```
