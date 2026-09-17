"""The shipped demo: a real MCP server, a real task pack, a real crossover.

This is the v0 proof from the design doc - a cheaper configuration beating a
dearer one on a private task set, with a confidence interval attached - and it
runs offline, which is what makes it a usable first-run experience.
"""
import sys
from pathlib import Path

import pytest

from crossbar.analysis import analyze
from crossbar.config import load_config
from crossbar.env import LocalEnvironment
from crossbar.runner import Runner
from crossbar.tasks import EnvironmentSpec, ServerSpec, load_pack

PY = sys.executable
ROOT = Path(__file__).resolve().parent.parent
PACK_DIR = ROOT / "taskpacks" / "support-triage"
DEMO_CONFIG = ROOT / "crossbar.yaml"

TICKETS = ServerSpec(name="tickets", command=PY, args=("-m", "crossbar.demo.tickets_server"))


@pytest.fixture
def env(tmp_path):
    e = LocalEnvironment(
        EnvironmentSpec(kind="local", servers=(TICKETS,)), workspace=str(tmp_path)
    )
    e.start()
    yield e
    e.stop()


class TestTicketsServer:
    def test_it_seeds_a_realistic_backlog(self, env):
        tickets = env.call("tickets__list_tickets", {}).structured["tickets"]
        assert len(tickets) >= 5
        assert {"id", "subject", "priority", "status"} <= set(tickets[0])

    def test_tickets_can_be_prioritised(self, env):
        env.call("tickets__set_priority", {"id": "T-1001", "priority": "urgent"})
        ticket = env.call("tickets__get_ticket", {"id": "T-1001"}).structured["ticket"]
        assert ticket["priority"] == "urgent"

    def test_tickets_can_be_assigned_and_tagged(self, env):
        env.call("tickets__assign", {"id": "T-1002", "assignee": "alice"})
        env.call("tickets__add_tag", {"id": "T-1002", "tag": "billing"})
        ticket = env.call("tickets__get_ticket", {"id": "T-1002"}).structured["ticket"]
        assert ticket["assignee"] == "alice"
        assert "billing" in ticket["tags"]

    def test_closing_a_ticket_changes_its_status(self, env):
        env.call("tickets__close_ticket", {"id": "T-1003"})
        ticket = env.call("tickets__get_ticket", {"id": "T-1003"}).structured["ticket"]
        assert ticket["status"] == "closed"

    def test_search_finds_by_subject_and_body(self, env):
        hits = env.call("tickets__search_tickets", {"query": "outage"}).structured["tickets"]
        assert hits and all("outage" in (t["subject"] + t["body"]).lower() for t in hits)

    def test_an_unknown_ticket_is_an_error_not_a_crash(self, env):
        result = env.call("tickets__set_priority", {"id": "T-9999", "priority": "urgent"})
        assert result.is_error

    def test_state_persists_in_the_workspace(self, tmp_path):
        first = LocalEnvironment(
            EnvironmentSpec(kind="local", servers=(TICKETS,)), workspace=str(tmp_path)
        )
        first.start()
        first.call("tickets__close_ticket", {"id": "T-1001"})
        first.stop()

        second = LocalEnvironment(
            EnvironmentSpec(kind="local", servers=(TICKETS,)), workspace=str(tmp_path)
        )
        second.start()
        try:
            ticket = second.call("tickets__get_ticket", {"id": "T-1001"}).structured["ticket"]
            assert ticket["status"] == "closed"
        finally:
            second.stop()

    def test_each_run_starts_from_the_same_seeded_backlog(self, tmp_path):
        first = LocalEnvironment(
            EnvironmentSpec(kind="local", servers=(TICKETS,)), workspace=str(tmp_path / "a")
        )
        first.start()
        first.call("tickets__close_ticket", {"id": "T-1001"})
        first.stop()

        second = LocalEnvironment(
            EnvironmentSpec(kind="local", servers=(TICKETS,)), workspace=str(tmp_path / "b")
        )
        second.start()
        try:
            ticket = second.call("tickets__get_ticket", {"id": "T-1001"}).structured["ticket"]
            assert ticket["status"] == "open"
        finally:
            second.stop()


class TestShippedPack:
    def test_the_pack_loads_and_validates(self):
        pack = load_pack(PACK_DIR)
        assert len(pack) >= 4
        assert pack.name

    def test_every_task_has_a_deterministic_check(self):
        for task in load_pack(PACK_DIR):
            assert task.checks
            assert any(c.type in ("mcp_state", "tool_called") for c in task.checks)

    def test_every_task_carries_a_demo_script(self):
        for task in load_pack(PACK_DIR):
            assert task.demo is not None and task.demo.steps

    def test_destructive_tools_are_forbidden_by_the_pack(self):
        for task in load_pack(PACK_DIR):
            assert "tickets.delete_all" in task.security.forbidden_tools

    def test_the_demo_config_loads(self):
        config = load_config(DEMO_CONFIG)
        assert config.agents
        assert any(m.provider == "mock" for m in config.models)


class TestOfflineCrossover:
    def test_a_strong_mock_solves_the_pack_and_a_weak_one_does_not(self, tmp_path):
        config = load_config(DEMO_CONFIG)
        pack = load_pack(PACK_DIR)
        result = Runner(config, pack, results_dir=str(tmp_path)).run()
        analysis = analyze(result)

        strong = next(c for c in analysis.cells if "strong" in c.agent_id)
        weak = next(c for c in analysis.cells if "weak" in c.agent_id)
        assert strong.pass_rate > weak.pass_rate

    def test_the_sweep_produces_a_verdict_with_an_interval(self, tmp_path):
        config = load_config(DEMO_CONFIG)
        result = Runner(config, load_pack(PACK_DIR), results_dir=str(tmp_path)).run()
        verdict = analyze(result).verdict
        assert verdict.winner.ci_low <= verdict.winner.pass_rate <= verdict.winner.ci_high
        assert verdict.confidence in ("low", "medium", "high")

    def test_the_whole_sweep_is_reproducible(self, tmp_path):
        config = load_config(DEMO_CONFIG)
        pack = load_pack(PACK_DIR)
        first = Runner(config, pack, results_dir=str(tmp_path / "a")).run()
        second = Runner(config, pack, results_dir=str(tmp_path / "b")).run()
        assert [r.score.passed for r in first.records] == [r.score.passed for r in second.records]
