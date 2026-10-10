import json
from pathlib import Path
import random
import subprocess
import time
import urllib.parse
import urllib.request
import pytest
import testinfra
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LAB_VARS_PATH = REPO_ROOT / "inventory/docker/group_vars/all/lab.yml"
with open(LAB_VARS_PATH, encoding="utf-8") as f:
    lab_vars = yaml.safe_load(f)

loki_ip = lab_vars["mgmt_services"]["loki"]
LOKI_URL = f"http://{loki_ip}:3100"


def query_loki(query, timeout=20, start_ns=None):
    params = {"query": query}
    if start_ns is not None:
        params["start"] = str(start_ns)
    url = f"{LOKI_URL}/loki/api/v1/query_range?{urllib.parse.urlencode(params)}"
    deadline = time.time() + timeout
    last_err = None
    while time.time() < deadline:
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=2) as resp:
                data = json.loads(resp.read().decode())
                if data.get("status") == "success":
                    results = data.get("data", {}).get("result", [])
                    if results:
                        return results
        except Exception as e:
            last_err = e
        time.sleep(1)
    if last_err is not None:
        raise AssertionError(f"Loki query {query!r} failed after {timeout}s: {last_err}")
    return []


def test_loki_ready():
    url = f"{LOKI_URL}/ready"
    deadline = time.time() + 20
    last_err = None
    while time.time() < deadline:
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=2) as resp:
                if resp.status == 200 and "ready" in resp.read().decode().lower():
                    return
        except Exception as e:
            last_err = e
        time.sleep(1)
    pytest.fail(f"Loki /ready failed: {last_err}")


def test_service_labels_include_sidecars():
    url = f"{LOKI_URL}/loki/api/v1/label/service/values"
    deadline = time.time() + 20
    values = []
    while time.time() < deadline:
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=2) as resp:
                data = json.loads(resp.read().decode())
                if data.get("status") == "success":
                    values = data.get("data", [])
                    if "route-monitor" in values and "wg-watch" in values:
                        return
        except Exception:
            pass
        time.sleep(1)
    assert "route-monitor" in values
    assert "wg-watch" in values


def test_route_monitor_logs_route_change():
    node = testinfra.get_host("docker://mysql-1")
    o3 = random.randint(0, 255)
    o4 = random.randint(1, 254)
    test_ip = f"198.18.{o3}.{o4}"
    start_ns = time.time_ns()
    try:
        node.check_output(f"ip route add {test_ip}/32 dev dummy0")
        results = query_loki(
            f'{{service="route-monitor", node="mysql-1"}} |= "{test_ip}"',
            timeout=20,
            start_ns=start_ns,
        )
        assert results, f"Route change {test_ip} did not appear in Loki within 20s"
        lines = [entry[1] for stream in results for entry in stream.get("values", [])]
        assert any(test_ip in line for line in lines)
    finally:
        node.run(f"ip route del {test_ip}/32 dev dummy0")


def test_wg_watch_logs_state():
    results = query_loki('{service="wg-watch"}', timeout=20)
    assert results, "wg-watch logs did not appear in Loki within 20s"
    lines = [entry[1] for stream in results for entry in stream.get("values", [])]
    assert any("wg peer=" in line and "state=" in line for line in lines)


def test_wg_watch_initial_state_on_restart():
    node = testinfra.get_host("docker://mysql-1")
    start_ns = time.time_ns()
    try:
        node.check_output("ip link set wg-via2 down")
        subprocess.run(["docker", "restart", "mysql-1-wg-watch"], check=True)
        results = query_loki(
            '{service="wg-watch", node="mysql-1"} |= "wg peer=wg-via2"',
            timeout=20,
            start_ns=start_ns,
        )
        assert results, "Initial state line for wg-via2 did not appear after restart"
        lines = [entry[1] for stream in results for entry in stream.get("values", [])]
        assert any("wg peer=wg-via2 state=" in line for line in lines)
    finally:
        node.run("ip link set wg-via2 up")


def test_frr_stream_exists():
    results = query_loki('{service="frr"}', timeout=20)
    assert results, "FRR log stream not found in Loki"


def test_mysql_stream_exists():
    results = query_loki('{service="mysql"}', timeout=20)
    assert results, "MySQL log stream not found in Loki"
