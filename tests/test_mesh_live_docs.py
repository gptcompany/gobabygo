from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_mesh_live_runbook_is_linked_from_primary_docs() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    quickstart = (ROOT / "QUICKSTART.md").read_text(encoding="utf-8")

    assert "[MESH_LIVE.md](MESH_LIVE.md)" in readme
    assert "[MESH_LIVE.md](MESH_LIVE.md)" in quickstart
    assert "mesh live recover-codex-submit <session> <id>" in quickstart


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
        "mcoordinator --all",
        "mcoordinator --all --continue",
        "mcoordinator --all --resume <claude-session-id>",
        "old conversation and the freshly generated contract",
        "does not depend on Mac aliases",
        "DELEGATION_ID",
        "successful tmux send is not treated as delivery",
        "bounded paste-settle recovery",
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
