"""Chaos test suite fixtures: steady-state check, prober, sequence writer, teardown."""

import json
from pathlib import Path
import subprocess
import threading
import time
import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BUILD_DIR = REPO_ROOT / "build"
RESULTS_JSONL = BUILD_DIR / "chaos-results.jsonl"

with open(REPO_ROOT / "inventory/docker/group_vars/all/lab.yml") as f:
    LAB_CONFIG = yaml.safe_load(f)

with open(REPO_ROOT / "inventory/docker/group_vars/all/network.yml") as f:
    NETWORK_CONFIG = yaml.safe_load(f)

MGMT_SERVICES = LAB_CONFIG.get("mgmt_services", {})
NODES = ["mysql-1", "mysql-2", "wg-1", "wg-2"]


def mysql_query(container, query):
    # the password stays inside the container so it never shows up in test output
    res = subprocess.run(
        ["docker", "exec", container, "sh", "-c", 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql -uroot -NB -e "$0"', query],
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        raise AssertionError(f"query failed on {container}: {query[:80]}: {res.stderr.strip()}")
    return res.stdout.strip()


def parse_replica_status(status_raw):
    status = {}
    for line in status_raw.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            status[k.strip()] = v.strip()
    return status


def mysql_replica_status(container):
    # plain -e keeps the "Key: value" labels that -NB strips
    res = subprocess.run(
        ["docker", "exec", container, "sh", "-c", 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql -uroot -e "SHOW REPLICA STATUS\\G"'],
        capture_output=True, text=True, check=True,
    )
    return parse_replica_status(res.stdout)


def vtysh_json(name, cmd, timeout=5):
    res = subprocess.run(
        ["docker", "exec", name, "vtysh", "-c", cmd],
        capture_output=True, text=True, check=True, timeout=timeout
    )
    return json.loads(res.stdout)


def check_steady_state():
    """Asserts that the lab is in full steady state."""
    # 1. BGP established on all nodes
    for n in NODES:
        data = vtysh_json(n, "show ip bgp summary json")
        peers = data.get("peers") or data.get("ipv4Unicast", {}).get("peers", {})
        assert len(peers) == 2, f"Node {n} has {len(peers)} peers, expected 2"
        for p_ip, p_info in peers.items():
            assert p_info.get("state") == "Established", (
                f"Peer {p_ip} on {n} is {p_info.get('state')}, expected Established"
            )

    # 2. BFD peers up on all nodes
    for n in NODES:
        data = vtysh_json(n, "show bfd peers json")
        peers = data if isinstance(data, list) else data.get("peers", [])
        assert len(peers) == 2, f"Node {n} has {len(peers)} BFD peers, expected 2"
        for p in peers:
            assert p.get("status") == "up", (
                f"BFD peer {p.get('peer')} on {n} status is {p.get('status')}, expected up"
            )

    # 3. Route mysql-1 -> 10.255.0.2 via wg-via1
    out = subprocess.check_output(
        ["docker", "exec", "mysql-1", "ip", "-j", "route", "get", "10.255.0.2"],
        text=True,
    )
    routes = json.loads(out)
    assert routes and routes[0].get("dev") == "wg-via1", (
        f"mysql-1 route to 10.255.0.2 is {routes[0].get('dev') if routes else 'none'}, expected wg-via1"
    )

    # 4. Replication running
    st = mysql_replica_status("mysql-2-db")
    assert st.get("Replica_IO_Running") == "Yes", f"Replica_IO_Running is {st.get('Replica_IO_Running')}"
    assert st.get("Replica_SQL_Running") == "Yes", f"Replica_SQL_Running is {st.get('Replica_SQL_Running')}"


def restore_lab():
    """Guaranteed restore of all lab components."""
    # 1. Unpause any paused containers
    for n in ["wg-1", "wg-2", "mysql-1", "mysql-2", "mysql-1-db", "mysql-2-db"]:
        subprocess.run(["docker", "unpause", n], capture_output=True)

    # 2. Start any stopped containers
    for n in ["wg-1", "wg-2", "mysql-1", "mysql-2", "mysql-1-db", "mysql-2-db"]:
        subprocess.run(["docker", "start", n], capture_output=True)

    # 3. Stop prober in mysql-1
    subprocess.run(["docker", "exec", "mysql-1", "pkill", "-f", "probe.sh"], capture_output=True)

    # 4. Remove nft rules and tc qdiscs, ensure links up
    for n in ["mysql-1", "mysql-2", "wg-1", "wg-2"]:
        subprocess.run(["docker", "exec", n, "nft", "delete", "table", "inet", "chaos"], capture_output=True)
        for iface in ["ul-a", "ul-b"]:
            subprocess.run(["docker", "exec", n, "tc", "qdisc", "del", "dev", iface, "root"], capture_output=True)
            subprocess.run(["docker", "exec", n, "ip", "link", "set", iface, "up"], capture_output=True)
        for iface in ["wg-via1", "wg-via2", "wg-to-m1", "wg-to-m2"]:
            subprocess.run(["docker", "exec", n, "ip", "link", "set", iface, "up"], capture_output=True)

    # 5. Ensure replication is running
    try:
        mysql_query("mysql-2-db", "START REPLICA;")
    except Exception:
        pass

    # 6. Wait for steady state to settle (up to 30s)
    deadline = time.time() + 30.0
    while time.time() < deadline:
        try:
            check_steady_state()
            return
        except Exception:
            time.sleep(1.0)


class Prober:
    """Prober running inside mysql-1 namespace."""

    def __init__(self, node="mysql-1", dst_ip="10.255.0.2", src_ip="10.255.0.1"):
        self.node = node
        self.dst_ip = dst_ip
        self.src_ip = src_ip
        self.log_file = "/tmp/probe.log"

    def start(self):
        script_src = REPO_ROOT / "scripts" / "probe.sh"
        subprocess.run(["docker", "cp", str(script_src), f"{self.node}:/tmp/probe.sh"], check=True)
        subprocess.run(["docker", "exec", self.node, "chmod", "+x", "/tmp/probe.sh"], check=True)
        # Kill any prior prober
        subprocess.run(["docker", "exec", self.node, "pkill", "-f", "probe.sh"], capture_output=True)
        # Start detached
        cmd = ["docker", "exec", "-d", self.node, "bash", "/tmp/probe.sh", self.log_file, self.dst_ip, self.src_ip]
        subprocess.run(cmd, check=True)
        time.sleep(0.1)

    def stop(self):
        subprocess.run(["docker", "exec", self.node, "pkill", "-f", "probe.sh"], capture_output=True)

    def get_samples(self):
        out = subprocess.check_output(["docker", "exec", self.node, "cat", self.log_file], text=True)
        samples = []
        for line in out.splitlines():
            parts = line.strip().split()
            if len(parts) >= 3:
                try:
                    ts = int(parts[0])
                    dev = parts[1]
                    ok = int(parts[2])
                    samples.append({"ts": ts, "dev": dev, "ok": ok})
                except ValueError:
                    continue
        return samples

    def compute_metrics(self, fault_ms, recover_ms=None, backup_dev="wg-via2", primary_dev="wg-via1"):
        samples = self.get_samples()
        switch_s = None
        for s in samples:
            if s["ts"] >= fault_ms and s["dev"] == backup_dev:
                switch_s = max(0.0, round((s["ts"] - fault_ms) / 1000.0, 3))
                break

        lost_pings = sum(1 for s in samples if s["ts"] >= fault_ms and s["ok"] == 0)

        failback_s = None
        if recover_ms:
            for s in samples:
                if s["ts"] >= recover_ms and s["dev"] == primary_dev:
                    failback_s = max(0.0, round((s["ts"] - recover_ms) / 1000.0, 3))
                    break

        return switch_s, lost_pings, failback_s


class SequenceWriter:
    """Writes auto-increment sequence rows into primary mysql."""

    def __init__(self):
        self.stop_event = threading.Event()
        self.thread = None
        self.errors = 0
        self.inserted_count = 0

    def start(self):
        mysql_query(
            "mysql-1-db",
            "CREATE DATABASE IF NOT EXISTS sre; "
            "CREATE TABLE IF NOT EXISTS sre.seq ("
            "id INT AUTO_INCREMENT PRIMARY KEY, ts TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP(6)"
            "); "
            "TRUNCATE TABLE sre.seq;"
        )
        self.stop_event.clear()
        self.errors = 0
        self.inserted_count = 0
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        while not self.stop_event.is_set():
            try:
                mysql_query("mysql-1-db", "INSERT INTO sre.seq () VALUES ();")
                self.inserted_count += 1
            except Exception:
                self.errors += 1
            time.sleep(0.1)

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=3.0)

    def verify_no_gaps(self, timeout=20.0):
        """Asserts replica has every id with no gaps."""
        deadline = time.time() + timeout
        p_count, p_min, p_max = None, None, None

        # Fetch primary bounds
        p_raw = mysql_query("mysql-1-db", "SELECT count(id), coalesce(min(id),0), coalesce(max(id),0) FROM sre.seq;")
        p_vals = [int(x) for x in p_raw.split()]
        p_count, p_min, p_max = p_vals[0], p_vals[1], p_vals[2]
        assert p_count > 0, "No rows were inserted on primary"
        assert p_count == p_max - p_min + 1, f"Primary itself has gaps: count={p_count}, min={p_min}, max={p_max}"

        # Poll replica until count matches
        r_count, r_min, r_max = 0, 0, 0
        while time.time() < deadline:
            try:
                r_raw = mysql_query("mysql-2-db", "SELECT count(id), coalesce(min(id),0), coalesce(max(id),0) FROM sre.seq;")
                r_vals = [int(x) for x in r_raw.split()]
                r_count, r_min, r_max = r_vals[0], r_vals[1], r_vals[2]
                if r_count == p_count:
                    break
            except Exception:
                pass
            time.sleep(0.5)

        assert p_count == r_count, f"Replica count {r_count} does not match primary count {p_count}"
        assert p_min == r_min, f"Replica min {r_min} does not match primary min {p_min}"
        assert p_max == r_max, f"Replica max {r_max} does not match primary max {p_max}"
        assert r_count == r_max - r_min + 1, f"Replica has gaps: count={r_count}, min={r_min}, max={r_max}"
        return True


def record_result(scenario, switch_s, lost_pings, failback_s, seq_ok, notes):
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    entry = {
        "scenario": scenario,
        "switch_s": switch_s,
        "lost_pings": lost_pings,
        "failback_s": failback_s,
        "seq_ok": seq_ok,
        "notes": notes,
    }
    with open(RESULTS_JSONL, "a") as f:
        f.write(json.dumps(entry) + "\n")


@pytest.fixture
def prober():
    p = Prober()
    yield p
    p.stop()


@pytest.fixture
def seq_writer():
    w = SequenceWriter()
    yield w
    w.stop()


@pytest.fixture(autouse=True)
def steady_state(request):
    """Guaranteed pre-check and guaranteed restore in teardown."""
    request.addfinalizer(restore_lab)
    check_steady_state()
    yield


def pytest_configure(config):
    config.addinivalue_line("markers", "chaos: automated failure scenarios with precise measurements")
