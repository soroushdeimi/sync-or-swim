import subprocess
import urllib.request
import pytest

NODES = {
    "mysql-1": "172.31.100.11",
    "mysql-2": "172.31.100.12",
    "wg-1": "172.31.100.21",
    "wg-2": "172.31.100.22",
}

MYSQL_NODES = {
    "mysql-1": "172.31.100.11",
    "mysql-2": "172.31.100.12",
}

EXPORTER_PORTS = {
    "node": 9100,
    "frr": 9342,
    "wireguard": 9586,
}


def fetch(url, timeout=5):
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode("utf-8", errors="replace")


@pytest.mark.parametrize("name,ip", NODES.items())
@pytest.mark.parametrize("exporter,port", EXPORTER_PORTS.items())
def test_node_wide_exporters_answer_200(name, ip, exporter, port):
    status, _ = fetch(f"http://{ip}:{port}/metrics")
    assert status == 200


@pytest.mark.parametrize("name,ip", MYSQL_NODES.items())
@pytest.mark.parametrize("exporter,port", [("blackbox", 9115), ("mysqld", 9104)])
def test_mysql_exporters_answer_200(name, ip, exporter, port):
    status, _ = fetch(f"http://{ip}:{port}/metrics")
    assert status == 200


@pytest.mark.parametrize("name,ip", MYSQL_NODES.items())
def test_node_exporter_exposes_sre_active_path(name, ip):
    status, body = fetch(f"http://{ip}:9100/metrics")
    assert status == 200
    assert "sre_active_path" in body


@pytest.mark.parametrize("name,ip", NODES.items())
def test_frr_exporter_exposes_frr_bgp(name, ip):
    status, body = fetch(f"http://{ip}:9342/metrics")
    assert status == 200
    assert "frr_bgp" in body


@pytest.mark.parametrize("name,ip", NODES.items())
def test_wireguard_exporter_exposes_handshake(name, ip):
    status, body = fetch(f"http://{ip}:9586/metrics")
    assert status == 200
    assert "wireguard_latest_handshake_seconds" in body


@pytest.mark.parametrize("name,ip", MYSQL_NODES.items())
def test_mysqld_exporter_mysql_up(name, ip):
    status, body = fetch(f"http://{ip}:9104/metrics")
    assert status == 200
    assert "mysql_up 1" in body


def test_blackbox_icmp_loopback_probe():
    url = "http://172.31.100.11:9115/probe?module=icmp_loopback&target=10.255.0.2"
    status, body = fetch(url)
    assert status == 200
    assert "probe_success 1" in body


def test_sidecars_share_current_node_network_namespace():
    # Detects stale sidecar network namespaces after a node recreate.
    node_ids = {
        name: subprocess.check_output(
            ["docker", "inspect", "--format", "{{.Id}}", name],
            text=True,
        ).strip()
        for name in NODES
    }

    exporter_ids = subprocess.check_output(
        [
            "docker", "ps", "-aq",
            "--filter", "label=com.docker.compose.project=sync-or-swim-exporters",
        ],
        text=True,
    ).split()
    assert exporter_ids

    lines = subprocess.check_output(
        [
            "docker", "inspect",
            "--format", '{{.Name}} {{index .Config.Labels "sos.node"}} {{.HostConfig.NetworkMode}}',
        ] + exporter_ids,
        text=True,
    ).splitlines()

    for line in lines:
        name, node_name, net_mode = line.split()
        assert net_mode == f"container:{node_ids[node_name]}", f"{name} is not attached to current {node_name}"
