# Problem log

Real problems hit while building the lab. Each one is a candidate for a postmortem in `docs/postmortems/`.

## 2026-10-10: dockerd fails to start: "all predefined address pools have been fully subnetted"

- **Symptom:** `docker.service` failed right after installation with the error `error creating default "bridge" network: all predefined address pools have been fully subnetted`.
- **Cause:** a VPN client on the host installs `0.0.0.0/1` and `128.0.0.0/1` routes on its TUN interface. Before Docker picks a subnet from its default pools, it checks each candidate against the host routes. Every candidate overlapped these two catch-all routes, so no pool was usable, not even for the default `bridge`.
- **Fix:** pin the default bridge and the pool in `/etc/docker/daemon.json`:
  ```json
  { "bip": "172.30.255.1/24",
    "default-address-pools": [{"base": "172.30.0.0/17", "size": 24}] }
  ```
  The lab networks use explicit subnets (`172.31.0.0/16`), so they don't depend on automatic allocation.
- **Lesson:** routes on the host affect container networking even when nothing looks related. Check `ip route show table all` first.

## 2026-10-10: FRR daemons exit at start: "privs_init: initial cap_set_proc failed"

- **Symptom:** only watchfrr and staticd stayed up; zebra, bgpd and bfdd died immediately.
- **Cause:** FRR 10.3 asks for `cap_net_admin,cap_net_raw,cap_sys_admin` and the nodes only had NET_ADMIN.
- **Fix:** `SYS_ADMIN` in the node's `cap_add`.

## 2026-10-10: nodes silently lost SYS_ADMIN again

- **Cause:** every worktree's `build/` points at the same directory, so running `site.yml` from an older branch re-rendered the shared compose file without the fix and recreated the nodes.
- **Fix:** only main deploys the lab; branches run their own playbook only.

## 2026-10-10: exporters and log sidecars go dark after `docker restart <node>`

- **Symptom:** after restarting mysql-1, nothing answered on its mgmt IP, though the sidecar containers were "Up".
- **Cause:** a restarted container gets a new network namespace. Containers started with `network_mode: container:<node>` stay in the old one. The container ID does not change, so an ID check cannot see it.
- **Fix:** a sidecar healer restarts a node's sidecars on the node's start event. A recreated node (new ID) is handled by the `sidecars` role on the next playbook run. Proven both ways: with the healer stopped the exporters stay down.

## 2026-10-10: a log test that could never fail

- **Symptom:** the route-change test passed in 0.09 s.
- **Cause:** it searched Loki's last hour for a fixed IP, so lines from earlier runs satisfied it.
- **Fix:** a random address per run and a query start time taken before the change.

## 2026-10-10: `docker pause` is not a server crash

- **Observation:** pausing wg-1 moved traffic to wg-2 in 0.95 s with no lost pings.
- **Why:** pause freezes processes, but WireGuard and forwarding live in the kernel, so wg-1 kept forwarding. Only bgpd/bfdd froze, and BFD noticed.
- **So:** `pause` simulates a control-plane hang. A real crash is `docker kill` (0.21 s to switch in a first run) or a blackholed path. The chaos suite covers both separately.
