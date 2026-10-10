# 4. BFD Timers: 300 ms x 3

Date: 2026-10-10

## Status

Accepted

## Context

Standard BGP relies on TCP keepalives to detect peer failure. With standard BGP timers (keepalive 60 seconds, hold time 180 seconds), detecting a path failure could take up to three minutes, resulting in unacceptably long database replication stalls and application timeouts.

To achieve sub-second fault detection and failover, BGP must be paired with Bidirectional Forwarding Detection (BFD). We needed to choose BFD timer parameters (`receive-interval`, `transmit-interval`, and `detect-multiplier`) that minimize convergence time while avoiding false positives caused by container scheduling jitter.

## Decision

We configured BFD timers to **300 ms transmit/receive interval with a multiplier of 3** (900 ms total detection threshold).

```
bfd
 profile wg-tunnels
  receive-interval 300
  transmit-interval 300
  detect-multiplier 3
 exit
```

Evaluation of alternatives:
1. **Aggressive timers (50 ms x 3 = 150 ms):** While theoretically faster, containerized environments running on shared Linux hosts experience occasional CPU scheduling latency or Docker I/O contention. 150 ms thresholds caused occasional false BFD drops during intensive Ansible container operations.
2. **Conservative timers (1000 ms x 3 = 3000 ms):** Too slow; exceeds the target for sub-second failover.
3. **300 ms x 3 (900 ms):** Provides the ideal balance. When a relay container freezes (`docker pause`), BFD detects the outage in ~0.95 s. In container crash scenarios (`docker kill`), the kernel tears down the socket immediately (0.21 s). Under normal load, 300 ms x 3 yields zero false positives.

## Consequences

### Positive
- **Sub-second failover:** Path failure detected in under 1 second (0.95 s measured during control-plane freeze; 0.21 s during crash).
- Robust against jitter: 3 dropped control packets required before declaring a peer dead, preventing spurious failovers during CPU spikes.
- Lightweight: BFD control packets consume negligible network bandwidth over the WireGuard tunnels.

### Negative
- In high-contention or severely overloaded host CPU environments, bursts of packet drops or scheduler delays > 900 ms can trigger route failover.
