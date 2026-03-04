"""Tests for topology loader and validator."""

from pathlib import Path

import pytest
import yaml

from src.router.topology import Topology, TopologyError, load_topology


def _write_yaml(tmp_path: Path, data: dict | str) -> str:
    """Write YAML data to a temp file and return its path."""
    p = tmp_path / "topology.yml"
    if isinstance(data, str):
        p.write_text(data)
    else:
        p.write_text(yaml.dump(data))
    return str(p)


def _minimal_topology(**overrides) -> dict:
    """Return a minimal valid topology dict."""
    base = {
        "version": 1,
        "global": {
            "boss_role": "boss",
            "cross_repo_policy": {"require_president_handoff": True},
        },
        "hosts": {"h1": {"address": "10.0.0.1"}},
        "workers": {"w1": {"host": "h1", "cli_type": "claude"}},
        "repos": {
            "myrepo": {
                "worker_pool": ["w1"],
                "preferred_host": "h1",
                "notify_room": "!room:matrix.example",
            }
        },
    }
    base.update(overrides)
    return base


class TestLoadTopology:
    def test_load_none_returns_none(self):
        assert load_topology(None) is None

    def test_load_valid_yaml(self, tmp_path):
        path = _write_yaml(tmp_path, _minimal_topology())
        topo = load_topology(path)
        assert topo is not None
        assert isinstance(topo, Topology)

    def test_load_missing_file_raises(self):
        with pytest.raises(TopologyError, match="not found"):
            load_topology("/nonexistent/topology.yml")

    def test_load_invalid_yaml_raises(self, tmp_path):
        p = tmp_path / "bad.yml"
        p.write_text(": : : not valid yaml [")
        with pytest.raises(TopologyError, match="invalid YAML"):
            load_topology(str(p))

    def test_load_missing_keys_raises(self, tmp_path):
        data = {"version": 1, "global": {}}  # missing hosts, workers, repos
        path = _write_yaml(tmp_path, data)
        with pytest.raises(TopologyError, match="missing required keys"):
            load_topology(path)

    def test_load_non_dict_raises(self, tmp_path):
        path = _write_yaml(tmp_path, "just a string")
        with pytest.raises(TopologyError, match="expected YAML mapping"):
            load_topology(path)


class TestTopologyQueries:
    @pytest.fixture
    def topo(self, tmp_path):
        path = _write_yaml(tmp_path, _minimal_topology())
        return load_topology(path)

    def test_get_repo_worker_pool(self, topo):
        pool = topo.get_repo_worker_pool("myrepo")
        assert pool == ["w1"]

    def test_get_repo_worker_pool_unknown_repo(self, topo):
        assert topo.get_repo_worker_pool("unknown") is None

    def test_get_repo_preferred_host(self, topo):
        assert topo.get_repo_preferred_host("myrepo") == "h1"

    def test_get_repo_preferred_host_unknown(self, topo):
        assert topo.get_repo_preferred_host("unknown") is None

    def test_get_repo_notify_room(self, topo):
        assert topo.get_repo_notify_room("myrepo") == "!room:matrix.example"

    def test_get_repo_notify_room_unknown(self, topo):
        assert topo.get_repo_notify_room("unknown") is None

    def test_is_president_handoff_required_true(self, topo):
        assert topo.is_president_handoff_required() is True

    def test_is_president_handoff_required_false(self, tmp_path):
        data = _minimal_topology()
        data["global"]["cross_repo_policy"]["require_president_handoff"] = False
        path = _write_yaml(tmp_path, data)
        topo = load_topology(path)
        assert topo.is_president_handoff_required() is False

    def test_repo_without_worker_pool(self, tmp_path):
        data = _minimal_topology()
        data["repos"]["bare"] = {"lead_role": "repo_lead"}
        path = _write_yaml(tmp_path, data)
        topo = load_topology(path)
        assert topo.get_repo_worker_pool("bare") is None

    def test_repo_with_empty_worker_pool(self, tmp_path):
        data = _minimal_topology()
        data["repos"]["empty"] = {"worker_pool": []}
        path = _write_yaml(tmp_path, data)
        topo = load_topology(path)
        assert topo.get_repo_worker_pool("empty") is None


class TestTopologyWithExampleFile:
    """Test against the actual example topology file shipped in deploy/."""

    def test_load_example_topology(self):
        example = Path(__file__).resolve().parents[2] / "deploy" / "topology.v1.4.example.yml"
        if not example.exists():
            pytest.skip("Example topology file not found")
        topo = load_topology(str(example))
        assert topo is not None
        # Verify known repos from example
        pool = topo.get_repo_worker_pool("rektslug")
        assert pool is not None
        assert "ws-claude-session-01" in pool
        assert topo.get_repo_preferred_host("rektslug") == "mac-112"
        assert topo.is_president_handoff_required() is True
