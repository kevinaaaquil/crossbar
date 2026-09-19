# Crossbar: the complete walkthrough

This guide assumes you have never used a terminal. It explains every step and
every number on screen. Follow it top to bottom and you will have a defensible
answer about whether your own model can do your work.

Nothing here can break your computer. Every command is safe and reversible.

---

## Part 0: What this is for

You are probably in one of these situations:

- You pay a lot for a frontier model and wonder whether something cheaper could
  do the same job.
- You fine-tuned your own model and need to know if it is good enough to use.
- Someone told you model X beats model Y and you have no idea whether that is
  true **for your work**.

Crossbar answers that with evidence instead of vibes.

### The two ideas you need

**One: agents are inconsistent.** Run the same task twice and you can get
different results. Roughly half the variation you see in a single benchmark run
is randomness, not ability. So a single run proves nothing. Crossbar runs each
task several times and reports a **range**. When two ranges overlap it refuses
to declare a winner — and that refusal is the point. It stops you switching
models because of noise.

**Two: you already know what a correct answer looks like.** You do not need to
write a test suite. You write down, in plain English, what a correct result
means. Crossbar works out what to inspect.

### Two ways to use it

| Mode | You connect | Answers |
|---|---|---|
| **Single** | One model | *Is this model good enough at all?* |
| **Comparison** | Two models | *Is my model as good as the expensive one, and what would switching save?* |

Single mode is the ordinary eval: how good is this model at these tasks. It is
usually the earlier question, and the place to start.

---

## Part 1: Setting up

### Step 1.1 — Open the terminal

**Mac:** press `Command` + `Space`, type `Terminal`, press Enter.
**Windows:** press the Windows key, type `PowerShell`, press Enter.
**Linux:** press `Ctrl` + `Alt` + `T`.

Everything below is typed into this window. Press Enter after each line. Copying
and pasting is normal and encouraged.

### Step 1.2 — Check you have Python

```bash
python3 --version
```

You want **3.11 or higher**. If you see an error or an older version, install
from [python.org/downloads](https://www.python.org/downloads/), then close and
reopen the terminal.

### Step 1.3 — Go to the crossbar folder and install

```bash
cd ~/dev/crossbar
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

`cd` means change directory. The first command creates a private workspace so
crossbar cannot interfere with anything else; the second installs it there.

> **Windows:** use `.venv\Scripts\...` instead of `.venv/bin/...` everywhere.

### Step 1.4 — Check it worked

```bash
.venv/bin/crossbar doctor
```

```
crossbar doctor

  python       3.12.4  (/Users/you/dev/crossbar/.venv/bin/python)
  docker       not found — only kind: local will work
  connectors   mcp
```

Docker is optional. If the Python line looks right, you are set up.

---

## Part 2: See it work before connecting anything

```bash
.venv/bin/crossbar demo
```

No account, no key, no internet. Two scripted stand-in models run four
support-desk tasks: one actually does the work through a real tool server, the
other only describes what it would do. A judge reads the resulting state.

You get a real report from a fake run, so you know what you are aiming at.

### Reading the verdict card

```
CROSSBAR VERDICT                              4 tasks judged

  BASELINE   idle-demo
             0.0% pass  [0.0 - 0.0]    $0.04 over this run

  CANDIDATE  diligent-demo
             100.0% pass  [100.0 - 100.0]    $0.00 over this run
```

**Candidate** is the model you are assessing. **Baseline** is what you compare
it against. The numbers in square brackets are the **confidence interval** —
"the true rate is somewhere in here". Narrow means you measured carefully. Wide
means you need more data.

```
  DIFFERENCE   diligent-demo vs idle-demo
               +100.0pp   [+100.0 to +100.0]
  SIGNIFICANT at 95% (paired bootstrap, p=0.000, 4 paired tasks)
```

The difference, **with a range**. If that range covers zero, the difference has
not been established — you cannot tell the models apart at this task count. That
line is the most useful thing crossbar prints and almost no other tool prints
it. Most teams switching models are reacting to a gap that is indistinguishable
from luck.

```
  CONFIDENCE  Low. 4 tasks is well below the ~150 needed to detect a 3-point
              gap. Add tasks, or treat this as directional only.
```

Honesty about the limits of the experiment. Four tasks is a demo.

### Where it all went

```
runs/demo/
  run.json                  every attempt, machine-readable
  report.md                 the report, shareable
  plans/<task>.json         what the judge decided to check
  attempts/<id>/
    trajectory.json         what the agent actually did
    evidence.json           what was captured afterwards
    judgement.json          the verdict and the judge's reasoning
```

These are the receipts. Every number traces back to the tool calls that produced
it.

---

## Part 3: Connect your own models

Open `roster.yaml` in any text editor.

### If your model runs on your own machine or server

Ollama, vLLM, LM Studio, a fine-tune you host, and most paid APIs all speak the
same format:

```yaml
models:
  - id: my-model
    provider: openai                    # the API *format*, not the company
    model: qwen3
    base_url: http://localhost:8000/v1
    price: {input_per_mtok: 0.20, output_per_mtok: 0.60}
```

- `id` — any short name. You will see it in results.
- `provider: openai` — the format it speaks. Nearly everything speaks it.
- `base_url` — Ollama is usually `http://localhost:11434/v1`; vLLM
  `http://localhost:8000/v1`.
- `price` — what you pay per million tokens, so the report can state costs. Zero
  for something you host yourself.

### If you use Claude through the Anthropic API

```yaml
  - id: frontier
    provider: anthropic
    model: claude-opus-5
    api_key_env: ANTHROPIC_API_KEY
    price: {input_per_mtok: 15.0, output_per_mtok: 75.0}
```

`api_key_env` is the **name of an environment variable**, never the key itself —
it is far too easy to share a file by accident. Set it in the terminal before
running:

```bash
export ANTHROPIC_API_KEY=sk-ant-your-key-here
```

That lasts until you close the window.

### Assign the roles

**For a single-model assessment:**

```yaml
roles:
  candidate: my-model
  judge: frontier          # required — see below
```

**For a comparison:**

```yaml
roles:
  candidate: my-model
  baseline: frontier
```

### About the judge

Something has to decide whether each attempt was correct. That is the **judge**,
and it is a model you connect.

In a comparison, leaving `judge` unset means the baseline does it. Its attempts
are blinded — the judge is never told whose work it is reading — but it is still
grading its own work, and the report says so on every verdict. For a decision
that matters, connect a third model.

In a single-model run there is nothing to fall back to, so you **must** name a
judge, and it **cannot be the model being assessed**. Crossbar refuses that
outright: with no second opinion, a model marking its own homework is not
evidence.

---

## Part 4: Write your own tasks

The demo is about a support desk. Yours will be about your work. Short version
here; the full reference is [TASK-AUTHORING.md](TASK-AUTHORING.md).

A test is a folder with three kinds of file:

```
my-test/
  test.yaml            name, how many attempts per task
  env.yaml             what the agent can touch
  01-first.task.yaml   the work, and what correct means
```

```yaml
# my-test/test.yaml
name: Refund checks
repeats: 3               # each task attempted three times
environment: env.yaml
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
  Find every order charged twice this month and refund the duplicate
  charge. Reply with the order ids you refunded.

golden: |
  Orders 4471 and 4488 each have one refund recorded against them, for the
  duplicate charge only. No other order has a refund. Order 4490 was charged
  twice but is already disputed, so it was left alone.
```

**That is the whole authoring burden.** No schema, no assertions, no verifier.

### Why `repeats` matters

Two models both score 50%:

- One passes tasks A and B **every time**, fails C and D **every time** — capable, with a clear gap.
- One passes every task **about half the time** — unreliable at everything.

Same number, completely different decisions. Repeats are how you tell them
apart. Three is a floor; five or more settles down.

### Three rules for a task worth trusting

1. **Solvable with the tools you provided.** Otherwise you are measuring your
   setup, not the model.
2. **Checkable from what the environment can show.** If your golden talks about
   the database, something must be able to read the database back.
3. **Not passable by talking.** If saying the right words satisfies the golden,
   the task measures nothing. Agents are very good at describing work
   convincingly and then not doing it.

---

## Part 5: Check, then run

```bash
.venv/bin/crossbar validate --test my-test
```

This prints the roles, every task, how many attempts would run, and a
**pre-flight report**: are the keys set, does each environment actually start,
is there anything read-only to check against.

```
PRE-FLIGHT

  [PASS] roles          candidate my-model, baseline frontier
  [FAIL] model-keys     frontier needs ANTHROPIC_API_KEY to be set
  [WARN] judge          no judge assigned, so the baseline will grade its
                        own attempts — blinded, but a conflict of interest
  [PASS] environment    1 connector(s), 10 tools
  [PASS] probes         4 read-only probe(s)

  24 attempts would run.
```

`crossbar run` does the same checks itself before starting, so you cannot
forget. **Failures stop it with nothing spent.** Warnings are your call.

Then:

```bash
.venv/bin/crossbar run --test my-test
```

---

## Part 6: The interactive app

```bash
.venv/bin/crossbar tui --test my-test
```

Four tabs:

| Tab | Shows |
|---|---|
| **1 Models** | Your models and their roles, and a warning if the baseline is judging itself |
| **2 Tests** | Your tests and tasks, and how many will be judged |
| **3 Run** | Which model is on which task **right now**, and the whole queue behind it |
| **4 Results** | The report, every attempt, and a panel per attempt |

Press `r` to run, `1`–`4` for tabs, `d` to zip the results, `q` to quit.

The **Results** detail panel is the good part. Select any attempt:

```
  my-model · candidate · Refund checks · refund-duplicates · repeat 0

  OUTCOME     graded    score 1.00

  CHECKS
    state-matches-golden  pass   read the order store and compared it
                                 with the golden
```

Every score traces back to what was actually inspected. Nothing is a black box.

---

## Part 7: When something goes wrong

| What you see | What it means |
|---|---|
| `command not found: crossbar` | Missing the `.venv/bin/` prefix, or wrong folder. `cd ~/dev/crossbar` first. |
| `environment: could not start MCP server` | The `command` cannot be run. Use `${CROSSBAR_PYTHON}` for Python servers; check the server starts on its own. |
| Everything is **Unchecked** | Your golden needs evidence nothing can supply. The reason names it. Add a read-only tool that shows that state, then `crossbar judge runs/<id>` — no need to re-run. |
| Everything is **Failed** | The agent could not run — usually an unreachable endpoint or a missing key. Run `crossbar doctor`. |
| Everything is "not significant" | Not enough data. More tasks, or more repeats. Crossbar is telling you the truth. |
| The agent describes the work instead of doing it | A real finding. Open the trajectory and confirm no tools were called. |
| You want to stop | `Ctrl` + `C`. Everything finished so far is already on disk. |

### "Unchecked" is not a failure

It means *we could not verify this*, which is different from *the model got it
wrong*. So it gets no score rather than a zero, and the reason tells you what to
fix. Fix it, then re-judge without re-running anything:

```bash
.venv/bin/crossbar judge runs/<run-id>
```

---

## Part 8: A sensible way to use this

1. Run `crossbar demo` and read the output.
2. Write **five** tasks from real work. Small ones, prose goldens.
3. Start in **single mode** — is my model any good at this at all?
4. `crossbar validate`, then run with `repeats: 3`.
5. Read the **Unchecked** reasons first. They are setup problems, not model problems.
6. Add a baseline and compare once single mode says the model is worth comparing.
7. Grow past twenty tasks before believing a close result. Under fifty, only
   large differences are real — and crossbar will keep saying so.

The goal is one defensible sentence:

> *"We can move this workload to the cheaper model and lose nothing we can
> measure — and here is the interval that says so."*
