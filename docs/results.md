# Chaos Test Results

- **Date:** 2026-10-11 18:16:51 UTC
- **Commit:** `e9e5beb`

| Scenario | Switch (s) | Lost Pings | Failback (s) | Seq OK | Notes |
|:---|:---:|:---:|:---:|:---:|:---|
| 1. wg-1 crash | 0.099 | 0 | 36.602 | PASS | Switched in 0.099s, failed back in 36.602s, zero reconnects, no seq gaps |
| 2. wg-1 hang | 0.904 | 0 | 33.293 | PASS | BFD failover in 0.904s, unpause failback in 33.293s, no seq gaps |
| 3. silent path blackhole | 1.144 | 8 | 31.926 | PASS | wg-via1 stayed UP, BFD switch in 1.144s, failback in 31.926s, no seq gaps |
| 4. degraded path | 7.524 | 51 | - | PASS | 1 path changes during 60s of 30% netem loss, replication intact with no gaps |
| 5. end-to-end failure | - | - | - | PASS | AllPathsDown fired within 90s, Replica IO left Yes, primary accepted all writes |
| 6. recovery from 5 | - | - | 5.017 | PASS | Replication caught up in 5.017s via GTID, all seq IDs verified without gaps |
| 7. routing daemon failure | 0.179 | 1 | 41.271 | PASS | Tunnel stayed up, switch in 0.179s, watchfrr restored bgpd in 41.271s, no gaps |
| 8. MySQL down, not the network | - | 0 | - | PASS | BGP stayed up, path stayed wg-1, MySQL alerts fired, AllPathsDown did not fire |
