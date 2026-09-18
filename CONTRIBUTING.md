# Contributing to crossbar

Thanks for looking. This document tells you what the project cares about, so
your time is not wasted on a patch that gets bounced for a reason nobody wrote
down.

## Before you start

**Licence.** crossbar is under the [PolyForm Noncommercial License
1.0.0](LICENSE.md). It is source-available, not OSI open source: noncommercial
use is free, commercial use is not permitted. By contributing you agree your
contribution is licensed under the same terms. If that does not work for you,
please do not contribute.

**Open an issue first for anything structural.** A new harness, a new check
type, a change to the scoring formula, a new environment backend — describe it
before you build it. Small fixes and documentation need no ceremony.

## Setting up

```bash
git clone <your fork>
cd crossbar
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest          # should be green before you change anything
```

The suite needs no network, no API key and no Docker. If it needs any of those
after your change, that is a bug in the change.

## The rules that actually get enforced

### 1. Tests first, always

Every module in this repository was written test-first, and it shows in the
design. Write the failing test, watch it fail for the right reason, then write
the smallest code that passes it.

A pull request that adds behaviour without a test that would fail without it
will be asked for the test. This is not bureaucracy: a test written afterwards
proves only that the code does what the code does.

### 2. Do not mock the integration surface

The point of this suite is that it exercises the real thing:

**Stubbed** — the model (`ScriptedProvider`, `MockProvider`), the `claude` CLI
(a fake binary replaying stream-json), `docker` (a stub shell script). These are
stubbed because they cost money, need credentials, or need a daemon.

**Never stubbed** — MCP servers, the harness loop, the environment, the scorer,
the statistics. MCP servers in tests are real subprocesses speaking real
JSON-RPC over real pipes. If your test mocks one, it is testing your mock.

### 3. Determinism is a feature, not a nicety

- Seed anything random, and seed it reproducibly across processes. Use
  `zlib.crc32`, never `hash()` — Python salts string hashing per process.
- No wall-clock dependence in logic. Inject a `clock` callable the way
  `ReactHarness` does.
- A test that passes on the third run is a failing test.

### 4. A failing rollout must never kill a sweep

Anything that can go wrong inside one rollout — a dead endpoint, a server that
will not start, a harness that raises — becomes a zero-scoring `RunRecord` with
the error attached. A sweep that dies on task 7 of 80 is worse than useless.

### 5. Statistics are load-bearing

If you touch `crossbar/stats/` or `crossbar/analysis.py`, be prepared to defend
the change on statistical grounds, with a test that pins the behaviour to a
known value. The whole value proposition is that these numbers are honest.

In particular: do not make the tool rank cells whose intervals overlap. The
refusal to declare a winner is a feature.

## Code style

Match the surrounding code. Beyond that:

- Comments explain *why*, never *what*. If a comment restates the line below it,
  delete the comment.
- Docstrings on modules and public functions; say what the thing is for and what
  decision it embodies, not its signature.
- Type hints on public functions.
- Dataclasses for data, `frozen=True` unless mutation is the point.
- No new runtime dependencies without a very good reason. Current set:
  `pyyaml`, `httpx`, `textual`. The statistics are pure Python on purpose.

## Where to add things

| Adding... | Goes in | Also update |
|---|---|---|
| A model backend | `src/crossbar/providers/` | `PROVIDER_KINDS` and `build_provider` in `config.py` |
| A harness | `src/crossbar/harness/` | `HARNESS_KINDS` and `build_harness` in `config.py` |
| A check type | `src/crossbar/scoring/score.py` | `CHECK_TYPES` in `tasks/model.py`, and the loader's validation |
| An isolation backend | `src/crossbar/env/` | `build_environment` in `env/factory.py` |
| A statistic | `src/crossbar/stats/core.py` | its export in `stats/__init__.py` |

New provider or harness? It must work with the existing `Provider` / `Harness`
protocol without changing the runner. If it cannot, that is worth an issue
before a patch.

## Particularly welcome

- **Task packs.** Real, sanitised task packs from real domains are the scarcest
  thing in this space. A pack with good `mcp_state` checks is worth more than a
  feature.
- **Harness adapters** for other agent frameworks, preserving their native
  behaviour rather than normalising them.
- **A live-Docker test.** The docker environment is covered against a stub
  binary only; nobody has run it against a real daemon.
- **Verifier patterns** — reusable check shapes for common output contracts.
- **Corrections to the statistics.** Genuinely. If something here is wrong,
  saying so loudly is the most useful contribution available.

## Pull requests

- One idea per PR.
- Say what problem it solves and how you verified it.
- `.venv/bin/pytest` green, and say so.
- Update the docs in the same PR. A feature nobody can find does not exist.
- If your change alters what the report prints, paste the before and after.

## Reporting bugs

Include: what you ran, what you expected, what happened, the versions
(`crossbar doctor` prints most of it), and — if a sweep behaved strangely — the
relevant file from `runs/traces/`. The trace is the receipt; it usually settles
the question immediately.
