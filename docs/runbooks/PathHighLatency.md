# Runbook: PathHighLatency

## Meaning
Round-trip probe duration (`probe_duration_seconds`) on network path `{{ $labels.path }}` is greater than 2 times its historical 1-hour average.

## Impact
- Increased query latency for cross-site database transactions and synchronous replication tasks.
- MySQL replication lag accumulation during high-write workloads.
- Potential delay in failover detection if packet processing stalls.

## First 3 Checks
1. Check current latency metrics and compare across both paths (`wg-1` vs `wg-2`):
   ```bash
   docker exec mysql-1 ping -c 10 10.10.11.1
   docker exec mysql-1 ping -c 10 10.10.21.1
   ```
2. Verify host CPU and load average:
   ```bash
   uptime
   docker stats --no-stream
   ```
3. Check route-monitor logs for intermediate route thrashing:
   ```bash
   docker logs <node>-route-monitor --tail 50
   ```

## Likely Causes
- Host system CPU throttling or CFS scheduler contention delaying container processing.
- WireGuard encryption overhead under heavy CPU saturation.
- Buffer bloat or queue buildup in underlay network namespaces.

## Fix
1. Identify high-CPU processes running on the host or inside containers.
2. Check if MTU mismatch causes packet fragmentation (`wg_mtu` must be 1420 to fit within standard 1500 byte Ethernet frame):
   ```bash
   docker exec <node> ip link show
   ```
3. If latency is isolated to `wg-1`, consider shifting traffic to `wg-2` while troubleshooting.

## Escalation
Escalate to Infrastructure team if host hardware or virtualization layer exhibits severe I/O or CPU latency spikes.
