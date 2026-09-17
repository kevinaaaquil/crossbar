# Architecture

```
crossbar.yaml ──┐
                ├──> Config ──> Runner ──> rollouts ──> SweepResult ──> Analysis ──> Report
taskpacks/  ────┘                 │                                          │
                                  │                                          └─ verdict, matrix,
                                  ├─ Environment  (MCP servers)                 failure taxonomy
                                  ├─ Harness      (the loop)
                                  ├─ Provider     (the model)
                                  ├─ Trajectory   (the recording)
                                  └─ Scoring      (Sec x Comp x Proc)
```

## The layers

| Package | Responsibility | Why it is separate |
|---|---|---|
| `crossbar.tasks` | Task pack format, parsing, validation | The customer's material; must fail loudly on a typo |
| `crossbar.mcpclient` | JSON-RPC over stdio, MCP flavoured | Owning the transport keeps it inspectable |
| `crossbar.env` | Bringing servers up, routing tool calls, tearing down | Swappable isolation: local today, docker, k8s later |
| `crossbar.providers` | Turning messages + tools into a next step | One adapter per vendor; nothing else knows about vendors |
| `crossbar.harness` | The loop from prompt to finished work | The axis under test; must stay native per harness |
| `crossbar.trace` | Recording one attempt | The evidence trail every score points at |
| `crossbar.scoring` | Deterministic checks, the multiplicative score, taxonomy | No LLM judge means reproducible numbers |
| `crossbar.stats` | Bootstrap, paired tests, Holm, variance | Pure functions, seeded, no dependencies |
| `crossbar.runner` | Matrix planning, execution, allocation, persistence | Where concurrency and failure containment live |
| `crossbar.analysis` | Rollouts to a recommendation | The product's actual opinion |
| `crossbar.report` | Rendering for humans | Shared by CLI and TUI so they cannot disagree |
| `crossbar.tui` | The terminal app | A view; owns no logic |

## Design decisions, and why

**The harness is a first-class axis, not a hidden constant.** Fix the task, the
budget, the timeout and the evaluator; vary the model and the harness; let each
harness keep its own prompting, tool interface and recovery behaviour. Normalise
the harnesses and you have destroyed the thing you are measuring.

**Scoring multiplies rather than adds.** `Security x Completion x Process`. One
security violation zeroes the task. High credit has to mean the task was
completed, nothing forbidden happened, and the run behaved.

**Process is gated by consistency, not averaged with it.** A run that crashed
executed no reliable process; averaging would hand it most of the credit for the
calls it did land before dying.

**Tasks are the resampling unit.** Both arms ran the same tasks, so the pairing
cancels per-task difficulty and the interval tightens. With small packs this is
the difference between a usable answer and noise.

**Every rollout gets its own environment.** No repeat can inherit state from the
one before it. This is checked by a test that would otherwise pass for the wrong
reason.

**Failures are recorded, never raised.** A sweep that dies on task 7 of 80 is
worse than useless. A broken environment, a dead endpoint or a harness that
raises all become zero-scoring records with the error attached.

**Seeds use `crc32`, not `hash()`.** Python salts string hashing per process. A
sweep that cannot be replayed in a fresh interpreter is not reproducible.

**The verdict answers the migration question.** Not "which cell scored highest"
but "what is the cheapest cell I cannot prove is worse than what I use today".
A cell one point behind at a tenth of the price is usually the right answer, and
saying so requires the interval, not the point estimate.

## How a single rollout runs

1. The runner builds a fresh `Environment` for the task and starts its MCP
   servers, allocating a workspace directory.
2. It builds the `(harness, provider)` pair for the cell, passing the task and
   repeat so seeded backends stay reproducible.
3. The harness runs its own loop: ask the model, execute tool calls against the
   environment, feed results back, stop on an answer or a limit.
4. Every step and every tool call lands in a `Trajectory`.
5. The scorer runs the task's checks against the live environment. If the
   harness ran its own copies of the servers (Claude Code does), the scorer
   reconnects to the workspace first.
6. The environment is torn down; the trajectory is written to `runs/traces/`.
7. A `RunRecord` — score, usage, cost, wall time, error — joins the sweep.

## Testing strategy

427 tests, no network, no API key, no Docker daemon, no model.

The trick is what gets replaced and what does not. **Replaced:** the model (a
`ScriptedProvider` returning pre-programmed steps), the `claude` CLI (a fake
binary replaying recorded stream-json), and `docker` (a stub shell script).
**Not replaced:** the MCP servers, which are real subprocesses speaking real
JSON-RPC over real pipes; the harness loop; the environment; the scorer; the
statistics.

So the suite exercises the actual integration surface deterministically, and the
parts that are stubbed are exactly the parts that would otherwise cost money or
require a network.
