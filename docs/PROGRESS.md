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
| 4 | `harness` — connector-routed agent loop | **done** | 19 |
| 5 | `agents` + `roster` — `ModelAgent`, model roles | **done** | 38 |
| 6 | `evidence` — capture, serialise, reload | **done** | 23 |
| 7 | `judging` — Check Plan, grading, blinding | **done** | 35 |
| 8 | `orchestrator` — roles, ordering, judging, storage, queue model | **done** | 32 |
| 9 | `dump` — zip the run | **done** | 11 |
| 10 | `analysis` + `report` | **done** | 48 |
| 11 | TUI — connect models, run, live queue view, results | **done** | 48 |

**Total tests:** 511

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

### Step 4 — harness — done
The agent loop, routed through connectors. 19 tests.

Tools are the union across every enabled connector and each call routes back to
its owner; the first connector to claim a tool name keeps it, so a collision
cannot silently redirect a call. A connector raising becomes a tool error fed
back to the model, never a crash.

One test greps the module source for `mcp` and `docker` and fails if either
appears. A second exercises the loop with a non-MCP `EchoConnector` alongside
the MCP one. Together they are what stop the seam quietly closing.

### Step 5 — agents and roster — done
`Agent` protocol plus `ModelAgent`, and the roster that connects models and
assigns roles. 38 tests.

The roster enforces what was decided: at least a candidate and a baseline, any
number of extra models, roles as labels that change nothing about execution, and
the judge falling back to the baseline. It exposes `judge_is_baseline` so the
report can state the conflict of interest rather than hide it, and
`execution_roles` so ordering lives in one place.

`CliAgent` is deliberately not built — nice to have, and the seam is what
matters. `owns_harness` is on the protocol so the report can label a product
comparison as one when a CLI agent does arrive.

### Step 6 — evidence — done
Capture, serialisation and reload. 23 tests.

Nothing in capture raises. An unknown connector, an unknown probe, a tool that
cannot be established as read-only, or a probe that errors all become an
`EvidenceItem` carrying the reason — which is what later turns into an
`Unchecked` outcome the user can act on.

Blinding is structural: `Evidence` has no model, agent or role field, so the
payload cannot leak what it does not have. Two tests hold that line — one greps
the serialised form for identity words, the other asserts the dataclass has no
such fields.

One test writes evidence, tears the environment down, and reads the file back to
prove judging never needs a live container.

### Step 7 — judging — done
`CheckPlan`, `Judgement`, the `Judge`, and a `ScriptedJudge`. 35 tests.

Planning sees only Task + Golden + the probe catalogue. A plan item referencing
a probe that does not exist is moved to `unsatisfiable` rather than kept, so the
user is told before a run instead of collecting Unchecked results after one.

Grading decides missing-evidence checks **without a judge call** — there is
nothing for a model to read — and a check the judge simply fails to answer is
Unchecked, never a silent pass. Any unchecked check leaves the whole Attempt
unscored, because a Golden is one statement and verifying half of it does not
establish the Task was done.

Three blinding tests hold the line: no identity words in the grading prompt, no
hint the judge might be grading its own work, and no pairwise phrasing (grading
is one Attempt against the Golden, which is less biased than "which is better").

### Step 8 — orchestrator — done
Plan, execute, capture, judge, store. 32 tests.

Order is Test-major then role: every Check Plan for a Test is derived first,
then the Candidate's Attempts, then the Baseline's. A test asserts a Test
finishes both roles before the next Test starts, which is what makes an aborted
run still yield a usable comparison.

Judging is opt-in per Test and defaults to the first Test only. Evidence is
captured even when judging is off, so an unjudged Test can be judged later
without re-running it — `Orchestrator.judge_stored` does exactly that, and a
test asserts it reuses the stored plan rather than regenerating it.

The queue is built before anything runs and carries pending/running/done/failed
per unit. One test watches events mid-run and asserts exactly one item is
`running` at a time, which is the serial guarantee the TUI will render.

Containment is covered three ways: a provider failure, an agent that raises, and
an environment that will not start. None of them stop the run.

### Step 9 — dump — done
One zip of the whole run directory. 11 tests, including one that dumps a real
finished run. A previous `dump.zip` is never packed into the new one.

### Step 10 — analysis and report — done
48 tests. `stats/` was reused untouched.

The important decision here: **Unchecked is not a failure.** An unchecked
Attempt means we could not verify the result, which is a different statement
from the model getting it wrong, so it is excluded from the score and reported
separately. A **Failed** Attempt *is* counted as a failure — the agent erroring
is the agent's problem. Both are tested directly.

Pairing only uses Tasks both models had graded, so an Unchecked result on one
side is never silently compared against a real score on the other.

Caveats are generated, not optional: the baseline judging itself, a model with
more than 20% unchecked attempts, and any test that ran but was not judged.

### CLI — done
Seven verbs: validate, run, judge, report, dump, doctor, tui. 21 tests.

Writing it found a real gap. A run with judging switched off produced no Check
Plan — and the plan is what says which evidence to capture — so such a run could
never be judged later without being re-run, defeating the point of deferring.
Plans are now derived whenever a judge is available, independently of whether
grading happens. Planning is one call per Task; grading is one per Attempt, and
that is where the cost lives.

### Demo — done
`examples/support-triage`: four Tasks with prose Goldens, over the ticketing MCP
server now shipped in `crossbar.demo`. Plus `roster.yaml` showing how models are
connected. The directory is `examples/` rather than `tests/` as the plan
originally said, because `tests/` is pytest's.

### End-to-end — done
`tests/test_end_to_end.py`: roster and Test loaded from disk, through planning,
execution, capture, judging, analysis, report and dump. 16 tests. Only the model
and the judge are scripted — the MCP servers, environment, connectors and every
file written are real.

The judge used here reads the captured dump and decides from actual state, so
the candidate that did the work passes and the baseline that only described it
fails. That is the whole product working, minus a live model.

### Step 11 — TUI — done
Four tabs — Models, Tests, Run, Results — with `r 1 2 3 4 d q`. 48 tests.

The run view renders `Orchestrator.queue` directly rather than reconstructing
state from events; events only say when to repaint. The sweep runs on a Textual
worker thread, and a test proves the UI stays responsive by blocking the agent
factory on an event and asserting the running model, Test and Task are already
painted before it is released.

Added after the first pass: the judge-count control on the Tests tab. CLAUDE.md
says the user must be *asked* how many Tests to judge when more than one is
scheduled, and the app was defaulting silently. An unreadable value now means
**none judged**, not all — judging everything because a field held a typo would
spend the user's money without being asked.

### Demo command — done
`crossbar demo` runs the shipped example with two scripted stand-in models and a
judge that decides from real captured state. Nothing is connected, so the whole
pipeline can be watched before a model or key exists.

It found a real bug: the report was taking the judge-is-baseline conflict from
the roster's fallback rather than from the model that actually judged, so the
demo claimed the baseline had graded its own work when a separate judge had.

---

## Discovered along the way

Notes that do not belong in the design doc but should not be lost.

- **The Textual markup trap is sharper than it looks.** `Static.content` returns
  the raw string you passed in, not parsed markup, so a test asserting on
  `.content` passes whether markup is on or off — only rendering raises. Nor are
  all brackets dangerous: `[  0.0 - 0.0]` from the report renders fine, while a
  bare `[/]` raises. "The report renders" is therefore not evidence that markup
  is off. Two tests hold the line: one checks the flag on every free-text panel,
  one drives a bracketed judgement through a real sweep.
- **A double-run guard belongs on the message loop**, in the action, not in the
  worker — set inside the worker it is set too late to refuse the second
  keypress.
- `Input.Changed` fires during compose, before the screen is on the stack, so
  any handler that queries widgets must tolerate not finding them yet.
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
