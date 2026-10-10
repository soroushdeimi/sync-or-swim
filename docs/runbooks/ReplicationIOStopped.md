# Runbook: ReplicationIOStopped

## Meaning
The MySQL replication I/O receiver thread on replica `{{ $labels.instance }}` is stopped (`mysql_slave_status_slave_io_running == 0`). The replica is not fetching binary log events from the primary.

## Impact
- **CRITICAL:** Replication is halted.
- The replica falls behind the primary; replication lag accumulates over time.
- If primary fails while I/O thread is stopped, data loss will occur if promoted.

## First 3 Checks
1. Check detailed replication status and error message on the replica:
   ```bash
   docker exec mysql-2-db mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" -e "SHOW REPLICA STATUS\G"
   ```
   Inspect `Last_IO_Errno` and `Last_IO_Error`.
2. Check network connectivity from replica to primary loopback (`10.255.0.1`):
   ```bash
   docker exec mysql-2 ping -c 3 10.255.0.1
   docker exec mysql-2 nc -zv 10.255.0.1 3306
   ```
3. Check binary log existence on primary:
   ```bash
   docker exec mysql-1-db mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" -e "SHOW BINARY LOGS;"
   ```

## Likely Causes
- Network connectivity loss to the primary loopback (BGP down, AllPathsDown).
- Authentication failure for user `repl` (password change, revoked grants).
- Primary purged a required binary log file before replica retrieved it (GTID purge).

## Fix
1. If network failure was resolved, restart the replica I/O thread:
   ```bash
   docker exec mysql-2-db mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" -e "START REPLICA IO_THREAD;"
   ```
2. Verify credentials and SSL configuration if authentication failed:
   ```bash
   docker exec mysql-2-db mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" -e "CHANGE REPLICATION SOURCE TO SOURCE_USER='repl', SOURCE_PASSWORD='...', SOURCE_SSL=1; START REPLICA;"
   ```
3. Confirm `Replica_IO_Running: Yes` after start.

## Escalation
Escalate to DBA / Senior SRE if `Last_IO_Error` indicates missing GTIDs or binary log file corruption.
