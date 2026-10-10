# Runbook: BFDPeerDown

## Meaning
Bidirectional Forwarding Detection (BFD) session with a peer router transitioned out of `Up` state (`state != 1` from `frr_exporter` metric `frr_bfd_peer_state`). BFD operates on 300 ms intervals with a multiplier of 3 (900 ms detection threshold).

## Impact
- BFD instantly triggers BGP to withdraw routes via the affected relay without waiting for standard BGP hold timers (which default to 90-180 seconds).
- Traffic automatically shifts to the alternative relay path (`wg-2`) if available.
- If both BFD sessions drop, end-to-end loopback reachability is lost.

## First 3 Checks
1. Inspect BFD peer status and state machine transitions:
   ```bash
   docker exec <node> vtysh -c 'show bfd peers brief'
   docker exec <node> vtysh -c 'show bfd peer <peer_ip>'
   ```
2. Check tunnel link status and packet counters:
   ```bash
   docker exec <node> ip -s link show <interface>
   ```
3. Check WireGuard peer handshakes and UDP reachability:
   ```bash
   docker exec <node> wg show <interface>
   ```

## Likely Causes
- Intermediate relay container frozen (`docker pause`), crashed, or killed.
- Severe underlay packet loss or queue delay exceeding the 900 ms BFD detection window.
- The `bfdd` daemon inside FRR crashed or stopped responding.

## Fix
1. Check if the relay container is paused or unresponsive:
   ```bash
   docker ps --filter "name=wg-"
   docker unpause <relay_container> # if accidentally paused
   ```
2. Check FRR process status and daemon health:
   ```bash
   docker exec <node> vtysh -c 'show daemons'
   ```
3. Restart FRR if `bfdd` is stalled:
   ```bash
   docker exec <node> /usr/local/bin/sos-apply
   ```

## Escalation
Escalate to Infrastructure / Platform team if underlay Docker bridges (`transit-a` or `transit-b`) show heavy packet drops or host-level kernel networking anomalies.
