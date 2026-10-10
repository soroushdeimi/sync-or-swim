# Chaos Test Results

- **Date:** 2026-10-10 16:00:09 UTC
- **Commit:** `0672f6b`

| Scenario | Switch (s) | Lost Pings | Failback (s) | Seq OK | Notes |
|:---|:---:|:---:|:---:|:---:|:---|
| 1. wg-1 crash | 0.099 | 0 | 6.631 | PASS | Switched in 0.099s, failed back in 6.631s, zero reconnects, no seq gaps |
| 2. wg-1 hang | 0.920 | 0 | 1.545 | PASS | BFD failover in 0.92s, unpause failback in 1.545s, no seq gaps |
| 3. silent path blackhole | 1.154 | 8 | 2.097 | PASS | wg-via1 stayed UP, BFD switch in 1.154s, failback in 2.097s, no seq gaps |
| 4. degraded path | 1.742 | 265 | 1.847 | PASS | 15 path changes during 60s of 30% netem loss, replication intact with no gaps |
| 5. end-to-end failure | - | - | - | PASS | AllPathsDown fired within 90s, Replica IO left Yes, primary accepted all writes |
| 6. recovery from 5 | - | - | 4.887 | PASS | Replication caught up in 4.887s via GTID, all seq IDs verified without gaps |
| 7. routing daemon failure | 0.180 | 1 | 11.270 | PASS | Tunnel stayed up, switch in 0.18s, watchfrr restored bgpd in 11.27s, no gaps |
| 8. MySQL down, not the network | - | 0 | - | PASS | BGP stayed up, path stayed wg-1, MySQL alerts fired, AllPathsDown did not fire |
