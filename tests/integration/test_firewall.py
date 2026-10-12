import json
import pathlib
import re
import subprocess
import time
import urllib.request

import pytest
import testinfra
import yaml

ROOT = pathlib.Path(__file__).parents[2]
GROUP = ROOT / "inventory/docker/group_vars/all"
LAB = yaml.safe_load((GROUP / "lab.yml").read_text())
NETWORK = yaml.safe_load((GROUP / "network.yml").read_text())
HOSTS = {n: yaml.safe_load((ROOT / f"inventory/docker/host_vars/{n}.yml").read_text()) for n in ("mysql-1", "mysql-2", "wg-1", "wg-2")}
MYSQL = ("mysql-1", "mysql-2")
RELAYS = ("wg-1", "wg-2")
PROJECT = LAB["lab_project"]
IFNAME = {net: cfg["ifname"] for net, cfg in LAB["lab_networks"].items()}


def node(name):
    return testinfra.get_host(f"docker://{name}")


def nft(name, *args):
    return subprocess.run(["docker", "exec", name, "nft", *args], capture_output=True, text=True)


def chain_text(name, chain):
    out = nft(name, "list", "chain", "inet", "sos_fw", chain)
    assert out.returncode == 0, f"{name} has no sos_fw firewall: {out.stderr.strip()}"
    return out.stdout


def chain_policy(name, chain):
    rules = json.loads(nft(name, "-j", "list", "chain", "inet", "sos_fw", chain).stdout)["nftables"]
    return next(r["chain"]["policy"] for r in rules if "chain" in r)


def local_tunnels(name):
    for t in NETWORK["wg_tunnels"]:
        for mine, peer in ((t["a"], t["b"]), (t["b"], t["a"])):
            if mine["host"] == name:
                yield {
                    "ifname": mine["ifname"],
                    "port": mine["port"],
                    "underlay": IFNAME[t["underlay"]],
                    "peer_ip": HOSTS[peer["host"]]["lab_links"][t["underlay"]],
                }


def image_of(name):
    return subprocess.run(["docker", "inspect", "-f", "{{.Config.Image}}", name], capture_output=True, text=True).stdout.strip()


def from_stranger(network, ip, script):
    """Run a shell snippet from an unknown host that sits on the same network as the lab."""
    cmd = ["docker", "run", "--rm", "--network", f"{PROJECT}_{network}", "--ip", ip, image_of("mysql-1"), "bash", "-c", script]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120)


def cannot_connect(network, stranger_ip, target_ip, port):
    res = from_stranger(network, stranger_ip, f"timeout 3 bash -c 'echo > /dev/tcp/{target_ip}/{port}'")
    return res.returncode != 0


def dropped(name):
    out = nft(name, "-j", "list", "counter", "inet", "sos_fw", "input_dropped").stdout
    return next(o["counter"]["packets"] for o in json.loads(out)["nftables"] if "counter" in o)


@pytest.mark.parametrize("name", HOSTS)
@pytest.mark.parametrize("chain", ["input", "forward"])
def test_default_is_drop(name, chain):
    assert chain_policy(name, chain) == "drop"


@pytest.mark.parametrize("name", HOSTS)
def test_wireguard_only_from_its_own_peer(name):
    text = chain_text(name, "input")
    for t in local_tunnels(name):
        rule = f'iifname "{t["underlay"]}" ip saddr {t["peer_ip"]} udp dport {t["port"]} accept'
        assert rule in text, f"missing: {rule}"
    # no WireGuard port is open to every source
    assert not re.search(r"^\s*udp dport (51821|51822)", text, re.M)


@pytest.mark.parametrize("name", MYSQL)
def test_mysql_port_open_only_to_the_peer_loopback(name):
    peer = next(n for n in MYSQL if n != name)
    assert f'ip saddr {HOSTS[peer]["loopback_ip"]} tcp dport 3306 accept' in chain_text(name, "input")


@pytest.mark.parametrize("name", RELAYS)
def test_relays_do_not_open_mysql(name):
    assert "3306" not in chain_text(name, "input")


@pytest.mark.parametrize("name", MYSQL)
def test_mysql_nodes_never_forward(name):
    # non-transit by firewall as well as by route policy
    assert "accept" not in chain_text(name, "forward")


@pytest.mark.parametrize("name", RELAYS)
def test_relays_forward_only_between_tunnels(name):
    forward = chain_text(name, "forward")
    ifs = {t["ifname"] for t in local_tunnels(name)}
    assert all(f'"{i}"' in forward for i in ifs)
    assert forward.count("accept") == 1


EXPORTER_PORT = LAB["exporter_ports"]["node"]


@pytest.mark.parametrize("name", HOSTS)
def test_exporter_reachable_from_the_docker_host_but_not_from_a_stranger(name):
    ip = HOSTS[name]["lab_links"]["mgmt"]
    # control: the trusted path works, so a failing stranger is the firewall and not a dead exporter
    with urllib.request.urlopen(f"http://{ip}:{EXPORTER_PORT}/metrics", timeout=5) as resp:
        assert resp.status == 200
    assert cannot_connect("mgmt", "172.31.100.200", ip, EXPORTER_PORT), f"a stranger on mgmt reached {name}:{EXPORTER_PORT}"


@pytest.mark.parametrize("name", RELAYS)
def test_bgp_port_closed_to_strangers_on_the_underlay(name):
    # bgpd listens on every address; without the firewall this connection completes
    underlay = next(net for net in HOSTS[name]["lab_links"] if net != "mgmt")
    target = HOSTS[name]["lab_links"][underlay]
    stranger = target.rsplit(".", 1)[0] + ".200"
    assert cannot_connect(underlay, stranger, target, 179), f"a stranger on {underlay} reached bgpd on {name}"


def test_strangers_wireguard_packets_are_dropped_and_counted():
    name = "wg-1"
    port = next(t["port"] for t in local_tunnels(name))
    target = HOSTS[name]["lab_links"]["transit-a"]
    time.sleep(3)
    quiet_before = dropped(name)
    time.sleep(3)
    noise = dropped(name) - quiet_before

    before = dropped(name)
    res = from_stranger("transit-a", "172.31.1.200", f"for i in $(seq 25); do echo x > /dev/udp/{target}/{port}; done")
    assert res.returncode == 0, res.stderr
    sent = dropped(name) - before
    assert sent >= 25, f"only {sent} of 25 stranger packets were counted as dropped"
    assert noise < 25, f"background drops ({noise} in 3 s) are too high to trust this count"


@pytest.mark.parametrize("name", HOSTS)
def test_applying_twice_does_not_duplicate_rules(name):
    def rules():
        text = nft(name, "list", "table", "inet", "sos_fw").stdout
        return re.sub(r"packets \d+ bytes \d+", "", text)

    first = rules()
    for _ in range(2):
        assert nft(name, "-f", "/etc/sos/firewall.nft").returncode == 0
    assert rules() == first
    assert nft(name, "list", "tables").stdout.count("table inet sos_fw") == 1


@pytest.mark.slow
def test_firewall_is_back_after_a_node_restarts():
    subprocess.run(["docker", "restart", "wg-2"], check=True, capture_output=True)
    deadline = time.time() + 60
    while time.time() < deadline:
        if nft("wg-2", "list", "chain", "inet", "sos_fw", "input").returncode == 0:
            break
        time.sleep(1)
    else:
        pytest.fail("wg-2 came back without its firewall")
    assert chain_policy("wg-2", "input") == "drop"
