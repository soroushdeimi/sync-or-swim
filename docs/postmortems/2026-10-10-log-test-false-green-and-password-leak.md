# Postmortem: Route Monitor False-Green Test and MySQL Password CI Leak

- **Date:** 2026-10-10
- **Status:** Resolved
- **Impact:** False positive test results in log verification; exposure of database root credentials in test run output.

## Summary

The route monitor integration test passed immediately (0.09 s) because it queried Loki for a static IP address present in historical log entries from prior runs. Separately, MySQL test failures printed database root passwords in CI test output because the password was supplied directly through `docker exec -e MYSQL_PWD=...` argv parameters.

## Impact

Integration tests gave a false sense of test coverage for kernel route event shipping without asserting fresh log ingestion. In failed test runs, the root password was exposed in plaintext command arguments displayed in CI job output.

## Timeline

- 2026-10-10: Observed `test_route_monitor_logs_route_change` passing in 0.09 s regardless of actual exporter streaming status.
- 2026-10-10 10:40:11 +0330: Documented false-green test findings in `docs/notes.md` (`825a50d`).
- 2026-10-10 16:14:29 +0330: Remediated password exposure in command argv and hardened MySQL restart recovery checks (`13426be`).

## Detection

1. Test execution duration for route change logging was unreasonably fast (0.09 s), significantly shorter than Loki stream ingestion latency and flush intervals.
2. In test execution failure output, pytest echoed the full process invocation command line:
   ```
   docker exec -e MYSQL_PWD=<root_password> mysql-2-db mysql -u root -e SHOW REPLICA STATUS\G
   ```
   This exposed the secret value in test logs.

## Root cause

1. The test queried Loki for an unchanging hardcoded destination IP over a one-hour query window without bounding the query start timestamp. Historical log lines from preceding runs matched the query immediately.
2. Secrets were read from host disk files and passed as environment flag options directly into the `docker exec` command line arguments, which are visible to the process table and captured in test assertion traces.

## Resolution

1. Updated `test_route_monitor_logs_route_change` to generate a random IP within `198.18.0.0/15` (benchmarking prefix) on each invocation and set `start_ns` right before triggering the `ip route add` command.
2. Refactored test helpers to execute `sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql ...'` inside the container, utilizing container-native environment variables rather than command arguments.

## What went well

- The root password was masked as soon as pytest exception traces were observed in test development.
- Randomizing test target parameters completely eliminated state pollution between test runs.

## What went wrong

- Initial tests queried time windows that spanned previous test runs.
- Password variables were passed across CLI boundaries instead of being kept within the target container environment.

## Action items

- [x] Scope Loki queries with monotonic nanosecond start times and random test IPs (`test_logging.py`).
- [x] Pass database credentials through container environment references (`13426be`).
- [x] Enforce zero credential leakage in test commands across integration test suites.
