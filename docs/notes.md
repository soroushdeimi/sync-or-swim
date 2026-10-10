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
