# Runbook: ExporterDown

## Meaning
A Prometheus scrape target is reporting `up == 0` for more than 1 minute. Prometheus cannot scrape metrics from the target service exporter.

## Impact
- Observability blind spot: telemetry, alert evaluation, and Grafana dashboard visualization for that component are halted.
- Failure detection mechanisms relying on metric state become ineffective.

## First 3 Checks
1. Identify the failing exporter job and instance from Prometheus:
   ```bash
   curl -s http://127.0.0.1:9090/api/v1/targets | jq '.data.activeTargets[] | select(.health!="up")'
   ```
2. Verify container state of the exporter sidecar on the target node:
   ```bash
   docker ps -a --filter "name=<instance>"
   ```
3. Test direct HTTP scrape endpoint from the monitoring container or host:
   ```bash
   curl -s -o /dev/null -w "%{http_code}\n" http://<node_mgmt_ip>:<exporter_port>/metrics
   ```

## Likely Causes
- Exporter container stopped, crashed, or ran out of memory.
- Target node container was restarted without the sidecar healer catching it (sidecar orphaned in old network namespace).
- Exporter process hung or deadlocked on database/daemon connection.

## Fix
1. If the node was restarted, check if the sidecar healer container is running:
   ```bash
   docker ps --filter "name=sidecar-healer"
   ```
2. Restart the specific exporter container or all sidecars for that node:
   ```bash
   docker restart <exporter_container_name>
   ```
3. Inspect container logs for crash traces:
   ```bash
   docker logs <exporter_container_name> --tail 50
   ```

## Escalation
Escalate to SRE team if the exporter container immediately crashes upon start or fails with internal runtime panics.
