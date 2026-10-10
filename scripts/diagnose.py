#!/usr/bin/env python3
"""Automated troubleshooting: checks the lab stack layer by layer from bottom up."""

import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
import yaml


def find_inventory_dir() -> Path:
    script_dir = Path(__file__).resolve().parent
    repo_inv = script_dir.parent / "inventory" / "docker"
    if repo_inv.exists():
        return repo_inv
    if Path("/inventory/docker").exists():
        return Path("/inventory/docker")
    if Path("/inventory").exists():
        return Path("/inventory")
    raise FileNotFoundError("Could not locate inventory directory")


def load_inventory(inv_dir: Path):
    all_vars = {}
    group_vars_all = inv_dir / "group_vars" / "all"
    for f in sorted(group_vars_all.glob("*.yml")):
        with open(f, "r", encoding="utf-8") as fp:
            data = yaml.safe_load(fp)
            if data:
                all_vars.update(data)

    host_vars = {}
    host_vars_dir = inv_dir / "host_vars"
    for f in sorted(host_vars_dir.glob("*.yml")):
        with open(f, "r", encoding="utf-8") as fp:
            data = yaml.safe_load(fp)
            if data:
                host_vars[f.stem] = data

    return all_vars, host_vars


def docker_exec(container: str, cmd: list, timeout: int = 5) -> subprocess.CompletedProcess:
    full_cmd = ["docker", "exec", container] + cmd
    return subprocess.run(full_cmd, capture_output=True, text=True, timeout=timeout)


def diagnose(node: str = "mysql-1") -> dict:
    inv_dir = find_inventory_dir()
    all_vars, host_vars = load_inventory(inv_dir)

    primary_relay = all_vars.get("primary_relay", "wg-1")
    mysql_primary = all_vars.get("mysql_primary", "mysql-1")
    mysql_replica = all_vars.get("mysql_replica", "mysql-2")
    peer_node = mysql_replica if node == mysql_primary else mysql_primary

    own_loopback = host_vars.get(node, {}).get("loopback_ip", "10.255.0.1")
    peer_loopback = host_vars.get(peer_node, {}).get("loopback_ip", "10.255.0.2")

    tunnels = []
    for t in all_vars.get("wg_tunnels", []):
        if t.get("a", {}).get("host") == node:
            relay = t.get("b", {}).get("host")
            underlay_net = t.get("underlay")
            relay_underlay_ip = host_vars.get(relay, {}).get("lab_links", {}).get(underlay_net)
            tunnels.append({
                "id": t.get("id"),
                "ifname": t.get("a", {}).get("ifname"),
                "relay": relay,
                "relay_tunnel_ip": t.get("b", {}).get("ip"),
                "relay_port": t.get("b", {}).get("port"),
                "underlay": underlay_net,
                "relay_underlay_ip": relay_underlay_ip,
            })

    checks = []
    stopped_at = None

    # L1: Underlay
    l1_results = {}
    for tun in tunnels:
        relay = tun["relay"]
        u_ip = tun["relay_underlay_ip"]
        res = docker_exec(node, ["ping", "-c", "1", "-W", "1", u_ip])
        ok = (res.returncode == 0)
        l1_results[relay] = ok
        checks.append({
            "layer": "L1",
            "name": f"underlay ping to {relay}",
            "status": "OK" if ok else "FAIL",
            "evidence": f"{u_ip}: ping OK (0% packet loss)" if ok else f"{u_ip}: ping failed",
            "relay": relay,
        })

    if not any(l1_results.values()):
        stopped_at = "L1"
        verdict = f"Root cause likely: All underlay network links down on {node}. Cannot reach any relay."
        return {
            "node": node,
            "status": "broken",
            "exit_code": 2,
            "verdict": verdict,
            "checks": checks,
        }

    # L2: Tunnels
    l2_results = {}
    now_epoch = int(time.time())
    try:
        t_res = docker_exec(node, ["date", "+%s"])
        if t_res.returncode == 0:
            now_epoch = int(t_res.stdout.strip())
    except Exception:
        pass

    for tun in tunnels:
        relay = tun["relay"]
        ifn = tun["ifname"]
        peer_ip = tun["relay_tunnel_ip"]

        hs_res = docker_exec(node, ["wg", "show", ifn, "latest-handshakes"])
        epoch = 0
        if hs_res.returncode == 0 and hs_res.stdout.strip():
            parts = hs_res.stdout.strip().split()
            if len(parts) >= 2:
                epoch = int(parts[1])

        age = (now_epoch - epoch) if epoch > 0 else None
        stale = (age is None or age > 180)

        ping_res = docker_exec(node, ["ping", "-c", "1", "-W", "1", peer_ip])
        ping_ok = (ping_res.returncode == 0)

        tun_ok = (not stale) and ping_ok
        l2_results[relay] = {
            "ok": tun_ok,
            "age": age,
            "stale": stale,
            "ping_ok": ping_ok,
        }

        if tun_ok:
            ev = f"{ifn} ({peer_ip}): handshake {age}s ago, ping OK"
        else:
            hs_desc = "handshake never completed" if age is None else (f"handshake stale ({age}s > 180s)" if stale else f"handshake {age}s ago")
            p_desc = "ping OK" if ping_ok else "ping failed"
            ev = f"{ifn} ({peer_ip}): {hs_desc}, {p_desc}"

        checks.append({
            "layer": "L2",
            "name": f"WireGuard tunnel {ifn} to {relay}",
            "status": "OK" if tun_ok else "FAIL",
            "evidence": ev,
            "relay": relay,
        })

    if not any(r["ok"] for r in l2_results.values()):
        stopped_at = "L2"
        verdict = f"Root cause likely: All WireGuard tunnels down on {node}. Handshakes stale or tunnel unreachable."
        return {
            "node": node,
            "status": "broken",
            "exit_code": 2,
            "verdict": verdict,
            "checks": checks,
        }

    # L3: Control plane
    l3_results = {}
    bfd_map = {}
    try:
        bfd_res = docker_exec(node, ["vtysh", "-c", "show bfd peers json"])
        if bfd_res.returncode == 0:
            b_data = json.loads(bfd_res.stdout)
            peers = b_data if isinstance(b_data, list) else b_data.get("peers", [])
            for p in peers:
                bfd_map[p.get("peer")] = p.get("status")
    except Exception:
        pass

    bgp_map = {}
    try:
        bgp_res = docker_exec(node, ["vtysh", "-c", "show ip bgp summary json"])
        if bgp_res.returncode == 0:
            bgp_data = json.loads(bgp_res.stdout)
            bgp_map = bgp_data.get("peers") or bgp_data.get("ipv4Unicast", {}).get("peers", {})
    except Exception:
        pass

    for tun in tunnels:
        relay = tun["relay"]
        peer_ip = tun["relay_tunnel_ip"]
        bfd_status = bfd_map.get(peer_ip, "down")
        bfd_up = (bfd_status == "up")

        bgp_peer = bgp_map.get(peer_ip, {})
        bgp_state = bgp_peer.get("state", "down")
        pfx_rcd = bgp_peer.get("pfxRcd", 0)
        bgp_ok = (bgp_state == "Established" and pfx_rcd > 0)

        cp_ok = bfd_up and bgp_ok
        l3_results[relay] = {
            "ok": cp_ok,
            "bfd_up": bfd_up,
            "bgp_state": bgp_state,
            "pfx_rcd": pfx_rcd,
        }

        if cp_ok:
            ev = f"BFD up, BGP Established, {pfx_rcd} prefixes received from {peer_ip}"
        else:
            parts = []
            if not bfd_up:
                parts.append(f"BFD {bfd_status}")
            if bgp_state != "Established":
                parts.append(f"BGP {bgp_state}")
            elif pfx_rcd == 0:
                parts.append("BGP pfx=0")
            ev = f"{peer_ip}: {', '.join(parts)}"

        checks.append({
            "layer": "L3",
            "name": f"control plane {relay}",
            "status": "OK" if cp_ok else "FAIL",
            "evidence": ev,
            "relay": relay,
        })

    if not any(r["ok"] for r in l3_results.values()):
        stopped_at = "L3"
        verdict = f"Root cause likely: BGP/BFD control plane down across all paths on {node}."
        return {
            "node": node,
            "status": "broken",
            "exit_code": 2,
            "verdict": verdict,
            "checks": checks,
        }

    # L4: Data plane
    route_res = docker_exec(node, ["ip", "-j", "route", "get", peer_loopback])
    route_ok = False
    active_relay = None
    dev = None
    src = None
    gw = None

    if route_res.returncode == 0:
        try:
            routes = json.loads(route_res.stdout)
            if routes:
                r = routes[0]
                dev = r.get("dev")
                src = r.get("prefsrc")
                gw = r.get("gateway")
                for tun in tunnels:
                    if tun["ifname"] == dev or tun["relay_tunnel_ip"] == gw:
                        active_relay = tun["relay"]
                        break
                if src == own_loopback:
                    route_ok = True
        except Exception:
            pass

    checks.append({
        "layer": "L4",
        "name": f"data plane route to {peer_loopback}",
        "status": "OK" if route_ok else "FAIL",
        "evidence": f"via {active_relay or 'unknown'} (dev {dev}, gw {gw}, src {src})" if route_ok else f"invalid route: dev={dev}, src={src} (expected {own_loopback})",
        "active_relay": active_relay,
    })

    if not route_ok:
        stopped_at = "L4"
        verdict = f"Root cause likely: No valid route to peer loopback {peer_loopback} with source {own_loopback}."
        return {
            "node": node,
            "status": "broken",
            "exit_code": 2,
            "verdict": verdict,
            "checks": checks,
        }

    # L5: Transport
    tcp_py = f"import socket; s = socket.create_connection(('{peer_loopback}', 3306), timeout=3); s.close()"
    tcp_res = docker_exec(node, ["python3", "-c", tcp_py])
    tcp_ok = (tcp_res.returncode == 0)

    checks.append({
        "layer": "L5",
        "name": f"transport TCP connect to {peer_loopback}:3306",
        "status": "OK" if tcp_ok else "FAIL",
        "evidence": "connected successfully" if tcp_ok else "connection failed or timed out",
    })

    if not tcp_ok:
        stopped_at = "L5"
        verdict = f"Root cause likely: Cannot connect to MySQL on {peer_loopback}:3306 from {node}."
        return {
            "node": node,
            "status": "broken",
            "exit_code": 2,
            "verdict": verdict,
            "checks": checks,
        }

    # L6: Application
    replica_container = f"{mysql_replica}-db"
    repl_res = docker_exec(
        replica_container,
        ["sh", "-c", 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql -uroot -e "SHOW REPLICA STATUS\\G"'],
    )
    repl_map = {}
    if repl_res.returncode == 0:
        for line in repl_res.stdout.splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                repl_map[k.strip()] = v.strip()

    lag_res = docker_exec(
        replica_container,
        ["sh", "-c", 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql -uroot -NB -e "SELECT TIMESTAMPDIFF(SECOND, ts, NOW()) FROM sre.heartbeat"'],
    )
    lag = None
    if lag_res.returncode == 0 and lag_res.stdout.strip().isdigit():
        lag = int(lag_res.stdout.strip())

    io_running = repl_map.get("Replica_IO_Running", "No")
    sql_running = repl_map.get("Replica_SQL_Running", "No")
    last_io_err = repl_map.get("Last_IO_Error", "")
    last_sql_err = repl_map.get("Last_SQL_Error", "")

    app_ok = (
        io_running == "Yes"
        and sql_running == "Yes"
        and not last_io_err
        and not last_sql_err
        and (lag is not None and lag <= 10)
    )

    ev_parts = [f"IO={io_running}", f"SQL={sql_running}"]
    if lag is not None:
        ev_parts.append(f"lag={lag}s")
    if last_io_err:
        ev_parts.append(f"Last_IO_Error: {last_io_err}")
    if last_sql_err:
        ev_parts.append(f"Last_SQL_Error: {last_sql_err}")

    checks.append({
        "layer": "L6",
        "name": f"application replication on {replica_container}",
        "status": "OK" if app_ok else "FAIL",
        "evidence": ", ".join(ev_parts),
    })

    if not app_ok:
        stopped_at = "L6"
        err_msg = last_sql_err or last_io_err or f"IO={io_running}, SQL={sql_running}, lag={lag}s"
        verdict = f"Root cause likely: MySQL replication error on {replica_container} ({err_msg})."
        return {
            "node": node,
            "status": "broken",
            "exit_code": 2,
            "verdict": verdict,
            "checks": checks,
        }

    # Evaluate Overall Verdict
    # Check if any lower layer had degraded paths
    degraded_relays = []
    for tun in tunnels:
        relay = tun["relay"]
        l1_ok = l1_results.get(relay, True)
        l2_info = l2_results.get(relay, {"ok": True})
        l3_info = l3_results.get(relay, {"ok": True})
        if not l1_ok or not l2_info.get("ok") or not l3_info.get("ok"):
            degraded_relays.append({
                "relay": relay,
                "l1_ok": l1_ok,
                "l2_info": l2_info,
                "l3_info": l3_info,
                "port": tun["relay_port"],
            })

    if not degraded_relays and active_relay == primary_relay:
        verdict = f"All layers healthy. Traffic is on primary relay {primary_relay}. Replication healthy."
        exit_code = 0
        status = "healthy"
    else:
        # Degraded
        exit_code = 1
        status = "degraded"
        if degraded_relays:
            dr = degraded_relays[0]
            r_name = dr["relay"]
            r_port = dr["port"]
            l2_info = dr["l2_info"]
            l1_ok = dr["l1_ok"]
            if l1_ok and not l2_info.get("ok"):
                age_str = f"{l2_info.get('age')} s" if l2_info.get("age") is not None else "never"
                verdict = (
                    f"Root cause likely: UDP to {r_name}:{r_port} blocked on {node} "
                    f"(handshake stale {age_str}, BFD down). "
                    f"Traffic is on {active_relay}. Replication healthy."
                )
            elif not l1_ok:
                verdict = (
                    f"Root cause likely: Underlay network to {r_name} down on {node}. "
                    f"Traffic is on {active_relay}. Replication healthy."
                )
            else:
                verdict = (
                    f"Root cause likely: Control plane to {r_name} down on {node}. "
                    f"Traffic is on {active_relay}. Replication healthy."
                )
        else:
            verdict = (
                f"Root cause likely: Traffic is on backup relay {active_relay} instead of {primary_relay}. "
                f"Replication healthy."
            )

    return {
        "node": node,
        "status": status,
        "exit_code": exit_code,
        "verdict": verdict,
        "checks": checks,
    }


def main():
    parser = argparse.ArgumentParser(description="Automated troubleshooting layer by layer.")
    parser.add_argument("--node", default="mysql-1", help="Node to diagnose (default: mysql-1)")
    parser.add_argument("--json", action="store_true", help="Print output as JSON")
    args = parser.parse_args()

    result = diagnose(node=args.node)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for c in result.get("checks", []):
            print(f"{c['status']}: {c['layer']} {c['name']}: {c['evidence']}")
        print()
        print(result.get("verdict", ""))

    sys.exit(result.get("exit_code", 0))


if __name__ == "__main__":
    main()
