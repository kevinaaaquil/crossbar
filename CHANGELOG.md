# Changelog

Notable changes to crossbar. Dates are ISO-8601.

The format is loosely [Keep a Changelog](https://keepachangelog.com/). This
project is pre-1.0: anything may change, and the version number does not yet
carry a compatibility promise.

## [Unreleased]

Nothing yet.

## [0.1.0] — 2026-09-18

First working version. The v0 milestone from the design document: two or more
models, two or more harnesses, a handful of hand-written tasks, fixed repeats,
bootstrap intervals in a printed table, and a reproducible crossover.

### Added

- **Task pack format.** YAML tasks with MCP environments, deterministic checks
  (`mcp_state`, `tool_called`, `final_text`, `no_tool_errors`), security
  constraints, and an optional `demo` script for offline runs.
- **MCP client.** JSON-RPC 2.0 over stdio: initialize, `tools/list`,
  `tools/call`, with timeouts and captured stderr.
- **Environments.** `local` (subprocesses) and `docker` (one throwaway
  container per rollout, network off). Per-rollout workspace directory,
  `${VAR}` expansion with built-in `CROSSBAR_PYTHON` and `CROSSBAR_WORKSPACE`.
- **Providers.** Any OpenAI-compatible endpoint, the Anthropic Messages API,
  plus `scripted` and `mock` backends for tests and offline demos.
- **Harnesses.** `react`, `react-plus-verify`, `single-shot`, and `claude-code`
  driving the real CLI with only the task's MCP servers attached.
- **Scoring.** `Security × Completion × Process`, all deterministic, with the
  Harness-Bench failure taxonomy assigned per rollout.
- **Statistics.** Bootstrap confidence intervals, paired bootstrap against the
  baseline, McNemar's exact test, Holm–Bonferroni correction, and a
  between-task versus within-task variance decomposition.
- **Runner.** Matrix execution with configurable concurrency, repeats, and a
  sequential budget allocator that buys extra rollouts only for contested cells.
- **Analysis and report.** Verdict card naming the cheapest cell that cannot be
  shown to be worse than the baseline, a matrix marking statistically tied
  cells, a failure taxonomy, noise warnings, and a list of tasks that separate
  nothing.
- **Terminal app.** Textual UI with a live matrix, per-rollout log, and a trace
  viewer showing checks and tool calls for any rollout.
- **CLI.** `run`, `tui`, `validate`, `doctor`, `init`.
- **Demo.** A support-desk MCP server and a four-task pack, plus two mock
  backends, so the whole bench runs with no API key, no network and no Docker.
- **Documentation.** A complete non-technical walkthrough, a task-authoring
  reference, an architecture note, and an honest limitations list.
- 435 tests, all offline.

### Known limitations

See [docs/LIMITATIONS.md](docs/LIMITATIONS.md). The short list: MCP tasks only,
no LLM judge, stdio transport only, the docker path has never met a live daemon,
the Claude Code harness has never met the live CLI, and there is no resume.
