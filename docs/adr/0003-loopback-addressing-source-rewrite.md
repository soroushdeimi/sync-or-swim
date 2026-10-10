# 3. Loopback Addressing and Source Address Rewrite

Date: 2026-10-10

## Status

Accepted

## Context

MySQL database replication relies on persistent, long-lived TCP connections between primary and replica servers. The lab features two separate WireGuard tunnels (`wg-via1` via `wg-1` and `wg-via2` via `wg-2`), each with distinct point-to-point IP subnets (`10.10.11.0/31`, `10.10.21.0/31`, etc.).

If MySQL replication were bound directly to the IP address of a specific tunnel interface (`wg-via1`):
1. A failure of relay `wg-1` would tear down that IP interface.
2. The MySQL replica TCP connection would break and fail to reconnect, because the source IP address would no longer exist on the node.
3. Applications connecting to the database would need to update their connection endpoints on every network failover.

## Decision

We decouple service endpoints from physical and tunnel interfaces using **stable loopback addresses (`dummy0`) combined with FRR source-address rewriting**.

Implementation details:
1. **Loopback configuration:** Each node assigns a stable `/32` IP to a `dummy0` device on startup (`mysql-1`: `10.255.0.1/32`, `mysql-2`: `10.255.0.2/32`).
2. **eBGP advertisement:** Nodes advertise only their own `/32` loopback prefix via eBGP. Relays propagate these loopback routes across the topology.
3. **Source IP rewrite (`ip protocol bgp route-map SET-SRC`):** By default in Linux, outgoing packets pick the IP address of the egress interface as the source address (e.g. `10.10.11.0`). If traffic switches to `wg-via2`, outgoing packets would suddenly change source IP to `10.10.21.0`, breaking active TCP state. In FRR, we configure:
   ```
   route-map SET-SRC permit 10
    set src <loopback_ip>
   exit
   !
   ip protocol bgp route-map SET-SRC
   ```
   This ensures that all routes learned via BGP are installed into the Linux kernel FIB with `src <loopback_ip>`.
4. **Service binding:** MySQL and replication threads bind exclusively to loopback addresses (`SOURCE_HOST='10.255.0.1'`, `SOURCE_BIND='10.255.0.2'`).

## Consequences

### Positive
- During manual failover runs, the replication connection survived without reconnecting when the next hop changed to `wg-2`.
- Seamless service migration: Services bind to invariant IP addresses regardless of underlying tunnel state.

### Negative
- Requires `dummy` kernel module and initialization script (`00-loopback.sh`) inside each container namespace.
- Requires loose reverse path filtering (`net.ipv4.conf.all.rp_filter = 2`) across all interfaces to accommodate asymmetric return routing.
