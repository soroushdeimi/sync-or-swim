# Runbook: BGPSessionDown

## Meaning
The BGP session with a neighboring peer is not in the `Established` state (`state != 2` from `frr_exporter` metric `frr_bgp_peer_state`). The peer connection is either in `Idle`, `Connect`, `Active`, or `OpenSent`/`OpenConfirm`.

## Impact
- **Single peer down (wg-1 or wg-2):** Redundancy is degraded. In `active_backup` mode, if `wg-1` fails, traffic fails over to `wg-2`. If `wg-2` is down, failover capability is lost.
- **Both peers down:** All transit paths are broken (`AllPathsDown`). MySQL replication between loopbacks halts completely.

## First 3 Checks
1. Check BGP neighbor state on the affected node:
   ```bash
   docker exec <node> vtysh -c 'show ip bgp summary'
   ```
2. Check underlying network interface and WireGuard tunnel status:
   ```bash
   docker exec <node> ip addr show
   docker exec <node> wg show
   ```
3. Test peer link connectivity over the WireGuard tunnel point-to-point IP:
   ```bash
   docker exec <node> ping -c 3 <peer_tunnel_ip>
   ```

## Likely Causes
- Relay container (`wg-1` or `wg-2`) stopped, crashed, or paused.
- BFD detected loss and tore down the BGP session before TCP timeout.
- MD5 BGP password mismatch or configuration drift in `/etc/frr/frr.conf`.
- WireGuard handshake failure or missing keepalive packets across the underlay bridge.

## Fix
1. Verify if relay container is running:
   ```bash
   docker ps -a --filter "name=wg-"
   ```
   If stopped, start it with `docker start <relay_container>`.
2. Inspect FRR daemon logs on the node and relay:
   ```bash
   docker exec <node> cat /var/run/frr/frr.log
   ```
3. Re-apply node runtime configuration if configuration drifted:
   ```bash
   docker exec <node> /usr/local/bin/sos-apply
   ```

## Escalation
Escalate to Network Engineering / On-Call SRE if point-to-point pings succeed and WireGuard handshakes are fresh, but BGP remains in `Active` or `Connect` state despite daemon restarts.
