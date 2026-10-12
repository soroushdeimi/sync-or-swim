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
1. **Aggressive timers (50 ms x 3 = 150 ms):** Not tried. On a host shared with Docker and Ansible work, one scheduling stall longer than 150 ms would drop a session for nothing. 900 ms leaves six times more room.
2. **Conservative timers (1000 ms x 3 = 3000 ms):** Too slow; exceeds the target for sub-second failover.
3. **300 ms x 3 (900 ms):** Detection in about one second. A frozen relay (`docker pause`) is caught in about 0.8 s and silently dropped packets in about 1 s ([results](../results.md)). A killed relay is faster, about 0.1 s, because the closing TCP session is noticed before BFD. `BFDPeerDown` never fires on a healthy lab, which a test checks.

## Consequences

### Positive
- **About one second:** a hang is detected in 0.8 s and silent packet loss in about 1 s, a killed relay in about 0.1 s.
- Robust against jitter: 3 dropped control packets required before declaring a peer dead, preventing spurious failovers during CPU spikes.
- Lightweight: BFD control packets consume negligible network bandwidth over the WireGuard tunnels.

### Negative
- In high-contention or severely overloaded host CPU environments, bursts of packet drops or scheduler delays > 900 ms can trigger route failover.
