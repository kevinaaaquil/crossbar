# crossbar — working notes

Sweep (model x harness) over MCP task packs, score deterministically, report
with confidence intervals. See `docs/ARCHITECTURE.md` for the layer map and the
reasoning behind each decision.

## Commands

```bash
.venv/bin/pytest                    # full suite: no network, no key, no docker
.venv/bin/crossbar run              # demo sweep with the built-in mock models
.venv/bin/crossbar tui              # the terminal app
.venv/bin/crossbar validate         # check roster + task pack
```

## Conventions

- **TDD throughout.** Every module was written test-first; keep it that way.
- **No network in tests.** Models are `ScriptedProvider`/`MockProvider`, the
  `claude` CLI and `docker` are fake binaries in `tests/fixtures`. MCP servers
  are real subprocesses — do not mock those, they are the integration surface.
- **Determinism is a feature.** Seeds use `crc32`, not `hash()` (Python salts
  string hashing per process). Anything that cannot replay in a fresh
  interpreter is a bug.
- Failures inside a rollout become zero-scoring records with the error attached;
  they never raise out of the runner.
- `crossbar.report` is shared by the CLI and TUI so the two cannot disagree.

## Where things live

| Want to change... | Go to |
|---|---|
| Task YAML schema | `src/crossbar/tasks/` |
| A new model backend | `src/crossbar/providers/` + `PROVIDER_KINDS` in `config.py` |
| A new harness | `src/crossbar/harness/` + `HARNESS_KINDS` in `config.py` |
| A new check type | `src/crossbar/scoring/score.py` + `CHECK_TYPES` |
| Isolation (k8s, Modal) | `src/crossbar/env/` — subclass `Environment` |
| The verdict logic | `src/crossbar/analysis.py` (`_decide`) |
