# Chaos Test Results

- **Date:** 2026-10-12 09:13:51 UTC
- **Commit:** `515d55f`

| Scenario | Switch (s) | Lost Pings | Failback (s) | Seq OK | Notes |
|:---|:---:|:---:|:---:|:---:|:---|
| 1. wg-1 crash | 0.096 | 0 | 36.637 | PASS | Switched in 0.096s, failed back in 36.637s, zero reconnects, no seq gaps |
| 2. wg-1 hang | 0.796 | 0 | 32.928 | PASS | BFD failover in 0.796s, unpause failback in 32.928s, no seq gaps |
| 3. silent path blackhole | 1.027 | 7 | 31.920 | PASS | wg-via1 stayed UP, BFD switch in 1.027s, failback in 31.92s, no seq gaps |
| 4. degraded path | 3.639 | 27 | - | PASS | 1 path changes during 60s of 30% netem loss, replication intact with no gaps |
| 5. end-to-end failure | - | - | - | PASS | AllPathsDown fired within 90s, Replica IO left Yes, primary accepted all writes |
| 6. recovery from 5 | - | - | 1.637 | PASS | Replication caught up in 1.637s via GTID, all seq IDs verified without gaps |
| 7. routing daemon failure | 0.197 | 1 | 41.316 | PASS | Tunnel stayed up, switch in 0.197s, watchfrr restored bgpd in 41.316s, no gaps |
| 8. MySQL down, not the network | - | 0 | - | PASS | BGP stayed up, path stayed wg-1, MySQL alerts fired, AllPathsDown did not fire |
| 9. backup path silent (stale handshake) | - | 0 | - | PASS | WireGuardHandshakeStale fired after 215s and cleared 15s after recovery; traffic stayed on wg-1, replication kept running |
| 10a. alerts: relay crash | - | - | - | PASS | BFDPeerDown, BGPSessionDown, TrafficOnBackupPath fired after 63s and cleared 45s after recovery |
| 10b. alerts: exporter stopped | - | - | - | PASS | ExporterDown fired after 69s and cleared 9s after recovery |
| 10c. alerts: replication lag | - | - | - | PASS | ReplicationLagHigh, ReplicationSQLStopped fired after 99s and cleared 9s after recovery |
| 10d. alerts: primary mysqld stopped | - | - | - | PASS | MySQLUnreachable fired after 24s and cleared 3s after recovery |
| 10e. alerts: lossy path | - | - | - | PASS | PathHighPacketLoss fired after 30s and cleared 105s after recovery |
| 10f. alerts: slow path | - | - | - | PASS | PathHighLatency fired after 63s and cleared 9s after recovery |
| 10g. alerts: repeated failovers | - | - | - | PASS | RouteFlapping fired after 110s (3 failover/failback cycles); the ten-minute window then empties and it clears by itself |
