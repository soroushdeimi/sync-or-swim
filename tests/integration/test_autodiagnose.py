"""Integration tests for automated diagnostics tool and alert webhook service."""

import sys
import json
from pathlib import Path
import subprocess
import time
import urllib.parse
import urllib.request
import uuid
import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

with open(REPO_ROOT / "inventory/docker/group_vars/all/lab.yml", encoding="utf-8") as f:
    LAB_VARS = yaml.safe_load(f)

with open(REPO_ROOT / "inventory/docker/group_vars/all/network.yml", encoding="utf-8") as f:
    NETWORK_VARS = yaml.safe_load(f)

with open(REPO_ROOT / "inventory/docker/host_vars/wg-1.yml", encoding="utf-8") as f:
    WG1_VARS = yaml.safe_load(f)

LOKI_IP = LAB_VARS["mgmt_services"]["loki"]
LOKI_URL = f"http://{LOKI_IP}:3100"
WEBHOOK_IP = LAB_VARS["mgmt_services"]["alert-webhook"]
WEBHOOK_PORT = 9095
WEBHOOK_URL = f"http://{WEBHOOK_IP}:{WEBHOOK_PORT}"

# Locate wg-1 tunnel from mysql-1
WG1_PORT = None
WG1_UNDERLAY_IP = None
for tun in NETWORK_VARS.get("wg_tunnels", []):
    if tun.get("a", {}).get("host") == "mysql-1" and tun.get("b", {}).get("host") == "wg-1":
        WG1_PORT = tun.get("b", {}).get("port", 51821)
        underlay = tun.get("underlay", "transit-a")
        WG1_UNDERLAY_IP = WG1_VARS.get("lab_links", {}).get(underlay)
        break


def query_loki(query: str, timeout: int = 20, start_ns: int = None):
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


def test_diagnose_healthy():
    """diagnose.py exits 0 on the healthy lab."""
    script_path = REPO_ROOT / "scripts" / "diagnose.py"
    proc = subprocess.run(
        [sys.executable, str(script_path), "--node", "mysql-1", "--json"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"Expected exit code 0, got {proc.returncode}: {proc.stderr or proc.stdout}"
    data = json.loads(proc.stdout)
    assert data["status"] == "healthy"
    assert data["exit_code"] == 0
    assert "healthy" in data["verdict"].lower()


def test_diagnose_degraded_dropped_udp():
    """With UDP to wg-1 dropped on mysql-1, diagnose.py exits 1 and names wg-1 in verdict."""
    script_path = REPO_ROOT / "scripts" / "diagnose.py"
    table_name = "test_drop_wg1"

    # Block UDP to wg-1's WireGuard port on transit network
    subprocess.run(["docker", "exec", "mysql-1", "nft", "add", "table", "inet", table_name], check=True)
    try:
        subprocess.run(
            [
                "docker",
                "exec",
                "mysql-1",
                "nft",
                "add",
                "chain",
                "inet",
                table_name,
                "output",
                "{ type filter hook output priority 0; }",
            ],
            check=True,
        )
        rule_spec = f"udp dport {WG1_PORT} ip daddr {WG1_UNDERLAY_IP} drop"
        subprocess.run(
            ["docker", "exec", "mysql-1", "nft", "add", "rule", "inet", table_name, "output"]
            + rule_spec.split(),
            check=True,
        )

        # Allow BFD to detect loss (300ms * 3 = 900ms) and traffic to shift to wg-2
        time.sleep(1.5)

        proc = subprocess.run(
            [sys.executable, str(script_path), "--node", "mysql-1", "--json"],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 1, (
            f"Expected exit code 1 for degraded state, got {proc.returncode}: {proc.stderr or proc.stdout}"
        )
        data = json.loads(proc.stdout)
        assert data["exit_code"] == 1
        assert data["status"] == "degraded"
        assert "wg-1" in data["verdict"], f"Expected 'wg-1' in verdict: {data['verdict']}"
    finally:
        subprocess.run(
            ["docker", "exec", "mysql-1", "nft", "delete", "table", "inet", table_name],
            capture_output=True,
        )
        # Settle back to primary path
        deadline = time.time() + 15.0
        while time.time() < deadline:
            p = subprocess.run(
                [sys.executable, str(script_path), "--node", "mysql-1", "--json"],
                capture_output=True,
                text=True,
            )
            if p.returncode == 0:
                break
            time.sleep(1.0)


def test_webhook_loki_log():
    """POSTing a fake critical alert produces an autodiagnose alert= line in Loki."""
    start_ns = time.time_ns()
    test_alert_name = f"FakeCriticalAlert_{uuid.uuid4().hex[:8]}"

    payload = {
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": test_alert_name,
                    "instance": "mysql-1",
                    "severity": "critical",
                },
                "annotations": {
                    "summary": "Integration test fake critical alert",
                },
            }
        ],
    }

    req = urllib.request.Request(
        WEBHOOK_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        assert resp.status == 200

    query = f'{{service="autodiagnose"}} |= "autodiagnose alert={test_alert_name}"'
    results = query_loki(query, timeout=20, start_ns=start_ns)
    assert results, f"Log line for {test_alert_name} not found in Loki"

    lines = [entry[1] for stream in results for entry in stream.get("values", [])]
    assert any(
        f"autodiagnose alert={test_alert_name}" in line and "verdict=" in line
        for line in lines
    ), f"Matching autodiagnose log line missing in Loki: {lines}"
