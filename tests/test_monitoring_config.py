"""Tests for monitoring configuration files."""

from __future__ import annotations

from pathlib import Path

import yaml

MONITORING_DIR = Path(__file__).parent.parent / "deploy" / "monitoring"


class TestAlertRules:
    """Validate Grafana alert rules YAML."""

    def test_alert_rules_yaml_valid(self):
        """Alert rules YAML should parse without errors."""
        path = MONITORING_DIR / "mesh-alerts.yaml"
        assert path.exists(), "mesh-alerts.yaml not found"
        data = yaml.safe_load(path.read_text())
        assert data is not None
        assert "apiVersion" in data
        assert "groups" in data

    def test_alert_rules_has_required_rules(self):
        """Should contain all 5 required alert rules."""
        data = yaml.safe_load((MONITORING_DIR / "mesh-alerts.yaml").read_text())
        rules = data["groups"][0]["rules"]
        uids = {r["uid"] for r in rules}
        expected = {
            "mesh-router-down",
            "mesh-worker-stale",
            "mesh-queue-depth-high",
            "mesh-no-data",
            "mesh-failure-rate-high",
        }
        assert expected == uids

    def test_alert_rules_severities(self):
        """RouterDown and NoData should be critical, others warning."""
        data = yaml.safe_load((MONITORING_DIR / "mesh-alerts.yaml").read_text())
        rules = {r["uid"]: r for r in data["groups"][0]["rules"]}

        assert rules["mesh-router-down"]["labels"]["severity"] == "critical"
        assert rules["mesh-no-data"]["labels"]["severity"] == "critical"
        assert rules["mesh-worker-stale"]["labels"]["severity"] == "warning"
        assert rules["mesh-queue-depth-high"]["labels"]["severity"] == "warning"
        assert rules["mesh-failure-rate-high"]["labels"]["severity"] == "warning"

    def test_alert_rules_for_durations(self):
        """Verify 'for' durations match design."""
        data = yaml.safe_load((MONITORING_DIR / "mesh-alerts.yaml").read_text())
        rules = {r["uid"]: r for r in data["groups"][0]["rules"]}

        assert rules["mesh-router-down"]["for"] == "1m"
        assert rules["mesh-no-data"]["for"] == "5m"
        assert rules["mesh-worker-stale"]["for"] == "2m"
        assert rules["mesh-queue-depth-high"]["for"] == "5m"
        assert rules["mesh-failure-rate-high"]["for"] == "10m"

    def test_alert_rules_have_annotations(self):
        """Each rule should have summary and description annotations."""
        data = yaml.safe_load((MONITORING_DIR / "mesh-alerts.yaml").read_text())
        for rule in data["groups"][0]["rules"]:
            assert "annotations" in rule, f"Missing annotations in {rule['uid']}"
            assert "summary" in rule["annotations"], f"Missing summary in {rule['uid']}"
            assert "description" in rule["annotations"], f"Missing description in {rule['uid']}"

    def test_alert_rules_have_team_label(self):
        """All rules should have team=mesh label."""
        data = yaml.safe_load((MONITORING_DIR / "mesh-alerts.yaml").read_text())
        for rule in data["groups"][0]["rules"]:
            assert rule["labels"].get("team") == "mesh", f"Missing team label in {rule['uid']}"


class TestScrapeConfig:
    """Validate VictoriaMetrics scrape configuration."""

    def test_scrape_config_yaml_valid(self):
        path = MONITORING_DIR / "scrape-mesh.yaml"
        assert path.exists(), "scrape-mesh.yaml not found"
        data = yaml.safe_load(path.read_text())
        assert data is not None
        assert "scrape_configs" in data

    def test_scrape_config_has_mesh_job(self):
        data = yaml.safe_load((MONITORING_DIR / "scrape-mesh.yaml").read_text())
        jobs = data["scrape_configs"]
        assert len(jobs) == 1
        assert jobs[0]["job_name"] == "mesh-router"

    def test_scrape_config_interval(self):
        data = yaml.safe_load((MONITORING_DIR / "scrape-mesh.yaml").read_text())
        job = data["scrape_configs"][0]
        assert job["scrape_interval"] == "15s"
        assert job["scrape_timeout"] == "10s"

    def test_scrape_config_metrics_path(self):
        data = yaml.safe_load((MONITORING_DIR / "scrape-mesh.yaml").read_text())
        job = data["scrape_configs"][0]
        assert job["metrics_path"] == "/metrics"

    def test_scrape_config_has_labels(self):
        data = yaml.safe_load((MONITORING_DIR / "scrape-mesh.yaml").read_text())
        labels = data["scrape_configs"][0]["static_configs"][0]["labels"]
        assert labels["instance"] == "mesh-router"
        assert labels["environment"] == "production"


class TestNotificationPolicy:
    """Validate notification policy documentation."""

    def test_notification_policy_doc_exists(self):
        path = MONITORING_DIR / "notification-policy.md"
        assert path.exists()

    def test_notification_policy_has_setup_checklist(self):
        content = (MONITORING_DIR / "notification-policy.md").read_text()
        assert "Setup Checklist" in content
        assert "VICTORIAMETRICS_UID" in content
