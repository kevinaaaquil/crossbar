# What this MVP does not do

Stated plainly, so nobody discovers it the hard way.

## Scope

**MCP tasks only.** The environment is one or more MCP servers over stdio. No
browser use, no filesystem tasks, no shell tasks. The `Environment` interface is
where those would attach.

**No LLM judge.** Every check is deterministic. That is a deliberate MVP choice —
judges are most of the cost in most eval bills, and the failure modes that matter
for MCP work (wrong state, wrong shape, never acted) are machine-checkable. Tasks
needing a rubric are out of scope for now.

**No HTTP or SSE MCP transports.** stdio only.

**The Claude Code harness has not been run against the live CLI.** It is tested
against a fake `claude` binary replaying recorded stream-json, which covers argv
construction, the MCP config file, transcript parsing, timeouts and exit codes.
Whether the real CLI's current output matches those fixtures is unverified.

**Docker is untested against a live daemon.** The code path is exercised against
a stub binary, which verifies argv construction, lifecycle and teardown, but no
container has actually been started by this code. Treat `kind: docker` as
unproven until you try it.

## Statistics

**Repeats are per (cell, task).** The adaptive allocator adds a whole extra pass
over every task for a contested cell rather than targeting individual tasks.

**No power analysis up front.** Crossbar tells you afterwards that 4 tasks cannot
resolve a 3-point gap. It will not tell you beforehand how many tasks you need.

**Bootstrap defaults to 4,000 resamples,** not the 10,000 the literature uses.
It moves the third decimal place; raise it in `crossbar.analysis.N_RESAMPLES` if
you care.

**Cost is modelled, not metered.** It comes from your `price:` entries and the
tokens reported by the provider. Claude Code reports its own cost and that is
used when present. Nothing reconciles against an actual invoice.

## Harnesses

**Claude Code runs on the host, not in the container.** With `kind: docker`, the
task's servers run in a container but the `claude` CLI does not. Pairing Claude
Code with a docker environment is not supported yet.

**Security enforcement differs by harness.** In-process harnesses block a
forbidden tool *before* it executes. Claude Code is given an allow-list that
excludes forbidden tools, and any that appear in the transcript anyway are
recorded as violations after the fact — detection, not prevention.

**No harness config search.** You choose the harness variants; crossbar does not
search over prompt or configuration space.

## Operations

**No resume.** An interrupted sweep keeps the trajectories written so far, but
`sweep.json` is only written at the end. Re-running starts over.

**No trace importer.** The design doc's strongest adoption idea — mining task
drafts and verifiers from production traces in Phoenix or Langfuse — is not
built.

**Results are local files.** `runs/sweep.json`, `runs/report.md`,
`runs/traces/`. No database, no server, no dashboard.

**`crossbar init` needs the source checkout.** Starter files are read from the
repository, not from package data.

## The honest summary

This is the v0 from the design doc: two or more models, two or more harnesses,
a handful of hand-written tasks, fixed repeats, bootstrap intervals in a printed
table, and a real crossover demonstrated offline. It is a planner, a scorer, a
statistics engine and a report. It is not yet a runner that competes with Harbor,
and it should not try to be.
