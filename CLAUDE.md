# crossbar — working notes

> **STATUS (2026-09-18).** The code in this repository (v0.1, 435 tests green)
> was built to an **earlier design** in which the harness was a swept axis and
> scoring was fully deterministic. That design has been **superseded** by the
> product spec recorded below. Read [Terminology](#terminology) and
> [Decisions](#decisions-log) before changing anything. The
> [Code map](#code-map-what-survives-the-rebuild) says which parts survive.

---

## What the product is

**Plug in your models, plug in your environment, find out whether your own model
does your work as well as a frontier model does — with a number you can defend.**

The user brings: models (any mix of local, fine-tuned, frontier), a list of
tasks, and golden data for those tasks. We provide: the isolated environment,
the connectors the agent uses to touch that environment, the harness that drives
the model, the orchestration, the judging, and the statistics.

The comparison axis is **models**. The harness is ours and is held constant.

---

## Terminology

**These are fixed. Use them in code, docs, UI and conversation.** Where the code
currently uses a different word, the rename is listed in
[Planned renames](#planned-renames).

| Term | Definition |
|---|---|
| **Test** | A list of Tasks to be run by models against an Environment. The number of times a Test is run, to average the result, is the user's choice. |
| **Task** | One isolated unit of work, run by an agent to perform an action. Its result is stored and later judged against a known correct result. |
| **Environment** | The platform the agent interacts with, through our harness, to perform a Task. Example: an MCP backend that performs DB actions. Pluggable — a Docker image with UI + backend, backend only, or anything else. |

### Supporting terms (proposed — confirm before they harden)

| Term | Definition |
|---|---|
| **Attempt** | One execution of one Task by one model in a fresh Environment. Repeating a Test produces multiple Attempts per Task. |
| **Connector** | A toggleable capability granted to the agent for touching the Environment (MCP, browser, HTTP, shell, files). MVP ships MCP only. |
| **Harness** | Ours. The runtime that drives a model through a Task using the enabled Connectors. Fixed, not a variable, and invisible to the user. |
| **Candidate** | The model being evaluated — typically the user's local or fine-tuned model. |
| **Baseline** | The model it is compared against — typically a frontier model. |
| **Judge** | A model the user connects, which our platform uses to analyse results against golden data. |
| **Golden** | The known correct result for a Task, supplied by the user. |

### Planned renames

| Currently in code | Becomes |
|---|---|
| `TaskPack` | `Test` |
| `taskpacks/` | `tests/` |
| rollout | Attempt |
| `ReactHarness` / `react` | the Harness (single, ours); `react` naming leaks implementation |
| cell / agent (model+harness pair) | model, since the harness no longer varies |

---

## Architecture

```
1. MODELS          one registry, three roles: candidate · baseline · judge
                   any OpenAI-compatible endpoint, Anthropic, local

2. ENVIRONMENT     pluggable, per Task
                   docker image | compose stack | remote endpoint
                   responsibilities: bring up, expose, snapshot, reset, destroy

3. CONNECTORS      toggleable capabilities granted to the agent
                   MVP: [x] mcp
                   later: browser, http, shell, files
                   each contributes tools; the agent routes calls through them

4. HARNESS         ours, fixed, invisible
                   model + enabled connectors, in a loop

5. ARTIFACTS       what an Attempt produced
                   final answer · files written · environment state snapshot

6. JUDGING         judge model analyses artifacts against the Golden
                   plus an optional human review pass

7. ORCHESTRATION   one container, one Task at a time (for now)
                   repeats, isolation, failure containment

8. STATISTICS      intervals, paired comparison, verdict
```

Layers 1, 7 and 8 exist and are solid. Layer 2 must become real (container-based).
Layers 3, 5 and 6 are new.

---

## Decisions log

Decisions taken in conversation, with the reasoning, so they are not re-litigated.

### 2026-09-18 — Models are the comparison axis, not model × harness

The v0.1 code swept `model × harness` and the demo's headline finding was *"the
harness was worth 35 points."* That followed the landscape document's thesis
rather than the product brief, and it teaches the wrong story.

**Decided:** the harness is ours, fixed, and invisible. One axis: models. The
user's question is *"can my model replace the frontier model on my work?"*, and
answering it requires holding everything else constant.

**Consequence:** harness variants (`verify`, etc.) are demoted to an optional
tuning diagnostic at most, not a sweep axis. Third-party harness adapters
(Claude Code CLI) are not part of the core product story.

### 2026-09-18 — Connectors are a toggleable layer; MVP is MCP only

The agent's capability set is the set of enabled Connectors, chosen by the user.

**Decided:** MVP ships **MCP only**. But the Connector boundary must exist from
day one, so adding browser/HTTP/shell later does not force a rewrite.

**Required shape:** a Connector protocol along the lines of
`setup(environment)` → `tools()` → `call(name, args)` → `teardown()`. The
harness asks every enabled Connector for its tools, presents the union to the
model, and routes each call back to the owning Connector. The harness must not
know that MCP exists.

**Open:** whether the Connector set is fixed per Task (controlled comparison) or
user-varied per run. Leaning fixed-per-Task with an explicit override.

### 2026-09-18 — The Environment is a container, not a process list

v0.1 modelled an environment as "run these MCP server commands." Real systems
are containers: a UI plus a backend, or a backend alone.

**Decided:** the Environment is pluggable and container-shaped. It owns bring-up,
exposure, **reset between Attempts**, and teardown.

**Watch:** state leaking between Attempts is the failure mode that produces
quietly wrong numbers with no error. Container-per-Attempt is the safe default;
snapshot-restore is the faster option if startup time becomes the bottleneck.

### 2026-09-18 — One container, one Task at a time

**Decided:** no parallelism in the MVP. Concurrency is designed for but not
enabled: keep the seam in the orchestrator, default it to serial.

### 2026-09-18 — Judging is a model, with an optional human check

**Decided:** a user-connected Judge model analyses each Attempt's artifacts
against the Golden. The user may additionally perform their own check and
override the judge's verdict.

**Implication for the UI:** judge output must be stored in a reviewable form,
and there must be a path for a human to mark a result pass/fail themselves. The
judge's own reasoning has to be visible, not just its score.

**Standing risk (raised, accepted, mitigate rather than avoid):** the product
sells statistical honesty, and a judge is a stochastic grader. If the judge is
noisy, the confidence intervals measure the judge's variance rather than the
model's. Mitigations worth building: run the judge at temperature 0, and measure
**judge self-agreement** on a sample (grade the same artifact twice, report how
often it agrees). Reference-based judging against a Golden is substantially more
reliable than open-ended quality judging, so the risk is real but bounded.

### 2026-09-18 — Licence: PolyForm Noncommercial 1.0.0

Noncommercial use free, including charities/schools/public bodies. Commercial
use not permitted. Source-available, **not** OSI open source — the README says
so plainly rather than burying it.

---

## Open questions

Unresolved. Do not guess; ask.

1. **Artifact shape.** What exactly is "the final output" that the judge
   compares against the Golden — final text, produced files, an environment
   state snapshot, or all three as a bundle? This determines the whole scoring
   layer.
2. **Golden shape.** What form does the user's golden data arrive in, and does
   it differ per Task type?
3. **Deterministic pre-checks.** Whether machine-checkable assertions (state
   diffs, schema validation) run before the judge as a cheaper first tier, or
   whether judging is the only mechanism.
4. **Connector set: fixed per Task or user-varied?** (see decision above)
5. **Browser sequencing.** Backend-only containers first with browser second, or
   is a UI-driving Task the motivating case that must be in the MVP?

---

## Code map: what survives the rebuild

| Keep | Why |
|---|---|
| `stats/` | Bootstrap, paired tests, Holm, variance. Correct and self-contained. |
| `analysis.py` | Verdict logic. Needs the axis changed from cells to models. |
| `report/` | Verdict card, matrix, taxonomy. Wording changes only. |
| `trace/` | Attempt recording. Extend with artifacts. |
| `providers/` | Model clients. Gain the judge role. |
| `runner/` | Orchestration skeleton, failure containment, repeats. |
| `mcpclient/` | Becomes the innards of the MCP **Connector**. |

| Rewrite | Why |
|---|---|
| `tasks/` | New Test/Task format, goldens, connector selection. |
| `env/` | Container-based, with reset between Attempts. |
| `harness/` | Single harness, connector-driven, no third-party adapters. |
| `scoring/` | Judge-based with human review, replacing deterministic-only. |
| `tui/` | New flows: connect models, run a Test, review judgements. |

Roughly 40% survives, and it is the hard-to-get-right 40%.

---

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
- Failures inside an Attempt become zero-scoring records with the error
  attached; they never raise out of the orchestrator.
- `crossbar.report` is shared by the CLI and TUI so the two cannot disagree.
- Declare every test dependency in `pyproject.toml`. CI installs from a clean
  machine; a dependency that only exists in the author's venv fails every job.
