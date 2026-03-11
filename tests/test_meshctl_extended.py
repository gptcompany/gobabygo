"""Extended tests for meshctl to increase coverage."""

from __future__ import annotations

import argparse
import json
import os
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.meshctl import (
    _format_duration,
    _router_timeout,
    _base_url,
    _headers,
    _repo_slug,
    cmd_worker_prune,
    cmd_thread_create,
    cmd_thread_status,
    cmd_thread_context,
    cmd_thread_add_step,
    cmd_thread_handoff,
    main,
)

def _mock_response(status_code: int, json_data: dict | None = None, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = text or json.dumps(json_data or {})
    return resp

class TestMeshctlExtended:
    def test_base_url_default(self):
        with patch.dict(os.environ, {}, clear=True):
            assert _base_url() == "http://localhost:8780"

    def test_base_url_override(self):
        with patch.dict(os.environ, {"MESH_ROUTER_URL": "http://mesh:9000/"}):
            assert _base_url() == "http://mesh:9000"

    def test_headers_with_token(self):
        with patch.dict(os.environ, {"MESH_AUTH_TOKEN": "secret"}):
            assert _headers() == {"Authorization": "Bearer secret"}

    def test_headers_no_token(self):
        with patch.dict(os.environ, {}, clear=True):
            assert _headers() == {}

    def test_router_timeout_invalid(self):
        with patch.dict(os.environ, {"MESH_ROUTER_TIMEOUT_S": "abc"}):
            assert _router_timeout() == 30.0

    def test_router_timeout_env(self):
        with patch.dict(os.environ, {"MESH_ROUTER_TIMEOUT_S": "45.5"}):
            assert _router_timeout() == 45.5

    def test_format_duration_short(self):
        assert _format_duration(5.5) == "5s"

    def test_format_duration_long(self):
        assert _format_duration(125.0) == "2m5s"

    def test_repo_slug(self):
        assert _repo_slug("my-repo", "my-project") == "my-repo"
        assert _repo_slug("", "proj") == "proj"

    @patch("requests.post")
    @patch("requests.get")
    def test_cmd_worker_prune_no_match(self, mock_get, mock_post, capsys):
        mock_get.return_value = _mock_response(200, {"workers": []})
        args = argparse.Namespace(older_than=0, statuses=["offline"], json_output=False)
        cmd_worker_prune(args)
        out, _ = capsys.readouterr()
        assert "No stale workers matched" in out

    @patch("requests.post")
    def test_cmd_thread_create_success(self, mock_post, capsys):
        mock_post.return_value = _mock_response(201, {"thread_id": "th1"})
        args = argparse.Namespace(name="my-thread", json_output=False)
        cmd_thread_create(args)
        out, _ = capsys.readouterr()
        assert "th1" in out

    @patch("requests.get")
    def test_cmd_thread_status_not_found(self, mock_get, capsys):
        # We use a UUID-like ID to skip resolution
        fake_id = "12345678-1234-5678-1234-123456789012"
        mock_get.return_value = _mock_response(404)
        args = argparse.Namespace(thread=fake_id, json_output=False)
        with pytest.raises(SystemExit):
            cmd_thread_status(args)
        _, err = capsys.readouterr()
        assert "Error: 404" in err

    @patch("requests.get")
    def test_cmd_thread_context_success(self, mock_get, capsys):
        fake_id = "12345678-1234-5678-1234-123456789012"
        mock_get.return_value = _mock_response(200, {"context": {"foo": "bar"}})
        args = argparse.Namespace(thread=fake_id, json_output=False)
        cmd_thread_context(args)
        out, _ = capsys.readouterr()
        assert "foo" in out

    @patch("requests.post")
    def test_cmd_thread_add_step_success(self, mock_post, capsys):
        fake_id = "12345678-1234-5678-1234-123456789012"
        mock_post.return_value = _mock_response(201, {"task_id": "tk1"})
        args = argparse.Namespace(
            thread=fake_id,
            step_index=1,
            title="step1",
            cli="claude",
            account="work",
            prompt="hi",
            repo="r1",
            role="",
            on_failure="abort",
            payload=None,
            json_output=False
        )
        cmd_thread_add_step(args)
        out, _ = capsys.readouterr()
        assert "tk1" in out

    @patch("requests.get")
    def test_cmd_thread_handoff_success(self, mock_get, capsys):
        fake_id = "12345678-1234-5678-1234-123456789012"
        # First call for thread status
        resp1 = _mock_response(200, {
            "thread": {"name": "th1"},
            "steps": [{
                "step_index": 1,
                "has_handoff": True,
                "task_id": "tk1"
            }]
        })
        # Second call for task payload
        resp2 = _mock_response(200, {
            "task_id": "tk1",
            "payload": {"handoff": {"source_repo": "s1", "target_repo": "t1"}}
        })
        mock_get.side_effect = [resp1, resp2]
        
        args = argparse.Namespace(thread=fake_id, step_index=1, json_output=False)
        cmd_thread_handoff(args)
        out, _ = capsys.readouterr()
        assert "HANDOFF: s1 -> t1" in out

    @patch("src.meshctl.cmd_status")
    @patch("sys.argv", ["meshctl", "status"])
    def test_main_dispatch_status(self, mock_status):
        main()
        mock_status.assert_called_once()

    @patch("src.meshctl.cmd_worker_prune")
    @patch("sys.argv", ["meshctl", "worker", "prune"])
    def test_main_dispatch_worker_prune(self, mock_prune):
        main()
        mock_prune.assert_called_once()

    @patch("src.meshctl.cmd_task_cancel")
    @patch("sys.argv", ["meshctl", "task", "cancel", "t1"])
    def test_main_dispatch_task_cancel(self, mock_cancel):
        main()
        mock_cancel.assert_called_once()

    @patch("src.meshctl.cmd_thread_create")
    @patch("sys.argv", ["meshctl", "thread", "create", "--name", "n1"])
    def test_main_dispatch_thread_create(self, mock_create):
        main()
        mock_create.assert_called_once()

    @patch("src.meshctl.cmd_submit")
    @patch("sys.argv", ["meshctl", "submit", "--title", "t1"])
    def test_main_dispatch_submit(self, mock_submit):
        main()
        mock_submit.assert_called_once()

    @patch("src.meshctl.cmd_drain")
    @patch("sys.argv", ["meshctl", "drain", "w1"])
    def test_main_dispatch_drain(self, mock_drain):
        main()
        mock_drain.assert_called_once()

    @patch("src.meshctl.cmd_task_fail")
    @patch("sys.argv", ["meshctl", "task", "fail", "t1"])
    def test_main_dispatch_task_fail(self, mock_fail):
        main()
        mock_fail.assert_called_once()

    @patch("src.meshctl.cmd_pipeline_create")
    @patch("sys.argv", ["meshctl", "pipeline", "create", "--template", "gsd", "--thread-name", "n1", "--repo", "r1"])
    def test_main_dispatch_pipeline_create(self, mock_create):
        main()
        mock_create.assert_called_once()

    @patch("src.meshctl.cmd_thread_add_step")
    @patch("sys.argv", ["meshctl", "thread", "add-step", "--thread", "th1", "--title", "s1", "--step-index", "0"])
    def test_main_dispatch_thread_add_step(self, mock_add):
        main()
        mock_add.assert_called_once()

    @patch("src.meshctl.cmd_thread_context")
    @patch("sys.argv", ["meshctl", "thread", "context", "th1"])
    def test_main_dispatch_thread_context(self, mock_context):
        main()
        mock_context.assert_called_once()

    @patch("src.meshctl.cmd_thread_handoff")
    @patch("sys.argv", ["meshctl", "thread", "handoff", "th1", "0"])
    def test_main_dispatch_thread_handoff(self, mock_handoff):
        main()
        mock_handoff.assert_called_once()

    @patch("requests.get")
    def test_cmd_thread_handoff_step_not_found(self, mock_get, capsys):
        fake_id = "12345678-1234-5678-1234-123456789012"
        mock_get.return_value = _mock_response(200, {"thread": {"name": "th1"}, "steps": []})
        args = argparse.Namespace(thread=fake_id, step_index=1, json_output=False)
        with pytest.raises(SystemExit):
            cmd_thread_handoff(args)
        _, err = capsys.readouterr()
        assert "Error: step 1 not found" in err

    @patch("requests.get")
    def test_cmd_thread_handoff_no_data(self, mock_get, capsys):
        fake_id = "12345678-1234-5678-1234-123456789012"
        mock_get.return_value = _mock_response(200, {"thread": {"name": "th1"}, "steps": [{"step_index": 1, "has_handoff": False}]})
        args = argparse.Namespace(thread=fake_id, step_index=1, json_output=False)
        with pytest.raises(SystemExit):
            cmd_thread_handoff(args)
        _, err = capsys.readouterr()
        assert "does not carry handoff data" in err

    def test_load_account_pool_config_missing(self):
        from src.meshctl import _load_account_pool_config
        assert _load_account_pool_config("/non/existent/path") == {}

    def test_load_account_pool_config_invalid_yaml(self, tmp_path):
        from src.meshctl import _load_account_pool_config
        p = tmp_path / "invalid.yaml"
        p.write_text("invalid: [yaml")
        assert _load_account_pool_config(str(p)) == {}

    def test_load_account_pool_config_empty_providers(self, tmp_path):
        from src.meshctl import _load_account_pool_config
        p = tmp_path / "empty.yaml"
        p.write_text("foo: bar")
        assert _load_account_pool_config(str(p)) == {}

    def test_load_account_pool_config_invalid_providers_type(self, tmp_path):
        from src.meshctl import _load_account_pool_config
        p = tmp_path / "invalid_type.yaml"
        p.write_text("providers: not-a-dict")
        assert _load_account_pool_config(str(p)) == {}
