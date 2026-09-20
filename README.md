<div align="center">

# crossbar

**Find out whether your own model can do your work as well as a frontier model
can — with a number you can defend.**

Connect your models. Point it at your tasks. It handles the isolated
environment, the tools the agent uses to touch it, the orchestration, the
judging, and the statistics.

[![licence](https://img.shields.io/badge/licence-PolyForm%20Noncommercial%201.0.0-orange)](LICENSE.md)
[![python](https://img.shields.io/badge/python-3.11%2B-blue)](#requirements)

</div>

---

## The question

> *"We pay a fortune for a frontier model. Could our fine-tune do this job
> instead — and how would we know?"*

Answering it properly means running both models against your real work, in a
clean environment, enough times to see past the noise, and grading the results
against what you already know a correct answer looks like.

That is all crossbar does.

## How it fits together

```
YOUR MODELS          crossbar                          YOUR TASKS
─────────────        ──────────────────────────        ──────────────
candidate    ──┐     isolated environment              prompt
baseline     ──┼──▶  connectors (MCP)          ◀────   golden result
judge        ──┘     orchestration                     (in prose)
                     evidence capture
                     judging
                     statistics
                              │
                              ▼
                     verdict · report · dump
```

**You bring** models, tasks, and a statement of what a correct result looks
like. **We handle** isolation, the agent's access to the environment,
execution, capture, judging, and the maths.

## Vocabulary

These words mean one thing here, everywhere:

| Term | Means |
|---|---|
| **Test** | A list of Tasks, run by models against an Environment. |
| **Task** | One isolated unit of work, judged against a known correct result. |
| **Environment** | The system the agent interacts with. An MCP backend today; a container with a UI and a backend is the same idea. |
| **Golden** | What a correct result means, written in prose. Never a schema. |
| **Attempt** | One execution of one Task by one model. |
| **Check Plan** | What to inspect and what to grade against, derived once per Task. |
| **Evidence** | What was captured from an Attempt for the judge to read. |
| **Unchecked** | We could not verify the result. Not a score, and not a failure. |
| **Dump** | A zip of everything a run produced, for you or your own AI. |

**Candidate**, **Baseline** and **Judge** are role labels you assign. They do
not change how a Task runs. They mean exactly two things: the candidate's
attempts run first, and the baseline judges when you do not connect a judge.

A **baseline is optional**. With one, you get a comparison and a verdict on
whether to switch. Without one, you get an assessment of a single model on its
own — *is this good enough at all?* — which is usually the earlier question.

## Quickstart

```bash
pip install crossbar

crossbar demo          # see the whole thing work — no key, no network
crossbar init          # create a .crossbar project here
```

`init` writes a commented config and copies an example Test in:

```
your-project/
  .crossbar/
    config.yaml              your models, their roles, which Tests to run
    tests/support-triage/    the bundled example, yours to replace
    runs/                    results land here
```

Edit `.crossbar/config.yaml` to point at your own models, then:

```bash
export ANTHROPIC_API_KEY=sk-ant-...

crossbar validate      # check the setup before spending anything
crossbar run           # run, judge, print the verdict
crossbar tui           # the same thing, interactive
```

Every command walks up from wherever you are to find `.crossbar`, the way `git`
finds `.git`. `--test` and `--roster` override it when you want something
one-off.

### Requirements

Python 3.11+. Docker only if you use `kind: docker` environments. Runtime
dependencies are `pyyaml`, `httpx` and `textual` — that is all.

Working on crossbar itself rather than using it:

```bash
git clone https://github.com/kevinaaaquil/crossbar.git && cd crossbar
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

## Writing a Test

A Test is a directory under `.crossbar/tests/`. This is the whole format:

```yaml
# .crossbar/tests/support-triage/test.yaml
name: Support triage
repeats: 3                  # how many times each Task is attempted per model
environment: env.yaml
```

```yaml
# .crossbar/tests/support-triage/env.yaml
kind: local                 # or: docker, with an image:
connectors:
  mcp:
    servers:
      - name: tickets
        command: ${CROSSBAR_PYTHON}
        args: ["-m", "crossbar.demo.tickets_server"]
```

```yaml
# .crossbar/tests/support-triage/01-escalate-outage.task.yaml
id: escalate-outage
prompt: |
  Every open ticket that mentions an outage must be escalated to urgent
  priority. Leave every other ticket's priority alone.

golden: |
  Tickets T-1001 and T-1004 both have priority "urgent". No other ticket's
  priority changed; T-1002 is still "normal".
```

**That is the whole authoring burden.** There is no check schema, no assertion
DSL, no verifier to write. You say what correct means; crossbar works out what
to inspect.

## How judging works

1. **The Check Plan is derived once per Task**, from the Task and its Golden,
   using the read-only probes the environment offers. It says what must be true
   and what to look at to decide it.
2. **It is derived before any Attempt runs.** The criteria are fixed before any
   model output exists, so they cannot be tailored to the output being graded.
3. **Evidence is captured per Attempt**, while the environment is alive — a
   database dump, a file, the agent's closing summary, whatever the plan needs.
4. **Each Attempt is graded against the plan**, one at a time, against the
   Golden. Never "which of these two is better", which is more biased.

Three outcomes:

| Outcome | Means |
|---|---|
| **Graded** | The evidence was there. There is a score. |
| **Unchecked** | The evidence was not available. **No score**, and a reason you can act on. |
| **Failed** | The Attempt itself errored. Counted as a failure. |

`Unchecked` matters. *"Your golden asks about the database and nothing here can
dump it"* is a useful answer. Silently scoring zero is not.

### The judge is blinded

The judge is never told whose Attempt it is grading. That matters most when the
baseline is also the judge, which is what happens if you do not connect a third
model — a structural conflict of interest.

Blinding is structural rather than a rule someone has to remember: the evidence
payload has no model, agent or role field, so it cannot leak what it does not
have. The report still says plainly when the baseline graded its own work.

### Judging is opt-in, because it is the expensive part

More than one Test scheduled? Only the first is judged by default. The rest run
and are stored, and you can judge them later without re-running anything:

```bash
crossbar judge .crossbar/runs
```

Re-judging reuses the stored plan. Regenerating it would silently change the
criteria and make old and new scores incomparable.

## What comes out

A verdict card stating both models' pass rates with confidence intervals, the
difference with an interval and a p-value, whether that difference is
established at all, what switching would save, and every caveat that applies.

Then a table per model, the reasons anything could not be checked, and:

```
.crossbar/runs/
  run.json                  every attempt, machine-readable
  report.md                 the report, shareable
  plans/<task>.json         the Check Plan
  attempts/<id>/
    trajectory.json         what the agent did
    evidence.json           what was captured
    judgement.json          the verdict and the judge's reasoning
  dump.zip                  all of it, zipped
```

## The statistics

- Pass rate per model with a **95% bootstrap confidence interval**, resampling
  **tasks** as the unit
- **Paired bootstrap** against the baseline, on the tasks both models had
  graded — an unchecked result on one side is never compared against a real
  score on the other
- **McNemar's exact test** as a cross-check
- Overlapping intervals are reported as **not significant**, not ranked

Roughly half the variance in a single agent benchmark run is noise. A point
estimate from a handful of attempts is not a result, which is why nothing here
is reported without an interval.

## Commands

| Command | Does |
|---|---|
| `crossbar init` | Create a `.crossbar` project folder here |
| `crossbar demo` | Run the bundled example with scripted models — no key needed |
| `crossbar validate` | Check the setup before spending anything |
| `crossbar run` | Pre-flight, then run, judge, and print the verdict |
| `crossbar judge <run>` | Judge a stored run without re-running it |
| `crossbar report <run>` | Render a stored run (`--json` for machines) |
| `crossbar attempts <run>` | List every attempt and its outcome (`--failed` to narrow) |
| `crossbar attempt <run> <id>` | One attempt: its checks, the judge's reasoning, the evidence it read |
| `crossbar dump <run>` | Zip a run for review |
| `crossbar doctor` | Check Python, Docker, connectors and your keys |
| `crossbar tui` | The interactive terminal app |

## Evaluating one model on its own

Leave the baseline out:

```yaml
roles:
  candidate: my-model
  judge: some-other-model     # required: nothing to fall back to
```

You get an **assessment** rather than a verdict: pass rate with an interval, the
graded/unchecked/failed split, and cost per success. None of the comparison
language appears, because a difference measured against nothing is meaningless.

Two rules keep it honest. A judge must be named explicitly, since there is no
baseline to fall back to. And the candidate may not judge itself — with a
baseline that conflict is at least visible as a caveat, but here there would be
no second opinion at all, so it is refused at load time.

## Every run checks itself first

`crossbar run` performs the same checks `validate` does before it commits to
anything, even if you just ran `validate` yourself. It confirms the roles are
assigned, the keys are set, docker is there if a Test needs it, and — the one
that matters — that each Test's environment **actually starts** and offers at
least one read-only probe.

An MCP server that will not launch fails every attempt. An environment that can
show nothing back makes every attempt `Unchecked`. Both cost seconds to detect
and real money to discover afterwards.

Failures stop the run with nothing spent. Warnings — the baseline doubling as
judge, a test with no probes — are printed and the run continues, because those
are your call. `--skip-preflight` is there when you know better.

The TUI is one front-end, not the product. Everything it shows is reachable
from the command line — `crossbar attempts` and `crossbar attempt` render the
same text the Results tab does, from the same renderer, so the two cannot
describe a run differently. `--single` does from the CLI what `m` does in the
app.

## The terminal app

`crossbar tui` gives you five tabs: the models and their roles, the Tests and
which will be judged, a **live run view** showing which model is on which Task
right now and the whole queue behind it, and the results — with a panel per
Attempt showing its checks, the judge's reasoning, and the evidence it read.

## Status

This is the MVP. Working: the domain format, MCP connector, local and container
environments, the harness, agents, evidence capture, the Check Plan and judging,
orchestration, statistics, the report, the dump, the CLI and the TUI.

Deliberately not built yet, with the trigger for revisiting each one recorded in
[`CLAUDE.md`](CLAUDE.md): a second connector (browser, HTTP, shell), parallel
execution, connecting an agent CLI such as Claude Code as a model, plan editing,
machine-checkable plan items that skip the judge call, and measuring the judge's
self-agreement.

Known limits: MCP is the only connector, so an environment must expose one; the
container path has not been exercised against a live Docker daemon; and the
judge's prompts have not been tuned against a live model.

## Documentation

| File | For |
|---|---|
| [`docs/WALKTHROUGH.md`](docs/WALKTHROUGH.md) | Complete beginner's guide. Start here. |
| [`docs/TASK-AUTHORING.md`](docs/TASK-AUTHORING.md) | Writing your own Tests and Tasks |
| [`CLAUDE.md`](CLAUDE.md) | Terminology, every design decision and why |
| [`docs/DESIGN.md`](docs/DESIGN.md) | The concrete shapes |
| [`docs/PROGRESS.md`](docs/PROGRESS.md) | What is built, what is next |
| [`docs/PLAN.md`](docs/PLAN.md) | Build order |

## Tests

```bash
.venv/bin/pytest
```

No network, no API key, no Docker daemon. Models and the judge are scripted; the
MCP servers are real subprocesses speaking real JSON-RPC over real pipes, and so
are the environment, the connectors and every file written.

## Licence

[PolyForm Noncommercial License 1.0.0](LICENSE.md). Free for any noncommercial
purpose, including charities, schools, public research bodies and government
institutions. Commercial use is not permitted — open an issue to discuss a
licence. Source-available, not OSI open source.
