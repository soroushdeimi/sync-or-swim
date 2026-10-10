# Postmortem: FRR Daemons Start Failure and Shared Compose Regression

- **Date:** 2026-10-10
- **Status:** Resolved
- **Impact:** Dynamic routing disabled; eBGP and BFD sessions failed to establish across WireGuard tunnels.

## Summary

FRR 10.3 routing daemons (`zebra`, `bgpd`, `bfdd`) crashed on container startup due to missing Linux capabilities (`CAP_SYS_ADMIN`). After adding `SYS_ADMIN` to compose capabilities, a subsequent run of `site.yml` from another feature branch re-rendered the shared compose file in the shared build directory and silently reverted the fix.

## Impact

No BGP routes were exchanged and BFD monitoring was unavailable. Loopback IPs could not route across WireGuard tunnels. Only `watchfrr` and `staticd` remained running.

## Timeline

- 2026-10-10 09:38:41 +0330: Initial Docker lab infrastructure committed (`bc3f51e`).
- 2026-10-10 10:02:43 +0330: Added `SYS_ADMIN` to `roles/lab_infra/templates/compose.yml.j2` (`ff30f5f`) after discovering `privs_init` failure.
- 2026-10-10: Running `site.yml` from another branch re-rendered `build/compose.yml` without `SYS_ADMIN`, recreating containers and regressing routing daemons.
- 2026-10-10 10:46:12 +0330: Merged `feat/frr` into `main` (`184d233`).

## Detection

Container logs from node containers showed daemon crashes during entrypoint initialization:
```
privs_init: initial cap_set_proc failed: Operation not permitted
```
Running `vtysh -c "show ip bgp summary"` reported daemon connection errors because `bgpd` and `zebra` were dead.

## Root cause

1. FRR 10.3 requires `CAP_NET_ADMIN`, `CAP_NET_RAW`, and `CAP_SYS_ADMIN` to set kernel routing attributes and manage networking privileges. Node containers were initially configured with only `NET_ADMIN`.
2. All git worktrees pointed to a shared `build/` directory. Running `site.yml` from an older branch that lacked the capability patch overwrote `build/compose.yml` and recreated the containers without `SYS_ADMIN`.

## Resolution

1. Added `SYS_ADMIN` under `cap_add` in `roles/lab_infra/templates/compose.yml.j2`.
2. Established an operational rule that only the `main` branch deploys infrastructure playbooks (`site.yml`), while feature branches run role-specific playbooks only.

## What went well

- FRR logs clearly reported the failing capability syscall (`cap_set_proc`).
- `watchfrr` kept the container running, allowing interactive inspection via `docker exec`.

## What went wrong

- Compose definitions lacked capability declarations mandated by FRR 10.3.
- Build artifacts in `build/` were shared across git worktrees without branch isolation.

## Action items

- [x] Add `SYS_ADMIN` capability to node container template (`ff30f5f`).
- [x] Restrict infrastructure re-provisioning to `main` branch workflow.
- [x] Add automated integration tests verifying BGP and BFD daemon status (`tests/integration/test_frr.py`).
