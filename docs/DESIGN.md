# crossbar — rebuild design

Concrete shapes for the product described in [`../CLAUDE.md`](../CLAUDE.md).
Terminology there is authoritative; this document says what gets built.

**Scope of this design:** the MVP. One Connector (MCP), one container at a time,
serial execution, judge-based scoring with a Check Plan.

---

## 1. Domain objects

```
Test ──┬── Task ──┬── prompt
       │          ├── Golden          (free-form; what "correct" means)
       │          ├── Environment ref
       │          └── limits          (timeout, steps, tokens)
       └── metadata

CheckPlan ── derived once per Task, before any Attempt
          └── CheckItem[] ── criterion + EvidenceRequest[]

Attempt ──┬── model + role + repeat index
          ├── Trajectory   (what the agent did)
          ├── Evidence     (what was captured, per the plan)
          └── Judgement    (graded / unchecked / failed)
```

### Test

A directory. `test.yaml` holds metadata; `*.task.yaml` files hold Tasks.

```yaml
# tests/support-triage/test.yaml
name: Support triage
description: Four support-desk workflows over a ticketing backend.
repeats: 3                    # how many times each Task is attempted per model
environment: env.yaml         # default Environment for every Task in this Test
```

### Task

```yaml
# tests/support-triage/01-escalate.task.yaml
id: escalate-outage
name: Escalate the outage tickets

prompt: |
  Every open ticket that mentions an outage must be escalated to urgent
  priority. Leave every other ticket's priority alone.

golden: |
  Tickets T-1001 and T-1004 both have priority "urgent".
  No other ticket's priority has changed; T-1002 is still "normal".

limits:
  timeout_s: 120
  max_steps: 12
  max_tokens: 60000
```

The Golden is **prose**. It is never shaped to fit a schema. The Check Plan is
derived from it.

### Environment

```yaml
# tests/support-triage/env.yaml
kind: docker                  # docker | local
image: crossbar/demo-tickets:latest
reset: recreate               # recreate the container between Attempts

connectors:
  mcp:
    servers:
      - name: tickets
        command: python
        args: ["-m", "tickets_server"]
```

`kind: local` runs the same servers as host subprocesses. It exists for
authoring and for the test suite; it offers no isolation.

---

## 2. Connectors

The seam that keeps browser/HTTP/shell from being a rewrite later. **The Harness
must never know MCP exists.**

```python
class Connector(Protocol):
    name: str

    def setup(self, env: EnvironmentHandle) -> None: ...
    def teardown(self) -> None: ...

    # For the agent: things it may do.
    def tools(self) -> Sequence[ToolSpec]: ...
    def call(self, tool: str, args: Mapping) -> ToolResult: ...

    # For evidence capture: things that only observe.
    def probes(self) -> Sequence[ToolSpec]: ...
    def probe(self, name: str, args: Mapping) -> ProbeResult: ...
```

`ToolSpec` carries `name`, `description`, `input_schema`, `read_only`.

### Read-only classification

Evidence capture must not mutate the thing it is measuring. Precedence:

1. **MCP tool annotations.** If a server sets `readOnlyHint`, trust it.
2. **Task/Environment declaration.** An explicit `read_only_tools` list wins over
   a missing annotation.
3. **Otherwise: not a probe.** Conservative by default. A tool we cannot
   establish as read-only is never called during capture; if the Check Plan
   wants it, that check becomes `Unchecked` with a reason.

The Check Plan is shown to the user, so which probes will run is inspectable
before anything is spent.

### MVP connector

`McpConnector` — wraps the existing stdio MCP client. Tools are the server's
tools, namespaced `server__tool`. Probes are the read-only subset.

---

## 3. Environment lifecycle

```python
class Environment(Protocol):
    def start(self) -> EnvironmentHandle: ...
    def reset(self) -> None: ...
    def stop(self) -> None: ...
```

`EnvironmentHandle` exposes what Connectors need: container id, workspace path,
exposed endpoints, and a way to exec.

**One container, one Task at a time.** `reset: recreate` destroys and rebuilds
between Attempts — the only policy that cannot leak state. Snapshot-restore is a
later optimisation, behind the same interface.

State leaking between Attempts produces quietly wrong numbers with no error, so
the default must be the safe one.

---

## 4. Harness

Ours, fixed, invisible. Connector-routed.

```python
class Harness:
    def run(
        self,
        task: Task,
        connectors: Sequence[Connector],
        model: ModelClient,
        limits: Limits,
    ) -> Trajectory: ...
```

Loop: system prompt, task prompt, the union of every enabled Connector's tools,
execute calls by routing to the owning Connector, stop on a tool-free reply or a
limit. Records every step and call into a `Trajectory`.

---

## 5. Evidence

Captured at the end of an Attempt, **while the Environment is still alive**,
because judging may happen long after the container is gone.

```python
@dataclass(frozen=True)
class EvidenceRequest:
    label: str          # "ticket T-1001 after the run"
    connector: str      # "mcp"
    probe: str          # "tickets__get_ticket"
    args: Mapping

@dataclass(frozen=True)
class EvidenceItem:
    request: EvidenceRequest
    content: str | None
    error: str | None   # why it could not be captured

@dataclass(frozen=True)
class Evidence:
    final_answer: str
    items: tuple[EvidenceItem, ...]
```

Evidence is serialised to disk per Attempt. **It must be sufficient to judge
from, standalone**, so a re-judge never needs a live container.

---

## 6. Judging

Two operations, both on a `Judge` backed by a model.

```python
class Judge:
    def make_plan(self, task: Task, catalogue: ProbeCatalogue) -> CheckPlan: ...
    def grade(self, plan: CheckPlan, evidence: Evidence) -> Judgement: ...
```

### Check Plan — derived once per Task, before any Attempt

```python
@dataclass(frozen=True)
class CheckItem:
    id: str
    criterion: str                          # what correct looks like
    evidence: tuple[EvidenceRequest, ...]   # what to capture to decide it

@dataclass(frozen=True)
class CheckPlan:
    task_id: str
    items: tuple[CheckItem, ...]
    unsatisfiable: tuple[str, ...]  # golden demands evidence nothing can supply
```

Generated from **Task + Golden + the probe catalogue** — never from an Attempt.
Fixing criteria before any model output is seen is an integrity property, not an
implementation detail. `unsatisfiable` lets us warn before spending anything.

Stored with the Test. Reused on re-judge unless regeneration is requested.

### Grading

```python
class CheckStatus(Enum): PASS, FAIL, UNCHECKED

@dataclass(frozen=True)
class CheckOutcome:
    check_id: str
    status: CheckStatus
    reason: str

@dataclass(frozen=True)
class Judgement:
    outcome: Outcome          # GRADED | UNCHECKED | FAILED
    checks: tuple[CheckOutcome, ...]
    score: float              # share of checks passed; 0.0 when not GRADED
    reasoning: str            # the judge's own words, stored for the user
```

- **GRADED** — every check decided.
- **UNCHECKED** — required evidence unavailable. **No score.** Reason attached.
- **FAILED** — the Attempt itself errored.

### Blinding

The judge is never told whose Attempt it is grading — which matters most when
the Baseline is doubling as Judge.

**Enforced structurally, not by convention:** `Evidence` carries no model
identity field, so the prompt builder cannot leak what it does not have. Attempt
order is randomised. Grading is reference-based (each Attempt against the
Golden) rather than pairwise, which leaves less room for comparative bias.

A test asserts that no model id, provider name or role label reaches the judge
payload.

---

## 7. Orchestration

Serial. One container at a time.

```
for test in tests:
    for task in test.tasks:
        plan[task] = judge.make_plan(task, catalogue)     # once, before anything

    for role in (CANDIDATE, BASELINE):                    # candidate first
        for task in test.tasks:
            for repeat in range(test.repeats):
                env.start()
                trajectory = harness.run(task, connectors, model)
                evidence   = capture(plan[task], connectors, trajectory)
                env.stop()
                store(Attempt(...))

    if test.judging_enabled:
        for attempt in attempts:
            judgement = judge.grade(plan[attempt.task], attempt.evidence)
```

**Judging is opt-in per Test, default first Test only.** More than one Test
scheduled → warn and ask. Judging is the expensive part of the bill.

An unjudged Test is marked `unjudged`; the user may later re-judge it alone, or
re-run the whole flow.

Failure containment: anything that goes wrong inside one Attempt becomes a
`FAILED` record with the error attached. It never raises out of the orchestrator.

---

## 8. Storage layout

```
runs/<run-id>/
  run.json                  roster, roles, tests, settings
  plans/<task-id>.json      the Check Plan
  attempts/<attempt-id>/
    attempt.json            model, role, repeat, status, timings, usage
    trajectory.json         what the agent did
    evidence.json           what was captured
    judgement.json          verdict, per-check outcomes, judge reasoning
  dump.zip                  the whole thing, for the user
```

Flat, inspectable, and re-judgeable without a container.

---

## 9. What is reused from v0.1

| Reused | Notes |
|---|---|
| `stats/` | Unchanged. Bootstrap, paired tests, Holm, variance. |
| `providers/` | Model clients. Gains the judge role. |
| `mcpclient/` | Becomes the internals of `McpConnector`. |
| `trace/` | Trajectory recording; Evidence is new alongside it. |
| `report/`, `analysis.py` | Adapted once judged results exist. |

| Rewritten | Why |
|---|---|
| `tasks/` → `domain/` | Test/Task/Golden, no check schema. |
| `env/` | Container-shaped, reset between Attempts. |
| `harness/` | Single harness, connector-routed. |
| `scoring/` → `judging/` | Check Plan and judge, replacing deterministic checks. |
| `runner/` → `orchestrator/` | Roles, ordering, opt-in judging. |

---

## 10. Testing approach

Component-level, offline, deterministic.

**Stubbed:** model clients (scripted), the judge (scripted plans and verdicts),
`docker` (a stub binary).

**Never stubbed:** MCP servers — real subprocesses over real pipes. The Connector
routing, evidence capture, and plan/evidence serialisation all run for real.

Specific properties worth a test of their own:

- A Connector's probes never include a tool not established as read-only.
- Evidence survives a round-trip to disk and is judgeable with no live container.
- No model identity reaches the judge payload.
- An Attempt's failure never escapes the orchestrator.
- Candidate Attempts precede Baseline Attempts for a Test.
- A Check Plan is generated once per Task and reused across Attempts.
