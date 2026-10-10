"""Milestone 3: eBGP + BFD with FRR, primary/backup policy, source-address fix."""

import json
import pytest
import testinfra

NODES = ["mysql-1", "mysql-2", "wg-1", "wg-2"]


def node(name):
    return testinfra.get_host(f"docker://{name}")


def vtysh_json(name, cmd):
    out = node(name).check_output(f"vtysh -c '{cmd}'")
    return json.loads(out)


@pytest.mark.parametrize("name", NODES)
def test_bgp_neighbors_established(name):
    data = vtysh_json(name, "show ip bgp summary json")
    peers = data.get("peers") or data.get("ipv4Unicast", {}).get("peers", {})
    assert len(peers) == 2, f"Expected 2 BGP peers on {name}, got {len(peers)}"
    for peer_ip, peer_info in peers.items():
        assert peer_info.get("state") == "Established", (
            f"Peer {peer_ip} on {name} is {peer_info.get('state')}, expected Established"
        )


@pytest.mark.parametrize("name", NODES)
def test_bfd_peers_up(name):
    data = vtysh_json(name, "show bfd peers json")
    peers = data if isinstance(data, list) else data.get("peers", [])
    assert len(peers) == 2, f"Expected 2 BFD peers on {name}, got {len(peers)}"
    for peer in peers:
        assert peer.get("status") == "up", (
            f"BFD peer {peer.get('peer')} on {name} status is {peer.get('status')}, expected up"
        )


def test_mysql1_bestpath_via_wg1():
    data = vtysh_json("mysql-1", "show ip bgp 10.255.0.2/32 json")
    paths = data.get("paths", [])
    assert len(paths) == 2, f"Expected 2 BGP paths for 10.255.0.2/32 on mysql-1, got {len(paths)}"
    best_paths = [p for p in paths if p.get("bestpath")]
    assert len(best_paths) == 1, "Expected exactly 1 bestpath"
    best = best_paths[0]
    nexthop_ips = [nh.get("ip") for nh in best.get("nexthops", [])]
    assert "10.10.11.1" in nexthop_ips, (
        f"Expected best path via wg-1 (10.10.11.1), got nexthops: {nexthop_ips}"
    )
    # Check local preferences in active_backup mode
    loc_prefs = {nh.get("ip"): p.get("locPrf") for p in paths for nh in p.get("nexthops", [])}
    assert loc_prefs.get("10.10.11.1") == 200, f"Expected locPrf 200 via wg-1, got {loc_prefs}"
    assert loc_prefs.get("10.10.21.1") == 100, f"Expected locPrf 100 via wg-2, got {loc_prefs}"


@pytest.mark.parametrize(
    "src_node,dst_ip,expected_dev,expected_src",
    [
        ("mysql-1", "10.255.0.2", "wg-via1", "10.255.0.1"),
        ("mysql-2", "10.255.0.1", "wg-via1", "10.255.0.2"),
    ],
)
def test_mysql_kernel_routes(src_node, dst_ip, expected_dev, expected_src):
    out = node(src_node).check_output(f"ip -j route get {dst_ip}")
    routes = json.loads(out)
    assert len(routes) >= 1
    route = routes[0]
    assert route.get("dev") == expected_dev, (
        f"Expected dev {expected_dev} on {src_node} to {dst_ip}, got {route.get('dev')}"
    )
    assert route.get("prefsrc") == expected_src, (
        f"Expected prefsrc {expected_src} on {src_node} to {dst_ip}, got {route.get('prefsrc')}"
    )


def test_ping_between_mysql_loopbacks():
    res = node("mysql-1").run("ping -c 3 -I 10.255.0.1 10.255.0.2")
    assert res.rc == 0, f"Ping failed: {res.stderr}\n{res.stdout}"


@pytest.mark.parametrize(
    "node_name,peer_ip,expected_prefix",
    [
        ("mysql-1", "10.10.11.1", "10.255.0.1/32"),
        ("mysql-1", "10.10.21.1", "10.255.0.1/32"),
        ("mysql-2", "10.10.12.1", "10.255.0.2/32"),
        ("mysql-2", "10.10.22.1", "10.255.0.2/32"),
    ],
)
def test_mysql_advertises_only_own_loopback(node_name, peer_ip, expected_prefix):
    data = vtysh_json(node_name, f"show ip bgp neighbors {peer_ip} advertised-routes json")
    adv = data.get("advertisedRoutes", {})
    assert list(adv.keys()) == [expected_prefix], (
        f"{node_name} advertised routes to {peer_ip}: {list(adv.keys())}, expected [{expected_prefix}]"
    )
