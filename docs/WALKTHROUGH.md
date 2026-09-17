# Crossbar: the complete walkthrough

This guide assumes you have never used a terminal in your life. It explains
every step, every word, and every number. Follow it top to bottom and you will
have a working comparison of two AI setups on your own work, with an honest
answer about which one to use.

Nothing here can break your computer. Every command is safe and reversible.

---

## Part 0: What this program is for, in plain words

You are probably in one of these situations:

- You pay a lot of money for a big AI model (Claude, GPT, Gemini) and you wonder
  whether a cheaper or self-hosted one could do the same job.
- You fine-tuned your own small model and you want to know if it is good enough
  to actually use.
- Someone told you "model X is better than model Y" and you have no idea whether
  that is true *for your work*.

Crossbar answers that question with evidence instead of vibes.

### The one idea you need

An AI agent is two things bolted together:

1. **The model** — the brain. Claude Opus, GPT, your fine-tuned Llama.
2. **The harness** — everything wrapped around the brain: how it is prompted,
   how it uses tools, whether it double-checks its work, how it recovers from
   errors.

Most people only compare models. That is a mistake. Research measured a
**24-point** difference in success rate between the best and worst harness
running the *exact same models*. The wrapper matters as much as the brain.

So crossbar tests **combinations**. Model A with harness 1. Model A with
harness 2. Model B with harness 1. And so on. Each combination is called a
**cell**, and the grid of all of them is the **matrix**.

### The second idea you need

AI agents are inconsistent. Run the same task twice and you can get a different
result. Research found that roughly **half** of the variation you see in a single
benchmark run is random noise, not real ability.

That means a single run proves nothing. Crossbar runs each combination many
times and reports a **range** rather than a single number. When two ranges
overlap, crossbar refuses to declare a winner — and tells you so. That refusal
is a feature. It stops you from switching models based on noise.

---

## Part 1: Getting set up

### Step 1.1 — Open the terminal

**On a Mac:** press `Command` and `Space` together, type the word `Terminal`,
press `Enter`. A window with text appears. That is the terminal.

**On Windows:** press the Windows key, type `PowerShell`, press `Enter`.

**On Linux:** press `Ctrl` + `Alt` + `T`.

Everything below is typed into this window. After typing a line, press `Enter`
to run it. You can copy and paste — that is normal and encouraged.

### Step 1.2 — Check you have Python

Crossbar is written in Python. Type this and press Enter:

```bash
python3 --version
```

You should see something like `Python 3.12.4`. Any version **3.11 or higher**
is fine.

**If you see an error or a version below 3.11:** go to
[python.org/downloads](https://www.python.org/downloads/), download the latest
version, install it (just click through the installer), then close and reopen
the terminal and try again.

### Step 1.3 — Go to the crossbar folder

You need to tell the terminal which folder to work in. If crossbar is in your
`dev` folder:

```bash
cd ~/dev/crossbar
```

`cd` means "change directory". The `~` means your home folder.

To check you are in the right place, type:

```bash
ls
```

You should see names including `README.md`, `crossbar.yaml`, `src`, `taskpacks`,
and `tests`. If you see something else, you are in the wrong folder.

### Step 1.4 — Install it

Copy and paste these two lines, one at a time:

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

The first line creates a private, isolated workspace for crossbar so it cannot
interfere with anything else on your computer. The second installs it there.

It will print a lot of text. That is normal. It is finished when you get your
prompt back (the line ending in `$` or `%`).

> **Windows note:** use `.venv\Scripts\pip` instead of `.venv/bin/pip`, and
> `.venv\Scripts\crossbar` instead of `.venv/bin/crossbar`, everywhere below.

### Step 1.5 — Check the installation

```bash
.venv/bin/crossbar doctor
```

You should see a report like this:

```
crossbar doctor

  python       3.12.4  (/Users/you/dev/crossbar/.venv/bin/python)
  docker       not found - only environments of kind: local will work
  claude CLI   /Users/you/.local/bin/claude

  config       crossbar.yaml
    - mock-strong            ready (built-in demo backend, no key needed)
    - mock-weak              ready (built-in demo backend, no key needed)
```

**Everything says "ready"? You are done setting up.** Docker and the Claude CLI
are optional — you only need them later, and the report tells you what each one
unlocks.

---

## Part 2: Your first run

### Step 2.1 — Run it

```bash
.venv/bin/crossbar run
```

That is it. This uses the built-in demo: two pretend AI models working through
four realistic customer-support tasks. It takes a few seconds and needs no
account, no key, and no internet.

You will see lines scrolling past as each attempt finishes:

```
  [  1/80 ] mock-strong+react                escalate-outage      pass
  [  2/80 ] mock-strong+react                escalate-outage      pass
  [  3/80 ] mock-strong+react                route-billing        pass
```

Then the report appears.

### Step 2.2 — Reading the verdict card

This is the part that matters. Here it is, line by line.

```
CROSSBAR VERDICT                              Support triage, 4 tasks
```

Which set of tasks was used, and how many there were.

```
  BASELINE   mock-strong+react
             95.0% pass  [85.0 - 100.0]    $1.02 over this sweep
```

**Baseline** is your current setup — the thing you are considering moving away
from. Here it succeeded on 95% of attempts and cost $1.02.

The two numbers in square brackets are the **confidence interval**. Read it as:
"the true success rate is somewhere between these two numbers." A narrow range
means you measured carefully. A wide range means you need more data.

```
  WINNER     mock-weak+react-plus-verify
             95.0% pass  [85.0 - 100.0]    $0.01 over this sweep
```

**Winner** is crossbar's recommendation: *the cheapest setup it cannot prove is
worse than your baseline.* Note that word, "cannot prove". More on that next.

```
  QUALITY DELTA   mock-weak+react-plus-verify vs baseline
                  +0.0pp   [-15.0 to +15.0]
  NOT SIGNIFICANT at 95% (paired bootstrap, p=1.000 Holm-corrected)
```

**Quality delta** is the difference in success rate. `+0.0pp` means the
challenger scored the same as the baseline on these tasks.

**But look at the range:** `-15.0 to +15.0`. The true difference could be as
bad as 15 points worse, or as good as 15 points better. Because that range
covers zero, no difference **has been proven** in either direction. That is
what `NOT SIGNIFICANT` means.

This is the single most useful line crossbar prints, and almost no other tool
prints it. Most teams switching models today are reacting to a gap that is
statistically indistinguishable from random luck.

```
  >> You cannot distinguish these at your task count.
     Savings: $1.00 over this sweep (99%).
```

The recommendation in one sentence, with the money attached.

```
  CONFIDENCE  Low. 4 tasks is well below the ~150 needed to detect a 3-point
              gap. Add tasks, or treat this as directional only.
```

**Honesty about the limits of the experiment.** Four tasks is a demo. For a real
decision you want dozens or hundreds of tasks. Crossbar tells you this rather
than letting you over-trust a small sample.

### Step 2.3 — Reading the matrix

```
CELL                         PASS       95% CI          COST  $/SUCCESS  NOTE
------------------------------------------------------------------------------
mock-strong+react-plus-ve  100.0%  [100.0 - 100.0]    1.20      $0.06  ~ tied with baseline
mock-weak+react-plus-veri   95.0%  [ 85.0 - 100.0]    0.01      $0.00  ~ tied with baseline
mock-strong+react           95.0%  [ 85.0 - 100.0]    1.02      $0.05  baseline
mock-weak+react             60.0%  [ 30.0 -  90.0]    0.01      $0.00  worse
```

One row per combination.

- **CELL** — the combination, written `model+harness`.
- **PASS** — what share of attempts fully succeeded.
- **95% CI** — the confidence range, again.
- **COST** — total money spent on that combination during this run.
- **$/SUCCESS** — cost divided by successes. The number that actually matters:
  a cheap setup that fails half the time is not cheap.
- **NOTE** — `~ tied with baseline` means the difference is not proven.
  `worse` or `better` means it is.

Look at the two `mock-weak` rows. Same model. The only difference is the
harness — one of them double-checks its work before finishing. That one turn
took it from 60% to 95%. **The wrapper was worth 35 points.** Meanwhile the
strong model gained 5 points from the same change, because it was barely making
mistakes to catch.

That is the entire thesis of this tool in four rows: cheap models are far more
sensitive to harness design than expensive ones, so harness engineering is how a
cheap model closes the gap — and you can only find the right combination by
sweeping.

### Step 2.4 — Reading the failure taxonomy

```
FAILURE TAXONOMY

  mock-weak+react  (20 rollouts)
    Contract / format          8  (40% of rollouts)
```

When a run fails, crossbar classifies *how*:

| Category | Plain English |
|---|---|
| **Contract / format** | It did the work but produced the wrong shape of output. |
| **Tool / recovery** | A tool errored and it could not recover. |
| **Evidence / grounding** | The state was right but it claimed something untrue. |
| **Artifact commitment** | It described the work convincingly and never did it. |
| **State / continuation** | It ran out of steps, time, or budget partway through. |
| **Security violation** | It used a tool the task forbids. Scores zero, always. |

This tells you what to fix. "Contract/format" failures mean your harness needs
output validation, not a better model.

You may also see:

```
  NOISE  mock-weak+react: 71% of score variance is run-to-run noise rather
         than task difficulty. Add repeats before trusting this cell's ranking.

  PRUNE  Tasks that every cell passed or every cell failed, so they separate
         nothing and cost money: route-billing
```

The first says your results are dominated by randomness — run more repeats. The
second lists tasks that every setup handled identically, so they add cost
without adding information. Delete them or make them harder.

### Step 2.5 — Where the results went

```
Results   runs/sweep.json     every number, machine-readable
Report    runs/report.md      the report you just read, as a file to share
Traces    runs/traces/        a full recording of every single attempt
```

The traces are the receipts. Each file records every step the agent took, every
tool it called, what came back, and what it finally said. When you disagree with
a score, this is where you look.

---

## Part 3: The interactive app

The report is a one-shot printout. The app is for exploring.

```bash
.venv/bin/crossbar tui
```

You will see three tabs across the top.

**Setup** — your models, your harnesses, your tasks, and the matrix that is
about to run. You can change the number of repeats here.

**Run** — a progress bar and a live log, one line per attempt.

**Results** — the verdict card at the top, a list of every attempt in the
middle, and a detail panel at the bottom.

### Keys

| Key | Does |
|---|---|
| `r` | Run the sweep |
| `1` `2` `3` | Jump to Setup / Run / Results |
| Arrow keys | Move around a list |
| `Enter` | Open the selected attempt in the detail panel |
| `q` | Quit |

### The detail panel is the good part

Select any attempt and press `Enter`. You see exactly what happened:

```
  mock-strong+react   escalate-outage   repeat 0
  status completed   score 1.00 (security 1 x completion 1.00 x process 1.00)

  checks
    [PASS] T-1001 (full outage) is urgent
    [PASS] T-1004 (partial outage) is urgent
    [PASS] the billing ticket was left alone

  trajectory
    step 0: Finding every ticket that mentions an outage.
    call tickets.search_tickets(query='outage') -> ok
    call tickets.set_priority(id='T-1001', priority='urgent') -> ok
    call tickets.set_priority(id='T-1004', priority='urgent') -> ok

  final answer: Escalated T-1001 and T-1004 to urgent.
```

Every score in crossbar can be traced to the actions that produced it. Nothing
is a black box.

---

## Part 4: Using your own models

The demo used pretend models. Here is how to plug in real ones.

### Step 4.1 — Open the configuration file

The file is `crossbar.yaml` in the crossbar folder. Open it with any text
editor — TextEdit, Notepad, VS Code, anything. It is plain text with comments
explaining each part.

### Step 4.2 — Add your model

Find the `models:` section. Add an entry using the shape that matches your
situation.

**If your model runs on your own machine or server** (Ollama, vLLM, LM Studio,
a fine-tune you host, or most paid APIs — nearly everything speaks this format):

```yaml
  - id: my-model
    provider: openai
    model: llama-3.3-70b
    base_url: http://localhost:11434/v1
    price: {input_per_mtok: 0.20, output_per_mtok: 0.60}
```

- `id` — any short name you choose. You will see it in the results.
- `provider: openai` — the *format* it speaks, not the company. Nearly every
  model server speaks this format, including local ones.
- `model` — the model name your server expects.
- `base_url` — the web address of your server. Ollama is usually
  `http://localhost:11434/v1`; vLLM is usually `http://localhost:8000/v1`.
- `price` — what you pay per million tokens, so crossbar can compute cost. Put
  `0` for a model you host yourself, or your electricity estimate.

**If you use Claude through Anthropic's API:**

```yaml
  - id: opus
    provider: anthropic
    model: claude-opus-5
    api_key_env: ANTHROPIC_API_KEY
    price: {input_per_mtok: 15.0, output_per_mtok: 75.0}
```

`api_key_env` is the *name of an environment variable*, not the key itself.
Never paste a key into this file — it is easy to accidentally share. Instead,
before running crossbar, type this in the terminal (with your real key):

```bash
export ANTHROPIC_API_KEY=sk-ant-your-key-here
```

**If you use Claude Code (the `claude` command):**

```yaml
  - id: claude-code-opus
    provider: claude-cli
    model: opus
```

This runs the actual `claude` program with its own real behaviour, giving it
only your task's tools. It uses whatever login `claude` already has, so there is
no key to configure. It must be paired with the `claude-code` harness:

```yaml
harnesses:
  - id: claude-code
    kind: claude-code
```

### Step 4.3 — Say which one is the baseline

Near the bottom of the file:

```yaml
run:
  repeats: 5
  baseline: opus+claude-code
```

The **baseline** is what you use today — the thing you are considering replacing.
Write it as `modelid+harnessid`, using the `id` values you chose. Everything is
measured against it.

**repeats** is how many times each combination attempts each task. Five or more
is the point at which estimates start to settle down. More repeats means more
confidence and more cost.

### Step 4.4 — Check before you spend

```bash
.venv/bin/crossbar validate
```

This reads your file, checks it makes sense, and prints exactly what it would
run — including the total number of attempts. If you made a typo, this tells you
now instead of halfway through a paid run.

```bash
.venv/bin/crossbar doctor
```

This confirms your keys are visible and your tools are installed.

### Step 4.5 — Run for real

```bash
.venv/bin/crossbar run
```

To be economical, start with one repeat and a couple of tasks:

```bash
.venv/bin/crossbar run --repeats 1
```

And to have crossbar spend its budget intelligently — three attempts everywhere,
then extra attempts only where the answer is still unclear:

```bash
.venv/bin/crossbar run --adaptive --max-repeats 10
```

Combinations that are clearly losing get eliminated early and stop costing you
money. Combinations that are too close to call get more evidence. Same
confidence, a fraction of the compute.

---

## Part 5: Using your own tasks

The demo tasks are about a support desk. Yours will be about your business. This
is the part that makes the answer actually about *you*.

A short version follows; the full reference is
[TASK-AUTHORING.md](TASK-AUTHORING.md).

### What a task needs

1. **An instruction** — what you want the agent to do, in English.
2. **An environment** — the MCP server(s) the agent is allowed to use.
3. **Checks** — how a machine can tell, with no opinions involved, whether it
   worked.

### The smallest possible task

Make a folder called `my-tasks`, and in it a file called
`first.task.yaml`:

```yaml
id: my-first-task
prompt: |
  Using the tickets tools, set ticket T-1001 to urgent priority.

environment:
  kind: local
  servers:
    - name: tickets
      command: ${CROSSBAR_PYTHON}
      args: ["-m", "crossbar.demo.tickets_server"]

checks:
  - type: mcp_state
    server: tickets
    tool: get_ticket
    args: {id: "T-1001"}
    expect: {ticket: {priority: "urgent"}}

demo:                       # only the built-in mock models read this
  script:
    - tool: tickets__set_priority
      args: {id: "T-1001", priority: "urgent"}
  final: Set T-1001 to urgent.
```

Run it:

```bash
.venv/bin/crossbar run --pack my-tasks
```

The `checks` section is the important part. It says: *after the agent is done,
ask the server for ticket T-1001, and confirm its priority is now "urgent".*
No judgement, no second AI grading the first — just a fact, checked.

The `demo` section is different: it is a hint for the **built-in mock models
only**, describing how a competent agent would solve this. Real models never see
it. Include it while you are still testing with mocks — without it they will do
nothing and every cell will score zero. Once you point crossbar at real models,
it is ignored.

Writing the demo first is also the best way to prove your checks are right: run
the task against `mock-strong` and it should score 100%. If it does not, your
check is wrong, not the model.

### The four kinds of check

| Type | Checks that... |
|---|---|
| `mcp_state` | the world ended up in the right state (**the good one**) |
| `tool_called` | a particular tool was used (or not used too often) |
| `final_text` | the agent's written answer contains something |
| `no_tool_errors` | nothing errored along the way |

Prefer `mcp_state`. It checks reality. `final_text` only checks that the agent
*said* the right thing, and agents are very good at saying the right thing while
having done nothing.

### Blocking dangerous tools

```yaml
security:
  forbidden_tools: ["tickets.delete_all"]
  max_tool_calls: 30
```

A forbidden tool is blocked before it runs, and the attempt scores **zero** no
matter how well everything else went. That multiplication is deliberate: a
setup that occasionally deletes your data is not "mostly fine".

### The rule that quietly ruins evaluations

The agent must not be able to get credit without doing the work. If the answer
is discoverable in the environment, or a protected file can be edited, or a
check can be satisfied by saying the right words, your task is measuring
nothing. Check every task against that before you trust its numbers.

---

## Part 6: When something goes wrong

**`command not found: crossbar`**
You are missing the `.venv/bin/` prefix, or you are in the wrong folder. Run
`cd ~/dev/crossbar` first, then use `.venv/bin/crossbar`.

**`config file not found: crossbar.yaml`**
You are in the wrong folder. `cd ~/dev/crossbar`.

**`error: <file>: check references unknown server 'x'`**
In a task file, a check names a server that the `environment` section does not
define. The names must match exactly.

**Every attempt fails with `environment: could not start MCP server`**
The `command` in your task cannot be run. Use `${CROSSBAR_PYTHON}` for Python
servers — it always points at the right interpreter. For others, give the full
path, and check the server runs on its own first.

**`no key: set ANTHROPIC_API_KEY in your environment`**
Run `export ANTHROPIC_API_KEY=sk-ant-...` in the same terminal window, then run
crossbar again. The setting lasts until you close the window.

**Every run scores zero and the failure mode is "artifact commitment"**
The model is describing the work instead of doing it. That is a real finding,
and usually a harness problem: try the `react-plus-verify` harness, or make the
task prompt insist that tools be used.

**Everything is "~ tied with baseline" and nothing is conclusive**
You do not have enough data. Increase `repeats`, add more tasks, or run with
`--adaptive --max-repeats 10`. Crossbar is telling you the truth: at this sample
size, the difference cannot be established.

**The terminal is frozen / I want to stop**
Press `Ctrl` + `C`. Everything finished so far is already written to `runs/`.

---

## Part 7: A sensible way to use this

1. **Start with the demo.** Confirm the machinery works before involving money.
2. **Write five tasks from your real work.** Small ones. `mcp_state` checks.
3. **Set your current setup as the baseline.** That is the thing you might
   replace.
4. **Add one cheap challenger and two harnesses.** Four cells is plenty.
5. **Run with `--repeats 3` first.** Confirm the tasks behave before scaling up.
6. **Grow to twenty or more tasks before believing a close result.** Under fifty
   tasks, only large differences are real. Crossbar will keep saying so.
7. **Read the failure taxonomy before changing models.** Often the fix is a
   harness change, and the cheap model was fine all along.
8. **Prune the tasks crossbar flags as uninformative.** They cost money and
   settle nothing.

The goal is not a leaderboard. The goal is one defensible sentence:

> "We can move this workload to the cheaper setup and lose nothing we can
> measure, and here is the interval that says so."
