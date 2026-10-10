# sync-or-swim

A highly available network link between two MySQL servers in two "geographic" sites, built for the ArvanCloud SRE challenge.

Two WireGuard relay paths (`wg-1` primary, `wg-2` backup) connect the sites. A routing protocol picks the path. Primary/Replica replication runs between stable loopback addresses, so a path failure only changes the next hop. The whole lab runs in Docker, and Ansible creates and configures all of it.

## Status

| Milestone | State |
|---|---|
| 1. Lab infrastructure via Ansible (image, networks, containers, loopbacks) | done |
| 2. WireGuard tunnels (4 × /31) | next |
| 3. BGP + BFD with FRR, primary/backup policy, source-IP fix | |
| 4. MySQL Primary/Replica over the loopbacks | |
| 5. Monitoring and alerting (Prometheus, Alertmanager, Grafana) | |
| 6. Central logging (Loki + Alloy) | |
| 7. Chaos tests, measured results, bonus items | |

## Topology (current)

```
 transit-a 172.31.1.0/24 (internal)   mysql-1 .11 ── wg-1 .21 ── mysql-2 .12
 transit-b 172.31.2.0/24 (internal)   mysql-1 .11 ── wg-2 .22 ── mysql-2 .12
 mgmt      172.31.100.0/24            every node; monitoring and logs only
```

| Node | Loopback (dummy0) | Interfaces |
|---|---|---|
| mysql-1 | 10.255.0.1/32 | ul-a, ul-b, mgmt0 |
| mysql-2 | 10.255.0.2/32 | ul-a, ul-b, mgmt0 |
| wg-1 | 10.255.0.11/32 | ul-a, mgmt0 |
| wg-2 | 10.255.0.12/32 | ul-b, mgmt0 |

## Quick start

Prerequisites: Linux with Docker 28 or later and the Compose v2 plugin, the WireGuard kernel module (`sudo modprobe wireguard`), Python 3, and make.

```bash
make deps   # .venv with pinned Ansible/pytest/linters + Ansible collections
make up     # build the image and start the lab (a second run reports changed=0)
make test   # integration tests
make lint   # yamllint, ansible-lint (production profile), shellcheck
make down   # remove everything
```

## Design notes

- **Ansible owns everything.** `lab_infra` renders `build/compose.yml` from the inventory and brings it up with `community.docker.docker_compose_v2`. Nobody edits a compose file by hand.
- **Configuration is not baked into images.** The node image contains only packages. Ansible renders idempotent scripts into `build/nodes/<host>/init.d/`, which is bind-mounted at `/etc/sos`. `sos-apply` runs them on every container start (kernel state such as `dummy0` does not survive a restart) and again whenever Ansible changes a script.
- **Content-addressed image tag.** The tag is a hash of the build context, so changing the Dockerfile rebuilds the image and recreates the containers, while an unchanged context keeps the run at `changed=0`.
- **Interfaces are named by network** (`ul-a`, `ul-b`, `mgmt0`) instead of relying on Docker's `eth*` ordering.
- **Sysctls are set through compose**, because `/proc/sys` is read-only in containers. `rp_filter=2` (loose) is required because failover makes paths asymmetric.
- **Management is separated from the data plane.** The default route uses `mgmt0` (`gw_priority`). The transit networks are `internal` and carry only tunnel traffic.

The problem log from building the lab is in [docs/notes.md](docs/notes.md).
