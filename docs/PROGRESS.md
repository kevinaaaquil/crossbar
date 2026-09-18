# Rebuild progress

**Read this first when picking up work.** It is the running state of the
rebuild: what is built, what is next, and anything discovered along the way.
Update it at the end of every step, in the same commit as the code.

Design: [`DESIGN.md`](DESIGN.md) · Plan: [`PLAN.md`](PLAN.md) ·
Decisions: [`../CLAUDE.md`](../CLAUDE.md)

Branch: `redesign`. `main` holds v0.1 and is not touched.

---

## Status

| # | Module | State | Tests |
|---|---|---|---|
| 0 | Carry-over: `stats`, `providers`, `mcpclient`, `trace` | **done** | 95 |
| 1 | `domain` — Test, Task, Golden, limits, env spec, roles | **done** | 48 |
| 2 | `connectors` — protocol, registry, `McpConnector` | next | — |
| 3 | `environment` — local + docker, reset between attempts | not started | — |
| 4 | `harness` — connector-routed agent loop | not started | — |
| 5 | `agents` — `ModelAgent`, `CliAgent` | not started | — |
| 6 | `evidence` — capture, serialise, reload | not started | — |
| 7 | `judging` — Check Plan, grading, blinding | not started | — |
| 8 | `orchestrator` — roles, ordering, judging, storage, queue model | not started | — |
| 9 | `dump` — zip the run | not started | — |
| 10 | statistics + report reconnect | not started | — |
| 11 | TUI — connect models, run, live queue view, results | not started | — |

**Total tests:** 143

---

## Conventions for this rebuild

- **TDD.** Test first, watch it fail, then implement.
- **Unit *and* integration tests per module.** Integration means real
  subprocesses, real files, real serialisation round-trips — not mocks talking
  to mocks. Stubbed only: model clients, the judge, and the `docker` binary.
- **No network, no API key, no Docker daemon** in the suite.
- Commit and push after every step. Small commits.
- Update this file in the same commit as the code it describes.

---

## Log

### Step 0 — ground cleared
Removed the packages being rewritten (task format, env, harness, scoring,
runner, TUI, CLI, config, analysis, report, demo) and their tests. Kept `stats`,
`providers`, `mcpclient`, `trace`, which the design reuses unchanged. 95 tests
green.

Also removed the community and CI scaffolding for now, at the user's request.

### Step 1 — domain — done
`Test`, `Task`, `Golden` (prose), `Limits`, `EnvironmentSpec`, `ConnectorConfig`,
`Role`, and the loader. 48 tests, including an integration suite over a real
fixture Test directory at `tests/fixtures/tests/support-triage/`, which later
steps reuse.

Two new requirements arrived mid-step and are recorded as decisions in
CLAUDE.md: agent CLIs connectable in any role (new step 5, `agents`), and a live
queue view in the TUI (changes the orchestrator's interface, step 8).

---

## Discovered along the way

Notes that do not belong in the design doc but should not be lost.

- The domain class `Test` collides with pytest's collection heuristics. Fixed
  with `__test__ = False` on the dataclass. Do not rename it; the terminology is
  fixed.
- `providers.MockProvider` was deleted with the old task format, since it
  replayed a `demo` script that no longer exists. `ScriptedProvider` remains and
  is what the rebuild's tests use. If an offline demo is wanted later, it needs
  a new mechanism.
