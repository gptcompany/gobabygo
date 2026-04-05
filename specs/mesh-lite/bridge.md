# Future Attached Bridge Design Slice

## Objective
Define the "Attached Semantic Runner" (PAL) that comes after the current manual relay path (`mesh lite relay-last`), without impacting the v1 `mesh-lite` runtime. 
This future path leverages the actual contracts defined in the `pal-mcp-server` repository, ensuring that the current v1 registry and relay interfaces remain structurally compatible.

## Current v1 State vs PAL

In v1 (`mesh-lite`), we use an internal registry (`scripts/mesh_lite/registry.py`) and a narrow transcript extractor (`scripts/mesh_lite/jsonl.py`). 
The `mesh lite relay-last` command orchestrates the manual sending of parsed text directly to iTerm2 panes. 

PAL (`pal-mcp-server/clink`), on the other hand, handles execution as detached subprocesses, parses their stdout/stderr via formal `BaseParser` implementations, and wraps CLI arguments in formal `CLIRoleConfig` and `CLIClientConfig` schemas.

## PAL Contracts & Compatibility

The future attached bridge must align with the following PAL real code contracts:

### 1. Client/Role Models
**PAL Contract (`clink/models.py`)**:
- `ResolvedCLIClient`: Defines `executable`, `working_dir`, `timeout_seconds`, `parser`, `runner`, `env`, and nested `roles`.
- `ResolvedCLIRole`: Defines `prompt_path` and `role_args`.

**v1 Compatibility**:
Our current registry entries optionally capture `backend_id`, `provider`, `launch_mode`, and `upstream_session_id`. These fields map cleanly to PAL's `ResolvedCLIClient` definitions. The `provider` acts as a selector for PAL's runner/parser factories. Our role definitions already isolate project-specific arguments in `mapping/operator_ui.yaml`.

### 2. Runner Contracts
**PAL Contract (`clink/agents/base.py`)**:
- `BaseCLIAgent.run()`: Takes `role`, `prompt`, `system_prompt`, `files`, and `images`, executes the CLI via subprocess, handles timeouts, and runs the output through a parser. It returns an `AgentOutput`.

**v1 Compatibility**:
While `mesh-lite` interacts with live panes, the attached semantic bridge will implement a custom runner class that implements `BaseCLIAgent` but instead of `asyncio.create_subprocess_exec`, it uses `mesh-lite`'s live-pane transport (`iterm.send_line()`) and transcript polling (`jsonl.wait_for_new_assistant_msg()`). By ensuring our manual relay path cleanly abstracts transcript parsing and terminal injection, wrapping them in a future `LivePaneAgent(BaseCLIAgent)` will not require refactoring `iterm.py` or `jsonl.py`.

### 3. Parser Contracts
**PAL Contract (`clink/parsers/base.py`)**:
- `BaseParser.parse(stdout, stderr) -> ParsedCLIResponse`

**v1 Compatibility**:
Our current `extract_last_assistant_msg` effectively functions as a naive `jsonl` parser. In the future, this extraction logic can be wrapped into a `LiveTranscriptParser(BaseParser)` that converts the live file tailing mechanism into a `ParsedCLIResponse` (where `content` is the assistant text, and `metadata` contains `session_id` and timestamps).

## Implementation Boundary
No structural changes to v1 are required now. The registry schema, discovery process, and decoupled transport layer already preserve maximum compatibility. When transitioning to the attached bridge, we will:
1. Provide a `LivePaneAgent` that overrides `run()` to send prompts to an existing pane instead of spawning a new process.
2. Replace static transcript polling in `cli.py` with `LiveTranscriptParser`.
3. Feed the resulting `AgentOutput` back to the operator or another role's pane programmatically.
