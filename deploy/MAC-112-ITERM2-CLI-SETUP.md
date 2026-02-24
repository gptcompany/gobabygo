# MacBook `.112` iTerm2 + CLI Setup (Session-First Control)

Scope: operator machine (macOS `.112`) used for iTerm2 control/visibility and manual intervention in interactive CLI sessions.

Addressing policy:

- Primary addressing: VPN (`10.0.0.x`)
- Fallback only if VPN path is unavailable: LAN (`192.168.1.x`)

## Design Rules

- `iTerm2` is operator UX only (not orchestration source of truth).
- Human approval gates are handled by each CLI's own configuration/mode.
- Router/DB remains the authoritative state and message bus.

## Claude Agent Teams Flag (Global User Config)

Claude Agent Teams is a Claude Code feature flag and must be enabled in the user config of the machine that runs interactive `claude`.

File:

```json
~/.claude/settings.json
```

Required setting:

```json
{
  "env": {
    "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"
  }
}
```

Notes:

- Merge with existing settings (hooks, etc.); do not overwrite existing keys.
- Optional shell export for interactive shells:

```bash
export CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1
```

## CLI Verification (macOS `.112`)

Use a login shell so PATH includes `nvm`, npm-global bin, etc.:

```bash
ssh sam@10.0.0.112 'zsh -lic "command -v claude codex gemini"'
ssh sam@10.0.0.112 'zsh -lic "claude --version; codex --version; gemini version"'
```

If `gemini --version` is noisy or interactive due local config, use:

```bash
ssh sam@10.0.0.112 'zsh -lic "npm -g ls --depth=0 | egrep \"(claude|codex|gemini|anthropic|openai)\""' 
```

## Install / Update (npm-global path)

Many CLI installs on `.112` are Node-based and live under user npm globals.

Check prefix:

```bash
ssh sam@10.0.0.112 'zsh -lic "npm config get prefix"'
```

Install/update targeted CLIs:

```bash
ssh sam@10.0.0.112 'zsh -lic "npm install -g @anthropic-ai/claude-code @openai/codex @google/gemini-cli"'
```

Verify after install/update:

```bash
ssh sam@10.0.0.112 'zsh -lic "claude --version; codex --version; gemini version || gemini --version || true"'
```

## iTerm2 Operator Layout (Recommended)

- Pane 1 (VPS): router logs (`journalctl -u mesh-router -f`)
- Pane 2 (VPS): queue / worker status (`meshctl status` or API polling)
- Pane 3 (WS `10.0.0.111`): session worker logs / tmux attach (Claude)
- Pane 4 (WS `10.0.0.111`): session worker logs / tmux attach (Codex)
- Pane 5 (WS `10.0.0.111`): optional Gemini reviewer batch logs

Quick helper:

```bash
./deploy/check-mac-112-cli.sh
```

It tries `10.0.0.112` first, then `192.168.1.112`.

## Session-First Reminder

Current `src/router/worker_client.py` is batch (`--print -p`) and does not satisfy interactive session requirements.

Target direction:

- persistent `session-worker` processes (PTY/tmux)
- router-persisted session/message state
- human enters the loop via iTerm2 attach when CLI asks for approval
