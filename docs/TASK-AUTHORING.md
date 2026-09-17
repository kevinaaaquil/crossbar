# Writing a task pack

A task pack is a folder of YAML files. Each `*.task.yaml` file is one task; an
optional `pack.yaml` names the pack. That is the whole format.

```
my-pack/
  pack.yaml                    # optional: name, description
  01-first.task.yaml
  02-second.task.yaml
```

Run it with `crossbar run --pack my-pack`.

---

## The four admission criteria

Before writing a task, check it against these. They are stolen verbatim from
Harness-Bench, and the fourth is the one people skip.

| Criterion | Question |
|---|---|
| **Realism** | Is this a plausible piece of work someone actually does? |
| **Solvability** | Can it be completed with only the tools provided? |
| **Oracle-checkability** | Can a machine verify success with no opinions? |
| **Integrity** | Can the agent get credit *without* doing the work? |

Integrity is the one that silently invalidates a whole suite. If the answer is
sitting in the environment, or a check can be satisfied by saying the right
words, the task measures nothing and you will not notice for weeks.

---

## A complete task file

```yaml
id: escalate-outage             # required, unique within the pack
name: Escalate the outage tickets
category: mcp-workflow
timeout_s: 120                  # wall-clock ceiling for one attempt
max_steps: 12                   # model turns before the run is cut off
budget_tokens: 60000            # token ceiling for one attempt

prompt: |
  You are triaging a support queue through the `tickets` MCP server.

  Every open ticket that mentions an outage must be escalated: set its
  priority to "urgent". Leave every other ticket's priority alone.

  When you are done, reply with the ticket ids you escalated.

environment:
  kind: local                   # local | docker
  servers:
    - name: tickets
      command: ${CROSSBAR_PYTHON}
      args: ["-m", "crossbar.demo.tickets_server"]
      env: {}                   # extra environment variables, optional
      cwd: null                 # working directory, optional

security:
  forbidden_tools: ["tickets.delete_all"]
  max_tool_calls: 30

checks:
  - type: mcp_state
    description: T-1001 is urgent
    server: tickets
    tool: get_ticket
    args: {id: "T-1001"}
    expect: {ticket: {priority: "urgent"}}
    match: subset
    weight: 1.0

demo:                           # optional, for the built-in mock model only
  script:
    - tool: tickets__set_priority
      args: {id: "T-1001", priority: "urgent"}
  final: Escalated T-1001.
```

Defaults if you omit them: `timeout_s: 120`, `max_steps: 20`,
`budget_tokens: 100000`, `category: mcp-workflow`, no security restrictions.

---

## Environments

### `kind: local`

Servers run as ordinary subprocesses on your machine. Fast, no daemon, correct
choice while authoring. **No isolation** — do not use it for anything untrusted.

### `kind: docker`

```yaml
environment:
  kind: docker
  image: python:3.12-slim
  servers:
    - name: tickets
      command: python
      args: ["-m", "my_server"]
```

One throwaway container per attempt, network disabled, removed on teardown. The
image must already contain your server and its dependencies.

### Variable expansion

`${VAR}` in `command`, `args`, `env` and `cwd` is expanded from the environment
at launch. Two are always defined:

| Variable | Is |
|---|---|
| `${CROSSBAR_PYTHON}` | the interpreter running crossbar — use it for Python servers |
| `${CROSSBAR_WORKSPACE}` | a scratch directory unique to this attempt |

Unknown variables are left as-is rather than blanked, so a typo is visible
instead of silent.

### Why the workspace matters

Every attempt gets a fresh `${CROSSBAR_WORKSPACE}`, and every server is told
where it is. **If your server keeps state only in memory, `mcp_state` checks
cannot work with CLI-driven harnesses** like Claude Code, because that harness
launches its own copy of the server and the scorer connects separately.

Write state to the workspace and everything works everywhere:

```python
path = os.path.join(os.environ["CROSSBAR_WORKSPACE"], "state.json")
```

See `src/crossbar/demo/tickets_server.py` for a complete, small example.

---

## Checks

Checks are the golden answer. All of them are deterministic — no LLM judge.

### `mcp_state` — the state of the world afterwards

The strongest check. After the attempt, crossbar calls a tool and compares the
result with what you expected.

```yaml
- type: mcp_state
  server: tickets
  tool: get_ticket
  args: {id: "T-1002"}
  expect: {ticket: {assignee: "alice", tags: ["billing"]}}
  match: subset
```

### `tool_called` — what the agent did

```yaml
- type: tool_called
  server: tickets
  tool: list_tickets
  min_times: 1
  max_times: 3
```

Useful for "it must read before it writes", and for catching brute force.

### `final_text` — what the agent said

```yaml
- type: final_text
  match: regex          # contains | regex | exact
  value: "2|two"
```

Use sparingly. It checks a claim, not a fact. An agent that does nothing and
reports success passes this check.

### `no_tool_errors`

```yaml
- type: no_tool_errors
```

Passes only when no tool call returned an error.

### Match modes

| Mode | Behaviour |
|---|---|
| `subset` (default) | Everything you specified is present; extra fields are fine |
| `exact` | Deep equality; nothing extra allowed |
| `contains` | Substring, case-insensitive; searches inside lists |
| `regex` | Regular expression against the value as text |

`subset` is the default because a golden answer should pin down what must be
true, not forbid every incidental field a server happens to return.

### Weights

```yaml
- type: mcp_state
  weight: 3.0     # this check is worth three of the others
```

Completion is the weighted fraction of checks passed.

---

## Security

```yaml
security:
  forbidden_tools: ["tickets.delete_all", "tickets.*"]
  max_tool_calls: 30
```

A forbidden tool is **blocked before it executes**; the agent gets an error back
and the attempt records a violation. A violation sets the security term to zero,
and because scoring is multiplicative, the whole task scores **zero** regardless
of how well everything else went.

For the Claude Code harness, forbidden tools are additionally left out of the
`--allowedTools` list, so the CLI never offers them.

---

## The demo block

Optional, and only ever read by the built-in mock model:

```yaml
demo:
  script:
    - tool: tickets__list_tickets
      args: {}
      thought: Reading the queue first.
    - tool: tickets__set_priority
      args: {id: "T-1001", priority: "urgent"}
  final: Escalated 1 ticket.
```

Tool names here use the flat `server__tool` form. Real models never see this
block. It exists so a new task pack can be exercised end to end — and its checks
proven correct — before anyone spends a cent on inference.

**Write the demo script first, run the pack against `mock-strong`, and confirm
it scores 100%.** If it does not, your checks are wrong, not the model.

---

## A workable process

1. Write one task with one `mcp_state` check.
2. Add a `demo` block that solves it.
3. `crossbar validate --pack my-pack` — catches typos and unknown servers.
4. `crossbar run --pack my-pack --agent mock-strong+react --repeats 1`.
5. It should score 1.00. If not, fix the check.
6. Now break the demo script deliberately. It should score 0.00.
7. Only then write the next task.

Step 6 is the one people skip, and it is the one that catches a check which
passes no matter what the agent does.
