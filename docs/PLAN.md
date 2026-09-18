# Rebuild plan

Build order for [`DESIGN.md`](DESIGN.md). Bottom-up, TDD, component-level tests
only — no end-to-end run until the components stand up on their own.

Each step lands as its own commit with tests green.

---

## Step 1 — Domain
`src/crossbar/domain/`

Test, Task, Golden, Limits, Environment spec, roles. Loading and validation from
YAML. No behaviour beyond parsing.

- Loud validation: unknown keys, missing golden, bad limits, duplicate task ids.
- Golden is prose; there is no check schema to validate against.

**Done when:** a Test directory loads into typed objects, and every malformed
input names its file.

## Step 2 — Connector protocol + MCP connector
`src/crossbar/connectors/`

The protocol from DESIGN §2, and `McpConnector` wrapping the existing stdio
client.

- `tools()` / `call()` for the agent, `probes()` / `probe()` for capture.
- Read-only classification: MCP `readOnlyHint`, then explicit declaration, then
  **not a probe**.
- A registry so a second connector is a registration, not a rewrite.

**Done when:** a real fixture MCP server is driven through the Connector
interface, and a tool with no read-only evidence never appears in `probes()`.

## Step 3 — Environment
`src/crossbar/environment/`

`LocalEnvironment` (subprocesses, for authoring and tests) and
`DockerEnvironment` (container per Attempt).

- `start` / `reset` / `stop`, with `reset: recreate`.
- `EnvironmentHandle` giving Connectors what they need.
- Docker tested against a stub binary; no daemon in the suite.

**Done when:** an Attempt cannot observe state left by the previous one.

## Step 4 — Harness
`src/crossbar/harness/`

Connector-routed loop. Adapted from v0.1's react loop, with the environment
dependency replaced by connectors.

- Tools are the union across connectors; calls route to the owner.
- Limits: steps, wall clock, tokens.
- **The harness must not import anything MCP-specific.** A test asserts it.

**Done when:** a scripted model drives real MCP servers through connectors, and
every stop condition is covered.

## Step 5 — Evidence
`src/crossbar/evidence/`

Capture per the Check Plan, serialise, reload.

- Missing or non-read-only probes produce an `EvidenceItem` with an error, not
  an exception.
- Round-trips to disk and is judgeable with no live environment.

**Done when:** evidence captured, written, reloaded in a fresh process, and
still complete.

## Step 6 — Judging
`src/crossbar/judging/`

`Judge` with `make_plan` and `grade`, over a model client.

- Plan generated from Task + Golden + probe catalogue. Never from an Attempt.
- `unsatisfiable` items surfaced before a run.
- Outcomes: GRADED / UNCHECKED / FAILED, with per-check reasons and the judge's
  own reasoning stored.
- **Blinding enforced structurally**: the payload has no identity to leak, and a
  test proves no model id, provider or role reaches it.
- A `ScriptedJudge` for tests.

**Done when:** grading is deterministic under a scripted judge, and the blinding
test passes.

## Step 7 — Orchestrator
`src/crossbar/orchestrator/`

Serial execution, roles, ordering, opt-in judging, storage layout.

- Plans first, then Candidate, then Baseline, per Test.
- Judging opt-in per Test, default first only.
- Attempt failures contained, never raised.
- Storage exactly as DESIGN §8.

**Done when:** a full sweep runs with scripted model and judge, and ordering,
containment and layout are each asserted.

## Step 8 — Dump
`src/crossbar/dump/`

Zip the run directory. One artifact, no variants.

## Step 9 — Reconnect statistics and report

`stats/` is unchanged; `analysis.py` and `report/` are adapted to judged results
and the three outcomes. Unjudged Tests must be visibly unjudged.

---

## Not in this rebuild

Deferred items live in [`../CLAUDE.md`](../CLAUDE.md) with their triggers.
Nothing here should start on: machine-checkable plan items, plan editing, a
second connector, parallel execution, or judge self-agreement measurement.

## Carried over unchanged

`stats/`, `providers/`, `mcpclient/` (as connector internals), and the testing
conventions: no network, no API key, no Docker daemon, seeds via `crc32`.
