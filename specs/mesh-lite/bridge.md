# Future Attached Bridge Design Slice

## Objective
Define the "Attached Semantic Runner" (PAL) that comes after the current manual relay path (`mesh lite relay-last`), without impacting the v1 `mesh-lite` runtime. 
This future path should leverage the actual contracts defined in the `pal-mcp-server` repository without overstating what the current v1 runtime already provides.

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

**v1 Boundary**:
The current v1 registry is only a live-pane binding store. It is useful as runtime metadata, but it is not a PAL-compatible client/role config source by itself. A future attached bridge will still need an explicit PAL-facing schema/config layer for fields such as:

- `executable`
- `timeout_seconds`
- `parser`
- `runner`
- `prompt_path`
- `role_args`

The current v1 fields like `provider`, `launch_mode`, and `upstream_session_id` should be treated only as helpful inputs into that future config layer, not as a drop-in replacement for PAL's resolved models.

### 2. Runner Contracts
**PAL Contract (`clink/agents/base.py`)**:
- `BaseCLIAgent.run()`: Takes `role`, `prompt`, `system_prompt`, `files`, and `images`, executes the CLI via subprocess, handles timeouts, and runs the output through a parser. It returns an `AgentOutput`.

**v1 Compatibility Direction**:
While `mesh-lite` interacts with live panes, the attached semantic bridge should implement a custom runner class that follows the `BaseCLIAgent` contract but uses `mesh-lite`'s live-pane transport (`iterm.send_line()`) and transcript polling (`jsonl.wait_for_new_assistant_msg()`) instead of `asyncio.create_subprocess_exec`.

That means the runner, not the parser, is responsible for:

- sending text to the live pane
- tailing/polling the transcript source
- collecting the text that will later be parsed

The current v1 split between `iterm.py` and `jsonl.py` is useful because it keeps transport and transcript collection isolated enough to be reused from a future `LivePaneAgent`.

### 3. Parser Contracts
**PAL Contract (`clink/parsers/base.py`)**:
- `BaseParser.parse(stdout, stderr) -> ParsedCLIResponse`

**v1 Compatibility Direction**:
Our current `extract_last_assistant_msg` is better understood as transcript text extraction logic, not as a PAL parser by itself. In a future attached bridge:

- transcript tailing / timestamp polling must live in the runner (`LivePaneAgent.run()`)
- the parser layer should only interpret already-captured text and convert it into `ParsedCLIResponse`

So a future `LiveTranscriptParser(BaseParser)` may still exist, but only for interpreting captured transcript text. It should not own file-watching or transcript polling.

## Implementation Boundary
No structural changes to v1 are required now. The registry schema, discovery process, and decoupled transport layer already preserve maximum compatibility. When transitioning to the attached bridge, we will:
1. Provide a `LivePaneAgent` that overrides `run()` to send prompts to an existing pane instead of spawning a new process.
2. Move transcript polling/tailing into that runner rather than into the parser layer.
3. Use a parser only to interpret the text captured by the runner.
4. Feed the resulting `AgentOutput` back to the operator or another role's pane programmatically.
