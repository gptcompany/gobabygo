from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_mesh_live_runbook_is_linked_from_primary_docs() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    quickstart = (ROOT / "QUICKSTART.md").read_text(encoding="utf-8")

    assert "[MESH_LIVE.md](MESH_LIVE.md)" in readme
    assert "[MESH_LIVE.md](MESH_LIVE.md)" in quickstart
    assert "mesh live recover-codex-submit <session> <id>" in quickstart
    assert "mesh live ensure-antigravity" in quickstart


def test_mesh_live_runbook_covers_operator_contract() -> None:
    runbook = (ROOT / "MESH_LIVE.md").read_text(encoding="utf-8")
    normalized = " ".join(runbook.split())

    for required in (
        "wboard 30",
        "wbrief --repo rektslug",
        "wbrief --all",
        "wsattach claude-coordinator",
        "wsend claude-rektslug",
        "mcoordinator rektslug --worker codex-rektslug",
        "mcoordinator rektslug # intra-repo; worker bootstrap is automatic",
        "mcoordinator --all",
        "mcoordinator rektslug --workflow speckit",
        "mcoordinator rektslug --workflow direct",
        "mcoordinator --all --continue",
        "mcoordinator --all --resume <claude-session-id>",
        "old conversation and the freshly generated contract",
        "private per-UUID `flock`",
        "simultaneous-start race",
        "`--continue` cannot provide the per-UUID guarantee",
        "does not depend on Mac aliases",
        "mesh live ensure-codex <repo>",
        "mesh live ensure-antigravity <repo>",
        "antigravity-<repo>",
        "fixed no-tools bootstrap prompt",
        "--dangerously-skip-permissions --new-project",
        "MESH_LIVE_REPO_ROOTS",
        "Neither bootstrap accepts task text",
        "unavailable through the remote live endpoint",
        "operator's explicit objective",
        "ambiguous names fail before tmux mutation",
        "immutable control-plane runtime",
        "Both ensure commands reject that exact Git root before tmux mutation",
        "do not create a branch inside the live runtime",
        "they are not an OS sandbox",
        "already running coordinator therefore keeps its old contract",
        "The default `adaptive` workflow",
        "MESH_COORDINATOR_WORKFLOW",
        "mesh live workflow show speckit --json",
        "mesh live workflow show speckit --scope coordinator --json",
        "mapping/pipeline_templates.yaml",
        "does not connect to the router, inspect tmux, or require iTerm2",
        "uses repository scope",
        "uses coordinator scope",
        "late-bound for each concrete delegation",
        "not mandatory startup parameters",
        "router thread/database may persist selected tasks",
        "`speckit.analyze` checks consistency",
        "Independent perspectives come from",
        "one active writer per repository",
        "different tmux session for each reviewer/challenger",
        "By default use Antigravity as writer and Codex as primary reviewer",
        "every provider may review",
        "REVIEW_VERDICT: PASS",
        "REVIEW_VERDICT: CHANGES_REQUIRED",
        "exact `file:line`",
        "coordinator contract rules, not filesystem locks or an OS sandbox",
        "YOLO workers retain every permission of their Dell user",
        "recheck tmux ownership and Git state",
        "independent context but must not be reported as a different model view",
        "distinct delegation IDs",
        "`ensure-codex` or `ensure-antigravity` may create a missing worker",
        "degraded coverage",
        "never creates a router thread",
        "DELEGATION_ID",
        "successful tmux send is not treated as delivery",
        "main Codex composer is recognizable, empty, and idle",
        "current Antigravity footer",
        "There is no Antigravity recovery command",
        "submission=verified",
        "recognized stock placeholder",
        "MCP startup warnings do not by themselves make the composer occupied",
        "refused before any key input or receipt invalidation",
        "Never bypass it by omitting `--delegation-id`",
        "bounded paste-settle recovery",
        "one literal line up to 8192 characters",
        "non-secret brief inside the target repository",
        "--delegation-id <DELEGATION_ID> --enter",
        "metadata-only recovery receipt",
        "submission=not-submitted",
        "tracked=no",
        "`[Pasted Content N chars]`",
        "expires after 15 minutes",
        "never clears the composer automatically",
        "never falls back to a naked Enter",
        "submission=unknown",
        "Unknown requires bounded follow-up peeks",
        "text_delivered",
        "enter_delivered",
        "exact current `DELEGATION_ID`",
        "rejects every second attempt",
        "evidence-driven",
        "MESH_COORDINATOR_CLAUDE_CMD",
        "MESH_COORDINATOR_MESH_SCRIPT",
        "clean runtime checkout",
        "missing target fails",
        "never fall back to the repository-base",
        "tmux owns process/session persistence",
        "MESH_LIVE_HOSTS",
        "router database",
        "iTerm2 local state",
        "Treat `send` as remote keyboard access",
        "mesh live tick --apply",
        "exact Claude rate-limit menu",
        "manual_rate_limit",
        "install-mesh-live-cron.sh",
        "internal non-blocking lock",
        "never stores pane captures",
        "do not point live tick at router-managed owners",
        "Claude as its current process",
        "cannot be read or its managed marker block is malformed",
        "cannot eliminate the race",
    ):
        assert required in normalized


def test_architecture_distinguishes_manual_and_router_managed_truth() -> None:
    architecture = (ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8")

    assert "Router-managed orchestration is authoritative" in architecture
    assert "manual session liveness" in architecture
    assert "router-owned sessions remain under `session_worker` policy" in architecture
    assert "Coordinator scope keeps the program objective" in architecture
    assert "late-binds `{repo}` plus `{feature}`" in architecture
    assert "router/database remains optional durable history" in architecture
