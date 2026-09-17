"""The terminal app, driven headlessly."""
from pathlib import Path

import pytest

from crossbar.tui import CrossbarApp

ROOT = Path(__file__).resolve().parent.parent
PACK = str(ROOT / "taskpacks" / "support-triage")
CONFIG = str(ROOT / "crossbar.yaml")


def app(**kwargs) -> CrossbarApp:
    return CrossbarApp(config_path=CONFIG, pack_path=PACK, **kwargs)


async def finished(pilot, timeout: float = 60.0) -> None:
    """Wait for the sweep worker to finish."""
    import asyncio

    waited = 0.0
    while not pilot.app.sweep_done and waited < timeout:
        await pilot.pause()
        await asyncio.sleep(0.05)
        waited += 0.05
    assert pilot.app.sweep_done, "sweep did not finish in time"


class TestStartup:
    async def test_it_starts_and_shows_the_roster(self):
        async with app().run_test() as pilot:
            text = pilot.app.query_one("#setup-summary").content
            assert "mock-strong" in str(text)

    async def test_it_shows_the_task_pack(self):
        async with app().run_test() as pilot:
            assert "Support triage" in str(pilot.app.query_one("#setup-summary").content)

    def test_a_broken_config_is_reported_not_crashed(self, tmp_path):
        broken = tmp_path / "broken.yaml"
        broken.write_text("models: []\n")
        instance = CrossbarApp(config_path=str(broken), pack_path=PACK)
        assert instance.load_error
        assert "model" in instance.load_error

    async def test_the_cell_table_lists_every_agent(self):
        async with app().run_test() as pilot:
            table = pilot.app.query_one("#cells")
            labels = [str(table.get_row_at(i)[0]) for i in range(table.row_count)]
            assert "mock-strong+react" in labels

    async def test_repeats_can_be_edited(self):
        async with app().run_test() as pilot:
            field = pilot.app.query_one("#repeats")
            field.value = "2"
            assert pilot.app.chosen_repeats() == 2

    async def test_a_nonsense_repeats_value_falls_back_to_one(self):
        async with app().run_test() as pilot:
            pilot.app.query_one("#repeats").value = "abc"
            assert pilot.app.chosen_repeats() == 1


class TestRunning:
    async def test_running_a_sweep_fills_in_the_results(self):
        async with app(repeats=1).run_test() as pilot:
            await pilot.press("r")
            await finished(pilot)
            report = str(pilot.app.query_one("#report").content)
            assert "CROSSBAR VERDICT" in report

    async def test_the_cell_table_shows_pass_rates_afterwards(self):
        async with app(repeats=1).run_test() as pilot:
            await pilot.press("r")
            await finished(pilot)
            table = pilot.app.query_one("#cells")
            rows = [[str(c) for c in table.get_row_at(i)] for i in range(table.row_count)]
            assert any("%" in cell for row in rows for cell in row)

    async def test_progress_is_logged_per_rollout(self):
        async with app(repeats=1).run_test() as pilot:
            await pilot.press("r")
            await finished(pilot)
            assert pilot.app.rollouts_seen == len(pilot.app.config.agents) * 4

    async def test_results_are_written_to_disk(self, tmp_path):
        async with app(repeats=1, results_dir=str(tmp_path)).run_test() as pilot:
            await pilot.press("r")
            await finished(pilot)
            assert (tmp_path / "sweep.json").exists()

    async def test_a_second_run_request_is_ignored_while_one_is_running(self):
        async with app(repeats=1).run_test() as pilot:
            pilot.app.action_run_sweep()
            pilot.app.action_run_sweep()
            await finished(pilot)
            assert pilot.app.sweeps_started == 1


class TestTraceViewer:
    async def test_rollouts_are_listed_after_a_sweep(self):
        async with app(repeats=1).run_test() as pilot:
            await pilot.press("r")
            await finished(pilot)
            assert pilot.app.query_one("#rollouts").row_count > 0

    async def test_selecting_a_rollout_shows_its_tool_calls(self):
        async with app(repeats=1).run_test() as pilot:
            await pilot.press("r")
            await finished(pilot)
            pilot.app.show_trace(0)
            detail = str(pilot.app.query_one("#trace").content)
            assert "tickets" in detail

    async def test_the_trace_shows_check_outcomes(self):
        async with app(repeats=1).run_test() as pilot:
            await pilot.press("r")
            await finished(pilot)
            pilot.app.show_trace(0)
            detail = str(pilot.app.query_one("#trace").content)
            assert "PASS" in detail or "FAIL" in detail


class TestQuitting:
    async def test_q_exits(self):
        async with app().run_test() as pilot:
            await pilot.press("q")
            await pilot.pause()
            assert not pilot.app.is_running
