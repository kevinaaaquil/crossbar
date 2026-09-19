# crossbar — working notes

> **STATUS (2026-09-20).** On branch `redesign`, the MVP is **built** — 612
> tests, offline. This file is the spec it was built to. `main` still holds
> v0.1, an earlier design in which the harness was a swept axis and scoring was
> deterministic; that design is superseded and `main` will be replaced.
>
> Read [Terminology](#terminology) and the [Decisions log](#decisions-log)
> before changing anything. Running state is in
> [`docs/PROGRESS.md`](docs/PROGRESS.md).

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

**These are fixed. Use them in code, docs, UI and conversation.**

| Term | Definition |
|---|---|
| **Test** | A list of Tasks to be run by models against an Environment. The number of times a Test is run, to average the result, is the user's choice. |
| **Task** | One isolated unit of work, run by an agent to perform an action. Its result is stored and later judged against a known correct result. |
| **Environment** | The platform the agent interacts with, through our harness, to perform a Task. Example: an MCP backend that performs DB actions. Pluggable — a Docker image with UI + backend, backend only, or anything else. |

### Supporting terms

Settled and in the code.

| Term | Definition |
|---|---|
| **Attempt** | One execution of one Task by one model in a fresh Environment. Repeating a Test produces multiple Attempts per Task. |
| **Connector** | A toggleable capability granted to the agent for touching the Environment (MCP, browser, HTTP, shell, files). MVP ships MCP only. |
| **Harness** | Ours. The runtime that drives a model through a Task using the enabled Connectors. Fixed, not a variable, and invisible to the user. |
| **Candidate** | A user-assigned role label. Its Attempts run first for a given Test. Typically the user's local or fine-tuned model, but the label carries no other meaning. |
| **Baseline** | A user-assigned role label. Its Attempts run second. Typically a frontier model. Also the Judge fallback when no Judge is assigned. Optional — see [single-model runs](#2026-09-20--single-model-runs). |
| **Judge** | A model used to analyse results against the Golden. Falls back to the Baseline when one exists; required explicitly when one does not. Always blinded to whose attempts it is grading. |
| **Dump** | A full zipped export of everything a run produced. For the user's own inspection or review by their own AI. We never need it to score: the Task tells us what to check. |
| **Golden** | The known correct result for a Task, supplied by the user, in whatever form is natural. Never shaped to fit a check schema — the judge infers what to check from it. |
| **Evidence** | Whatever was captured from an Attempt for the judge to examine. No fixed schema; determined by the Check Plan. |
| **Check Plan** | What to inspect and what criteria to grade against, derived once per Task from Task + Golden before any Attempt runs, then applied to every Attempt of that Task. |
| **Unchecked** | A Task outcome meaning the required evidence was unavailable, so no score was produced. Carries a reason. |

### Renames, all done

`TaskPack` → `Test`. `rollout` → `Attempt`. `ReactHarness`/`react` → `Harness`,
single and ours. "cell" is gone: the harness no longer varies, so the unit is a
model. The shipped example Test lives in **`examples/`**, not `tests/` as
originally planned — `tests/` is pytest's.

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

5. EVIDENCE        whatever the Golden implies is needed, captured while the
                   Environment is alive: final answer · files · DB dumps ·
                   state snapshots. No fixed schema.

6. JUDGING         Check Plan derived once per Task from Task + Golden, before
                   any Attempt; drives both capture and grading. Judge is
                   blinded and falls back to the Baseline if none is given.
                   Outcomes: graded / unchecked (with a reason) / failed.
                   Plus a Dump for the user.

7. ORCHESTRATION   one container, one Task at a time (for now)
                   order: per Test, Candidate then Baseline
                   repeats, isolation, failure containment

8. STATISTICS      intervals, paired comparison, verdict
```

All eight layers are built. Module map:

| Layer | Package |
|---|---|
| 1 | `roster.py`, `providers/`, `agents/` |
| 2 | `environment/` |
| 3 | `connectors/` |
| 4 | `harness/` |
| 5 | `evidence/` |
| 6 | `judging/` |
| 7 | `orchestrator/`, `preflight.py` |
| 8 | `stats/`, `analysis.py`, `report/` |

Plus `domain/` (Test, Task, Golden), `dump/`, `tui/`, `cli.py`, `demo/`.

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

### 2026-09-18 — Judging: optional model, blinded, with an exportable dump

**Decided:**

- A **Judge** model is **optional**. If the user connects one, it analyses each
  Attempt's artifacts against the Golden.
- **If no Judge is connected, the Baseline model is used to judge.**
- **The Judge is never told whose attempts it is grading.** In particular, when
  the Baseline is acting as Judge it must not know it is grading its own
  attempts against a different model's.
- There is **no human pass/fail review UI**. Instead we produce a **dump**: a
  self-contained export of a Task, its Golden, an Attempt's artifacts, the trace
  and the judge's reasoning, which a user can hand to their own AI — or read
  themselves — for an independent opinion. That optionality is *why* our judge
  can be optional.

**Why blinding matters:** LLM judges show self-preference — they score their own
output higher. With the Baseline doubling as Judge, that conflict of interest is
structural, and blinding is the minimum mitigation.

**What blinding requires in practice** (all of it, or it leaks):

1. Strip model identity from artifacts and from any trace the judge sees.
2. Randomise attempt order; never present the candidate in a fixed position.
3. Never phrase the prompt as "compare model X with model Y", or hint that an
   attempt may be the judge's own.
4. Scrub or flag self-identifying text in the agent's own output ("As Claude,
   I..."), which leaks identity even with labels removed.
5. Keep identity-correlated metadata (token counts, timings, model-specific
   formatting quirks) out of the judged payload.

**Prefer reference-based grading over pairwise.** Grading each Attempt
independently against the Golden leaves less room for comparative bias than
asking "which of these two is better". Pairwise is more sensitive but more
biased; default to reference-based.

**Honest limits, to state in the docs rather than paper over:**

- Blinding is imperfect. Models often recognise their own style, and
  self-preference survives the removal of labels.
- When Judge == Baseline the conflict exists structurally. The report must say
  so plainly on the verdict, and recommend connecting an independent judge for
  any decision that matters.
- This is measurable, and we should measure it: with an independent judge
  available on even a sample, compare its scores with the baseline-as-judge
  scores and report the gap. Also worth reporting judge **self-agreement** —
  grade the same artifact twice at temperature 0 and see whether the verdict
  holds.

**Edge case:** originally resolved by requiring both a Candidate and a Baseline.
That minimum was later relaxed — see
[single-model runs](#2026-09-20--single-model-runs), where a Judge must instead
be named explicitly and may not be the model under test.

### 2026-09-19 — Model roles are labels; execution order and judging budget

**Minimum two model connections.** At least one Candidate and one Baseline. The
user may connect as many models as they like and assign roles in the UI.

> **Revised 2026-09-20.** A Baseline is no longer required — see
> [single-model runs](#2026-09-20--single-model-runs) below. A Candidate always is.

**Roles are naming conventions, not behaviour.** Candidate, Baseline and Judge
are user-assigned labels. They do not change how a Task is executed. They have
exactly two mechanical consequences:

1. **Order.** For a given Test, the Candidate's Attempts run first, then the
   Baseline's.
2. **Judge fallback.** If no Judge is assigned, the Baseline is used to judge
   (blinded — see the judging decision above).

**Execution order is Test-major, then role:**

```
Test 1 -> Candidate
Test 1 -> Baseline
Test 2 -> Candidate
Test 2 -> Baseline
...
```

Not "all Candidate Tests, then all Baseline Tests". This means complete paired
data exists for each Test before the next one starts, so an aborted or
interrupted run still yields a valid comparison for the Tests that finished,
rather than one side of everything.

**Judging is opt-in per Test, and defaults to the first Test only.** When more
than one Test is scheduled, warn the user and ask how many to judge. Default:
the first. **Judging is the expensive part of an eval bill**, and the default
must not quietly spend the user's money.

**Consequence to surface in the UI:** an unjudged Test produces no score, so no
statistics and no verdict for it. Whatever the report says about the run must be
explicit about which Tests were judged and which were only executed.

### 2026-09-19 — The Dump is a full zip, and is not part of scoring

**Decided:** the Dump is a complete zipped export of a run — Tasks, Goldens,
artifacts, traces, judge reasoning, everything. It exists for the user who wants
to dig, or wants to hand the whole thing to their own AI.

It is **not** an input to our scoring. We define the Task, so we know what to
check; the Dump is an output for the user, not a mechanism we depend on.

This closes the earlier open question about Dump format and granularity: one
full zip, no per-Attempt or failures-only variants to design.

### 2026-09-19 — The Golden is free-form; the judge infers what to check

**Decided.** The Golden is **never shaped to fit a check schema**. The user
writes what a correct result means, in whatever terms are natural. Because the
system knows the Task and knows the Golden, the judge can work out for itself
what evidence it needs.

Example: if the Golden says *"the DB should contain this data"*, the judge knows
it must inspect the database, and does so by dumping it — if a dump is
available.

**Three outcomes per Task, not two:**

| Outcome | When |
|---|---|
| **Graded** | The required evidence was available. A score is recorded. |
| **Unchecked** | The evidence was not available. **No score.** A reason is attached and surfaced to the user — e.g. *"the Golden requires a database dump, and no dump is available to us."* |
| **Failed** | The Attempt itself errored. |

`Unchecked` is a first-class result, not an error. It tells the user precisely
what to fix in their setup (expose a dump endpoint, enable a connector) instead
of silently scoring zero or inventing a pass.

**Consequences:**

- There is **no fixed artifact schema**. What gets captured is whatever the
  Golden implies is needed.
- The judge needs **read-only access to evidence**, not just a text blob. The
  Connector layer should serve this in a read-only mode, so we do not build a
  second access path.
- **Evidence must be captured while the Environment is alive.** Judging can be
  deferred or re-run later, by which time the container is gone — so anything
  the judge might need has to be captured at the end of the Attempt. This is
  what makes `Unchecked` happen in practice: the judge wanted something nobody
  captured.

### 2026-09-19 — Unjudged Tests can be re-judged without re-running

**Decided.** An unjudged Test is marked **unjudged**. The user can then either
force a re-run of **the judging step alone**, or a re-run of **the whole Test
flow**. Their choice.

**Requirement this creates:** an Attempt's captured evidence must be complete and
standalone enough to judge from, long after its Environment has been destroyed.
Judging cannot depend on a live container.

### 2026-09-19 — Rebuild work happens on a branch, never on main

`main` holds v0.1. The rebuild happens on a separate branch and `main` will be
force-pushed over when it is ready. Never commit rebuild work directly to
`main`.

### 2026-09-19 — The Check Plan: derived once per Task, applied to every Attempt

**Decided.** The judge does not decide what to inspect afresh on every Attempt.
It derives a **Check Plan** once per Task, from the Task and its Golden, and
that plan governs every Attempt of that Task.

**The plan is generated before any Attempt runs.** This is forced by the fact
that it drives evidence capture, and it has a second benefit worth protecting:
**the criteria are fixed before any model output has been seen.** The plan is
derived from Task + Golden only — never from an Attempt — so it cannot be
tailored, consciously or otherwise, to whichever output is in front of the
judge. Treat that as an integrity property and do not break it for convenience.

**What the plan governs:**

1. **Capture** — what evidence the orchestrator must collect from each Attempt
   while the Environment is alive.
2. **Grading** — the criteria each Attempt is scored against.

**Rules:**

- Made by the **Judge**; falls back to the **Baseline** when no Judge is
  assigned, same as grading.
- **Stored with the Task** and shown to the user.
- **Reused on re-judge by default.** Regenerate only if the user explicitly asks
  — otherwise a re-judge silently changes the criteria and old and new scores
  stop being comparable.
- Applied **identically to every Attempt** of that Task.

**Why it matters:** without it, the judge might inspect the database on one
Attempt and only the final text on another. The grades would then reflect which
evidence happened to be examined rather than how the model performed, and the
statistics would be comparing unlike things while looking perfectly healthy.

**Bonus:** because the plan states what evidence is required, we can warn the
user *before* spending anything on a run — "this Golden needs a database dump
and nothing exposes one" — instead of discovering it as `Unchecked` afterwards.

**Still open on this:**

- Can the user **edit** the plan when the judge gets it wrong? Showing it to
  them implies they can; that is a UI decision not yet taken.
- Can individual plan items be marked **machine-checkable**, settled
  deterministically with no judge call? That is the deterministic pre-check
  question, and the plan is the natural place to express it.

### 2026-09-19 — Agent CLIs are connectable in any role

**Decided.** The user may connect an agent CLI — Claude Code, Codex, or similar
— and assign it as Candidate, Baseline or Judge, exactly like a model endpoint.

**Priority: nice to have, not required for the MVP.** The `Agent` abstraction
below is worth landing regardless, because it is cheap and keeps the seam open.
`CliAgent` itself can wait until the rest of the product works.

**Design consequence: an `Agent` abstraction sits above the Harness.** An Agent
is anything that can execute a Task in an Environment and return a Trajectory
plus a final answer. Two kinds:

| Kind | What it is |
|---|---|
| `ModelAgent` | Our Harness driving a model endpoint. The normal case. |
| `CliAgent` | An external agent CLI in headless mode, handed the same connectors. |

Because both produce the same output shape, evidence capture, judging and
statistics are unchanged downstream. The Harness becomes an implementation
detail of `ModelAgent` rather than the only path.

**Caveat that must stay visible, not be buried:** a `CliAgent` brings its own
harness. Comparing a model in our Harness against Claude Code is a **product
comparison**, not a controlled model comparison — the CLI has years of
engineering around it. This is a legitimate thing to want to measure, but the
report and the UI must label it, or the number quietly means something other
than what the user thinks.

For the MVP's MCP-only connector, a CLI agent is wired up by generating an MCP
config for it, the way v0.1's Claude Code harness did. That code is on `main`
and is a usable reference.

### 2026-09-19 — The TUI shows live execution state

**Decided.** The TUI must show, while a run is in progress: which models are
running, which Test and Task each is on, and what is queued behind them.

**Design consequence:** the orchestrator cannot only emit progress events. It
must expose a **queue model** — every planned unit of work with a state of
`pending` / `running` / `done` / `failed` — that the TUI can render at any
moment. Build this into the orchestrator from the start; retrofitting a queue
view onto an event stream means reconstructing state the orchestrator already
had.

### 2026-09-20 — Mode is chosen at run time, and either label may hold the solo model

**Decided.** Single mode means **one executing model**. Which role label it
carries — Candidate or Baseline — is the user's choice and the machinery does
not care. `execution_roles` yields whichever of the two the roster assigns.

**The mode is selectable at run time**, not baked into the roster. The
orchestrator takes a `roles` override and the TUI toggles it with `m`, so a
two-model roster can be run either way without editing a file. Narrowing to a
role the roster does not assign is refused at construction.

**The toggle is refused mid-run.** The queue is already built and half executed;
changing what it means partway would make the results incomparable.

**A consequence worth knowing:** in single mode with a two-model roster, the
baseline judges but never executes — so it is *not* grading its own work, and
the conflict-of-interest warning must not fire. The warning is driven by whether
the judging model is among the models actually running, not by the roster's
role assignments. Same class of mistake as the earlier `judge_is_baseline` bug:
a conflict claimed where none exists misleads exactly as much as one hidden.

### 2026-09-20 — Single-model runs

**Decided.** A run may have a Candidate and no Baseline: one model assessed on
its own. It answers *"is this good enough at all?"*, which is a different and
usually earlier question than *"is it as good as what we pay for?"*

This revises the earlier minimum of two connections. A **Candidate is still
required**; a Baseline is not.

**Two rules make it safe:**

1. **A Judge must be assigned explicitly.** With no Baseline there is nothing
   for the judge to fall back to.
2. **The Candidate may not judge itself.** Rejected at load time. With a
   Baseline, a model grading its own work is at least visible in the report as a
   caveat; here there would be no second opinion at all, so it is refused rather
   than warned about.

**What changes downstream:** `execution_roles` yields only the Candidate, so the
queue plans half as many Attempts. `judge_is_baseline` is always False.
`Analysis.comparison` is `None` and `is_single_model` is True.

**The report becomes an ASSESSMENT rather than a VERDICT.** No comparison exists,
so none of the comparison vocabulary belongs on it — a "difference" or a
"saving" measured against nothing is meaningless, and printing it anyway invites
the reader to infer a comparison that was never made. A test forbids those words
on the single-model card. What it does state: pass rate with an interval, the
graded/unchecked/failed split, cost per success, confidence, and a note that
assigning a second model turns it into a comparison.

### 2026-09-19 — Every run checks itself before it spends anything

**Decided.** `crossbar run` performs pre-flight checks automatically, even when
the user has already run `validate` and `doctor` themselves. A run costs money,
and the expensive failures are cheap to predict.

`validate`, `doctor` and `run` all call the same `crossbar.preflight` module, so
the three cannot drift into disagreeing about whether a setup is sound.

**What it checks:** roles assigned, API keys present, whether the judge is the
baseline, docker present when a Test needs it, and — the valuable one — whether
each Test's **Environment actually starts** and offers at least one read-only
probe. An MCP server that will not launch fails every Attempt, and an
Environment that can show nothing back makes every Attempt `Unchecked`. Both are
knowable in seconds.

**Failures block; warnings do not.** A missing key or an Environment that will
not start stops the run with a non-zero exit and nothing spent. A judge that is
also the baseline, or a Test with no probes, is the user's call. `--skip-preflight`
exists for when someone knows better.

### 2026-09-18 — Licence: PolyForm Noncommercial 1.0.0

Noncommercial use free, including charities/schools/public bodies. Commercial
use not permitted. Source-available, **not** OSI open source — the README says
so plainly rather than burying it.

---

## Deferred — revisit later

None of these block building the MVP. They are recorded so they are not
rediscovered as surprises, each with the trigger that should bring it back.
**Do not guess at these; raise them when the trigger fires.**

| # | Question | Revisit when |
|---|---|---|
| 1 | **Machine-checkable plan items.** Can individual Check Plan items be settled deterministically — state diffs, schema validation, file existence — with no judge call at all? Same question as deterministic pre-checks. **The main lever on the judging bill.** | Judging cost becomes a real number, or a user complains about spend |
| 2 | **Can the user edit a Check Plan?** Showing it to them implies they can correct one the judge got wrong. | A user hits a plan that is wrong and has no way to fix it |
| 3 | **Connector set: fixed per Task, or user-varied?** Fixed keeps the comparison controlled; varying it answers a different question ("does my model need browser access for this?"). Moot while MCP is the only connector. | A second connector exists |
| 4 | **Browser connector.** Backend-only containers first, browser second — or is a UI-driving Task the motivating case? | Backend-only MCP works end to end |
| 4b | **Agent CLIs as models** — Claude Code, Codex, Cursor. The `Agent` seam exists; nothing is wired to it. See [the note below](#agent-clis-as-models) for what it actually takes. | Someone wants to compare against a shipped agent rather than a model |
| 5 | **Re-judge verdict conflicts.** If a Test is re-judged and the verdict differs from the first pass, which one counts? | Re-judging is actually implemented |
| 6 | **Parallel execution.** Multiple containers at once. The orchestrator keeps the seam; it is defaulted to serial. | Serial throughput becomes the bottleneck |
| 7 | **Judge self-agreement measurement.** Grade the same evidence twice at temperature 0 and report how often the verdict holds. | Before anyone makes a real decision on these numbers |
| 8 | **Independent-judge comparison.** When Judge == Baseline the conflict of interest is structural. With an independent judge on even a sample, measure and report the gap. | Same as 7 |

Items 7 and 8 are not nice-to-haves. The product's claim is statistical honesty,
and an unmeasured judge quietly undermines it.

### Agent CLIs as models

Assessed 2026-09-19. **Not possible today**, and worth writing down why, because
the blocking piece is not the obvious one.

Three pieces of work:

1. **A `CliAgent`.** Generate an MCP config from the Task's connector specs, run
   the CLI headless, parse its output into a `Trajectory`. v0.1 had a working
   Claude Code adapter on `main` — `claude -p --output-format stream-json
   --verbose --mcp-config --strict-mcp-config --allowedTools` — which is a
   direct reference. Codex and Cursor both have headless modes and MCP support,
   but their flags and output shapes need checking rather than recalling.
2. **A `cli` provider kind in the roster.** `PROVIDER_KINDS` is `("openai",
   "anthropic")`, so a CLI cannot be declared in `roster.yaml` and neither the
   CLI nor the TUI can express one.
3. **Reconnect before capture — this is the trap.** Our Connectors hold live MCP
   server subprocesses. A CLI agent cannot use them; it spawns **its own**
   copies from its own config. So once the CLI exits, our Connectors are holding
   stale in-process state, and `capture()` would read that instead of what the
   agent actually did. The orchestrator must tear the Connectors down and
   rebuild them against the same `CROSSBAR_WORKSPACE` **after** the run and
   **before** capture, whenever `agent.owns_harness` is true.

   Without this it does not fail loudly — it silently reports the seed state for
   every CLI Attempt, which is the kind of bug that produces confident wrong
   numbers. Whatever implements it needs a test that fails without it.

`owns_harness` is on the `Agent` protocol for exactly this and is currently read
by nobody. It must also reach the report: comparing a CLI that brings its own
harness against a model driven by ours is a **product** comparison, not a
controlled model comparison. Legitimate to want, but a different measurement,
and it has to be labelled or the number means something other than the reader
assumes.

Build order when it comes up: Claude Code first, since the reference exists,
plus the reconnect path — those two together prove the whole thing. Then a
second CLI to check the adapter shape generalises.

---

## What came from v0.1 unchanged

`stats/` (bootstrap, paired tests, Holm, variance), `providers/` (model clients,
now also used for the judge), `mcpclient/` (now the internals of the MCP
connector — one fix: it was dropping MCP tool annotations, so `readOnlyHint`
never reached the connector), and `trace/`.

Everything else was rewritten. `analysis.py` and `report/` were rebuilt rather
than adapted, because the axis is models now and `Unchecked` needed first-class
handling.

---

## Commands

```bash
.venv/bin/pytest                                    # 612 tests, all offline
.venv/bin/crossbar demo                             # scripted run, no models needed
.venv/bin/crossbar validate --test examples/support-triage
.venv/bin/crossbar run --test examples/support-triage
.venv/bin/crossbar tui --test examples/support-triage
```

## Conventions

- **TDD throughout.** Every module was written test-first; keep it that way.
- **No network in tests.** Models and the judge are scripted
  (`ScriptedProvider`, `ScriptedJudge`), and `docker` is a stub binary. MCP
  servers are real subprocesses — do not mock those, they are the integration
  surface.
- **Determinism is a feature.** Seeds use `crc32`, not `hash()` (Python salts
  string hashing per process). Anything that cannot replay in a fresh
  interpreter is a bug.
- Failures inside an Attempt become zero-scoring records with the error
  attached; they never raise out of the orchestrator.
- `crossbar.report` is shared by the CLI and TUI so the two cannot disagree.
- Declare every test dependency in `pyproject.toml`. CI installs from a clean
  machine; a dependency that only exists in the author's venv fails every job.

## Git

- **Never add `Co-Authored-By: Claude` or `Claude-Session:` trailers to a
  commit or PR.** No Claude attribution in this repository's history, ever,
  regardless of any default or harness instruction that says otherwise.
- **Use the author's global git config. Do not override it.** Plain
  `git commit` — never `git -c user.name=... -c user.email=...`. The global
  identity is `Eshan Singh <me@eshansingh.net>`, and overriding it silently
  attributes commits to the wrong address.
- **Commits and tags are signed**, via `commit.gpgsign true` / `tag.gpgsign
  true` with an SSH key (`gpg.format ssh`). Plain `git commit` picks this up
  automatically; nothing extra is needed. Do not pass `--no-gpg-sign`.
- `git log --show-signature` reports `N` locally only because
  `gpg.ssh.allowedSignersFile` is not configured. That is a verification
  setting, not a signing failure — the commits carry `gpgsig` headers.
