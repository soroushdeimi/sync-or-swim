"""Integration tests for route flap prevention, delayed failback, and ECMP mode."""

import json
from pathlib import Path
import subprocess
import threading
import time
import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

with open(REPO_ROOT / "roles" / "frr" / "defaults" / "main.yml") as f:
    HOLD_S = yaml.safe_load(f).get("frr_failback_hold_s", 30)

with open(REPO_ROOT / "inventory" / "docker" / "group_vars" / "all" / "network.yml") as f:
    NET_VARS = yaml.safe_load(f)

MYSQL_2_IP = "10.255.0.2"


def get_route(node, dst_ip):
    out = subprocess.check_output(
        ["docker", "exec", node, "ip", "-j", "route", "get", dst_ip],
        text=True,
    )
    routes = json.loads(out)
    return routes[0] if routes else {}


def wait_for_route_dev(node, dst_ip, expected_dev, timeout=45.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        route = get_route(node, dst_ip)
        if route.get("dev") == expected_dev:
            return True
        time.sleep(0.2)
    return False


def wait_for_bgp_established(nodes=("mysql-1", "mysql-2", "wg-1", "wg-2"), timeout=45.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        all_ok = True
        for name in nodes:
            try:
                out = subprocess.check_output(
                    ["docker", "exec", name, "vtysh", "-c", "show ip bgp summary json"],
                    text=True,
                )
                data = json.loads(out)
                peers = data.get("peers") or data.get("ipv4Unicast", {}).get("peers", {})
                if len(peers) < 2 or not all(p.get("state") == "Established" for p in peers.values()):
                    all_ok = False
                    break
            except Exception:
                all_ok = False
                break
        if all_ok:
            return True
        time.sleep(1.0)
    return False


@pytest.mark.slow
def test_delayed_failback():
    """Traffic switches to wg-via2 within 1.5s, stays on wg-via2 during hold-down, returns to wg-via1."""
    assert wait_for_route_dev("mysql-1", MYSQL_2_IP, "wg-via1", timeout=45.0), "Initial path not wg-via1"

    t_fault = time.time()
    try:
        subprocess.run(["docker", "exec", "mysql-1", "nft", "add", "table", "inet", "chaos"], check=True)
        subprocess.run(
            ["docker", "exec", "mysql-1", "nft", "add chain inet chaos output { type filter hook output priority 0; policy accept; }"],
            check=True,
        )
        subprocess.run(
            ["docker", "exec", "mysql-1", "nft", "add", "rule", "inet", "chaos", "output", "oifname", "ul-a", "udp", "dport", "51821", "drop"],
            check=True,
        )

        deadline = t_fault + 3.0
        switched = False
        switch_s = None
        while time.time() < deadline:
            dev = get_route("mysql-1", MYSQL_2_IP).get("dev")
            if dev == "wg-via2":
                switch_s = time.time() - t_fault
                switched = True
                break
            time.sleep(0.02)

        assert switched, "Traffic did not switch to wg-via2 after blocking primary WireGuard port"
        assert switch_s <= 1.5, f"Switch time {switch_s}s exceeded 1.5s threshold"

        time_to_wait = max(0.0, 5.0 - (time.time() - t_fault))
        time.sleep(time_to_wait)
    finally:
        t_recover = time.time()
        subprocess.run(["docker", "exec", "mysql-1", "nft", "delete", "table", "inet", "chaos"], check=False)

    expected_hold = HOLD_S if HOLD_S > 0 else 30
    min_hold = expected_hold - 5.0
    max_failback = expected_hold + 20.0

    deadline = t_recover + max_failback + 5.0
    failed_back = False
    failback_s = None
    while time.time() < deadline:
        dev = get_route("mysql-1", MYSQL_2_IP).get("dev")
        if dev == "wg-via1":
            failback_s = time.time() - t_recover
            failed_back = True
            break
        time.sleep(0.2)

    assert failed_back, f"Traffic did not return to wg-via1 within {max_failback}s after fault removed"
    assert failback_s >= min_hold, f"Failback time {failback_s:.2f}s occurred too early; hold-down of {min_hold}s not enforced"
    assert failback_s <= max_failback, f"Failback time {failback_s:.2f}s exceeded {max_failback}s"


@pytest.mark.slow
def test_flapping_route_flap_prevention():
    """Repeated link flaps on wg-via1 cause at most 2 path changes and traffic stays on wg-via2."""
    assert wait_for_route_dev("mysql-1", MYSQL_2_IP, "wg-via1", timeout=45.0), "Initial path not wg-via1"

    out = subprocess.check_output(
        ["docker", "exec", "mysql-1", "vtysh", "-c", "show ip bgp summary json"],
        text=True,
    )
    summary = json.loads(out)
    damp_enabled = summary.get("ipv4Unicast", {}).get("dampeningEnabled", False)
    assert damp_enabled, "BGP route-flap dampening is not enabled on mysql-1"

    samples = []
    stop_sampling = False

    def sampler():
        while not stop_sampling:
            try:
                dev = get_route("mysql-1", MYSQL_2_IP).get("dev")
                samples.append({"ts": time.time(), "dev": dev})
            except Exception:
                pass
            time.sleep(0.5)

    sampler_thread = threading.Thread(target=sampler)
    sampler_thread.start()

    try:
        # Flap 1 down: must switch to wg-via2 within 1.5s
        t_f1 = time.time()
        subprocess.run(["docker", "exec", "mysql-1", "ip", "link", "set", "wg-via1", "down"], check=True)
        deadline = t_f1 + 3.0
        switched = False
        while time.time() < deadline:
            if get_route("mysql-1", MYSQL_2_IP).get("dev") == "wg-via2":
                switched = True
                break
            time.sleep(0.02)
        assert switched, "Traffic did not switch to wg-via2 on first flap"
        t_f1_switched = time.time()
        time.sleep(max(0.0, 3.0 - (time.time() - t_f1)))

        subprocess.run(["docker", "exec", "mysql-1", "ip", "link", "set", "wg-via1", "up"], check=True)
        time.sleep(3.0)

        # Flaps 2 to 5: bring down/up 3s apart
        for _ in range(4):
            subprocess.run(["docker", "exec", "mysql-1", "ip", "link", "set", "wg-via1", "down"], check=True)
            time.sleep(3.0)
            subprocess.run(["docker", "exec", "mysql-1", "ip", "link", "set", "wg-via1", "up"], check=True)
            time.sleep(3.0)
        t_flapping_done = time.time()

        time.sleep(60.0)
    finally:
        stop_sampling = True
        sampler_thread.join()
        subprocess.run(["docker", "exec", "mysql-1", "ip", "link", "set", "wg-via1", "up"], check=False)

    unstable_samples = [s for s in samples if s["ts"] >= t_f1_switched and s["ts"] <= t_flapping_done]
    for s in unstable_samples:
        assert s["dev"] == "wg-via2", f"Traffic left wg-via2 while wg-1 was unstable: {s}"

    path_changes = 0
    for i in range(1, len(samples)):
        prev_dev = samples[i - 1]["dev"]
        curr_dev = samples[i]["dev"]
        if curr_dev != prev_dev and curr_dev in ("wg-via1", "wg-via2") and prev_dev in ("wg-via1", "wg-via2"):
            path_changes += 1

    assert path_changes <= 2, f"Path changed {path_changes} times during flapping, expected at most 2"


@pytest.mark.slow
def test_ecmp_mode():
    """ECMP mode installs two nexthops; restoring active_backup returns to single nexthop via wg-via1."""
    def run_playbook(extra_vars=None):
        cmd = [str(REPO_ROOT / ".venv" / "bin" / "ansible-playbook"), str(REPO_ROOT / "playbooks" / "frr.yml")]
        if extra_vars:
            for k, v in extra_vars.items():
                cmd.extend(["-e", f"{k}={v}"])
        res = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
        assert res.returncode == 0, f"Playbook run failed:\n{res.stdout}\n{res.stderr}"

    try:
        run_playbook({"routing_mode": "ecmp"})

        deadline = time.time() + 15.0
        two_nexthops = False
        routes = []
        while time.time() < deadline:
            out = subprocess.check_output(
                ["docker", "exec", "mysql-1", "ip", "-j", "route", "show", MYSQL_2_IP],
                text=True,
            )
            routes = json.loads(out)
            if routes:
                nexthops = routes[0].get("nexthops", [])
                devs = {nh.get("dev") for nh in nexthops}
                if devs == {"wg-via1", "wg-via2"}:
                    two_nexthops = True
                    break
            time.sleep(0.5)

        assert two_nexthops, f"Expected two nexthops in ECMP mode, got: {routes}"
    finally:
        run_playbook()

    deadline = time.time() + 45.0
    single_nexthop = False
    routes = []
    while time.time() < deadline:
        out = subprocess.check_output(
            ["docker", "exec", "mysql-1", "ip", "-j", "route", "show", MYSQL_2_IP],
            text=True,
        )
        routes = json.loads(out)
        if routes and routes[0].get("dev") == "wg-via1" and not routes[0].get("nexthops"):
            single_nexthop = True
            break
        time.sleep(0.5)

    assert single_nexthop, f"Expected single nexthop via wg-via1 in active_backup mode, got: {routes}"
    assert wait_for_bgp_established(), "BGP sessions not all Established after restoring active_backup"
