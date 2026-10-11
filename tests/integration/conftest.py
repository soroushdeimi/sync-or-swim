import json
import subprocess
import time

import pytest

NODES = ("mysql-1", "mysql-2", "wg-1", "wg-2")


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: restarts containers; deselect with -m 'not slow'")


def bgp_converged():
    for name in NODES:
        out = subprocess.run(["docker", "exec", name, "vtysh", "-c", "show ip bgp summary json"], capture_output=True, text=True).stdout
        try:
            peers = json.loads(out)["ipv4Unicast"]["peers"]
        except (ValueError, KeyError):
            return False
        if len(peers) != 2 or any(p["state"] != "Established" for p in peers.values()):
            return False
    route = subprocess.run(["docker", "exec", "mysql-1", "ip", "-j", "route", "get", "10.255.0.2"], capture_output=True, text=True).stdout
    return '"dev":"wg-via1"' in route


@pytest.fixture(autouse=True)
def converged_lab():
    # Faults injected by an earlier test hold wg-1 back on purpose: 30 s delayopen, and
    # dampening can suppress it for up to 4 minutes. Every test starts from a converged lab.
    deadline = time.time() + 300
    while not bgp_converged():
        if time.time() > deadline:
            pytest.fail("lab did not converge (BGP Established everywhere, traffic on wg-1) within 300 s")
        time.sleep(2)
