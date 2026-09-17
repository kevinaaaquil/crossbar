# crossbar

**A migration test bench for agents.** Point it at your own tasks. It runs every
combination of *model* and *harness* in a sandboxed environment, repeats each one
until the result is statistically solid, and tells you which combination is
cheapest at the quality bar you set — and whether you can trust the difference.

> A score belongs to the pair (model + harness), never to the model alone.
> Harness-Bench measured a 23.8-point spread across harnesses on the *same*
> models. That spread is why this exists.

This is the MVP. The environment is MCP servers; the tasks are yours.

---

## Try it in one minute

No API key, no network, no Docker needed — crossbar ships with a mock backend
and a demo task pack so you can see the whole thing work first.

```bash
cd crossbar
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/crossbar run          # prints a verdict card
.venv/bin/crossbar tui          # the same thing, interactive
```

New to this? Read **[docs/WALKTHROUGH.md](docs/WALKTHROUGH.md)** — a complete,
plain-language guide that assumes nothing.

## What comes out

```
CROSSBAR VERDICT                              Support triage, 4 tasks

  BASELINE   mock-strong+react
             95.0% pass  [85.0 - 100.0]    $1.02 over this sweep

  WINNER     mock-weak+react-plus-verify
             95.0% pass  [85.0 - 100.0]    $0.01 over this sweep

  QUALITY DELTA   mock-weak+react-plus-verify vs baseline
                  +0.0pp   [-15.0 to +15.0]
  NOT SIGNIFICANT at 95% (paired bootstrap, p=1.000 Holm-corrected)

  >> You cannot distinguish these at your task count.
     Savings: $1.00 over this sweep (99%).

  CONFIDENCE  Low. 4 tasks is well below the ~150 needed to detect a 3-point
              gap. Add tasks, or treat this as directional only.
```

Three things no other tool prints: a **money** number, **non-significance stated
as a finding**, and a **prescribed harness fix** drawn from the failure taxonomy.

## The five commands

| Command | What it does |
|---|---|
| `crossbar run` | Run the sweep, print the verdict, write results to `runs/` |
| `crossbar tui` | The same sweep in an interactive terminal app, with a trace viewer |
| `crossbar validate` | Check your roster and task pack before spending money |
| `crossbar doctor` | Check this machine: Python, Docker, the Claude CLI, your API keys |
| `crossbar init` | Drop a starter `crossbar.yaml` and demo task pack into a directory |

## Connecting your own models

Edit `crossbar.yaml`. Three kinds of backend are supported:

```yaml
models:
  # Any OpenAI-compatible endpoint: vLLM, Ollama, LM Studio, Together,
  # Fireworks, OpenRouter, your own fine-tune behind your own server.
  - id: my-finetune
    provider: openai
    model: my-org/my-finetune
    base_url: http://localhost:8000/v1
    api_key_env: LOCAL_API_KEY
    price: {input_per_mtok: 0.20, output_per_mtok: 0.60}

  # The Anthropic API directly (the model on its own, no harness).
  - id: opus
    provider: anthropic
    model: claude-opus-5
    api_key_env: ANTHROPIC_API_KEY
    price: {input_per_mtok: 15.0, output_per_mtok: 75.0}

  # Claude Code, driven through its own CLI, keeping its native behaviour.
  - id: claude-code-opus
    provider: claude-cli
    model: opus
```

## Harnesses on the sweep axis

| kind | what it is |
|---|---|
| `react` | A plain tool-calling loop. The control condition. |
| `react` + `verify: true` | The same loop with one forced self-check turn before finishing. |
| `single-shot` | One call, no tools. The floor. |
| `claude-code` | The real `claude` CLI, with only your task's MCP servers attached. |

Harnesses are never normalised into a common internal policy — that would
destroy the thing being measured. The task, budget, timeout and evaluator are
fixed; each harness stays itself.

## How a task is scored

```
TaskScore = Security × Completion × Process
```

Multiplicative on purpose. One security violation zeroes the task no matter how
good the output looked. Every term is deterministic — no LLM judge in the MVP —
so the same trace always yields the same number.

## What the statistics do

- Per-cell pass rate with a **95% bootstrap CI**, tasks as the resampling unit
- **Paired bootstrap** against the baseline, because both arms ran the same tasks
- **McNemar's exact test** as a cross-check on the discordant pairs
- **Holm–Bonferroni** correction, because a matrix means many comparisons
- **Variance decomposition**: how much of the spread is task difficulty and how
  much is seed noise
- Tasks that separate nothing are flagged so you can prune them

Overlapping intervals are marked `~` and are never ranked against each other.

## Documentation

| File | For |
|---|---|
| [docs/WALKTHROUGH.md](docs/WALKTHROUGH.md) | Complete beginner's guide. Start here. |
| [docs/TASK-AUTHORING.md](docs/TASK-AUTHORING.md) | Writing your own task pack |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | How the pieces fit, and why |
| [docs/LIMITATIONS.md](docs/LIMITATIONS.md) | What this MVP deliberately does not do |

## Tests

```bash
.venv/bin/pytest          # 427 tests, no network, no API key, no Docker
```

Every layer is exercised against real subprocesses: a real MCP server over
stdio, a real harness loop, a real scorer. The model and the `claude` CLI are
the only things replaced by scripted stand-ins, which is what makes the suite
deterministic.
