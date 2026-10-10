import datetime
import json
import pathlib
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
