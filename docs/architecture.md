# Architecture

System architecture, layers, and failover mechanics of the sync-or-swim high-availability lab.

## System Layers

The platform is structured into decoupled, modular layers managed declaratively via Ansible.

```
+---------------------------------------------------------------------------------------------------+
| Management & Observability Plane                                                                  |
|  - Prometheus (172.31.100.30), Alertmanager (172.31.100.31), Grafana (172.31.100.32)             |
|  - Loki (172.31.100.33), Alloy (172.31.100.34), Mailpit (172.31.100.35)                          |
|  - Sidecar Healer: watches Docker events, reattaches sidecars on container restart               |
+---------------------------------------------------------------------------------------------------+
| Database Tier (Service Layer)                                                                     |
|  - mysql-1-db (Primary, server-id 1, loopback 10.255.0.1)                                         |
|  - mysql-2-db (Replica, server-id 2, super_read_only, GTID auto-position, bind 10.255.0.2)        |
|  - Heartbeat generator: sre.heartbeat table updated every 1s by event_scheduler                   |
+---------------------------------------------------------------------------------------------------+
| Routing & Policy Plane (FRRouting 10.3)                                                           |
|  - eBGP: AS 65001 (mysql-1) <-> AS 65101 (wg-1) / AS 65102 (wg-2) <-> AS 65002 (mysql-2)          |
|  - BFD: 300 ms transmit/receive, multiplier 3 (900 ms fault detection threshold)                  |
|  - Policy: wg-1 preferred (LOCAL_PREF 200 vs 100); ECMP supported via config toggle               |
|  - Security: TCP MD5 auth, GTSM (TTL security hops 1), strict prefix lists, max-prefix 10         |
|  - Source Rewrite: route-map SET-SRC sets fib route src to node loopback                          |
+---------------------------------------------------------------------------------------------------+
| Overlay Network Layer (WireGuard)                                                                 |
|  - 4 point-to-point /31 tunnels: m1-r1, m2-r1, m1-r2, m2-r2                                      |
|  - Persistent keepalives: 25s; MTU: 1420 bytes                                                    |
+---------------------------------------------------------------------------------------------------+
| Underlay Network Layer (Docker Bridges)                                                           |
|  - transit-a (172.31.1.0/24, internal): carries m1-r1 and m2-r1 WireGuard traffic                 |
|  - transit-b (172.31.2.0/24, internal): carries m1-r2 and m2-r2 WireGuard traffic                 |
|  - mgmt (172.31.100.0/24): isolated out-of-band monitoring and telemetry                           |
+---------------------------------------------------------------------------------------------------+
```

---

### 1. Underlay Networks
The physical substrate is modeled using three Docker bridge networks:
- `transit-a` (`172.31.1.0/24`): Connects `mysql-1` (`.11`), `wg-1` (`.21`), and `mysql-2` (`.12`). Marked `internal: true` to prevent external Internet routing.
- `transit-b` (`172.31.2.0/24`): Connects `mysql-1` (`.11`), `wg-2` (`.22`), and `mysql-2` (`.12`). Also internal.
- `mgmt` (`172.31.100.0/24`): Connects every node and management container. The default route on each node points out `mgmt0` (`gw_priority`), ensuring telemetry and control traffic never contaminate the data plane.

### 2. Nodes and Sidecars
Containers use an unbaked base image containing required tooling (`iproute2`, `wireguard-tools`, `frr`, `mysql-client`). Configuration is rendered into `/etc/sos/init.d/` scripts executed by `sos-apply`:
- `00-loopback.sh`: Configures `dummy0` with the node's loopback `/32`.
- `10-wireguard.sh`: Brings up WireGuard tunnels.
- `20-frr.sh`: Configures and starts or reloads FRR daemons (`zebra`, `bgpd`, `bfdd`).

Ancillary services (exporters, `mysql:8.4`, log monitors) run as sidecars attached via `network_mode: "container:<node>"`. This grants them direct access to node interfaces while isolating their process boundaries.

### 3. WireGuard Overlay
Four point-to-point tunnels form a dual-hub overlay across the relays:
- `m1-r1` (`10.10.11.0/31`): `mysql-1` (`wg-via1`) to `wg-1` (`wg-to-m1`).
- `m2-r1` (`10.10.12.0/31`): `mysql-2` (`wg-via1`) to `wg-1` (`wg-to-m2`).
- `m1-r2` (`10.10.21.0/31`): `mysql-1` (`wg-via2`) to `wg-2` (`wg-to-m1`).
- `m2-r2` (`10.10.22.0/31`): `mysql-2` (`wg-via2`) to `wg-2` (`wg-to-m2`).

All tunnels operate with `mtu 1420` and `PersistentKeepalive = 25`.

### 4. BGP / BFD Policy and Routing Security
Routing uses eBGP with Bidirectional Forwarding Detection (BFD):
- **BFD profile:** Transmit 300 ms, receive 300 ms, detect multiplier 3 (900 ms failure detection).
- **Active / Backup policy:** `mysql-1` and `mysql-2` apply route maps on inbound BGP updates. Routes learned from `wg-1` receive `LOCAL_PREF 200`; routes from `wg-2` receive `LOCAL_PREF 100`. In `ecmp` mode, both paths receive equal preference with `multipath-relax` enabled.
- **Routing security:**
  - TCP MD5 authentication (`neighbor <ip> password <secret>`).
  - Generalized TTL Security Mechanism (`neighbor <ip> ttl-security hops 1`).
  - Prefix lists: MySQL nodes advertise only their own `/32` loopback and accept only `10.255.0.0/24 ge 32`. Relays only accept and announce loopback prefixes, preventing non-transit leaks.
  - Maximum prefix limits (`neighbor <ip> maximum-prefix 10`).

### 5. Source Address Rewrite
When dynamic failover occurs, the outgoing physical/tunnel interface changes. Standard Linux kernel behavior chooses the primary IP of the egress interface as the source address, which would reset established TCP streams.
To prevent this, FRR installs routes into the Linux kernel with an explicit preferred source address:
```
route-map SET-SRC permit 10
 set src <loopback_ip>
exit
!
ip protocol bgp route-map SET-SRC
```
Both `10.255.0.1` and `10.255.0.2` remain the fixed source and destination IPs across all failovers.

### 6. MySQL Replication Over Loopbacks
Replication runs between stable loopback IPs (`10.255.0.1:3306` and `10.255.0.2:3306`):
- Engine: MySQL 8.4 with Global Transaction Identifiers (`gtid_mode = ON`, `enforce_gtid_consistency = ON`).
- Replica configuration: `SOURCE_AUTO_POSITION = 1`, `SOURCE_SSL = 1`, `SOURCE_BIND = '10.255.0.2'`.
- Protection: `read_only = ON` and `super_read_only = ON` are persisted on `mysql-2`.
- Heartbeat tracking: `mysql-1` runs an event scheduler updating `sre.heartbeat` every second. `mysqld_exporter` monitors the difference between current time and heartbeat time on the replica.

### 7. Monitoring, Logging, and Sidecar Healer
- **Prometheus:** Scrapes 22 metrics targets every 5 seconds across management IPs (`172.31.100.0/24`).
- **Active Path Collector:** Node exporter textfile collector runs `active-path.sh` on MySQL nodes, polling `ip -j route get <peer>` every 2 seconds to export `sre_active_path`.
- **Loki & Alloy:** Alloy ingests container stdout/stderr, FRR log files (`/var/run/frr/frr.log`), and sidecar event streams (`route-monitor` running `ip monitor route link` and `wg-watch`).
- **Sidecar Healer:** Docker event watcher daemon that restarts sidecars when a parent node container restarts, reconnecting them to the newly generated network namespace.

---

## Step-by-Step: What Happens When `wg-1` Dies

When the primary relay container `wg-1` crashes or stops, failover occurs through a deterministic sequence across both sides of the topology:

```
[wg-1 crashes / killed]
   |
   +--> 1. Tunnel / Data Plane drop on wg-via1
   |
   +--> 2. BFD detection on mysql-1 & mysql-2 (<= 900 ms or immediate socket reset)
   |       bfdd transitions session 10.10.11.1 / 10.10.12.1 to DOWN
   |
   +--> 3. BGP Peer Tear-down
   |       bgpd receives BFD notification and drops BGP neighbor session immediately
   |
   +--> 4. Route Withdrawal & RIB Recalculation
   |       Route via wg-1 (LOCAL_PREF 200) withdrawn
   |       Backup route via wg-2 (LOCAL_PREF 100) promoted to best path
   |
   +--> 5. Kernel FIB Update (Zebra)
   |       Zebra updates Linux FIB: 10.255.0.2/32 via 10.10.21.1 dev wg-via2 src 10.255.0.1
   |
   +--> 6. TCP Connection Survival
   |       MySQL replication socket remains OPEN (source 10.255.0.1, dest 10.255.0.2 unchanged)
   |       Outbound replication packets route immediately out wg-via2
   |
   +--> 7. Telemetry & Alerts
           - route-monitor logs netlink RTM_NEWROUTE event to Loki
           - active-path collector updates sre_active_path{path="wg-2"} 1
           - Alertmanager fires TrafficOnBackupPath warning after 1 minute
```

### Detailed Sequence:
1. **Physical Loss:** Primary relay `wg-1` terminates.
2. **Failure Detection:**
   - In a crash/kill scenario (`docker kill`), the local kernel receives ICMP port unreachable or socket reset immediately (measured at 0.21 s).
   - In a frozen control-plane scenario (`docker pause`), BFD misses 3 consecutive packets (300 ms x 3) and drops the session at 0.95 s.
3. **Control-Plane Convergence:**
   - `bfdd` notifies `bgpd` via internal zebra/bfd IPC.
   - `bgpd` removes the routes advertised by `wg-1`.
   - The backup BGP route via `wg-2` (which was already maintained in the BGP RIB with `LOCAL_PREF 100`) is immediately selected.
4. **FIB Programming:**
   - FRR `zebra` issues a Netlink `RTM_NEWROUTE` call to the Linux kernel.
   - The kernel routing table for `10.255.0.2/32` switches nexthop to `10.10.21.1` on interface `wg-via2`.
   - The `src` attribute remains `10.255.0.1`.
5. **Data Plane Continuity:**
   - In manual failover tests, the established MySQL replication TCP socket between `10.255.0.1:3306` and `10.255.0.2` survived without disconnection or reconnection because both endpoint IP addresses are bound to local dummy devices.
   - TCP retransmits any unacknowledged packets through interface `wg-via2`.
   - Replication proceeds with zero interrupted transactions and zero dropped connections.
6. **Recovery / Failback:**
   - When `wg-1` recovers, WireGuard handshakes re-establish.
   - BFD sessions establish, followed by BGP peering.
   - `wg-1` re-advertises peer routes with `LOCAL_PREF 200`.
   - `mysql-1` and `mysql-2` switch FIB next-hops back to `wg-via1` (manual test measured failback in 1.6 s after container startup, or 6.7 s including container boot).
