# Postmortem: Initial GitHub Actions CI Failures on Clean Runners

- **Date:** 2026-10-10
- **Status:** Resolved
- **Impact:** Automated CI workflow failed on clean GitHub Actions runner environments across image builds and integration tests.

## Summary

Initial CI runs on GitHub Actions runners failed across three separate areas: the image build script required an uninstalled Ansible callback plugin, the default runner Docker engine was older than Docker 28.1 required by Docker Compose network interface naming, and post-restart tests suffered from race conditions with MySQL container initialization.

## Impact

Pull request validation and automated CI checks failed on fresh GitHub runner environments despite passing on developer workstations with warm caches.

## Timeline

- 2026-10-10 16:01:54 +0330: Fixed image tag extraction script without `ansible.posix` JSON callback plugin (`089aee5`).
- 2026-10-10 16:05:54 +0330: Configured runner setup to install Docker 28.5 (`dbceddc`).
- 2026-10-10 16:14:29 +0330: Added health and replication waits after node restart in `test_lab.py` (`13426be`).
- 2026-10-10 16:27:39 +0330: Fixed restart test race by comparing container start timestamps (`b2f7da5`).

## Detection

CI runs in `.github/workflows/ci.yml` failed with explicit errors:
1. `scripts/image.sh tag` crashed with missing plugin `json` when invoking `ansible localhost`.
2. Docker Compose failed to configure network interfaces with `interface_name: ul-a` on Docker 26/27.
3. `tests/integration/test_lab.py::test_restart_restores_state` failed intermittently when running immediately before `test_mysql.py`.

## Root cause

1. `scripts/image.sh` set `ANSIBLE_STDOUT_CALLBACK=json`, which depends on `ansible.posix`. The lightweight image build job in CI did not install galaxy collections.
2. Compose v2 `interface_name` directive requires Docker Engine 28.1 or newer. Default GitHub Ubuntu runners provided Docker 26.x.
3. When `mysql-1` was restarted, `sidecar-healer` sequentially restarted sidecars. The test checked container status before `mysql-1-db` finished restarting, falsely reading the old healthy status before it was terminated.

## Resolution

1. Replaced the Ansible stdout callback with `ANSIBLE_CALLBACK_RESULT_FORMAT=json` and parsed stdout directly using Python's standard library (`089aee5`).
2. Added an explicit runner step to install Docker 28.5 using the official Docker install script (`dbceddc`).
3. Updated `test_restart_restores_state` in `test_lab.py` to compare container start timestamps (`State.StartedAt`) to ensure `mysql-1-db` restarted after `mysql-1`, then verified MySQL health and replica status (`b2f7da5`).

## What went well

- Issues were isolated to clean-environment prerequisites and uncovered before production releases.
- Timestamp comparisons provided a reliable, non-flaky synchronization barrier.

## What went wrong

- Local developer workstations had pre-installed Ansible collections and newer Docker engines masking clean environment requirements.
- Initial test synchronization assumed synchronous restart of all sidecars.

## Action items

- [x] Switch image script to native JSON parsing without external plugin dependencies (`089aee5`).
- [x] Pin runner Docker engine to version 28.5 (`dbceddc`).
- [x] Use container start timestamp comparison in restart tests (`b2f7da5`).
