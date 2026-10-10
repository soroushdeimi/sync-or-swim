# Runbook: ReplicationSQLStopped

## Meaning
The MySQL replication SQL applier thread on replica `{{ $labels.instance }}` is stopped (`mysql_slave_status_slave_sql_running == 0`). The replica is receiving binary log events but cannot execute them against the local dataset.

## Impact
- **CRITICAL:** Replica dataset diverges from primary.
- Relay logs accumulate on disk, potentially causing disk exhaustion.
- Stale read queries returned to applications connecting to the replica.

## First 3 Checks
1. Check replication error status on the replica:
   ```bash
   docker exec mysql-2-db mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" -e "SHOW REPLICA STATUS\G"
   ```
   Inspect `Last_SQL_Errno` and `Last_SQL_Error`.
2. Check executed GTID set vs retrieved GTID set:
   ```bash
   docker exec mysql-2-db mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" -e "SELECT @@global.gtid_executed, @@global.gtid_purged;"
   ```
3. Check replica write permissions and read-only flags:
   ```bash
   docker exec mysql-2-db mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" -e "SELECT @@global.read_only, @@global.super_read_only;"
   ```

## Likely Causes
- Data conflict or constraint violation (e.g., duplicate key, missing row on replica caused by rogue local writes).
- Out of disk space on MySQL data directory.
- DDL syntax error or unsupported operation in binary log stream.

## Fix
1. Inspect the offending transaction and error code in `Last_SQL_Error`.
2. Ensure `super_read_only = ON` is enabled on replica to prevent rogue writes.
3. If a benign transaction needs to be skipped using GTID:
   ```bash
   # ONLY after DBA confirmation:
   # SET GTID_NEXT = 'UUID:TRANSACTION_ID'; BEGIN; COMMIT; SET GTID_NEXT = 'AUTOMATIC';
   ```
4. Restart SQL applier thread:
   ```bash
   docker exec mysql-2-db mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" -e "START REPLICA SQL_THREAD;"
   ```

## Escalation
Escalate immediately to Lead DBA. Never inject empty transactions or skip replication events without explicit DBA authorization.
