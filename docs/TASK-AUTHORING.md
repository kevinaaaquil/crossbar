# Writing Tests and Tasks

A **Test** is a directory, normally under `.crossbar/tests/`. Everything about
it is three kinds of YAML file.

```
.crossbar/tests/my-test/
  test.yaml               name, repeats, which environment
  env.yaml                what the agent is allowed to touch
  01-first.task.yaml      the work, and what a correct result means
  02-second.task.yaml
```

List it under `tests:` in `.crossbar/config.yaml` and it runs with `crossbar run`.

---

## The whole format

### `test.yaml`

```yaml
name: Support triage
description: Support-desk workflows over a ticketing backend.
repeats: 3                  # how many times each Task is attempted per model
environment: env.yaml
```

`repeats` is what lets you tell *consistently mediocre* apart from *wildly
inconsistent*. Both can average 50%, and they mean different things. Three is a
sensible floor; five or more settles down.

### `env.yaml`

```yaml
kind: local                 # local | docker
connectors:
  mcp:
    servers:
      - name: tickets
        command: ${CROSSBAR_PYTHON}
        args: ["-m", "crossbar.demo.tickets_server"]
        env: {}             # optional
        cwd: null           # optional
```

`kind: local` runs the servers as subprocesses on this machine. Fast, needs no
daemon, right for authoring. **No isolation** — do not point it at a Test you
did not write.

`kind: docker` gives each Attempt a throwaway container:

```yaml
kind: docker
image: your-image:latest
reset: recreate             # the only policy: destroyed and rebuilt each Attempt
connectors:
  mcp:
    servers:
      - name: orders
        command: python
        args: ["-m", "order_server"]
```

The image must already contain your server and its dependencies.

**Variable expansion.** `${VAR}` works in `command`, `args`, `env` and `cwd`.
Two are always defined:

| Variable | Is |
|---|---|
| `${CROSSBAR_PYTHON}` | the interpreter running crossbar — use it for Python servers |
| `${CROSSBAR_WORKSPACE}` | a scratch directory unique to this Attempt |

An unknown name is left as-is rather than blanked, so a typo is visible.

Under `kind: docker`, `env`, `cwd` and the workspace are all *inside the
container*: crossbar passes them to `docker exec`, not to the local process, so
they mean what they say on the server's side and nothing on yours.

### `*.task.yaml`

```yaml
id: escalate-outage         # required, unique within the Test
name: Escalate the outage tickets

prompt: |
  Every open ticket that mentions an outage must be escalated to urgent
  priority. Leave every other ticket's priority alone. Reply with the ticket
  ids you escalated.

golden: |
  Tickets T-1001 and T-1004 both have priority "urgent", because both mention
  an outage. No other ticket's priority changed: T-1002 is still "normal".

limits:                     # all optional
  timeout_s: 120
  max_steps: 12
  max_tokens: 60000

environment: other-env.yaml  # optional, overrides the Test's default
```

That is the entire schema. There is no `checks:` block, no assertion syntax, no
verifier to write.

---

## The Golden is prose

**Say what a correct result means. Do not describe how to check it.**

```yaml
golden: |
  Orders 4471 and 4488 each have one refund recorded against them, for the
  duplicate charge only. No other order has a refund. Order 4490 was charged
  twice but is already disputed, so it was left alone.
```

Before any Attempt runs, the judge reads the Task and this Golden alongside the
list of read-only tools your environment offers, and derives a **Check Plan** —
what must be true, and which tools would show it. That plan is stored, shown to
you, and applied identically to every Attempt.

Deriving it before any model output exists is deliberate: the criteria are fixed
before anyone has seen an answer, so they cannot be bent towards it.

### Write goldens that can actually be checked

The judge can only ask for evidence something can supply. If your golden talks
about the database, something must be able to read the database back. If
nothing can, the result is **Unchecked** — no score, with that as the reason.

`crossbar validate` tells you this before you spend anything: it starts each
environment and warns when nothing read-only exists to check against.

### Be specific about what must *not* change

```yaml
# Weak: an agent that sets every ticket to urgent passes.
golden: |
  T-1001 and T-1004 are urgent.

# Better: the negative case is stated, so over-eager work fails.
golden: |
  T-1001 and T-1004 are urgent. No other ticket's priority changed —
  T-1002 is still "normal" and T-1005 is still "low".
```

---

## Read-only tools, and why they matter

Evidence capture must not change the thing it is measuring, so crossbar only
calls tools it can **establish** are read-only. In order:

1. **The server's own annotation.** An MCP tool advertising `readOnlyHint: true`
   is trusted.
2. **An explicit declaration** in `env.yaml`, for servers that do not annotate:

   ```yaml
   connectors:
     mcp:
       servers: [...]
       read_only_tools: ["tickets__list_tickets", "tickets__dump_db"]
   ```
3. **Otherwise it is not a probe.** Nothing is inferred from a tool's name — a
   tool called `get_everything` could still delete your data.

If your server has no read-only tools at all, **every Attempt will come back
Unchecked.** Adding one that dumps the relevant state is usually the single
highest-value change you can make to a Test.

### State must outlive the agent

Judging can be deferred and re-run long after the container is gone, so evidence
is captured while the environment is alive and written to disk. If your server
keeps state only in memory, write it to `${CROSSBAR_WORKSPACE}` too:

```python
path = os.path.join(os.environ["CROSSBAR_WORKSPACE"], "state.json")
```

**Under `kind: docker` the workspace is not a way to keep anything.** It is a
directory inside the container, with nothing mounted behind it, so it dies when
the container does — and it must, or one Attempt could read the last one's
state. What survives a containerised Attempt is the evidence crossbar captures
through your read-only tools while the container is still up. So a
containerised Test needs a probe that can show the state that matters — a
`dump_database` or equivalent — and that probe is what makes it judgeable, not
the workspace.

The bundled `crossbar.demo.tickets_server` is a complete, small example.

---

### If your server keeps state, declare the hooks

`reset: recreate` gives every Attempt its own container. Correct, and it pays
the image's start cost every time and throws away the one artefact the Attempt
produced — the state it actually wrote.

An Environment whose server keeps state of its own can hand that state out and
take it back instead:

```yaml
kind: docker
image: your-image:latest
reset: hooks

state:
  dir: /app/db                    # everything durable lives here
  snapshot_dir: /app/snapshots    # never inside dir
  snapshot: hooks/snapshot.sh     # prints the path it wrote, last line
  restore: hooks/restore.sh       # installs the first file in snapshot_dir
  dump: hooks/dump.sh             # optional: the state as text, for the judge
  seed: seed/library.db           # optional, relative to this file
  restart_after_restore: false    # true if the server caches state across calls
```

crossbar then runs one container for the whole Test: it installs the seed,
takes a baseline, and after each Attempt keeps that Attempt's end state and
puts the baseline back. Each Attempt's state lands under `state/<attempt-id>/`
in the results directory, and the Attempt records where.

**`seed` is usually not optional in practice.** Images commonly ship with an
empty state directory because their real data arrives through a bind mount, and
an eval cannot use that mount — every Attempt would write to your real store.
Point `seed` at a state file in your Test and crossbar installs it before the
baseline is taken. Without it, the baseline is whatever empty store the image
shipped, and every Attempt starts from nothing.

Two rules the hooks must follow, because crossbar relies on both:

- **`snapshot` prints the path it wrote as its last line.** crossbar copies out
  what the hook names, and keeps the hook's filename — it cannot know whether
  this store's state is a `.db`, a `.dump` or a `.tar`.
- **`restore` takes the first file in the snapshot directory by name.** crossbar
  empties that directory and leaves exactly one file there, so "first" is never
  ambiguous. The hook must not tidy the directory itself: those are restore
  points, and a hook that cleaned them would destroy what it was asked to read.

A consistent copy matters. For SQLite that is `sqlite3 .backup`, not `cp`: a
`cp` mid-write can capture a torn page or miss a hot journal.

**`dump` is what lets a judge grade on state.** A snapshot is bytes — a SQLite
file, a `pg_dump`, a tarball — and a judge is a language model, which cannot
read any of those. Your environment is the only thing that knows how to render
its own state, so it does:

```sh
#!/bin/sh
sqlite3 "$(working_db)" .dump      # or pg_dump --format=plain, or tar -tv
```

crossbar takes it after the Attempt and before the baseline goes back, and
hands it to the judge alongside the probe results, labelled "the environment's
state after the attempt". For the reference environment that turns 118KB of
pages into 211 lines of SQL the judge can read line by line.

Without `dump`, the judge still works — it grades from your read-only probes —
but it only sees what those probes expose. With it, it sees the world.

---

### An Environment must not bake in the date it was built

If your image seeds a database, anything it computes at **build** time is frozen
into the image. `CURRENT_DATE - 30` in a seed script is not "thirty days ago" —
it is one specific date, decided on the machine that ran `docker build`, and it
stops being thirty days ago tomorrow.

This is the worst failure an Environment can have, because nothing looks broken.
A Golden that says "four loans are overdue" stays true for exactly one day. On
day three it is five loans, and a model that answers correctly is marked wrong —
by the Environment, not the judge. Nobody investigating a bad score would think
to check the image's build date.

Seed whatever you like at build time, for speed, but **shift time-relative data
onto today when the container starts**. The bundled library example keeps a
one-row anchor table recording the date the seed was written, and its entrypoint
shifts every seeded date by `today - anchor` before anything can connect.

The same goes for anything else that ages: expiry dates, tokens, "recent"
activity, retention windows.

---

## Three rules for a Task worth trusting

**1. Solvable with the tools provided.** If the agent cannot reach what it
needs, you are measuring your setup, not the model.

**2. Checkable from what the environment can show.** Otherwise every result is
Unchecked, which is a setup problem wearing the costume of a result.

**3. Not passable without doing the work.** This is the one people skip, and it
silently invalidates a whole Test. If saying the right words would satisfy the
Golden, the Task measures nothing. Agents are very good at describing work
convincingly and then not doing it — there is a whole failure mode for it.

---

## A workable process

1. Write one Task with a prose Golden, and list it under `tests:` in
   `.crossbar/config.yaml`.
2. `crossbar validate` — catches typos, confirms the environment starts, warns
   if nothing can be read back.
3. `crossbar run --repeats 1`.
4. Read the **Check Plan** in `.crossbar/runs/plans/`. Does it actually describe what
   you meant? If not, the Golden was ambiguous — fix the prose, not the plan.
5. Read one Attempt's `evidence.json` and `judgement.json`. Did the judge look
   at the right things, and did its reasoning hold up?
6. **Now break it on purpose.** Point the Task at a model you expect to fail, or
   loosen the prompt, and confirm it scores zero. A check that passes no matter
   what is the failure mode that costs you weeks.
7. Only then write the next Task.

Step 6 is the one people skip.

---

## What the outcomes mean

| Outcome | Means | What to do |
|---|---|---|
| **Graded** | The evidence was there. There is a score. | Read it. |
| **Unchecked** | The evidence was not available. **No score**, and a reason. | Fix your environment, then `crossbar judge .crossbar/runs` — no need to re-run. |
| **Failed** | The Attempt itself errored. Counted as a failure. | Check the trajectory; usually an unreachable model or a limit hit. |

Any single unchecked check leaves the whole Attempt unscored. A Golden is one
statement, and verifying half of it does not establish that the Task was done.
