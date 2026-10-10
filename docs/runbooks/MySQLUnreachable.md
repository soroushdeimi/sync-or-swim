# Runbook: MySQLUnreachable

## Meaning
The MySQL daemon on node `{{ $labels.instance }}` is down or not responding to queries (`mysql_up == 0` from `mysqld_exporter`) for more than 30 seconds.

## Impact
- **CRITICAL:** Database availability loss on the affected node.
- If primary (`mysql-1-db`): Total write outage for applications.
- If replica (`mysql-2-db`): Total read outage for read-scaled workloads; replication halted.

## First 3 Checks
1. Check container running and health state:
   ```bash
   docker ps -a --filter "name=mysql-"
   docker inspect --format='{{.State.Status}} ({{.State.Health.Status}})' mysql-1-db
   ```
2. Test local MySQL client connection inside the database container:
   ```bash
   docker exec <mysql_container> mysqladmin -uroot -p"${MYSQL_ROOT_PASSWORD}" ping
   ```
3. Inspect MySQL error log inside the container or via Docker logs:
   ```bash
   docker logs <mysql_container> --tail 100
   ```

## Likely Causes
- MySQL daemon crashed (OOM killed, assertion failure, corrupt redo log).
- Database container stopped or paused.
- Port binding issue or socket permission failure inside the node network namespace.

## Fix
1. If container exited or stopped, inspect exit code and start it:
   ```bash
   docker inspect <mysql_container> --format='{{.State.ExitCode}}'
   docker start <mysql_container>
   ```
2. If crashed due to OOM, inspect host `dmesg -T | grep -i oom`.
3. Check disk space on the Docker root volume:
   ```bash
   df -h /var/lib/docker
   ```

## Escalation
Escalate immediately to Database Administrator (DBA) and Lead SRE. Do NOT attempt uncoordinated failover without verifying GTID consistency.
