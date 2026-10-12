# 7. Per-node default-drop firewall

Date: 2026-10-12

## Status

Accepted

## Context

BGP, BFD, WireGuard, MySQL and the exporters all listen on the nodes. BGP already has MD5, GTSM and prefix lists, but nothing stopped another host on the same network from reaching `bgpd`, the WireGuard ports or an exporter. A relay also forwards packets by design, and a MySQL node must never do so.

## Decision

Every node loads an nftables table `inet sos_fw` in its own network namespace, with `policy drop` on input and forward. The rules are rendered by Ansible from the inventory (tunnels, ports, loopbacks, addresses), so nothing is written by hand per node:

- WireGuard UDP only from the tunnel's own peer, on the underlay that tunnel uses.
- BGP (TCP 179) and BFD (UDP 3784, 3785, 4784) only on the tunnel interfaces.
- MySQL (3306) only from the other MySQL node's loopback.
- Exporter ports only from Prometheus and the Docker host, on the management interface.
- ICMP, loopback and established connections are accepted.
- Relays forward only between their own tunnels; MySQL nodes forward nothing.
- Named counters on the final rule of each chain show how much is dropped.

The ruleset is checked with `nft -c` before it is installed, and an init script (`05-firewall.sh`, the first one to run) loads it on every container start, so a restarted node is never unprotected.

## Consequences

- The tests act as a stranger: a container on the same network tries the exporters and `bgpd` and must fail, while the Docker host and the real peers keep working. Without the firewall that connection to `bgpd` completes.
- The firewall protects each node from its neighbours on the lab networks. It does not replace a host firewall on the machine that runs Docker.
- A new service on a node needs an explicit rule, which is intended, but it is one more place to forget.
- Outbound traffic is not filtered.
