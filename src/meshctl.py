"""meshctl -- Mesh operator CLI.

Standalone HTTP client for inspecting and managing the mesh router.
No imports from src.router.* -- this is a pure HTTP client using only
argparse (stdlib) and requests.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------


def _base_url() -> str:
    """Read MESH_ROUTER_URL from env, default http://localhost:8780."""
    url = os.environ.get("MESH_ROUTER_URL", "http://localhost:8780")
    return url.rstrip("/")


def _headers() -> dict[str, str]:
    """Return auth header dict. Empty if MESH_AUTH_TOKEN is not set."""
    token = os.environ.get("MESH_AUTH_TOKEN", "")
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


def _router_timeout() -> float:
    """HTTP timeout for mesh router calls.

    Defaults higher than before because thread/step creation and status calls can
    legitimately exceed 10s on the live router under load.
    """
    raw = os.environ.get("MESH_ROUTER_TIMEOUT_S", "30").strip()
    try:
        timeout = float(raw)
    except ValueError:
        return 30.0
    return max(timeout, 1.0)


def _default_account_pool_config_path() -> str:
    """Default editable provider account policy file."""
    return str(Path(__file__).resolve().parents[1] / "mapping" / "account_pools.yaml")


def _load_account_pool_config(config_path: str | None = None) -> dict[str, dict[str, object]]:
    """Load central provider account defaults from YAML."""
    path_value = config_path
    if path_value is None:
        path_value = os.environ.get("MESH_ACCOUNT_POOL_CONFIG") or _default_account_pool_config_path()
    if path_value == "":
        return {}

    path = Path(path_value)
    if not path.is_file():
        return {}

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}

    providers = raw.get("providers")
    if not isinstance(providers, dict):
        return {}

    return {
        str(provider).strip(): entry
        for provider, entry in providers.items()
        if isinstance(entry, dict)
    }


def _resolve_default_accounts(
    *,
    account_scope: str,
    repo: str,
    project: str,
    config_path: str | None = None,
) -> dict[str, str]:
    """Resolve default target accounts for pipeline rendering."""
    if account_scope == "repo":
        repo_slug = _repo_slug(repo, project)
        return {
            "claude": f"claude-{repo_slug}",
            "codex": f"codex-{repo_slug}",
            "gemini": f"gemini-{repo_slug}",
        }

    static_defaults = {
        "claude": "work-claude",
        "codex": "work-codex",
        "gemini": "work-gemini",
    }
    if account_scope == "static":
        return static_defaults

    provider_config = _load_account_pool_config(config_path)
    resolved: dict[str, str] = {}
    for provider, fallback in static_defaults.items():
        entry = provider_config.get(provider, {})
        account = str(entry.get("default_account", "")).strip()
        if not account:
            raw_accounts = entry.get("accounts")
            if isinstance(raw_accounts, list):
                for candidate in raw_accounts:
                    account = str(candidate).strip()
                    if account:
                        break
        resolved[provider] = account or fallback
    return resolved


# ---------------------------------------------------------------------------
# Time formatting helpers
# ---------------------------------------------------------------------------


def _format_age(iso_timestamp: str | None) -> str:
    """Convert ISO-8601 timestamp to relative age string.

    Examples: "2s ago", "45s ago", "3m ago", "1h 15m ago", "2d ago".
    Returns "n/a" if parse fails or timestamp is None/empty.
    """
    if not iso_timestamp:
        return "n/a"
    try:
        ts = datetime.fromisoformat(iso_timestamp)
        delta = datetime.now(timezone.utc) - ts
        total_seconds = int(delta.total_seconds())
        if total_seconds < 0:
            total_seconds = 0
        if total_seconds < 60:
            return f"{total_seconds}s ago"
        if total_seconds < 3600:
            minutes = total_seconds // 60
            return f"{minutes}m ago"
        if total_seconds < 86400:
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            if minutes:
                return f"{hours}h {minutes}m ago"
            return f"{hours}h ago"
        days = total_seconds // 86400
        return f"{days}d ago"
    except (ValueError, TypeError, OSError):
        return "n/a"


def _format_duration(seconds: float) -> str:
    """Convert seconds to compact duration string.

    Examples: "12s", "3m12s", "1h5m", "2h15m".
    No "ago" suffix (used for task age and uptime).
    """
    total = int(seconds)
    if total < 60:
        return f"{total}s"
    if total < 3600:
        m = total // 60
        s = total % 60
        if s:
            return f"{m}m{s}s"
        return f"{m}m"
    h = total // 3600
    m = (total % 3600) // 60
    if m:
        return f"{h}h{m}m"
    return f"{h}h"


# ---------------------------------------------------------------------------
# Status command
# ---------------------------------------------------------------------------


def cmd_status(args: argparse.Namespace) -> None:
    """Show mesh worker state and queue summary."""
    base = _base_url()
    headers = _headers()

    # Fetch workers
    try:
        workers_resp = requests.get(f"{base}/workers", headers=headers, timeout=_router_timeout())
    except requests.ConnectionError as e:
        print(f"Error: Cannot connect to mesh router at {base} -- {e}", file=sys.stderr)
        sys.exit(1)

    if workers_resp.status_code == 401:
        print("Error: Authentication failed. Set MESH_AUTH_TOKEN.", file=sys.stderr)
        sys.exit(1)
    if workers_resp.status_code != 200:
        print(f"Error: /workers returned {workers_resp.status_code}", file=sys.stderr)
        sys.exit(1)

    # Fetch health
    try:
        health_resp = requests.get(f"{base}/health", headers=headers, timeout=_router_timeout())
    except requests.ConnectionError as e:
        print(f"Error: Cannot connect to mesh router at {base} -- {e}", file=sys.stderr)
        sys.exit(1)

    if health_resp.status_code != 200:
        print(f"Error: /health returned {health_resp.status_code}", file=sys.stderr)
        sys.exit(1)

    workers_data = workers_resp.json()
    health_data = health_resp.json()

    # --json: raw combined output
    if args.json_output:
        combined = {"workers": workers_data.get("workers", []), "health": health_data}
        print(json.dumps(combined, indent=2))
        return

    # Human-readable table
    workers = workers_data.get("workers", [])
    if not workers:
        print("No workers registered.")
    else:
        print("WORKERS")
        print(
            f"{'ID':<10} {'MACHINE':<12} {'TYPE':<8} {'STATUS':<10} "
            f"{'LAST HB':<12} TASKS"
        )
        for w in workers:
            wid = w.get("worker_id", "?")[:8]
            machine = w.get("machine", "?")[:12]
            cli_type = w.get("cli_type", "?")[:8]
            status = w.get("status", "?")[:10]
            last_hb = _format_age(w.get("last_heartbeat"))
            running_tasks = w.get("running_tasks", [])
            if running_tasks:
                count = len(running_tasks)
                oldest_age = max(t.get("age_s", 0) for t in running_tasks)
                tasks_str = f"{count} running ({_format_duration(oldest_age)})"
            else:
                tasks_str = "-"
            print(
                f"{wid:<10} {machine:<12} {cli_type:<8} {status:<10} "
                f"{last_hb:<12} {tasks_str}"
            )

    # Queue summary from /health
    queue_depth = health_data.get("queue_depth", 0)
    worker_count = health_data.get("workers", 0)
    uptime_s = health_data.get("uptime_s", 0)

    print()
    print("QUEUE")
    print(f"Queued: {queue_depth} | Workers: {worker_count}")
    print(f"Uptime: {_format_duration(uptime_s)}")


# ---------------------------------------------------------------------------
# Drain command
# ---------------------------------------------------------------------------


def cmd_drain(args: argparse.Namespace) -> None:
    """Drain a worker: POST /workers/<id>/drain, then poll until offline."""
    base = _base_url()
    headers = _headers()
    worker_id = args.worker_id
    timeout = args.timeout  # default 300s

    # Initiate drain
    try:
        resp = requests.post(
            f"{base}/workers/{worker_id}/drain", headers=headers, timeout=_router_timeout()
        )
    except requests.ConnectionError as e:
        print(
            f"Error: Cannot connect to mesh router at {base} -- {e}",
            file=sys.stderr,
        )
        sys.exit(1)

    if resp.status_code == 404:
        print(f"Error: Worker '{worker_id}' not found.", file=sys.stderr)
        sys.exit(1)
    elif resp.status_code == 409:
        detail = resp.json().get("detail", resp.json().get("error", "conflict"))
        print(
            f"Error: Cannot drain worker '{worker_id}': {detail}", file=sys.stderr
        )
        sys.exit(1)
    elif resp.status_code == 401:
        print("Error: Authentication failed. Set MESH_AUTH_TOKEN.", file=sys.stderr)
        sys.exit(1)
    elif resp.status_code != 202:
        print(
            f"Error: Unexpected response {resp.status_code}: {resp.text}",
            file=sys.stderr,
        )
        sys.exit(1)

    data = resp.json()
    status = data.get("status", "")

    # If worker drained immediately (was idle, no tasks)
    if status == "drained_immediately":
        print(
            f"Worker {worker_id} drained and retired. (was idle, no tasks)"
        )
        return

    # Poll GET /workers/<id> every 2s until status == "offline" or timeout
    print(f"Draining worker {worker_id}...")
    start = time.monotonic()
    poll_interval = 2

    while True:
        elapsed = time.monotonic() - start
        if elapsed >= timeout:
            print(
                f"Warning: Drain timed out after {timeout}s. "
                "Worker may still be draining on server.",
                file=sys.stderr,
            )
            sys.exit(1)

        time.sleep(poll_interval)

        try:
            resp = requests.get(
                f"{base}/workers/{worker_id}", headers=headers, timeout=_router_timeout()
            )
        except requests.ConnectionError:
            print(
                "Warning: Lost connection to router during drain polling.",
                file=sys.stderr,
            )
            sys.exit(1)

        if resp.status_code == 404:
            # Worker already gone (deregistered)
            print(f"Worker {worker_id} drained and retired.")
            return

        if resp.status_code != 200:
            print(
                f"Warning: Poll returned {resp.status_code}, retrying...",
                file=sys.stderr,
            )
            continue

        worker = resp.json()
        worker_status = worker.get("status", "")
        running = worker.get("running_tasks", [])

        if worker_status == "offline":
            print(f"Worker {worker_id} drained and retired.")
            return

        # Show progress
        task_count = len(running)
        if task_count > 0:
            print(f"  Waiting for {task_count} task(s)...")
        else:
            print(f"  Status: {worker_status}, waiting...")


# ---------------------------------------------------------------------------
# Submit command
# ---------------------------------------------------------------------------


def cmd_submit(args: argparse.Namespace) -> None:
    """Submit a new task via POST /tasks."""
    base = _base_url()
    headers = _headers()
    headers["Content-Type"] = "application/json"

    body: dict[str, object] = {"title": args.title}
    if args.cli:
        body["target_cli"] = args.cli
    if args.account:
        body["target_account"] = args.account
    if args.phase:
        body["phase"] = args.phase
    if args.priority is not None:
        body["priority"] = args.priority
    if args.payload:
        try:
            body["payload"] = json.loads(args.payload)
        except json.JSONDecodeError:
            print("Error: --payload must be valid JSON", file=sys.stderr)
            sys.exit(1)

    try:
        resp = requests.post(f"{base}/tasks", json=body, headers=headers, timeout=_router_timeout())
    except requests.ConnectionError as e:
        print(f"Error: Cannot connect to mesh router at {base} -- {e}", file=sys.stderr)
        sys.exit(1)

    if resp.status_code == 201:
        data = resp.json()
        print(f"Task created: {data.get('task_id', '?')}")
    elif resp.status_code == 401:
        print("Error: Authentication failed. Set MESH_AUTH_TOKEN.", file=sys.stderr)
        sys.exit(1)
    elif resp.status_code == 409:
        print(f"Error: Duplicate task -- {resp.json().get('detail', '')}", file=sys.stderr)
        sys.exit(1)
    else:
        print(f"Error: {resp.status_code} -- {resp.text}", file=sys.stderr)
        sys.exit(1)


def _post_task_admin_action(
    endpoint: str,
    task_id: str,
    reason: str,
) -> requests.Response:
    """POST a task admin action to the router."""
    base = _base_url()
    headers = _headers()
    headers["Content-Type"] = "application/json"
    return requests.post(
        f"{base}{endpoint}",
        json={"task_id": task_id, "reason": reason},
        headers=headers,
        timeout=_router_timeout(),
    )


def cmd_task_cancel(args: argparse.Namespace) -> None:
    """Admin cancel a non-running task."""
    base = _base_url()
    try:
        resp = _post_task_admin_action("/tasks/cancel", args.task_id, args.reason)
    except requests.ConnectionError as e:
        print(f"Error: Cannot connect to mesh router at {base} -- {e}", file=sys.stderr)
        sys.exit(1)

    if resp.status_code == 200:
        print(f"Task canceled: {args.task_id}")
        return
    if resp.status_code == 401:
        print("Error: Authentication failed. Set MESH_AUTH_TOKEN.", file=sys.stderr)
        sys.exit(1)
    if resp.status_code == 404:
        print(f"Error: task not found: {args.task_id}", file=sys.stderr)
        sys.exit(1)
    if resp.status_code == 409:
        detail = resp.json().get("detail", "transition_failed")
        print(f"Error: cannot cancel task {args.task_id} -- {detail}", file=sys.stderr)
        sys.exit(1)
    print(f"Error: {resp.status_code} -- {resp.text}", file=sys.stderr)
    sys.exit(1)


def cmd_task_fail(args: argparse.Namespace) -> None:
    """Admin fail a non-running task."""
    base = _base_url()
    try:
        resp = _post_task_admin_action("/tasks/admin-fail", args.task_id, args.reason)
    except requests.ConnectionError as e:
        print(f"Error: Cannot connect to mesh router at {base} -- {e}", file=sys.stderr)
        sys.exit(1)

    if resp.status_code == 200:
        print(f"Task failed: {args.task_id}")
        return
    if resp.status_code == 401:
        print("Error: Authentication failed. Set MESH_AUTH_TOKEN.", file=sys.stderr)
        sys.exit(1)
    if resp.status_code == 404:
        print(f"Error: task not found: {args.task_id}", file=sys.stderr)
        sys.exit(1)
    if resp.status_code == 409:
        detail = resp.json().get("detail", "transition_failed")
        print(f"Error: cannot fail task {args.task_id} -- {detail}", file=sys.stderr)
        sys.exit(1)
    print(f"Error: {resp.status_code} -- {resp.text}", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Pipeline commands
# ---------------------------------------------------------------------------


def _default_pipeline_template_file() -> Path:
    """Default YAML path for pipeline templates."""
    return Path(__file__).resolve().parent.parent / "mapping" / "pipeline_templates.yaml"


def _load_pipeline_templates(path: str) -> dict:
    """Load pipeline templates YAML from disk."""
    file_path = Path(path).expanduser()
    if not file_path.exists():
        raise FileNotFoundError(f"Template file not found: {file_path}")
    with file_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError("Template YAML root must be a mapping")
    templates = data.get("templates")
    if not isinstance(templates, dict):
        raise ValueError("Template YAML must contain 'templates' mapping")
    return data


class _StrictFormatDict(dict):
    """str.format_map dict that fails for missing keys."""

    def __missing__(self, key: str) -> str:
        raise KeyError(key)


def _render_text(template: str, variables: dict[str, str]) -> str:
    """Render a format string with strict placeholder validation."""
    return template.format_map(_StrictFormatDict(variables))


def _repo_slug(repo_path: str, project: str = "") -> str:
    """Build a stable repo slug for account/profile naming."""
    candidate = Path(repo_path.rstrip("/")).name or project or "repo"
    slug = re.sub(r"[^a-z0-9]+", "-", candidate.lower()).strip("-")
    return slug or "repo"


def _pipeline_execution_policy_from_env() -> tuple[str, bool]:
    """Return (default_execution_mode, enforce_session_only) from env."""
    default_mode = os.environ.get("MESH_DEFAULT_EXECUTION_MODE", "batch").strip()
    if default_mode not in {"batch", "session"}:
        default_mode = "batch"
    enforce_session_only = os.environ.get("MESH_ENFORCE_SESSION_ONLY", "").strip().lower() in {
        "1", "true", "yes",
    }
    return default_mode, enforce_session_only


def cmd_pipeline_create(args: argparse.Namespace) -> None:
    """Create a thread and all steps from a named pipeline template."""
    base = _base_url()
    headers = _headers()
    headers["Content-Type"] = "application/json"

    template_file = args.template_file or str(_default_pipeline_template_file())
    try:
        loaded = _load_pipeline_templates(template_file)
    except (FileNotFoundError, ValueError, yaml.YAMLError) as e:
        print(f"Error: cannot load template file -- {e}", file=sys.stderr)
        sys.exit(1)

    templates = loaded["templates"]
    template = templates.get(args.template)
    if not isinstance(template, dict):
        known = ", ".join(sorted(templates.keys()))
        print(
            f"Error: unknown template '{args.template}'. Known templates: {known}",
            file=sys.stderr,
        )
        sys.exit(1)

    raw_steps = template.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        print(
            f"Error: template '{args.template}' has no steps",
            file=sys.stderr,
        )
        sys.exit(1)

    account_scope = str(getattr(args, "account_scope", "config") or "config").strip().lower()
    if account_scope not in {"config", "static", "repo"}:
        print("Error: --account-scope must be 'config', 'static', or 'repo'", file=sys.stderr)
        sys.exit(1)

    default_accounts = _resolve_default_accounts(
        account_scope=account_scope,
        repo=args.repo,
        project=args.project,
    )

    account_claude = (args.account_claude or default_accounts["claude"]).strip()
    account_codex = (args.account_codex or default_accounts["codex"]).strip()
    account_gemini = (args.account_gemini or default_accounts["gemini"]).strip()

    variables = {
        "repo": args.repo,
        "phase": args.phase,
        "project": args.project,
        "feature": args.feature,
        "claude_account": account_claude,
        "codex_account": account_codex,
        "gemini_account": account_gemini,
    }

    prepared_steps: list[dict[str, object]] = []
    valid_cli = {"claude", "codex", "gemini"}
    valid_modes = {"batch", "session"}
    valid_failure = {"abort", "skip", "retry"}
    default_mode_from_env, enforce_session_only = _pipeline_execution_policy_from_env()
    default_account_template = {
        "claude": "{claude_account}",
        "codex": "{codex_account}",
        "gemini": "{gemini_account}",
    }

    for idx, raw in enumerate(raw_steps):
        if not isinstance(raw, dict):
            print(f"Error: template step #{idx} is not a mapping", file=sys.stderr)
            sys.exit(1)

        name = str(raw.get("name", f"step-{idx}")).strip()
        title_template = str(raw.get("title", "")).strip()
        prompt_template = str(raw.get("prompt", "")).strip()
        target_cli = str(raw.get("target_cli", "claude")).strip()
        execution_mode_raw = raw.get("execution_mode")
        if execution_mode_raw is None or not str(execution_mode_raw).strip():
            execution_mode = default_mode_from_env
        else:
            execution_mode = str(execution_mode_raw).strip()
        if enforce_session_only:
            execution_mode = "session"
        on_failure = str(raw.get("on_failure", "abort")).strip()
        role = str(raw.get("role", "")).strip()
        critical = bool(raw.get("critical", False))
        review_policy = str(raw.get("review_policy", "none")).strip()

        if target_cli not in valid_cli:
            print(
                f"Error: invalid target_cli '{target_cli}' in step '{name}'",
                file=sys.stderr,
            )
            sys.exit(1)
        if execution_mode not in valid_modes:
            print(
                f"Error: invalid execution_mode '{execution_mode}' in step '{name}'",
                file=sys.stderr,
            )
            sys.exit(1)
        if on_failure not in valid_failure:
            print(
                f"Error: invalid on_failure '{on_failure}' in step '{name}'",
                file=sys.stderr,
            )
            sys.exit(1)
        if not title_template or not prompt_template:
            print(
                f"Error: step '{name}' requires non-empty title and prompt",
                file=sys.stderr,
            )
            sys.exit(1)

        account_template = str(
            raw.get("target_account", default_account_template[target_cli])
        ).strip()

        depends_on_steps_raw = raw.get("depends_on_steps", [])
        if depends_on_steps_raw is None:
            depends_on_steps_raw = []
        if not isinstance(depends_on_steps_raw, list):
            print(
                f"Error: depends_on_steps must be a list in step '{name}'",
                file=sys.stderr,
            )
            sys.exit(1)
        depends_on_steps: list[int] = []
        for dep in depends_on_steps_raw:
            try:
                dep_idx = int(dep)
            except (TypeError, ValueError):
                print(
                    f"Error: invalid depends_on_steps value '{dep}' in step '{name}'",
                    file=sys.stderr,
                )
                sys.exit(1)
            depends_on_steps.append(dep_idx)

        try:
            title = _render_text(title_template, variables)
            prompt = _render_text(prompt_template, variables)
            target_account = _render_text(account_template, variables)
        except KeyError as e:
            print(
                f"Error: missing template variable '{e.args[0]}' in step '{name}'",
                file=sys.stderr,
            )
            sys.exit(1)

        prepared_steps.append({
            "index": idx,
            "name": name,
            "title": title,
            "prompt": prompt,
            "target_cli": target_cli,
            "target_account": target_account,
            "execution_mode": execution_mode,
            "critical": critical,
            "on_failure": on_failure,
            "role": role,
            "review_policy": review_policy,
            "depends_on_steps": depends_on_steps,
        })

    if args.dry_run:
        output = {
            "template": args.template,
            "thread_name": args.thread_name,
            "repo": args.repo,
            "account_scope": account_scope,
            "accounts": {
                "claude": account_claude,
                "codex": account_codex,
                "gemini": account_gemini,
            },
            "policy": {
                "default_execution_mode": default_mode_from_env,
                "enforce_session_only": enforce_session_only,
            },
            "steps": prepared_steps,
        }
        print(json.dumps(output, indent=2))
        return

    try:
        thread_resp = requests.post(
            f"{base}/threads",
            headers=headers,
            json={"name": args.thread_name},
            timeout=_router_timeout(),
        )
    except requests.ConnectionError as e:
        print(f"Error: Cannot connect to mesh router at {base} -- {e}", file=sys.stderr)
        sys.exit(1)
    if thread_resp.status_code != 201:
        print(
            f"Error: cannot create thread ({thread_resp.status_code}) -- {thread_resp.text}",
            file=sys.stderr,
        )
        sys.exit(1)

    thread_data = thread_resp.json()
    thread_id = thread_data.get("thread_id", "")
    if not thread_id:
        print("Error: thread create response missing thread_id", file=sys.stderr)
        sys.exit(1)

    step_task_ids: dict[int, str] = {}
    created = 0
    for step in prepared_steps:
        depends_on_steps = step["depends_on_steps"]
        depends_on_task_ids: list[str] = []
        for dep_idx in depends_on_steps:
            dep_task_id = step_task_ids.get(dep_idx)
            if not dep_task_id:
                print(
                    f"Error: step '{step['name']}' references unknown dependency index {dep_idx}",
                    file=sys.stderr,
                )
                sys.exit(1)
            depends_on_task_ids.append(dep_task_id)

        body: dict[str, object] = {
            "title": step["title"],
            "step_index": step["index"],
            "repo": args.repo,
            "role": step["role"],
            "target_cli": step["target_cli"],
            "target_account": step["target_account"],
            "execution_mode": step["execution_mode"],
            "critical": step["critical"],
            "on_failure": step["on_failure"],
            "payload": {
                "prompt": step["prompt"],
                "working_dir": args.repo,
                "pipeline_template": args.template,
                "pipeline_step": step["name"],
                "review_policy": step["review_policy"],
            },
        }
        if depends_on_task_ids:
            body["depends_on"] = depends_on_task_ids

        try:
            step_resp = requests.post(
                f"{base}/threads/{thread_id}/steps",
                headers=headers,
                json=body,
                timeout=_router_timeout(),
            )
        except requests.ConnectionError as e:
            print(
                f"Error: Cannot connect to mesh router at {base} while creating steps -- {e}",
                file=sys.stderr,
            )
            sys.exit(1)

        if step_resp.status_code != 201:
            print(
                f"Error: cannot create step {step['index']} ({step_resp.status_code}) -- {step_resp.text}",
                file=sys.stderr,
            )
            sys.exit(1)

        data = step_resp.json()
        task_id = str(data.get("task_id", "")).strip()
        if not task_id:
            print(
                f"Error: step create response missing task_id for step {step['index']}",
                file=sys.stderr,
            )
            sys.exit(1)
        step_task_ids[int(step["index"])] = task_id
        created += 1

    if args.json_output:
        print(json.dumps({
            "thread_id": thread_id,
            "thread_name": args.thread_name,
            "template": args.template,
            "steps_created": created,
            "step_task_ids": step_task_ids,
        }, indent=2))
        return

    print(f"Pipeline thread created: {thread_id} ({args.thread_name})")
    print(f"Template: {args.template} | Steps: {created}")
    for idx in sorted(step_task_ids.keys()):
        print(f"  step {idx}: task_id={step_task_ids[idx]}")


# ---------------------------------------------------------------------------
# Thread helper
# ---------------------------------------------------------------------------


def _resolve_thread_id(name_or_id: str) -> str:
    """Resolve a thread name or ID to a thread_id.

    If the input looks like a UUID (36 chars with dashes), use as-is.
    Otherwise, query GET /threads?name=X and resolve:
    - 1 match: return its thread_id
    - 0 matches: error
    - >1 matches: error (ambiguous)
    """
    # UUID format check: 8-4-4-4-12 = 36 chars with dashes
    if len(name_or_id) == 36 and name_or_id.count("-") == 4:
        return name_or_id

    base = _base_url()
    headers = _headers()
    try:
        resp = requests.get(
            f"{base}/threads", params={"name": name_or_id}, headers=headers, timeout=_router_timeout()
        )
    except requests.ConnectionError as e:
        print(f"Error: Cannot connect to mesh router at {base} -- {e}", file=sys.stderr)
        sys.exit(1)

    if resp.status_code != 200:
        print(f"Error: /threads returned {resp.status_code}", file=sys.stderr)
        sys.exit(1)

    threads = resp.json().get("threads", [])
    if len(threads) == 0:
        print(f"Error: Thread not found: '{name_or_id}'", file=sys.stderr)
        sys.exit(1)
    if len(threads) > 1:
        print(
            f"Error: Ambiguous thread name '{name_or_id}', "
            f"{len(threads)} threads match. Use thread_id instead.",
            file=sys.stderr,
        )
        sys.exit(1)

    return threads[0]["thread_id"]


# ---------------------------------------------------------------------------
# Thread commands
# ---------------------------------------------------------------------------


def cmd_thread_create(args: argparse.Namespace) -> None:
    """Create a new thread."""
    base = _base_url()
    headers = _headers()
    headers["Content-Type"] = "application/json"
    body = {"name": args.name}
    try:
        resp = requests.post(f"{base}/threads", json=body, headers=headers, timeout=_router_timeout())
    except requests.ConnectionError as e:
        print(f"Error: Cannot connect to mesh router at {base} -- {e}", file=sys.stderr)
        sys.exit(1)

    if resp.status_code == 201:
        data = resp.json()
        print(f"Thread created: {data.get('thread_id', '?')} ({data.get('name', '')})")
    else:
        print(f"Error: {resp.status_code} -- {resp.text}", file=sys.stderr)
        sys.exit(1)


def cmd_thread_add_step(args: argparse.Namespace) -> None:
    """Add a step to a thread."""
    base = _base_url()
    headers = _headers()
    headers["Content-Type"] = "application/json"
    thread_id = _resolve_thread_id(args.thread)
    body: dict[str, object] = {
        "title": args.title,
        "step_index": args.step_index,
        "repo": args.repo or "",
        "role": args.role or "",
        "target_cli": args.cli or "claude",
        "target_account": args.account or "work",
        "on_failure": args.on_failure or "abort",
    }
    if args.payload:
        try:
            body["payload"] = json.loads(args.payload)
        except json.JSONDecodeError:
            print("Error: --payload must be valid JSON", file=sys.stderr)
            sys.exit(1)
    try:
        resp = requests.post(
            f"{base}/threads/{thread_id}/steps", json=body, headers=headers, timeout=_router_timeout()
        )
    except requests.ConnectionError as e:
        print(f"Error: Cannot connect to mesh router at {base} -- {e}", file=sys.stderr)
        sys.exit(1)

    if resp.status_code == 201:
        data = resp.json()
        print(f"Step {args.step_index} added: task_id={data.get('task_id', '?')}")
    else:
        print(f"Error: {resp.status_code} -- {resp.text}", file=sys.stderr)
        sys.exit(1)


def cmd_thread_status(args: argparse.Namespace) -> None:
    """Show thread status with steps table."""
    base = _base_url()
    headers = _headers()
    thread_id = _resolve_thread_id(args.thread)
    try:
        resp = requests.get(
            f"{base}/threads/{thread_id}/status", headers=headers, timeout=_router_timeout()
        )
    except requests.ConnectionError as e:
        print(f"Error: Cannot connect to mesh router at {base} -- {e}", file=sys.stderr)
        sys.exit(1)

    if resp.status_code != 200:
        print(f"Error: {resp.status_code}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    thread = data["thread"]
    steps = data["steps"]

    if args.json_output:
        print(json.dumps(data, indent=2))
        return

    print(f"THREAD: {thread.get('name', '?')} [{thread.get('status', '?')}]")
    print(f"{'STEP':<6} {'STATUS':<12} {'REPO':<16} {'WORKER':<10} {'ATTEMPT':<9} {'POLICY':<8} TITLE")
    for s in steps:
        idx = s.get("step_index", "?")
        status = str(s.get("status", "?"))[:12]
        repo = (s.get("repo", "") or "")[:16]
        worker = (s.get("assigned_worker", "") or "")[:8]
        attempt = f"{s.get('attempt', 1)}/3"
        on_failure = (s.get("on_failure", "abort") or "abort")[:8]
        title = s.get("title", "")[:30]
        if s.get("has_handoff"):
            title = f"[HANDOFF] {title}"[:40]
        print(f"{idx:<6} {status:<12} {repo:<16} {worker:<10} {attempt:<9} {on_failure:<8} {title}")


def cmd_thread_handoff(args: argparse.Namespace) -> None:
    """Show handoff details for a specific thread step."""
    base = _base_url()
    headers = _headers()
    thread_id = _resolve_thread_id(args.thread)

    # Get thread status to find the task_id for the step
    try:
        resp = requests.get(
            f"{base}/threads/{thread_id}/status", headers=headers, timeout=_router_timeout()
        )
    except requests.ConnectionError as e:
        print(f"Error: Cannot connect to mesh router at {base} -- {e}", file=sys.stderr)
        sys.exit(1)

    if resp.status_code != 200:
        print(f"Error: {resp.status_code}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    steps = data["steps"]
    target_step = None
    for s in steps:
        if s.get("step_index") == args.step_index:
            target_step = s
            break

    if target_step is None:
        print(f"Error: step {args.step_index} not found in thread", file=sys.stderr)
        sys.exit(1)

    if not target_step.get("has_handoff"):
        print(f"Step {args.step_index} does not carry handoff data.", file=sys.stderr)
        sys.exit(1)

    # Fetch full task to get payload
    task_id = target_step["task_id"]
    try:
        resp = requests.get(f"{base}/tasks/{task_id}", headers=headers, timeout=_router_timeout())
    except requests.ConnectionError as e:
        print(f"Error: Cannot connect to mesh router at {base} -- {e}", file=sys.stderr)
        sys.exit(1)

    if resp.status_code != 200:
        print(f"Error: {resp.status_code}", file=sys.stderr)
        sys.exit(1)

    task_data = resp.json()
    payload = task_data.get("payload") or {}
    handoff = payload.get("handoff")
    if not handoff:
        print("No handoff data found in task payload.", file=sys.stderr)
        sys.exit(1)

    if args.json_output:
        print(json.dumps(handoff, indent=2))
        return

    src = handoff.get("source_repo", "?")
    tgt = handoff.get("target_repo", "?")
    print(f"HANDOFF: {src} -> {tgt}")
    if handoff.get("summary"):
        print(f"Summary: {handoff['summary']}")
    if handoff.get("question"):
        print(f"Question: {handoff['question']}")
    for key in ("decisions", "artifacts", "open_risks"):
        items = handoff.get(key, [])
        if items:
            print(f"{key.replace('_', ' ').title()}: ({len(items)})")
            for item in items:
                print(f"  - {item}")
    sessions = handoff.get("related_session_ids", [])
    if sessions:
        print(f"Related Sessions: {', '.join(sessions)}")


def cmd_thread_context(args: argparse.Namespace) -> None:
    """Show thread context (aggregated results from completed steps)."""
    base = _base_url()
    headers = _headers()
    thread_id = _resolve_thread_id(args.thread)
    try:
        resp = requests.get(
            f"{base}/threads/{thread_id}/context", headers=headers, timeout=_router_timeout()
        )
    except requests.ConnectionError as e:
        print(f"Error: Cannot connect to mesh router at {base} -- {e}", file=sys.stderr)
        sys.exit(1)

    if resp.status_code != 200:
        print(f"Error: {resp.status_code}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    print(json.dumps(data, indent=2))


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

parser = argparse.ArgumentParser(prog="meshctl", description="Mesh operator CLI")
sub = parser.add_subparsers(dest="command")

status_parser = sub.add_parser("status", help="Show mesh state")
status_parser.add_argument(
    "--json",
    action="store_true",
    dest="json_output",
    help="Machine-readable JSON output",
)

drain_parser = sub.add_parser("drain", help="Gracefully drain a worker")
drain_parser.add_argument("worker_id", help="Worker ID to drain")
drain_parser.add_argument(
    "--timeout",
    type=int,
    default=300,
    help="Timeout in seconds (default: 300)",
)

submit_parser = sub.add_parser("submit", help="Submit a new task")
submit_parser.add_argument("--title", required=True, help="Task title")
submit_parser.add_argument("--cli", default=None, help="Target CLI type (claude/codex/gemini)")
submit_parser.add_argument("--account", default=None, help="Target account profile")
submit_parser.add_argument("--phase", default=None, help="Task phase")
submit_parser.add_argument("--priority", type=int, default=None, help="Priority (higher = first)")
submit_parser.add_argument("--payload", default=None, help="JSON payload string")

task_parser = sub.add_parser("task", help="Administrative task actions")
task_sub = task_parser.add_subparsers(dest="task_command")

task_cancel_parser = task_sub.add_parser("cancel", help="Cancel a non-running task")
task_cancel_parser.add_argument("task_id", help="Task ID")
task_cancel_parser.add_argument(
    "--reason",
    default="admin_cancel",
    help="Audit reason written to the transition event",
)

task_fail_parser = task_sub.add_parser("fail", help="Fail a non-running task")
task_fail_parser.add_argument("task_id", help="Task ID")
task_fail_parser.add_argument(
    "--reason",
    default="admin_fail",
    help="Audit reason written to the transition event",
)

pipeline_parser = sub.add_parser("pipeline", help="Pipeline template orchestration")
pipeline_sub = pipeline_parser.add_subparsers(dest="pipeline_command")

pipeline_create_parser = pipeline_sub.add_parser(
    "create",
    help="Create thread + ordered steps from YAML template",
)
pipeline_create_parser.add_argument("--template", required=True, help="Template name (e.g. gsd, speckit)")
pipeline_create_parser.add_argument("--thread-name", required=True, help="Thread name to create")
pipeline_create_parser.add_argument("--repo", required=True, help="Repository path")
pipeline_create_parser.add_argument("--phase", default="", help="Pipeline phase label/number")
pipeline_create_parser.add_argument("--project", default="", help="Project name for prompt rendering")
pipeline_create_parser.add_argument("--feature", default="", help="Feature name for prompt rendering")
pipeline_create_parser.add_argument(
    "--template-file",
    default=str(_default_pipeline_template_file()),
    help="YAML template file path",
)
pipeline_create_parser.add_argument(
    "--account-scope",
    choices=["config", "static", "repo"],
    default="config",
    help="Account selection strategy: config (mapping/account_pools.yaml), static (work-*), or repo (<provider>-<repo>).",
)
pipeline_create_parser.add_argument(
    "--account-claude", default=None, help="Override account profile for Claude steps.",
)
pipeline_create_parser.add_argument(
    "--account-codex", default=None, help="Override account profile for Codex steps.",
)
pipeline_create_parser.add_argument(
    "--account-gemini", default=None, help="Override account profile for Gemini steps.",
)
pipeline_create_parser.add_argument(
    "--dry-run", action="store_true", help="Render and print pipeline plan without API calls",
)
pipeline_create_parser.add_argument(
    "--json", action="store_true", dest="json_output", help="Machine-readable output",
)

thread_parser = sub.add_parser("thread", help="Thread management")
thread_sub = thread_parser.add_subparsers(dest="thread_command")

thread_create_parser = thread_sub.add_parser("create", help="Create a new thread")
thread_create_parser.add_argument("--name", required=True, help="Thread name")

thread_add_step_parser = thread_sub.add_parser("add-step", help="Add step to thread")
thread_add_step_parser.add_argument("--thread", required=True, help="Thread ID or name")
thread_add_step_parser.add_argument("--title", required=True, help="Step title")
thread_add_step_parser.add_argument(
    "--step-index", type=int, required=True, dest="step_index", help="Step index (0-based)"
)
thread_add_step_parser.add_argument("--repo", default="", help="Repository path")
thread_add_step_parser.add_argument("--role", default="", help="Step role (e.g. PRESIDENT_GLOBAL for cross-repo handoff)")
thread_add_step_parser.add_argument("--cli", default=None, help="Target CLI")
thread_add_step_parser.add_argument("--account", default=None, help="Target account")
thread_add_step_parser.add_argument("--payload", default=None, help="JSON payload")
thread_add_step_parser.add_argument(
    "--on-failure", default="abort", dest="on_failure",
    choices=["abort", "skip", "retry"],
    help="Failure policy: abort (default), skip, retry",
)

thread_status_parser = thread_sub.add_parser("status", help="Show thread status")
thread_status_parser.add_argument("thread", help="Thread ID or name")
thread_status_parser.add_argument("--json", action="store_true", dest="json_output")

thread_context_parser = thread_sub.add_parser("context", help="Show thread context")
thread_context_parser.add_argument("thread", help="Thread ID or name")

thread_handoff_parser = thread_sub.add_parser("handoff", help="Show handoff details for a step")
thread_handoff_parser.add_argument("thread", help="Thread ID or name")
thread_handoff_parser.add_argument("step_index", type=int, help="Step index (0-based)")
thread_handoff_parser.add_argument("--json", action="store_true", dest="json_output")

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parsed_args = parser.parse_args()
    if parsed_args.command == "status":
        cmd_status(parsed_args)
    elif parsed_args.command == "drain":
        cmd_drain(parsed_args)
    elif parsed_args.command == "submit":
        cmd_submit(parsed_args)
    elif parsed_args.command == "task":
        if parsed_args.task_command == "cancel":
            cmd_task_cancel(parsed_args)
        elif parsed_args.task_command == "fail":
            cmd_task_fail(parsed_args)
        else:
            task_parser.print_help()
            sys.exit(1)
    elif parsed_args.command == "pipeline":
        if parsed_args.pipeline_command == "create":
            cmd_pipeline_create(parsed_args)
        else:
            pipeline_parser.print_help()
            sys.exit(1)
    elif parsed_args.command == "thread":
        if parsed_args.thread_command == "create":
            cmd_thread_create(parsed_args)
        elif parsed_args.thread_command == "add-step":
            cmd_thread_add_step(parsed_args)
        elif parsed_args.thread_command == "status":
            cmd_thread_status(parsed_args)
        elif parsed_args.thread_command == "context":
            cmd_thread_context(parsed_args)
        elif parsed_args.thread_command == "handoff":
            cmd_thread_handoff(parsed_args)
        else:
            thread_parser.print_help()
            sys.exit(1)
    else:
        parser.print_help()
        sys.exit(1)
