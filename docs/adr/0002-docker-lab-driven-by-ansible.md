# 2. Docker Lab Driven by Ansible

Date: 2026-10-10

## Status

Accepted

## Context

The evaluation requires demonstrating a multi-site high-availability architecture with redundant network relays, dynamic routing, and database replication. We needed an environment capable of running the entire infrastructure locally, in CI pipelines, and reproducibly on any evaluation machine.

The two main deployment models considered:
1. Virtual machines (e.g., Vagrant with Libvirt/KVM or VirtualBox).
2. Linux container lab driven by Ansible and Docker Compose.

## Decision

We decided to build the lab entirely using **Docker containers managed strictly through Ansible**, using network namespaces, unbaked container images, and host bind mounts.

Key architectural characteristics:
1. **Ansible drives compose:** Ansible renders `build/compose.yml` from inventory definitions and manages lifecycle through Docker Compose v2. No files are edited manually.
2. **Clean image / runtime separation:** The container image contains only standard packages (iproute2, WireGuard tools, FRR, client tools). Node-specific configuration (loopback IP, WireGuard keys, FRR configs) is rendered by Ansible into `/etc/sos/init.d/` and applied on startup via `sos-apply`.
3. **CI portability:** Runs cleanly inside standard GitHub Actions runners without requiring nested virtualization or hardware acceleration.

### Engineering Reality: Docker Containers vs VMs

Using Docker containers for network infrastructure introduces specific differences compared to hardware VMs:
- **Shared Linux kernel:** WireGuard encryption and IP packet forwarding execute inside the host Linux kernel.
- **`docker pause` vs `docker kill`:** As documented in `docs/notes.md`, `docker pause` freezes user-space processes (pausing `bgpd` and `bfdd`), but does **not** stop kernel-space forwarding. The paused container continues forwarding data-plane traffic until BFD detects control-plane loss (0.95 s). In contrast, `docker kill` halts both control and data planes immediately (0.21 s failover).
- **Read-only `/proc/sys`:** Container network sysctls (`net.ipv4.ip_forward`, `rp_filter=2`, `fib_multipath_hash_policy=1`) cannot be modified via `sysctl -w` inside running unprivileged containers and must be set declaratively in the compose definition.

## Consequences

### Positive
- Fully automated deployment via `make up` with zero manual intervention.
- Automated CI testing: Complete integration test suite runs in standard GitHub Actions runners.

### Negative
- Containers share the host kernel; kernel-level network state (interfaces, routes, dummy devices) does not persist across container reboots, requiring the `sos-apply` initialization hook.
- Requires `CAP_NET_ADMIN` and `CAP_SYS_ADMIN` for FRR 10.3 daemon privilege handling.
