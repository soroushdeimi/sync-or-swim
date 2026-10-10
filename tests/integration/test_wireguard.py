import ipaddress
import json
import pathlib
import time
import pytest
import testinfra
import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
NETWORK_VARS = yaml.safe_load((REPO_ROOT / "inventory/docker/group_vars/all/network.yml").read_text())

WG_TUNNELS = NETWORK_VARS["wg_tunnels"]
WG_MTU = NETWORK_VARS.get("wg_mtu", 1420)

ENDPOINTS = []
for tunnel in WG_TUNNELS:
    ENDPOINTS.append({
        "id": tunnel["id"],
        "host": tunnel["a"]["host"],
        "ifname": tunnel["a"]["ifname"],
        "ip": tunnel["a"]["ip"],
        "peer_host": tunnel["b"]["host"],
        "peer_ip": tunnel["b"]["ip"],
    })
    ENDPOINTS.append({
        "id": tunnel["id"],
        "host": tunnel["b"]["host"],
        "ifname": tunnel["b"]["ifname"],
        "ip": tunnel["b"]["ip"],
        "peer_host": tunnel["a"]["host"],
        "peer_ip": tunnel["a"]["ip"],
    })

ALL_HOSTS = sorted({ep["host"] for ep in ENDPOINTS})


def node(name):
    return testinfra.get_host(f"docker://{name}")


@pytest.mark.parametrize("host", ALL_HOSTS)
def test_private_key_permissions(host):
    key_file = node(host).file("/etc/sos/wireguard/private.key")
    assert key_file.exists
    assert key_file.mode == 0o600


@pytest.mark.parametrize("ep", ENDPOINTS, ids=lambda ep: f"{ep['host']}-{ep['ifname']}")
def test_interface_and_mtu(ep):
    n = node(ep["host"])
    assert n.interface(ep["ifname"]).exists
    addr_out = n.check_output(f"ip -4 -br addr show dev {ep['ifname']}")
    assert f"{ep['ip']}/31" in addr_out
    mtu = int(n.check_output(f"cat /sys/class/net/{ep['ifname']}/mtu"))
    assert mtu == WG_MTU


@pytest.mark.parametrize("ep", ENDPOINTS, ids=lambda ep: f"{ep['host']}->{ep['peer_ip']}")
def test_ping_peer(ep):
    res = node(ep["host"]).run(f"ping -c 1 -W 2 {ep['peer_ip']}")
    assert res.rc == 0


@pytest.mark.parametrize("ep", ENDPOINTS, ids=lambda ep: f"{ep['host']}-{ep['ifname']}")
def test_latest_handshakes(ep):
    out = node(ep["host"]).check_output(f"wg show {ep['ifname']} latest-handshakes").strip()
    assert out, f"No handshake output for {ep['ifname']} on {ep['host']}"
    parts = out.split()
    assert len(parts) >= 2
    handshake_ts = int(parts[1])
    assert handshake_ts > 0
    assert (time.time() - handshake_ts) < 180


@pytest.mark.parametrize("ep", ENDPOINTS, ids=lambda ep: f"{ep['host']}-{ep['ifname']}")
def test_no_routes_besides_connected_and_bgp(ep):
    # wg must not install routes; only the connected /31 and FRR's routes may use the tunnel
    routes = node(ep["host"]).check_output(f"ip -j route show dev {ep['ifname']}")
    expected_network = str(ipaddress.ip_interface(f"{ep['ip']}/31").network)
    for route in json.loads(routes):
        assert route["dst"] != "default"
        assert route["dst"] == expected_network or route.get("protocol") == "bgp", route


def test_sos_apply_idempotency():
    for host in ALL_HOSTS:
        n = node(host)
        assert n.run("/usr/local/bin/sos-apply").rc == 0
        assert n.run("/usr/local/bin/sos-apply").rc == 0

    for ep in ENDPOINTS:
        assert node(ep["host"]).run(f"ping -c 1 -W 2 {ep['peer_ip']}").rc == 0
