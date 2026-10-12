import datetime
import json
import pathlib
import re
import subprocess
import time
import urllib.error
import urllib.request
import uuid
import pytest
import yaml

LAB_YML = pathlib.Path(__file__).parents[2] / "inventory/docker/group_vars/all/lab.yml"
with open(LAB_YML) as f:
    LAB_CONFIG = yaml.safe_load(f)

MGMT_SERVICES = LAB_CONFIG["mgmt_services"]


def http_get(url, timeout=5):
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.getcode(), resp.read().decode("utf-8")


def http_post_json(url, data, timeout=5):
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.getcode(), resp.read().decode("utf-8")


def test_prometheus_ready():
    ip = MGMT_SERVICES["prometheus"]
    code, _ = http_get(f"http://{ip}:9090/-/ready")
    assert code == 200


def test_prometheus_rules_loaded():
    ip = MGMT_SERVICES["prometheus"]
    code, body = http_get(f"http://{ip}:9090/api/v1/rules")
    assert code == 200
    data = json.loads(body)
    assert data["status"] == "success"
    rule_names = [
        rule["name"]
        for group in data["data"]["groups"]
        for rule in group["rules"]
    ]
    assert "AllPathsDown" in rule_names
    assert "BGPSessionDown" in rule_names
    assert "TrafficOnBackupPath" in rule_names


def test_alertmanager_ready():
    ip = MGMT_SERVICES["alertmanager"]
    code, _ = http_get(f"http://{ip}:9093/-/ready")
    assert code == 200


def test_alertmanager_inhibition():
    ip = MGMT_SERVICES["alertmanager"]
    test_id = str(uuid.uuid4())
    now = datetime.datetime.now(datetime.timezone.utc)
    future = now + datetime.timedelta(minutes=2)
    now_str = now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    future_str = future.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    alerts = [
        {
            "labels": {
                "alertname": "AllPathsDown",
                "severity": "critical",
                "instance": "mysql-1",
                "test_id": test_id,
            },
            "annotations": {
                "summary": "Inhibition test AllPathsDown",
            },
            "startsAt": now_str,
            "endsAt": future_str,
        },
        {
            "labels": {
                "alertname": "BGPSessionDown",
                "severity": "warning",
                "instance": "mysql-1",
                "test_id": test_id,
            },
            "annotations": {
                "summary": "Inhibition test BGPSessionDown",
            },
            "startsAt": now_str,
            "endsAt": future_str,
        },
    ]

    try:
        code, _ = http_post_json(f"http://{ip}:9093/api/v2/alerts", alerts)
        assert code == 200

        filter_param = urllib.parse.quote(f'test_id="{test_id}"')
        code, body = http_get(f"http://{ip}:9093/api/v2/alerts?filter={filter_param}")
        assert code == 200
        active_alerts = json.loads(body)
        alerts_by_name = {a["labels"]["alertname"]: a for a in active_alerts}

        assert "AllPathsDown" in alerts_by_name
        assert alerts_by_name["AllPathsDown"]["status"]["state"] == "active"

        assert "BGPSessionDown" in alerts_by_name
        inhibited_by = alerts_by_name["BGPSessionDown"]["status"].get("inhibitedBy", [])
        assert len(inhibited_by) > 0
    finally:
        resolved_alerts = [
            {
                "labels": a["labels"],
                "annotations": a["annotations"],
                "startsAt": a["startsAt"],
                "endsAt": now_str,
            }
            for a in alerts
        ]
        http_post_json(f"http://{ip}:9093/api/v2/alerts", resolved_alerts)


def test_grafana_health_and_dashboard():
    ip = MGMT_SERVICES["grafana"]
    code, body = http_get(f"http://{ip}:3000/api/health")
    assert code == 200
    health = json.loads(body)
    assert health.get("database") == "ok"

    code, body = http_get(f"http://{ip}:3000/api/search?query=Replication")
    assert code == 200
    dashboards = json.loads(body)
    slugs_and_titles = [d.get("title", "") for d in dashboards]
    assert any("Replication" in title for title in slugs_and_titles)


def test_mailpit_ui_ready():
    ip = MGMT_SERVICES["mailpit"]
    code, body = http_get(f"http://{ip}:8025/api/v1/info")
    assert code == 200
    info = json.loads(body)
    assert "version" in info or "Database" in info


def test_scrape_targets_up():
    """Verify Prometheus scrape targets status.

    Fails with a clear diagnostic message if exporters are down or pending deployment.
    """
    ip = MGMT_SERVICES["prometheus"]
    code, body = http_get(f"http://{ip}:9090/api/v1/targets")
    assert code == 200
    data = json.loads(body)
    assert data["status"] == "success"
    active_targets = data["data"]["activeTargets"]
    assert len(active_targets) > 0

    down_targets = [t["scrapeUrl"] for t in active_targets if t["health"] != "up"]
    if down_targets:
        pytest.fail(
            f"Exporters pending deployment or down: {len(down_targets)}/{len(active_targets)} targets down (e.g. {down_targets[:3]})"
        )


METRIC = re.compile(r"\b((?:frr|wireguard|mysql|probe|sre|node)_[a-z0-9_]+)")


def iter_panels(panels):
    for panel in panels:
        yield panel
        yield from iter_panels(panel.get("panels", []))


def referenced_metrics():
    root = pathlib.Path(__file__).parents[2]
    rules = yaml.safe_load((root / "monitoring/prometheus/rules/alerts.yml").read_text())
    exprs = [r["expr"] for g in rules["groups"] for r in g["rules"]]
    dashboard = json.loads((root / "monitoring/grafana/dashboards/replication-link.json").read_text())
    exprs += [t["expr"] for p in iter_panels(dashboard["panels"]) for t in p.get("targets", []) if "expr" in t]
    return sorted({m for e in exprs for m in METRIC.findall(e)})


@pytest.mark.parametrize("name", referenced_metrics())
def test_alert_and_dashboard_metrics_have_data(name):
    # a rule on a metric that has no samples never fires and never errors
    code, body = http_get(f"http://{MGMT_SERVICES['prometheus']}:9090/api/v1/label/__name__/values")
    assert code == 200
    assert name in json.loads(body)["data"], f"{name} is used by an alert or the dashboard but Prometheus has no samples for it"


def replica_sql(statement):
    subprocess.run(
        ["docker", "exec", "mysql-2-db", "sh", "-c", 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql -uroot -e "$0"', statement],
        check=True, capture_output=True,
    )


def firing_alerts():
    _, body = http_get(f"http://{MGMT_SERVICES['prometheus']}:9090/api/v1/alerts")
    return {a["labels"]["alertname"] for a in json.loads(body)["data"]["alerts"] if a["state"] == "firing"}


@pytest.mark.slow
@pytest.mark.parametrize("thread,alert", [("IO_THREAD", "ReplicationIOStopped"), ("SQL_THREAD", "ReplicationSQLStopped")])
def test_replication_alert_fires_and_clears(thread, alert):
    assert alert not in firing_alerts(), f"{alert} already firing before the test"
    try:
        replica_sql(f"STOP REPLICA {thread}")
        deadline = time.time() + 120
        while alert not in firing_alerts():
            assert time.time() < deadline, f"{alert} did not fire within 120 s of stopping the {thread}"
            time.sleep(3)
    finally:
        replica_sql(f"START REPLICA {thread}")
    deadline = time.time() + 120
    while alert in firing_alerts():
        assert time.time() < deadline, f"{alert} kept firing 120 s after the {thread} was started again"
        time.sleep(3)


# RouteFlapping counts path changes over ten minutes, so it stays active long after the
# failover tests that cause it. It has its own chaos test.
WINDOWED_ALERTS = {"RouteFlapping"}


def active_alerts():
    # pending counts too: a rule that is wrong for healthy data spends its first `for:` pending
    _, body = http_get(f"http://{MGMT_SERVICES['prometheus']}:9090/api/v1/alerts")
    names = {a["labels"]["alertname"] for a in json.loads(body)["data"]["alerts"]}
    return sorted(names - WINDOWED_ALERTS)


def test_healthy_lab_has_no_active_alerts():
    # alerts left over from earlier tests clear within a minute or two; a rule that is
    # wrong for healthy data (e.g. a state value off by one) never clears
    deadline = time.time() + 240
    while active_alerts():
        assert time.time() < deadline, f"alerts still active on a healthy lab: {active_alerts()}"
        time.sleep(5)
