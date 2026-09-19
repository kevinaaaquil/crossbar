# Getting started

From nothing to a defensible answer about your own model. Fifteen minutes.

---

## 1. Install

```bash
cd crossbar
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/crossbar doctor
```

`doctor` tells you what is present: Python, Docker (optional), the connectors
available, and which of your models have keys.

## 2. See the shape of the output first

```bash
.venv/bin/crossbar demo
```

Nothing is connected. Two scripted stand-in models run the shipped example —
one performs each task through an MCP server, one only describes what it would
do — and a judge reads the resulting state. You get a real verdict card from a
fake run, so you know what you are aiming at.

## 3. Connect your models

Edit `roster.yaml`:

```yaml
models:
  - id: my-model
    provider: openai                    # the API *format*, not the company
    model: qwen3
    base_url: http://localhost:8000/v1  # vLLM, Ollama, LM Studio, anything
    api_key_env: LOCAL_API_KEY
    price: {input_per_mtok: 0.20, output_per_mtok: 0.60}

  - id: frontier
    provider: anthropic
    model: claude-opus-5
    api_key_env: ANTHROPIC_API_KEY
    price: {input_per_mtok: 15.0, output_per_mtok: 75.0}

roles:
  candidate: my-model
  baseline: frontier
```

`api_key_env` is the **name of an environment variable**, never the key itself.
Set it in your shell:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

**Prices matter.** They are how the verdict states what switching would save.

### A third model as judge

Leave `judge` unset and the baseline grades its own attempts. They are blinded
— the judge is never told whose work it is reading — but the conflict is
structural, and the report says so on every verdict. For a decision that
matters, connect a third model:

```yaml
roles:
  candidate: my-model
  baseline: frontier
  judge: some-other-model
```

## 4. Write your first Task

A Test is a directory with three kinds of file. Copy
`examples/support-triage` and edit it, or start fresh:

```
my-test/
  test.yaml            name, how many repeats
  env.yaml             which MCP server the agent talks to
  01-first.task.yaml   the work, and what a correct result means
```

```yaml
# my-test/env.yaml
kind: local
connectors:
  mcp:
    servers:
      - name: orders
        command: ${CROSSBAR_PYTHON}
        args: ["-m", "your_order_server"]
```

```yaml
# my-test/01-first.task.yaml
id: refund-duplicates
prompt: |
  Find every order charged twice this month and issue a refund for the
  duplicate charge. Reply with the order ids you refunded.

golden: |
  Orders 4471 and 4488 each have one refund recorded against them, for the
  duplicate charge only. No other order has a refund. Order 4490 was charged
  twice but is already disputed, so it was left alone.
```

**The golden is prose.** No schema, no assertions, no verifier. Say what correct
means and crossbar works out what to inspect.

### Three rules for a Task worth trusting

1. **Solvable with the tools provided.** If the agent cannot reach what it
   needs, you are measuring your setup, not the model.
2. **Checkable from what the environment can show.** If your golden talks about
   the database, something must be able to read the database back — otherwise
   the result comes back **Unchecked**, with that as the reason.
3. **Not passable without doing the work.** If saying the right words would
   satisfy the golden, the Task measures nothing.

## 5. Check before you spend

```bash
.venv/bin/crossbar validate --test my-test
```

Prints the roles, every Task, the total number of attempts, and a pre-flight
report: are the keys set, does each environment actually start, can anything be
read back. Typos surface here, not halfway through a paid run.

`crossbar run` does the same checks itself before it starts, so you cannot
forget. Failures stop it with nothing spent.

## 6. Run it

```bash
.venv/bin/crossbar run --test my-test
```

Or watch it happen:

```bash
.venv/bin/crossbar tui --test my-test
```

Press `r`. The **Run** tab shows which model is on which Task right now and the
whole queue behind it. The **Results** tab gives you the verdict, every attempt,
and a panel per attempt showing its checks, the judge's reasoning, and the
evidence it read.

## 7. Read the verdict

```
  CANDIDATE  my-model
             87.5% pass  [62.5 - 100.0]    $0.41 over this run

  BASELINE   frontier
             95.0% pass  [85.0 - 100.0]    $8.90 over this run

  DIFFERENCE   my-model vs frontier
               -7.5pp   [-31.2 to +12.5]
  NOT SIGNIFICANT at 95% (paired bootstrap, p=0.42, 8 paired tasks)
```

Read the **interval**, not the gap. `-31.2 to +12.5` covers zero, so the
difference has not been established — at this task count you cannot tell these
apart. That is a real finding, and it is the one most teams get wrong.

`CONFIDENCE` tells you what your sample can actually resolve. Under fifty tasks,
only large differences are real.

## 8. When something is Unchecked

```
  Unchecked: the golden asks about the order database, and no read-only
  probe can dump it.
```

Not a failure — we could not verify it, so there is no score. Fix what was
missing, then judge again without re-running anything:

```bash
.venv/bin/crossbar judge runs/<run-id>
```

## When things go wrong

| Symptom | Cause |
|---|---|
| `environment: could not start MCP server` | The `command` cannot be run. Use `${CROSSBAR_PYTHON}` for Python servers; check the server starts on its own. |
| Everything comes back **Unchecked** | Your golden needs evidence nothing can supply. The reason names it. Add a read-only tool that can show that state. |
| Everything comes back **Failed** | The agent could not run at all — usually an unreachable endpoint or a missing key. `crossbar doctor`. |
| Every result is "not significant" | You do not have enough data. More tasks, or more repeats. Crossbar is telling you the truth. |
| The agent describes the work instead of doing it | A real finding. Check the trajectory in the Results tab to confirm no tools were called. |

## A sensible way to use this

1. Run `crossbar demo` and read the output.
2. Write **five** tasks from real work. Small ones, prose goldens.
3. Set your current setup as the baseline.
4. `crossbar validate`, then run with `repeats: 3`.
5. Read the Unchecked reasons first — they are setup problems, not model problems.
6. Grow to twenty or more tasks before believing a close result.

The goal is one defensible sentence:

> *"We can move this workload to the cheaper model and lose nothing we can
> measure — and here is the interval that says so."*
