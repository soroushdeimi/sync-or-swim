# 5. No Automatic Database Promotion

Date: 2026-10-10

## Status

Accepted

## Context

The lab architecture includes two database nodes: `mysql-1` (designated primary) and `mysql-2` (designated replica), synchronizing via MySQL 8.4 GTID replication with row-based binary logging.

A core question in high-availability database engineering is whether to implement automated database promotion (making `mysql-2` the primary automatically if `mysql-1` appears unreachable).

## Decision

We deliberately decided **against automatic failover / promotion** of the database replica in this two-node topology without a third quorum witness or fencing mechanism.

Key technical and operational reasons:
1. **The split-brain hazard:** In a 2-node topology across two geographic sites, a complete network partition (`AllPathsDown`) cannot be distinguished from a true primary node crash. If the replica automatically promoted itself during a network partition, both nodes would accept writes simultaneously, producing catastrophic split-brain data corruption.
2. **Data safety over automatic write availability:** In SRE and database operations, losing writes or corrupting transactional history is significantly worse than temporary write unavailability.
3. **Hardened replica safeguards:**
   - The replica enforces `read_only = ON` and `super_read_only = ON` to prevent accidental writes.
   - Replication uses GTID auto-positioning (`SOURCE_AUTO_POSITION = 1`) and SSL encryption.
   - Network layer auto-recovers: The underlying network routes fail over automatically (via BGP/BFD), allowing the replica to reconnect to the primary loopback without database-level reconfiguration.

### What Production Would Use

In a true production deployment requiring automated database failover, we would implement:
1. **Three-node quorum consensus:** A third node acting as an orchestrator / witness (e.g. Orchestrator, Raft consensus group in MySQL Group Replication, or etcd).
2. **STONITH / Fencing:** Automated hardware or cloud API fencing (Shoot The Other Node In The Head) to guarantee the old primary is dead before promoting a replica.
3. **Virtual IP / Proxy routing:** ProxySQL or HAProxy fronting the database tier to dynamically steer application traffic to the active primary.

## Consequences

### Positive
- Zero risk of split-brain data corruption or conflicting GTID transactions.
- Failover at the network level is completely transparent to the database tier: if `wg-1` drops, BGP reroutes replication packets through `wg-2` without restarting or repointing MySQL.

### Negative
- If `mysql-1` suffers an unrecoverable host failure, promoting `mysql-2` to primary requires an explicit manual administrative procedure or external orchestrator intervention.
