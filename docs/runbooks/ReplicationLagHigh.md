# Runbook: ReplicationLagHigh

## Meaning
The measured replication lag between the primary database and replica `{{ $labels.instance }}` has exceeded threshold:
- **Warning:** `> 30 seconds` for 1 minute
- **Critical:** `> 300 seconds` for 1 minute

Lag is measured by calculating the difference between the current timestamp and the heartbeat timestamp injected every second into `sre.heartbeat` by the primary's event scheduler (`mysql_heartbeat_now_timestamp_seconds - mysql_heartbeat_stored_timestamp_seconds`).

## Impact
- **Warning:** Replica serves increasingly stale reads; failover risk increases (promoting replica will result in recovery delays or data loss window).
- **Critical:** Severe data drift; high risk of transaction loss during unplanned primary outage.

## First 3 Checks
1. Check heartbeat lag directly via query on replica:
   ```bash
   docker exec mysql-2-db mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" -e "
     SELECT TIMESTAMPDIFF(SECOND, ts, UTC_TIMESTAMP(6)) AS lag_sec, ts, server_id
     FROM sre.heartbeat WHERE server_id = 1;"
   ```
2. Verify replica threads and Seconds_Behind_Source:
   ```bash
   docker exec mysql-2-db mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" -e "SHOW REPLICA STATUS\G"
   ```
3. Check primary event scheduler status:
   ```bash
   docker exec mysql-1-db mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" -e "
     SELECT event_name, status, last_executed
     FROM information_schema.events
     WHERE event_schema = 'sre';"
   ```

## Likely Causes
- High write volume or long-running transactions (large schema migration, bulk insert) saturating single-threaded or multi-threaded applier on replica.
- Network bandwidth bottleneck or intermittent packet loss across transit path.
- Resource starvation (disk I/O, CPU, memory) on the replica container.
- Primary event scheduler disabled or stalled, preventing heartbeat ticks from generating.

## Fix
1. Identify blocking queries or high-load threads on replica:
   ```bash
   docker exec mysql-2-db mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" -e "SHOW FULL PROCESSLIST;"
   ```
2. Ensure event scheduler is running on primary:
   ```bash
   docker exec mysql-1-db mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" -e "SET GLOBAL event_scheduler = ON;"
   ```
3. Check underlying network latency and packet loss between loopbacks:
   ```bash
   docker exec mysql-1 ping -c 10 10.255.0.2
   ```

## Escalation
Escalate to DBA and Application On-Call if lag continues to climb monotonically during peak business hours.
