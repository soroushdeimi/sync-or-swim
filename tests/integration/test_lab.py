import datetime
import subprocess
import time

import pytest
import testinfra

LOOPBACKS = {
    "mysql-1": "10.255.0.1",
    "mysql-2": "10.255.0.2",
    "wg-1": "10.255.0.11",
    "wg-2": "10.255.0.12",
}

SYSCTLS = {
    "net.ipv4.ip_forward": 1,
    "net.ipv4.conf.all.rp_filter": 2,
    "net.ipv4.conf.default.rp_filter": 2,
    "net.ipv4.fib_multipath_hash_policy": 1,
}


def node(name):
    return testinfra.get_host(f"docker://{name}")


def can_ping(src, dst):
    return node(src).run(f"ping -c 1 -W 1 {dst}").rc == 0


@pytest.mark.parametrize("name,loopback", LOOPBACKS.items())
def test_loopback_on_dummy0(name, loopback):
    out = node(name).check_output("ip -4 -br addr show dev dummy0")
    assert f"{loopback}/32" in out


@pytest.mark.parametrize("name", LOOPBACKS)
@pytest.mark.parametrize("key,value", SYSCTLS.items())
def test_sysctls(name, key, value):
    assert node(name).sysctl(key) == value


@pytest.mark.parametrize("name", LOOPBACKS)
def test_interfaces_named_by_network(name):
    links = node(name).check_output("ip -br link")
    assert "mgmt0" in links


@pytest.mark.parametrize(
    "src,dst",
    [
        ("mysql-1", "172.31.1.21"),  # transit-a -> wg-1
        ("mysql-2", "172.31.1.21"),
        ("mysql-1", "172.31.2.22"),  # transit-b -> wg-2
        ("mysql-2", "172.31.2.22"),
    ],
)
def test_underlay_reachability(src, dst):
    assert can_ping(src, dst)


def test_paths_are_isolated():
    # wg-1 sits only on transit-a and wg-2 only on transit-b.
    assert not node("wg-1").interface("ul-b").exists
    assert not node("wg-2").interface("ul-a").exists


def test_default_route_is_mgmt():
    route = node("mysql-1").check_output("ip -4 route show default")
    assert "dev mgmt0" in route


@pytest.mark.slow
def test_restart_restores_state():
    subprocess.run(["docker", "restart", "mysql-1"], check=True, capture_output=True)
    deadline = time.time() + 30
    while time.time() < deadline:
        out = node("mysql-1").run("ip -4 -br addr show dev dummy0")
        if out.rc == 0 and "10.255.0.1/32" in out.stdout:
            break
        time.sleep(1)
    else:
        pytest.fail("dummy0 loopback not restored after docker restart")

    # sidecars joined the old netns; the healer must restart them into the new one
    while time.time() < deadline:
        if subprocess.run(["curl", "-sf", "-m", "2", "-o", "/dev/null", "http://172.31.100.11:9100/metrics"]).returncode == 0:
            break
        time.sleep(1)
    else:
        pytest.fail("mysql-1 sidecars did not come back after the node restarted")

    # leave the lab as we found it: the healer restarts mysql-1-db one by one with the
    # other sidecars, so wait for a db that started after the node, then for replication
    node_started = started_at("mysql-1")
    deadline = time.time() + 120
    while time.time() < deadline:
        db_restarted = started_at("mysql-1-db") > node_started
        healthy = inspect("mysql-1-db", "{{.State.Health.Status}}") == "healthy"
        if db_restarted and healthy and replica_io_running():
            return
        time.sleep(2)
    pytest.fail("mysql-1-db or replication did not recover after mysql-1 restarted")


def inspect(container, fmt):
    return subprocess.run(["docker", "inspect", "-f", fmt, container], capture_output=True, text=True).stdout.strip()


def started_at(container):
    # docker trims trailing zeros from the fraction, so parse instead of comparing strings
    stamp = inspect(container, "{{.State.StartedAt}}").rstrip("Z")
    whole, _, frac = stamp.partition(".")
    return datetime.datetime.fromisoformat(whole).timestamp() + float(f"0.{frac or 0}")


def replica_io_running():
    out = subprocess.run(
        ["docker", "exec", "mysql-2-db", "sh", "-c", 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql -uroot -e "SHOW REPLICA STATUS\\G"'],
        capture_output=True, text=True,
    ).stdout
    return "Replica_IO_Running: Yes" in out and "Replica_SQL_Running: Yes" in out
