"""Milestone 4: MySQL 8.4 primary/replica over loopbacks with GTID and heartbeat."""

import datetime
from pathlib import Path
import subprocess
import time
import uuid

import pytest
import testinfra

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def db_node(name):
    return testinfra.get_host(f"docker://{name}")


def mysql_query(container, query):
    # the password stays inside the container so it never shows up in test output
    cmd = ["docker", "exec", container, "sh", "-c", 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql -uroot -NB -e "$0"', query]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise AssertionError(f"query failed on {container}: {query[:80]}: {res.stderr.strip()}")
    return res.stdout.strip()


def parse_replica_status(status_raw):
    """Parses SHOW REPLICA STATUS\\G key-value output."""
    status = {}
    for line in status_raw.splitlines():
        if ":" in line:
            key, val = line.split(":", 1)
            status[key.strip()] = val.strip()
    return status


@pytest.mark.parametrize("container", ["mysql-1-db", "mysql-2-db"])
def test_containers_healthy(container):
    out = subprocess.check_output(
        ["docker", "inspect", "--format", "{{.State.Health.Status}}", container],
        text=True,
    ).strip()
    assert out == "healthy"


@pytest.mark.parametrize("container", ["mysql-1-db", "mysql-2-db"])
def test_gtid_mode_on(container):
    val = mysql_query(container, "SELECT @@gtid_mode;")
    assert val == "ON"


def test_replica_super_read_only():
    primary_ro = mysql_query("mysql-1-db", "SELECT @@super_read_only;")
    replica_ro = mysql_query("mysql-2-db", "SELECT @@super_read_only;")
    assert primary_ro == "0"
    assert replica_ro == "1"


def test_replica_status():
    status_raw = subprocess.check_output(
        ["docker", "exec", "mysql-2-db", "sh", "-c", 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql -uroot -e "SHOW REPLICA STATUS\\G"'],
        text=True,
    )
    status = parse_replica_status(status_raw)
    assert status.get("Source_Host") == "10.255.0.1"
    assert status.get("Replica_IO_Running") == "Yes"
    assert status.get("Replica_SQL_Running") == "Yes"


def test_data_replication():
    mysql_query(
        "mysql-1-db",
        "CREATE DATABASE IF NOT EXISTS sre; "
        "CREATE TABLE IF NOT EXISTS sre.probe ("
        "id INT AUTO_INCREMENT PRIMARY KEY, msg VARCHAR(64), created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
        ");",
    )
    token = f"probe-{uuid.uuid4()}"
    mysql_query("mysql-1-db", f"INSERT INTO sre.probe (msg) VALUES ('{token}');")

    deadline = time.time() + 5.0
    found = False
    while time.time() < deadline:
        cnt = mysql_query("mysql-2-db", f"SELECT COUNT(*) FROM sre.probe WHERE msg = '{token}';")
        if cnt == "1":
            found = True
            break
        time.sleep(0.5)

    assert found, f"Row with token {token} did not replicate within 5 s"


def test_heartbeat_lag():
    deadline = time.time() + 5.0
    ts_str = ""
    while time.time() < deadline:
        try:
            ts_str = mysql_query("mysql-2-db", "SELECT ts FROM sre.heartbeat WHERE server_id = 1;")
            if ts_str:
                break
        except Exception:
            pass
        time.sleep(0.5)

    assert ts_str, "No heartbeat row found on replica"
    ts = datetime.datetime.fromisoformat(ts_str).replace(tzinfo=datetime.timezone.utc)
    now = datetime.datetime.now(datetime.timezone.utc)
    lag = abs((now - ts).total_seconds())
    assert lag < 5.0, f"Heartbeat lag is {lag}s, expected < 5s"


@pytest.mark.slow
def test_replica_persists_super_read_only_after_restart():
    subprocess.run(["docker", "restart", "mysql-2-db"], check=True, capture_output=True)
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            val = mysql_query("mysql-2-db", "SELECT @@super_read_only;")
            if val == "1":
                return
        except Exception:
            pass
        time.sleep(1)
    pytest.fail("super_read_only not restored on mysql-2-db after restart")


def test_replication_failure_and_catchup():
    token = f"probe-catchup-{uuid.uuid4()}"
    try:
        mysql_query("mysql-2-db", "STOP REPLICA IO_THREAD;")
        mysql_query(
            "mysql-1-db",
            "CREATE DATABASE IF NOT EXISTS sre; "
            "CREATE TABLE IF NOT EXISTS sre.probe ("
            "id INT AUTO_INCREMENT PRIMARY KEY, msg VARCHAR(64), created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
            "); "
            f"INSERT INTO sre.probe (msg) VALUES ('{token}');",
        )
        time.sleep(1.0)
        cnt = mysql_query("mysql-2-db", f"SELECT COUNT(*) FROM sre.probe WHERE msg = '{token}';")
        assert cnt == "0", f"Row {token} should not be on replica while IO thread is stopped"
    finally:
        mysql_query("mysql-2-db", "START REPLICA IO_THREAD;")

    deadline = time.time() + 10.0
    found = False
    while time.time() < deadline:
        cnt = mysql_query("mysql-2-db", f"SELECT COUNT(*) FROM sre.probe WHERE msg = '{token}';")
        if cnt == "1":
            found = True
            break
        time.sleep(0.5)

    assert found, f"Row {token} did not catch up within 10 s after starting IO thread"

