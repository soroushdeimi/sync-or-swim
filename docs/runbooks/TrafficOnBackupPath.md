# Runbook: TrafficOnBackupPath

## Meaning
In `active_backup` routing mode, traffic is actively being routed over backup relay `wg-2` (`sre_active_path{path="wg-2"} == 1`) for longer than 1 minute. The primary path via `wg-1` is expected to carry normal traffic.

## Impact
- System is operating in a degraded redundancy state. Any subsequent failure on `wg-2` will result in total loss of connectivity (`AllPathsDown`).
- Latency or bandwidth characteristics may differ if paths are asymmetrical in real network environments.

## First 3 Checks
1. Check the active route to peer loopback on MySQL nodes:
   ```bash
   docker exec mysql-1 ip -j route get 10.255.0.2
   docker exec mysql-1 vtysh -c 'show ip route 10.255.0.2'
   ```
2. Verify why the primary path `wg-1` is not selected (check BGP ribs and attributes):
   ```bash
   docker exec mysql-1 vtysh -c 'show ip bgp 10.255.0.2'
   ```
3. Check the operational state of primary relay container `wg-1`:
   ```bash
   docker ps -a --filter "name=wg-1"
   docker exec wg-1 vtysh -c 'show ip bgp summary'
   ```

## Likely Causes
- Primary relay `wg-1` is stopped, rebooting, or experiencing service failure.
- BGP session or BFD session between `mysql-1`/`mysql-2` and `wg-1` is down.
- WireGuard tunnel `wg-via1` on either MySQL node lost handshake or dropped link.

## Fix
1. If `wg-1` is stopped or exited, start it:
   ```bash
   docker start wg-1
   ```
2. Check `wg-1` WireGuard handshake status:
   ```bash
   docker exec wg-1 wg show
   ```
3. Once `wg-1` recovers, verify that BGP re-establishes and sets higher local preference (`local-preference 200` vs `100`), restoring traffic to `wg-1`.

## Escalation
Escalate to Network Engineering if `wg-1` is up and BGP is established, but routes via `wg-1` are not installed or local-preference is not evaluated correctly.
