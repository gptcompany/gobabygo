<p align="center">
  <img src="logo.png" alt="AI Mesh Router Logo" width="800">
</p>

# AI Mesh Router

![CI](https://github.com/gptcompany/gobabygo/actions/workflows/ci.yml/badge.svg)
![Sandbox Validation](https://github.com/gptcompany/gobabygo/actions/workflows/sandbox-validate.yml/badge.svg)
![Coverage](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/gptcompany/gobabygo/master/.github/badges/coverage.json)
![Python](https://img.shields.io/badge/python-3.11%2B-blue?style=flat-square&logo=python)
![Last Commit](https://img.shields.io/github/last-commit/gptcompany/gobabygo?style=flat-square)
![Issues](https://img.shields.io/github/issues/gptcompany/gobabygo?style=flat-square)
![Lines of Code](https://sloc.xyz/github/gptcompany/gobabygo)

**Distributed multi-agent task orchestration router with SQLite persistence**

This repository contains the session-first mesh router, worker coordination logic, deployment assets, and test suite for the AI mesh network runtime.

## What is here

- `src/router/`: router runtime, scheduling, worker lifecycle, persistence, metrics, and bridge adapters
- `src/meshctl.py`: lightweight HTTP client CLI for inspecting and operating the mesh
- `deploy/`: systemd units, environment templates, deployment scripts, and monitoring configs
- `tests/`: unit, integration, and in-process smoke coverage

## Quick Start

```bash
python -m pip install '.[dev]'
pytest -q
```

## Notes

- Canonical architecture notes live in [kiss_mesh/README.md](kiss_mesh/README.md).
- Quick operator guidance is in [QUICKSTART.md](QUICKSTART.md).
