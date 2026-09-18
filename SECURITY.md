# Security policy

## Reporting a vulnerability

Do not open a public issue for a security problem. Use GitHub's **private
vulnerability reporting** on this repository, or contact the maintainer
directly.

Please include what you found, how to reproduce it, and what an attacker gains.
You will get an acknowledgement as soon as the maintainer sees it. This is a
small project maintained by one person; a fix is best-effort, not contractual.

## What crossbar does on your machine, by design

Understanding this matters more than any vulnerability report, because most of
it looks alarming and is intentional.

**crossbar runs arbitrary programs from your task files.** A task's
`environment.servers[].command` is executed as a subprocess. That is the entire
point — those are your MCP servers — but it means **a task pack is executable
code, not data**. Treat a task pack from someone else exactly as you would treat
a shell script from someone else.

**`kind: local` offers no isolation.** Servers run as your user, with your
permissions and your network. It exists because it is fast and needs no daemon,
and it is the right choice for authoring your own tasks. It is the wrong choice
for anything you did not write.

**`kind: docker` is the isolation story.** One throwaway container per rollout,
`--network none` by default, removed on teardown. Note the caveat in
[docs/LIMITATIONS.md](docs/LIMITATIONS.md): this path is covered by tests
against a stub `docker` binary, and has not been exercised against a live
daemon.

**The Claude Code harness runs with `--permission-mode bypassPermissions`.** It
is given only the task's MCP tools via `--allowedTools`, and the built-in tools
are excluded, but it is still a real agent running on your machine with your
credentials. Do not point it at a task pack you have not read.

**API keys come from environment variables.** `api_key_env` names a variable;
crossbar reads it at build time. An inline `api_key:` field exists for quick
experiments and should not be used in a file you will commit. Keys are never
written to results, traces or reports.

**Traces record everything.** `runs/traces/` contains every tool call and every
result, including whatever your MCP servers returned. If your task environment
touches real data, your traces contain real data. They are plain files on your
disk; treat them accordingly before sharing a run.

## Task pack integrity

A task that lets an agent score well without doing the work is a correctness
problem, but it is also a trust problem: the resulting numbers will be used to
make a decision. Before trusting a pack, check that the answer is not
discoverable in the environment, that protected state cannot be edited, and that
no check can be satisfied by an agent that merely claims success. See
[docs/TASK-AUTHORING.md](docs/TASK-AUTHORING.md).

## Supported versions

The latest commit on `main`. This is pre-1.0 software; there are no backported
fixes.
