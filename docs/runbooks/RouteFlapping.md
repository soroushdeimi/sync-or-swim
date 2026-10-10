# Runbook: RouteFlapping

## Meaning
The active routing path metric `sre_active_path` has changed more than 4 times within a 10-minute window (`changes(sre_active_path[10m]) > 4`). The route between MySQL nodes is unstable, rapidly oscillating between `wg-1` and `wg-2`.

## Impact
- Frequent TCP connection resets, retransmissions, and packet reordering.
- MySQL replica connection instability and repeated reconnection loops.
- CPU spikes on FRR daemons (`bgpd`, `bfdd`) processing continuous route recalculations.

## First 3 Checks
1. Check route-monitor logs for history of recent route installations:
   ```bash
   docker logs <node>-route-monitor --tail 100
   ```
2. Check BFD peer state transitions:
   ```bash
   docker exec <node> vtysh -c 'show bfd peers brief'
   docker exec <node> cat /var/run/frr/frr.log | grep BFD
   ```
3. Inspect interface flapping or link up/down state on WireGuard tunnels:
   ```bash
   docker exec <node> dmesg | tail -n 50
   ```

## Likely Causes
- Intermittent connectivity loss on the primary path `wg-1` (flapping link or flapping container).
- BFD timers (300 ms x 3 = 900 ms) triggered by transient packet loss bursts.
- Relay container crashing in a restart loop.

## Fix
1. Dampen flapping by pinning the route to backup relay `wg-2` temporarily:
   ```bash
   # On mysql-1, shutdown neighbor to wg-1
   docker exec mysql-1 vtysh -c 'conf t' -c 'router bgp 65001' -c 'neighbor 10.10.11.1 shutdown'
   ```
2. Stabilize the primary relay (`wg-1`) before restoring neighbor session.
3. Once stable, un-shutdown the neighbor:
   ```bash
   docker exec mysql-1 vtysh -c 'conf t' -c 'router bgp 65001' -c 'no neighbor 10.10.11.1 shutdown'
   ```

## Escalation
Escalate to Network Engineering to investigate physical underlay or host virtual switch interface flapping.
