# Runbook: WireGuardHandshakeStale

## Meaning
The latest WireGuard cryptographic handshake on a tunnel interface is older than 180 seconds (`(time() - wireguard_latest_handshake_seconds) > 180`). Normal keepalive packets should renew handshakes approximately every 120-180 seconds.

## Impact
- High risk of imminent tunnel failure and packet loss.
- If bidirectional communication has stopped, BFD will detect timeout and trigger BGP failover.
- If both tunnels experience stale handshakes, network isolation will occur.

## First 3 Checks
1. Query WireGuard status on the affected node:
   ```bash
   docker exec <node> wg show <interface>
   ```
2. Verify underlay network endpoint reachability to the remote peer:
   ```bash
   docker exec <node> ping -c 3 <peer_underlay_ip>
   ```
3. Check WireGuard watcher logs via Loki or directly in container logs:
   ```bash
   docker logs <node>-wg-watch --tail 50
   ```

## Likely Causes
- Remote peer container stopped, frozen, or network interface removed.
- Firewall or Docker network bridge issue blocking UDP tunnel ports (ports 51821 / 51822).
- Key rotation or mismatched WireGuard public keys between endpoints.

## Fix
1. Verify the remote peer container is up and running:
   ```bash
   docker ps --filter "name=<peer_container>"
   ```
2. Check that the peer endpoint IP and port match configuration:
   ```bash
   docker exec <node> cat /etc/sos/wireguard/<interface>.conf
   ```
3. Restart WireGuard link or re-apply configuration:
   ```bash
   docker exec <node> /usr/local/bin/sos-apply
   ```

## Escalation
Escalate to Network Operations if underlay endpoints respond to ICMP but UDP packets on ports 51821-51822 are blocked or discarded by host iptables/nftables.
