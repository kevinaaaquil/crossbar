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
| 2 | `connectors` — protocol, registry, `McpConnector` | **done** | 37 |
| 3 | `environment` — local + docker, reset between attempts | **done** | 24 |
| 4 | `harness` — connector-routed agent loop | next | — |
| 5 | `agents` — `ModelAgent`; `CliAgent` *(nice to have)* | not started | — |
| 6 | `evidence` — capture, serialise, reload | not started | — |
| 7 | `judging` — Check Plan, grading, blinding | not started | — |
| 8 | `orchestrator` — roles, ordering, judging, storage, queue model | not started | — |
| 9 | `dump` — zip the run | not started | — |
| 10 | statistics + report reconnect | not started | — |
| 11 | TUI — connect models, run, live queue view, results | not started | — |

**Total tests:** 206

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

### Step 2 — connectors — done
`Connector` protocol, registry, and `McpConnector`. 37 tests, driven against a
real `tests/fixtures/tickets_server.py` subprocess.

Read-only classification works as designed: the server's own `readOnlyHint`
annotation first, then an explicit `read_only_tools` declaration in the Test,
then **not a probe**. Nothing is inferred from a tool's name, and `probe()`
refuses a tool it cannot establish as read-only.

`EnvironmentHandle` landed here rather than in step 3, since connectors are what
consume it: workspace, command prefix, env vars, endpoints, and `${VAR}`
expansion with `CROSSBAR_PYTHON` and `CROSSBAR_WORKSPACE` always available.

### Step 3 — environment — done
`LocalEnvironment` and `DockerEnvironment` behind one protocol, plus the
factory. 24 tests; docker is driven against a stub binary that execs through, so
a real MCP server answers over `docker exec` without a daemon.

`reset()` recreates rather than cleans. The test that matters asserts an Attempt
cannot observe state left by the previous one — it writes a ticket priority,
resets, and checks the value is back to its seed.

Docker's workspace is a path **inside** the container, since that is where the
servers run and where evidence probes will read from.

---

## Discovered along the way

Notes that do not belong in the design doc but should not be lost.

- Connecting an agent CLI (Claude Code, Codex) is **nice to have, not required**
  for the MVP. The `Agent` abstraction still lands in step 5 because it is cheap
  and keeps the seam; `CliAgent` itself can be deferred.
- `mcpclient.ToolSpec` did not parse MCP tool `annotations`, so `readOnlyHint`
  never reached the connector. Added with a test. This is the first change to a
  carry-over module in the rebuild.
- The domain class `Test` collides with pytest's collection heuristics. Fixed
  with `__test__ = False` on the dataclass. Do not rename it; the terminology is
  fixed.
- `providers.MockProvider` was deleted with the old task format, since it
  replayed a `demo` script that no longer exists. `ScriptedProvider` remains and
  is what the rebuild's tests use. If an offline demo is wanted later, it needs
  a new mechanism.
