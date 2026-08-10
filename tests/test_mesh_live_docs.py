from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_mesh_live_runbook_is_linked_from_primary_docs() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    quickstart = (ROOT / "QUICKSTART.md").read_text(encoding="utf-8")

    assert "[MESH_LIVE.md](MESH_LIVE.md)" in readme
    assert "[MESH_LIVE.md](MESH_LIVE.md)" in quickstart


def test_mesh_live_runbook_covers_operator_contract() -> None:
    runbook = (ROOT / "MESH_LIVE.md").read_text(encoding="utf-8")

    for required in (
        "wboard 30",
        "wbrief --repo rektslug",
        "wbrief --all",
        "wsattach claude-coordinator",
        "wsend claude-rektslug",
        "mcoordinator rektslug --worker codex-rektslug",
        "mcoordinator --all",
        "DELEGATION_ID",
        "successful tmux send is not treated as delivery",
        "MESH_COORDINATOR_CLAUDE_CMD",
        "MESH_COORDINATOR_MESH_SCRIPT",
        "clean runtime checkout",
        "missing target fails with",
        "never fall back to the repository-base",
        "tmux owns process/session persistence",
        "MESH_LIVE_HOSTS",
        "router database",
        "iTerm2 local state",
        "Treat `send` as remote keyboard access",
    ):
        assert required in runbook


def test_architecture_distinguishes_manual_and_router_managed_truth() -> None:
    architecture = (ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8")

    assert "Router-managed orchestration is authoritative" in architecture
    assert "manual session liveness" in architecture
