# Chaos Test Results

- **Date:** 2026-10-10 15:43:53 UTC
- **Commit:** `9b6445d`

| Scenario | Switch (s) | Lost Pings | Failback (s) | Seq OK | Notes |
|:---|:---:|:---:|:---:|:---:|:---|
| 1. wg-1 crash | 0.113 | 0 | 6.668 | PASS | Switched in 0.113s, failed back in 6.668s, zero reconnects, no seq gaps |
| 2. wg-1 hang | 0.746 | 1 | 2.907 | PASS | BFD failover in 0.746s, unpause failback in 2.907s, no seq gaps |
| 3. silent path blackhole | 0.903 | 6 | 2.348 | PASS | wg-via1 stayed UP, BFD switch in 0.903s, failback in 2.348s, no seq gaps |
| 4. degraded path | 0.977 | 278 | 0.103 | PASS | 12 path changes during 60s of 30% netem loss, replication intact with no gaps |
| 5. end-to-end failure | - | - | - | PASS | AllPathsDown fired within 90s, Replica IO left Yes, primary accepted all writes |
| 6. recovery from 5 | - | - | 1.616 | PASS | Replication caught up in 1.616s via GTID, all seq IDs verified without gaps |
| 7. routing daemon failure | 0.204 | 1 | 11.232 | PASS | Tunnel stayed up, switch in 0.204s, watchfrr restored bgpd in 11.232s, no gaps |
| 8. MySQL down, not the network | - | 0 | - | PASS | BGP stayed up, path stayed wg-1, MySQL alerts fired, AllPathsDown did not fire |
