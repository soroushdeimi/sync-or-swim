# 1. eBGP over OSPF

Date: 2026-10-10

## Status

Accepted

## Context

The challenge requires dynamic routing across two separate transit relays (`wg-1` primary, `wg-2` backup) interconnecting two geographic database sites (`mysql-1` and `mysql-2`). The routing protocol must detect path failures, switch traffic automatically without manual intervention, and support path preference policies (such as preferring `wg-1` over `wg-2` or supporting ECMP).

The primary routing protocol options considered for this architecture were:
1. Interior Gateway Protocol (OSPFv2).
2. Exterior Gateway Protocol (eBGP).

## Decision

We chose **eBGP** (External Border Gateway Protocol) paired with BFD (Bidirectional Forwarding Detection) using FRRouting (FRR 10.3). Each node is assigned a distinct Autonomous System Number (ASN):
- `mysql-1`: AS 65001
- `mysql-2`: AS 65002
- `wg-1`: AS 65101
- `wg-2`: AS 65102

Key technical reasons:
1. **Explicit policy control:** BGP provides fine-grained, policy-based path selection using path attributes (such as `LOCAL_PREF` and route maps). OSPF cost metrics are uniform within an area and harder to enforce across administrative and organizational boundaries.
2. **Failure domain isolation:** OSPF synchronizes full link-state databases (LSDB) across all routers within an area. A flapping link in an OSPF domain triggers Dijkstra (SPF) recalculations on every node. With eBGP, routes are isolated per peer, and prefix updates are incremental.
3. **Loop prevention:** eBGP inherently prevents routing loops using the `AS_PATH` attribute.
4. **Transit filtering and boundary security:** eBGP allows strict inbound and outbound prefix filtering (`prefix-list PL-IN`, `prefix-list PL-OUT`), preventing relays from accidentally leaking unintended routes or acting as open transits.

## Consequences

### Positive
- Predictable path selection: `LOCAL_PREF 200` on primary relay `wg-1` and `LOCAL_PREF 100` on backup relay `wg-2` guarantees deterministic active/backup behavior.
- Support for ECMP: Switching `routing_mode` to `ecmp` enables multipath routing (`maximum-paths 2`, `bgp bestpath as-path multipath-relax`) without topology restructuring.
- Strict prefix filtering at every administrative boundary prevents rogue route leaks.

### Negative
- BGP default keepalive (60 s) and hold timers (180 s) are too slow for sub-second failover. This requires pairing BGP with BFD (300 ms timers) on all peerings.
- Configuration verbosity: BGP requires explicit neighbor definitions, update-sources, prefix lists, and route maps on every router.
