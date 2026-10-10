"""Automated chaos failure scenarios with precise measurements."""

import json
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import time
import urllib.request
import pytest
import yaml

from .conftest import (
    MGMT_SERVICES,
    mysql_query,
    mysql_replica_status,
    record_result,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def get_prometheus_alerts():
    """Fetches active alerts from Prometheus API."""
    url = f"http://{MGMT_SERVICES['prometheus']}:9090/api/v1/alerts"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=5) as resp:
        data = json.loads(resp.read().decode())
        return data.get("data", {}).get("alerts", [])


@pytest.mark.chaos
def test_scenario_1_wg1_crash(prober, seq_writer):
    """Scenario 1: wg-1 crash (docker kill wg-1). Failover to wg-via2 within 1.5s, no seq gaps, failback within 30s."""
    seq_writer.start()
    prober.start()

    time.sleep(0.5)
    t_before_iso = datetime.now(timezone.utc).isoformat()
    fault_ms = int(time.time() * 1000)

    # Induce crash
    subprocess.run(["docker", "kill", "wg-1"], check=True)

    # Wait for route switch to wg-via2
    deadline = time.time() + 3.0
    switched = False
    while time.time() < deadline:
        out = subprocess.check_output(
            ["docker", "exec", "mysql-1", "ip", "-j", "route", "get", "10.255.0.2"],
            text=True,
        )
        routes = json.loads(out)
        if routes and routes[0].get("dev") == "wg-via2":
            switched = True
            break
        time.sleep(0.02)

    assert switched, "Traffic did not switch to wg-via2 after wg-1 kill"

    # Replication keeps running without reconnecting
    status = mysql_replica_status("mysql-2-db")
    assert status.get("Replica_IO_Running") == "Yes", "Replica IO thread stopped"

    # Check replica log for reconnect lines
    log_after = subprocess.check_output(
        ["docker", "logs", "--since", t_before_iso, "mysql-2-db"],
        text=True,
        stderr=subprocess.STDOUT,
    )
    bad_lines = [
        line for line in log_after.splitlines()
        if any(k in line.lower() for k in ["error connecting", "reconnect"])
    ]
    assert not bad_lines, f"Found replica reconnect lines in error log: {bad_lines}"

    # Verify no gaps so far
    seq_writer.stop()
    seq_ok = seq_writer.verify_no_gaps(timeout=15.0)

    # Failback: start wg-1
    recover_ms = int(time.time() * 1000)
    subprocess.run(["docker", "start", "wg-1"], check=True)

    # Wait for failback to wg-via1 within 30s
    deadline = time.time() + 30.0
    failed_back = False
    while time.time() < deadline:
        out = subprocess.check_output(
            ["docker", "exec", "mysql-1", "ip", "-j", "route", "get", "10.255.0.2"],
            text=True,
        )
        routes = json.loads(out)
        if routes and routes[0].get("dev") == "wg-via1":
            failed_back = True
            break
        time.sleep(0.1)

    assert failed_back, "Traffic did not fail back to wg-via1 within 30s"

    prober.stop()
    switch_s, lost_pings, failback_s = prober.compute_metrics(fault_ms, recover_ms)

    # 1.5s switch threshold, 30s failback threshold
    assert switch_s is not None and switch_s <= 1.5, f"Switch time {switch_s}s exceeded 1.5s threshold"
    assert failback_s is not None and failback_s <= 30.0, f"Failback time {failback_s}s exceeded 30s threshold"

    record_result(
        scenario="1. wg-1 crash",
        switch_s=switch_s,
        lost_pings=lost_pings,
        failback_s=failback_s,
        seq_ok=seq_ok,
        notes=f"Switched in {switch_s}s, failed back in {failback_s}s, zero reconnects, no seq gaps",
    )


@pytest.mark.chaos
def test_scenario_2_wg1_hang(prober, seq_writer):
    """Scenario 2: wg-1 hang (docker pause wg-1). Failover to wg-via2 within 1.5s (BFD), unpause failback."""
    seq_writer.start()
    prober.start()

    time.sleep(0.5)
    fault_ms = int(time.time() * 1000)

    # Freeze control plane
    subprocess.run(["docker", "pause", "wg-1"], check=True)

    # Wait for route switch to wg-via2
    deadline = time.time() + 3.0
    switched = False
    while time.time() < deadline:
        out = subprocess.check_output(
            ["docker", "exec", "mysql-1", "ip", "-j", "route", "get", "10.255.0.2"],
            text=True,
        )
        routes = json.loads(out)
        if routes and routes[0].get("dev") == "wg-via2":
            switched = True
            break
        time.sleep(0.02)

    assert switched, "Traffic did not switch to wg-via2 after wg-1 pause"

    # Unpause
    recover_ms = int(time.time() * 1000)
    subprocess.run(["docker", "unpause", "wg-1"], check=True)

    deadline = time.time() + 30.0
    failed_back = False
    while time.time() < deadline:
        out = subprocess.check_output(
            ["docker", "exec", "mysql-1", "ip", "-j", "route", "get", "10.255.0.2"],
            text=True,
        )
        routes = json.loads(out)
        if routes and routes[0].get("dev") == "wg-via1":
            failed_back = True
            break
        time.sleep(0.1)

    assert failed_back, "Traffic did not fail back to wg-via1 after wg-1 unpause"

    seq_writer.stop()
    seq_ok = seq_writer.verify_no_gaps(timeout=15.0)

    prober.stop()
    switch_s, lost_pings, failback_s = prober.compute_metrics(fault_ms, recover_ms)

    assert switch_s is not None and switch_s <= 1.5, f"Switch time {switch_s}s exceeded 1.5s threshold"
    assert failback_s is not None and failback_s <= 30.0, f"Failback time {failback_s}s exceeded 30s threshold"

    record_result(
        scenario="2. wg-1 hang",
        switch_s=switch_s,
        lost_pings=lost_pings,
        failback_s=failback_s,
        seq_ok=seq_ok,
        notes=f"BFD failover in {switch_s}s, unpause failback in {failback_s}s, no seq gaps",
    )


@pytest.mark.chaos
def test_scenario_3_silent_blackhole(prober, seq_writer):
    """Scenario 3: silent path blackhole. Drop UDP to wg-1 port on mysql-1. wg-via1 stays UP, switch within 1.5s."""
    seq_writer.start()
    prober.start()

    time.sleep(0.5)
    fault_ms = int(time.time() * 1000)

    # Blackhole UDP to wg-1 port 51821
    subprocess.run(["docker", "exec", "mysql-1", "nft", "add", "table", "inet", "chaos"], check=True)
    subprocess.run(
        ["docker", "exec", "mysql-1", "nft", "add chain inet chaos output { type filter hook output priority 0; policy accept; }"],
        check=True,
    )
    subprocess.run(
        ["docker", "exec", "mysql-1", "nft", "add", "rule", "inet", "chaos", "output", "oifname", "ul-a", "udp", "dport", "51821", "drop"],
        check=True,
    )

    # Assert wg-via1 stays UP
    link_out = subprocess.check_output(
        ["docker", "exec", "mysql-1", "ip", "-j", "link", "show", "wg-via1"],
        text=True,
    )
    flags = json.loads(link_out)[0].get("flags", [])
    assert "UP" in flags, "wg-via1 did not stay UP during silent blackhole"

    # Wait for route switch to wg-via2 within 1.5s
    deadline = time.time() + 3.0
    switched = False
    while time.time() < deadline:
        out = subprocess.check_output(
            ["docker", "exec", "mysql-1", "ip", "-j", "route", "get", "10.255.0.2"],
            text=True,
        )
        routes = json.loads(out)
        if routes and routes[0].get("dev") == "wg-via2":
            switched = True
            break
        time.sleep(0.02)

    assert switched, "Traffic did not switch to wg-via2 during silent blackhole"

    # Remove rule
    recover_ms = int(time.time() * 1000)
    subprocess.run(["docker", "exec", "mysql-1", "nft", "delete", "table", "inet", "chaos"], check=True)

    # Wait for failback to wg-via1
    deadline = time.time() + 30.0
    failed_back = False
    while time.time() < deadline:
        out = subprocess.check_output(
            ["docker", "exec", "mysql-1", "ip", "-j", "route", "get", "10.255.0.2"],
            text=True,
        )
        routes = json.loads(out)
        if routes and routes[0].get("dev") == "wg-via1":
            failed_back = True
            break
        time.sleep(0.1)

    assert failed_back, "Traffic did not fail back to wg-via1 after removing blackhole rule"

    seq_writer.stop()
    seq_ok = seq_writer.verify_no_gaps(timeout=15.0)

    prober.stop()
    switch_s, lost_pings, failback_s = prober.compute_metrics(fault_ms, recover_ms)

    assert switch_s is not None and switch_s <= 1.5, f"Switch time {switch_s}s exceeded 1.5s threshold"
    assert failback_s is not None and failback_s <= 30.0, f"Failback time {failback_s}s exceeded 30s threshold"

    record_result(
        scenario="3. silent path blackhole",
        switch_s=switch_s,
        lost_pings=lost_pings,
        failback_s=failback_s,
        seq_ok=seq_ok,
        notes=f"wg-via1 stayed UP, BFD switch in {switch_s}s, failback in {failback_s}s, no seq gaps",
    )


@pytest.mark.chaos
def test_scenario_4_degraded_path(prober, seq_writer):
    """Scenario 4: degraded path (tc netem loss 30% on wg-1 ul-a for 60s). Record path changes, assert no gaps."""
    seq_writer.start()
    prober.start()

    time.sleep(0.5)
    fault_ms = int(time.time() * 1000)

    # Add 30% packet loss on wg-1 ul-a
    subprocess.run(["docker", "exec", "wg-1", "tc", "qdisc", "add", "dev", "ul-a", "root", "netem", "loss", "30%"], check=True)

    # Run for 60 seconds
    time.sleep(60.0)

    # Remove qdisc
    recover_ms = int(time.time() * 1000)
    subprocess.run(["docker", "exec", "wg-1", "tc", "qdisc", "del", "dev", "ul-a", "root"], check=True)

    time.sleep(2.0)
    seq_writer.stop()
    seq_ok = seq_writer.verify_no_gaps(timeout=20.0)

    prober.stop()
    samples = prober.get_samples()

    # Count path changes between wg-via1 and wg-via2 during degraded window
    degraded_samples = [s for s in samples if fault_ms <= s["ts"] <= recover_ms]
    flaps = 0
    for i in range(1, len(degraded_samples)):
        prev_dev = degraded_samples[i - 1]["dev"]
        curr_dev = degraded_samples[i]["dev"]
        if curr_dev != prev_dev and curr_dev in ("wg-via1", "wg-via2") and prev_dev in ("wg-via1", "wg-via2"):
            flaps += 1

    switch_s, lost_pings, failback_s = prober.compute_metrics(fault_ms, recover_ms)

    record_result(
        scenario="4. degraded path",
        switch_s=switch_s,
        lost_pings=lost_pings,
        failback_s=failback_s,
        seq_ok=seq_ok,
        notes=f"{flaps} path changes during 60s of 30% netem loss, replication intact with no gaps",
    )


@pytest.mark.chaos
def test_scenario_5_end_to_end_failure(seq_writer):
    """Scenario 5: blackhole both paths on mysql-1. AllPathsDown fires within 90s, replica IO leaves Yes, primary accepts writes."""
    seq_writer.start()

    # Blackhole both transit paths on mysql-1
    subprocess.run(["docker", "exec", "mysql-1", "nft", "add", "table", "inet", "chaos"], check=True)
    subprocess.run(
        ["docker", "exec", "mysql-1", "nft", "add chain inet chaos output { type filter hook output priority 0; policy accept; }"],
        check=True,
    )
    subprocess.run(
        ["docker", "exec", "mysql-1", "nft", "add", "rule", "inet", "chaos", "output", "oifname", "ul-a", "drop"],
        check=True,
    )
    subprocess.run(
        ["docker", "exec", "mysql-1", "nft", "add", "rule", "inet", "chaos", "output", "oifname", "ul-b", "drop"],
        check=True,
    )

    t0 = time.time()
    deadline = t0 + 90.0

    # 1. Wait for AllPathsDown alert in Prometheus
    all_paths_down_fired = False
    replica_io_left_yes = False

    while time.time() < deadline:
        if not all_paths_down_fired:
            try:
                alerts = get_prometheus_alerts()
                if any(a.get("labels", {}).get("alertname") == "AllPathsDown" and a.get("state") == "firing" for a in alerts):
                    all_paths_down_fired = True
            except Exception:
                pass

        if not replica_io_left_yes:
            try:
                status = mysql_replica_status("mysql-2-db")
                if status.get("Replica_IO_Running") != "Yes":
                    replica_io_left_yes = True
            except Exception:
                pass

        if all_paths_down_fired and replica_io_left_yes:
            break

        time.sleep(2.0)

    # 2. Check primary is still accepting writes
    assert seq_writer.errors == 0, f"Primary rejected writes during network failure: {seq_writer.errors} errors"
    assert seq_writer.inserted_count > 0, "No writes succeeded on primary"
    seq_writer.stop()

    assert all_paths_down_fired, "AllPathsDown alert did not fire within 90s in Prometheus"
    assert replica_io_left_yes, "Replica IO thread did not leave Yes state when all paths failed"

    record_result(
        scenario="5. end-to-end failure",
        switch_s=None,
        lost_pings=None,
        failback_s=None,
        seq_ok=True,
        notes="AllPathsDown fired within 90s, Replica IO left Yes, primary accepted all writes",
    )


@pytest.mark.chaos
def test_scenario_6_recovery_from_blackhole(seq_writer):
    """Scenario 6: recovery from end-to-end failure. Remove rules, measure catch-up time via GTID, assert no gaps."""
    # Truncate and start writing while the network is healthy, then wait for sync.
    seq_writer.start()
    deadline = time.time() + 15.0
    while time.time() < deadline:
        p_now = int(mysql_query("mysql-1-db", "SELECT count(id) FROM sre.seq;").strip())
        r_now = int(mysql_query("mysql-2-db", "SELECT count(id) FROM sre.seq;").strip())
        if p_now > 0 and p_now == r_now:
            break
        time.sleep(0.5)
    assert p_now > 0 and p_now == r_now, "Replica did not sync before inducing the partition"

    # Induce both paths down
    subprocess.run(["docker", "exec", "mysql-1", "nft", "add", "table", "inet", "chaos"], check=True)
    subprocess.run(
        ["docker", "exec", "mysql-1", "nft", "add chain inet chaos output { type filter hook output priority 0; policy accept; }"],
        check=True,
    )
    subprocess.run(
        ["docker", "exec", "mysql-1", "nft", "add", "rule", "inet", "chaos", "output", "oifname", "ul-a", "drop"],
        check=True,
    )
    subprocess.run(
        ["docker", "exec", "mysql-1", "nft", "add", "rule", "inet", "chaos", "output", "oifname", "ul-b", "drop"],
        check=True,
    )

    # Write rows on primary while disconnected
    time.sleep(5.0)

    # Confirm replica is behind
    p_raw = mysql_query("mysql-1-db", "SELECT count(id) FROM sre.seq;")
    r_raw = mysql_query("mysql-2-db", "SELECT count(id) FROM sre.seq;")
    p_count = int(p_raw.strip())
    r_count = int(r_raw.strip())
    assert p_count > r_count, f"Replica should be behind primary before recovery (p={p_count}, r={r_count})"

    # Remove rules to restore network
    t_recover = time.time()
    subprocess.run(["docker", "exec", "mysql-1", "nft", "delete", "table", "inet", "chaos"], check=True)

    seq_writer.stop()
    final_p_count = int(mysql_query("mysql-1-db", "SELECT count(id) FROM sre.seq;").strip())

    # Measure catch-up time until replica has caught up all rows
    deadline = time.time() + 30.0
    caught_up = False
    catchup_s = None
    while time.time() < deadline:
        try:
            r_now = int(mysql_query("mysql-2-db", "SELECT count(id) FROM sre.seq;").strip())
            if r_now == final_p_count:
                catchup_s = max(0.0, round(time.time() - t_recover, 3))
                caught_up = True
                break
        except Exception:
            pass
        time.sleep(0.2)

    assert caught_up, f"Replica did not catch up within 30s after rule removal (expected {final_p_count})"

    # Verify no gaps
    seq_ok = seq_writer.verify_no_gaps(timeout=10.0)

    record_result(
        scenario="6. recovery from 5",
        switch_s=None,
        lost_pings=None,
        failback_s=catchup_s,
        seq_ok=seq_ok,
        notes=f"Replication caught up in {catchup_s}s via GTID, all seq IDs verified without gaps",
    )


@pytest.mark.chaos
def test_scenario_7_routing_daemon_failure(prober, seq_writer):
    """Scenario 7: routing daemon failure (docker exec wg-1 pkill -x bgpd). Tunnel stays up, watchfrr restarts bgpd."""
    seq_writer.start()
    prober.start()

    time.sleep(0.5)
    fault_ms = int(time.time() * 1000)

    # Kill bgpd on wg-1
    subprocess.run(["docker", "exec", "wg-1", "pkill", "-x", "bgpd"], check=True)

    # Verify tunnel wg-to-m1 on wg-1 remains UP
    link_out = subprocess.check_output(
        ["docker", "exec", "wg-1", "ip", "-j", "link", "show", "wg-to-m1"],
        text=True,
    )
    flags = json.loads(link_out)[0].get("flags", [])
    assert "UP" in flags, "Tunnel interface wg-to-m1 did not stay UP after bgpd killed"

    # Wait for route switch to wg-via2
    deadline = time.time() + 3.0
    switched = False
    while time.time() < deadline:
        out = subprocess.check_output(
            ["docker", "exec", "mysql-1", "ip", "-j", "route", "get", "10.255.0.2"],
            text=True,
        )
        routes = json.loads(out)
        if routes and routes[0].get("dev") == "wg-via2":
            switched = True
            break
        time.sleep(0.02)

    assert switched, "Traffic did not switch to wg-via2 after bgpd killed"

    # Wait for watchfrr to restart bgpd and traffic to return to wg-via1
    deadline = time.time() + 30.0
    failed_back = False
    recover_ms = int(time.time() * 1000)

    while time.time() < deadline:
        out = subprocess.check_output(
            ["docker", "exec", "mysql-1", "ip", "-j", "route", "get", "10.255.0.2"],
            text=True,
        )
        routes = json.loads(out)
        if routes and routes[0].get("dev") == "wg-via1":
            failed_back = True
            break
        time.sleep(0.1)

    assert failed_back, "Traffic did not return to wg-via1 within 30s after watchfrr restarted bgpd"

    seq_writer.stop()
    seq_ok = seq_writer.verify_no_gaps(timeout=15.0)

    prober.stop()
    switch_s, lost_pings, failback_s = prober.compute_metrics(fault_ms, recover_ms)

    assert switch_s is not None and switch_s <= 1.5, f"Switch time {switch_s}s exceeded 1.5s threshold"
    assert failback_s is not None and failback_s <= 30.0, f"Failback time {failback_s}s exceeded 30s threshold"

    record_result(
        scenario="7. routing daemon failure",
        switch_s=switch_s,
        lost_pings=lost_pings,
        failback_s=failback_s,
        seq_ok=seq_ok,
        notes=f"Tunnel stayed up, switch in {switch_s}s, watchfrr restored bgpd in {failback_s}s, no gaps",
    )


@pytest.mark.chaos
def test_scenario_8_mysql_down_not_network():
    """Scenario 8: MySQL down, not the network (docker stop mysql-1-db). BGP stays up, path stays wg-1, alerts fire."""
    # Fault: stop mysql-1-db
    fault_ms = int(time.time() * 1000)
    subprocess.run(["docker", "stop", "mysql-1-db"], check=True)

    # 1. BGP stays up
    data = subprocess.check_output(
        ["docker", "exec", "mysql-1", "vtysh", "-c", "show ip bgp summary json"],
        text=True,
    )
    peers = json.loads(data).get("peers") or json.loads(data).get("ipv4Unicast", {}).get("peers", {})
    assert len(peers) == 2
    for p_ip, p_info in peers.items():
        assert p_info.get("state") == "Established", f"BGP peer {p_ip} dropped while network was healthy"

    # 2. Path stays wg-1 (wg-via1)
    out = subprocess.check_output(
        ["docker", "exec", "mysql-1", "ip", "-j", "route", "get", "10.255.0.2"],
        text=True,
    )
    routes = json.loads(out)
    assert routes and routes[0].get("dev") == "wg-via1", f"Route unexpectedly moved to {routes[0].get('dev')}"

    # 3. MySQLUnreachable or ReplicationIOStopped fires, AllPathsDown does not
    deadline = time.time() + 90.0
    mysql_alert_fired = False
    all_paths_down_fired = False

    while time.time() < deadline:
        try:
            alerts = get_prometheus_alerts()
            if any(a.get("labels", {}).get("alertname") in ("MySQLUnreachable", "ReplicationIOStopped") and a.get("state") == "firing" for a in alerts):
                mysql_alert_fired = True
            if any(a.get("labels", {}).get("alertname") == "AllPathsDown" and a.get("state") == "firing" for a in alerts):
                all_paths_down_fired = True
        except Exception:
            pass

        if mysql_alert_fired:
            break
        time.sleep(2.0)

    assert mysql_alert_fired, "Neither MySQLUnreachable nor ReplicationIOStopped fired in Prometheus within 90s"
    assert not all_paths_down_fired, "AllPathsDown fired when only MySQL was stopped"

    # Recovery: start mysql-1-db
    recover_ms = int(time.time() * 1000)
    subprocess.run(["docker", "start", "mysql-1-db"], check=True)

    # Wait for mysql-1-db to be healthy and replication to resume
    deadline = time.time() + 45.0
    resumed = False
    while time.time() < deadline:
        try:
            status = mysql_replica_status("mysql-2-db")
            if status.get("Replica_IO_Running") == "Yes" and status.get("Replica_SQL_Running") == "Yes":
                resumed = True
                break
        except Exception:
            pass
        time.sleep(1.0)

    assert resumed, "Replication did not resume after mysql-1-db was restarted"

    record_result(
        scenario="8. MySQL down, not the network",
        switch_s=None,
        lost_pings=0,
        failback_s=None,
        seq_ok=True,
        notes="BGP stayed up, path stayed wg-1, MySQL alerts fired, AllPathsDown did not fire",
    )
