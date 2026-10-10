# Runbook: AllPathsDown

## Meaning
All network transit paths between MySQL nodes are non-functional. Both `probe_success{path="e2e"} == 0` and `sre_peer_route_present == 0` on the monitoring node. No viable routes exist to the peer loopback in the kernel routing table.

## Impact
- **CRITICAL:** Total data plane partition between Site 1 (`mysql-1`) and Site 2 (`mysql-2`).
- MySQL replication stops immediately.
- Replication lag increases monotonically. Risk of stale reads if replica serves read traffic.

## First 3 Checks
1. Check kernel routing table for loopback routes on MySQL nodes:
   ```bash
   docker exec mysql-1 ip route show 10.255.0.2/32
   docker exec mysql-2 ip route show 10.255.0.1/32
   ```
2. Check container status of both relay nodes (`wg-1` and `wg-2`):
   ```bash
   docker ps --filter "name=wg-"
   ```
3. Check BGP and BFD summaries across both sides:
   ```bash
   docker exec mysql-1 vtysh -c 'show ip bgp summary'
   docker exec mysql-1 vtysh -c 'show bfd peers brief'
   ```

## Likely Causes
- Simultaneous crash, stop, or pause of both relay containers (`wg-1` and `wg-2`).
- Both underlay transit networks (`transit-a` and `transit-b`) disconnected or failed.
- Network security policy or prefix-list misconfiguration dropping all advertised loopback prefixes (`10.255.0.0/24`).

## Fix
1. Restart relay containers if dead:
   ```bash
   docker restart wg-1 wg-2
   ```
2. Verify underlay transit IP reachability:
   ```bash
   docker exec mysql-1 ping -c 2 172.31.1.21  # wg-1 on transit-a
   docker exec mysql-1 ping -c 2 172.31.2.22  # wg-2 on transit-b
   ```
3. Re-run node configuration script to restore interfaces and routing policies:
   ```bash
   docker exec mysql-1 /usr/local/bin/sos-apply
   docker exec mysql-2 /usr/local/bin/sos-apply
   ```

## Escalation
Escalate immediately to Lead SRE and Infrastructure Incident Commander. Initiate P1 outage response protocol.
