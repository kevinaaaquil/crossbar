# Writing Tests and Tasks

A **Test** is a directory. Everything about it is three kinds of YAML file.

```
my-test/
  test.yaml               name, repeats, which environment
  env.yaml                what the agent is allowed to touch
  01-first.task.yaml      the work, and what a correct result means
  02-second.task.yaml
```

Run it with `crossbar run --test my-test`.

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

`src/crossbar/demo/tickets_server.py` is a complete, small example.

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

1. Write one Task with a prose Golden.
2. `crossbar validate --test my-test` — catches typos, confirms the environment
   starts, warns if nothing can be read back.
3. `crossbar run --test my-test --repeats 1`.
4. Read the **Check Plan** in `runs/<id>/plans/`. Does it actually describe what
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
| **Unchecked** | The evidence was not available. **No score**, and a reason. | Fix your environment, then `crossbar judge <run>` — no need to re-run. |
| **Failed** | The Attempt itself errored. Counted as a failure. | Check the trajectory; usually an unreachable model or a limit hit. |

Any single unchecked check leaves the whole Attempt unscored. A Golden is one
statement, and verifying half of it does not establish that the Task was done.
