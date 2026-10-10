# Runbook: PathHighPacketLoss

## Meaning
Packet loss measured by Blackbox ICMP probes on path probe (`path="<path>"`) exceeds 5% averaged over a 2-minute sliding window (`(1 - avg_over_time(probe_success[2m])) > 0.05`).

## Impact
- Degraded network throughput and intermittent TCP retransmissions.
- MySQL replication delays and possible heartbeat check jitter.
- Risk of false BFD session drops if loss bursts exceed the 3-packet BFD threshold.

## First 3 Checks
1. Run manual ICMP probe along the affected path to verify loss rate:
   ```bash
   docker exec mysql-1 ping -c 50 -i 0.05 <peer_ip>
   ```
2. Check interface error and drop counters on the node and relay:
   ```bash
   docker exec mysql-1 ip -s link show
   docker exec wg-1 ip -s link show
   ```
3. Inspect system CPU and network throttling on the host:
   ```bash
   docker stats --no-stream
   ```

## Likely Causes
- Docker bridge interface overload or host system buffer exhaust.
- Asymmetric routing or reverse path filtering issues causing kernel packet drops.
- Network congestion or CPU contention on container worker processes.

## Fix
1. Inspect reverse path filter sysctls on node containers:
   ```bash
   docker exec <node> sysctl net.ipv4.conf.all.rp_filter
   ```
   (Must be `2` for loose mode to accommodate asymmetric failover paths).
2. Check for container CPU starvation or throttle events.
3. If packet loss persists on primary path, manually force failover by adjusting BGP route maps or temporarily shutting the primary BGP neighbor:
   ```bash
   docker exec mysql-1 vtysh -c 'conf t' -c 'router bgp 65001' -c 'neighbor 10.10.11.1 shutdown'
   ```

## Escalation
Escalate to Systems / Infrastructure SRE if packet loss occurs across multiple Docker bridge networks simultaneously.
