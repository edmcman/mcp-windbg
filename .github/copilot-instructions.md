# GitHub Copilot instructions for mcp-windbg ⚙️

This file contains concise, actionable guidance for AI coding agents working in this repository. Follow these rules to be immediately productive and avoid making incorrect or unrelated changes.

## Big picture (what this repo is)
- This is an MCP (Model Context Protocol) server that bridges AI models to WinDbg/CDB for crash dump analysis and remote debugging.
- Core pieces:
  - `src/mcp_windbg/server.py` — MCP server definition, tool and prompt registration, tool handlers.
  - `src/mcp_windbg/cdb_session.py` — Low-level wrapper around CDB (`cdb.exe`) that runs commands and detects completion using a marker (`.echo COMMAND_COMPLETED_MARKER`).
  - `src/mcp_windbg/prompts/` — Prompt templates (e.g. `dump-triage.prompt.md`) that define workflows and required output format.
  - `src/mcp_windbg/tests/` — Tests that assume Windows CDB availability and include sample dumps under `tests/dumps/` (managed with Git LFS).

## Important workflows & commands ✅
- Install dev deps: `uv sync --dev`
- Run tests: `uv run pytest src/mcp_windbg/tests/ -v` (tests skip if CDB or test dump is missing)
- Run server (stdio): `mcp-windbg` or `uv run python -m mcp_windbg --verbose`
- Run server (HTTP): `python -m mcp_windbg --transport streamable-http --host 127.0.0.1 --port 8000`
- Version bump & release: update `pyproject.toml`, both `server.json` version fields, and `CHANGELOG.md`; validate with `.	ables\scripts\check-version-consistency.ps1` and `uv run python scripts/validate-server-schema.py` before tagging.

> Note: CDB and WinDbg are Windows-only; tests and many features require a Windows environment or a provided `--cdb-path` that points to a real `cdb.exe`.

## Project-specific conventions and patterns ✨
- Tool inputs use Pydantic models (`OpenWindbgDump`, `RunWindbgCmdParams`, etc.). Tools expose schemas via `Tool.inputSchema = Model.model_json_schema()`; use this when adding tools.
- Error handling uses MCP `McpError` with an `ErrorData` object (see `server.py`) — return machine-readable errors instead of raising raw exceptions when possible.
- CDB sessions are managed centrally via `get_or_create_session()` and `unload_session()`; active sessions are stored in the `active_sessions` global dict keyed by absolute dump path or `remote:<connection>` string. Clean up sessions with `cleanup_sessions()` on exit.
- When interacting with CDB:
  - Use `CDBSession.send_command()` which relies on an echo marker to detect when a command finishes; handle `CDBError` (timeouts, unresponsive CDB).
  - The server often runs a sequence of commands (`.lastevent`, `!analyze -v`, `lm`, `~`) used by `execute_common_analysis_commands()`.
- Prompts drive workflow: the `dump-triage` prompt lists an exact sequence of commands (open, extract metadata with `run_windbg_cmd`, then `close_windbg_dump`) and specifies an exact markdown report format. Follow this format strictly when implementing prompt-driven behavior.

## Testing & CI notes ⚠️
- Tests assume a real CDB; they `pytest.skip()` when `cdb.exe` or the test dump are not available. Do not change tests to remove this guard unless you add a robust test double.
- Dumps are stored under `src/mcp_windbg/tests/dumps/` and may be large — they are managed with Git LFS. Do not add large binary test data into the repo without LFS.
- CI runs on multiple Python versions (3.10–3.14) and includes version consistency checks and schema validation before publish.

## Common changes and where to update docs 🔧
- When adding a new tool, add:
  - Pydantic model in `server.py` or a new module
  - Tool registration in `_create_server()`
  - Unit tests in `src/mcp_windbg/tests/`
  - Prompt or prompt template if user-facing workflow is affected (`src/mcp_windbg/prompts/`)
- When changing the public API surface (tool names, prompt names, arguments) update `README.md`, `AGENTS.md`, and `dump-triage.prompt.md` if relevant.

## Examples (copyable snippets) 📎
- Open a dump (server tool usage):
```json
Tool: "open_windbg_dump"
Params: {"dump_path": "C:\\dumps\\app.dmp", "include_stack_trace": true, "include_modules": true, "include_threads": true}
```
- Run an individual WinDbg command via the tool:
```json
Tool: "run_windbg_cmd"
Params: {"dump_path": "C:\\dumps\\app.dmp", "command": "vertarget"}
```

## Safety checks for PRs and patches 🔍
- Ensure version numbers in `pyproject.toml`, `server.json`, and `CHANGELOG.md` are synced.
- Add or update tests when changing behavior (especially for session lifecycle, timeouts, and tool output formatting).
- Preserve prompt templates and their output requirements — consumers and clients rely on the specified report format in `dump-triage.prompt.md`.

---
If anything in this file is unclear or you want more details about a particular area (prompts, CDB session lifecycle, tests, or release steps), tell me which section to expand and I'll iterate. 🙋‍♂️
