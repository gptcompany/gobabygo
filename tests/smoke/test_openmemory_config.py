import json
import re
from pathlib import Path

import pytest
import yaml


def test_config_syntax_validity():
    """Verify that OpenMemory config files are syntactically correct."""
    compose_path = Path("deploy/openmemory/compose.yml")
    snippet_path = Path("deploy/openmemory/mcp-config-snippet.json")

    assert compose_path.exists(), "compose.yml is missing"
    assert snippet_path.exists(), "mcp-config-snippet.json is missing"

    # Validate YAML
    with open(compose_path) as f:
        yaml.safe_load(f)

    # Validate JSON
    with open(snippet_path) as f:
        json.load(f)


def test_endpoint_consistency():
    """Verify that the MCP URL is consistent across topology and snippet."""
    topology_path = Path("deploy/topology.v1.4.example.yml")
    snippet_path = Path("deploy/openmemory/mcp-config-snippet.json")

    if not topology_path.exists():
        pytest.skip("topology.v1.4.example.yml not found")

    with open(topology_path) as f:
        topo = yaml.safe_load(f)
    with open(snippet_path) as f:
        snippet = json.load(f)

    topo_url = topo.get("global", {}).get("memory", {}).get("endpoint")
    snippet_url = snippet.get("mcpServers", {}).get("openmemory", {}).get("url")

    assert topo_url is not None, "Memory endpoint missing in topology"
    assert snippet_url is not None, "Memory URL missing in snippet"
    assert topo_url == snippet_url, f"Endpoint mismatch: {topo_url} vs {snippet_url}"


def test_env_example_completeness():
    """Verify that required environment variables in compose.yml are in .env.example."""
    compose_path = Path("deploy/openmemory/compose.yml")
    env_example_path = Path("deploy/openmemory/.env.example")

    assert compose_path.exists(), "compose.yml is missing"
    assert env_example_path.exists(), ".env.example is missing"

    # Find all mandatory variables using ${VAR:?msg} or ${VAR?msg} syntax
    content = compose_path.read_text()
    required_vars = re.findall(r"\$\{([A-Z0-9_]+)(?::\?|\?)[^}]*\}", content)

    # Find all variables with defaults using ${VAR:-default} or ${VAR-default} syntax
    optional_vars = re.findall(r"\$\{([A-Z0-9_]+)(?::-|-)[^}]*\}", content)

    # Find simple variables ${VAR} without modifiers
    simple_vars = re.findall(r"\$\{([A-Z0-9_]+)\}", content)

    all_found_vars = set(required_vars + optional_vars + simple_vars)

    env_content = env_example_path.read_text()

    for var in all_found_vars:
        assert var in env_content, f"Variable {var} found in compose.yml missing from .env.example"
