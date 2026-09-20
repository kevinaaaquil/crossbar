"""Pre-flight: what gets checked before a run is allowed to spend anything."""
import sys

import pytest

from crossbar.domain import ConnectorConfig, EnvironmentSpec, Task, Test
from crossbar.preflight import Status, preflight
from crossbar.roster import RosterError, parse_roster

PY = sys.executable

GOOD_MCP = ConnectorConfig(
    name="mcp",
    options={"servers": [{"name": "tickets", "command": "${CROSSBAR_PYTHON}",
                          "args": ["-m", "tests.fixtures.tickets_server"]}]},
)
BROKEN_MCP = ConnectorConfig(
    name="mcp", options={"servers": [{"name": "x", "command": "no-such-binary-xyz"}]}
)


def roster(**overrides):
    data = {
        "models": [
            {"id": "local", "provider": "openai", "model": "q",
             "base_url": "http://localhost:1/v1"},
            {"id": "frontier", "provider": "anthropic", "model": "big",
             "api_key_env": "CROSSBAR_TEST_KEY"},
        ],
        "roles": {"candidate": "local", "baseline": "frontier"},
    }
    data.update(overrides)
    return parse_roster(data, source="<test>")


def a_test(connector=GOOD_MCP, kind="local", image=None, name="T"):
    env = EnvironmentSpec(kind=kind, connectors=(connector,), image=image)
    task = Task(id="t1", prompt="p", golden="g", environment=env)
    return Test(name=name, tasks=(task,), environment=env, repeats=1)


def statuses(report):
    return {c.name: c.status for c in report.checks}


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("CROSSBAR_TEST_KEY", "sk-test")


class TestHappyPath:
    def test_a_sound_setup_passes(self):
        report = preflight(roster(), [a_test()])
        assert report.ok is True
        assert not report.failures

    def test_every_check_is_reported_even_when_it_passes(self):
        report = preflight(roster(), [a_test()])
        assert len(report.checks) >= 4
        assert all(c.name and c.detail for c in report.checks)

    def test_the_roles_are_checked(self):
        assert "roles" in statuses(preflight(roster(), [a_test()]))

    def test_it_reports_how_many_attempts_would_run(self):
        report = preflight(roster(), [a_test()])
        assert report.planned_attempts == 2


class TestModelKeys:
    def test_a_missing_key_is_a_failure(self, monkeypatch):
        monkeypatch.delenv("CROSSBAR_TEST_KEY", raising=False)
        report = preflight(roster(), [a_test()])
        assert report.ok is False
        assert any("CROSSBAR_TEST_KEY" in f.detail for f in report.failures)

    def test_a_model_with_no_key_variable_is_only_a_warning(self):
        """Plenty of local endpoints need no key at all."""
        report = preflight(roster(), [a_test()])
        keys = next(c for c in report.checks if c.name == "model-keys")
        assert keys.status is not Status.FAIL

    def test_the_failing_model_is_named(self, monkeypatch):
        monkeypatch.delenv("CROSSBAR_TEST_KEY", raising=False)
        assert any("frontier" in f.detail for f in preflight(roster(), [a_test()]).failures)


class TestJudgeIndependence:
    def test_the_baseline_judging_itself_is_a_warning_not_a_failure(self):
        report = preflight(roster(), [a_test()])
        judge = next(c for c in report.checks if c.name == "judge")
        assert judge.status is Status.WARN
        assert report.ok is True, "a conflict of interest must not block a run"

    def test_an_independent_judge_passes(self):
        cfg = parse_roster(
            {
                "models": [
                    {"id": "local", "provider": "openai", "model": "q",
                     "base_url": "http://localhost:1/v1"},
                    {"id": "frontier", "provider": "anthropic", "model": "big"},
                    {"id": "third", "provider": "anthropic", "model": "x"},
                ],
                "roles": {"candidate": "local", "baseline": "frontier", "judge": "third"},
            },
            source="<test>",
        )
        judge = next(c for c in preflight(cfg, [a_test()]).checks if c.name == "judge")
        assert judge.status is Status.OK


class TestEnvironments:
    def test_an_environment_that_starts_passes(self):
        report = preflight(roster(), [a_test()])
        env = next(c for c in report.checks if c.name.startswith("environment"))
        assert env.status is Status.OK

    def test_an_environment_that_will_not_start_is_a_failure(self):
        report = preflight(roster(), [a_test(connector=BROKEN_MCP)])
        assert report.ok is False
        assert any("no-such-binary" in f.detail for f in report.failures)

    def test_the_failing_test_is_named(self):
        report = preflight(roster(), [a_test(connector=BROKEN_MCP, name="Claims")])
        assert any("Claims" in f.detail for f in report.failures)

    def test_it_can_be_told_not_to_start_anything(self):
        """The cheap path, for when you only want the static checks."""
        report = preflight(roster(), [a_test(connector=BROKEN_MCP)], start_environments=False)
        assert report.ok is True

    def test_environments_are_torn_down_again(self):
        import subprocess

        before = subprocess.run(["pgrep", "-f", "tickets_server"], capture_output=True)
        preflight(roster(), [a_test()])
        after = subprocess.run(["pgrep", "-f", "tickets_server"], capture_output=True)
        assert after.stdout == before.stdout, "pre-flight left a server running"


class TestProbes:
    def test_an_environment_with_no_read_only_probes_warns(self):
        """Nothing can be checked, so everything would come back Unchecked."""
        no_probes = ConnectorConfig(
            name="mcp",
            options={"servers": [{"name": "echo", "command": PY,
                                  "args": ["-m", "tests.fixtures.probeless_server"]}]},
        )
        report = preflight(roster(), [a_test(connector=no_probes)])
        probes = next(c for c in report.checks if c.name.startswith("probes"))
        assert probes.status is Status.WARN
        assert "unchecked" in probes.detail.lower()

    def test_an_environment_with_probes_passes(self):
        report = preflight(roster(), [a_test()])
        probes = next(c for c in report.checks if c.name.startswith("probes"))
        assert probes.status is Status.OK


class TestDocker:
    def test_a_docker_test_without_docker_is_a_failure(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda name: None)
        report = preflight(
            roster(), [a_test(kind="docker", image="x")], start_environments=False
        )
        assert report.ok is False
        assert any("docker" in f.detail.lower() for f in report.failures)

    def test_local_tests_do_not_need_docker(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda name: None)
        assert preflight(roster(), [a_test()], start_environments=False).ok is True


class TestRendering:
    def test_the_report_renders_every_check(self):
        from crossbar.preflight import render_preflight

        text = render_preflight(preflight(roster(), [a_test()]))
        assert "roles" in text
        assert "PASS" in text.upper() or "OK" in text.upper()

    def test_failures_are_visible(self):
        from crossbar.preflight import render_preflight

        text = render_preflight(preflight(roster(), [a_test(connector=BROKEN_MCP)]))
        assert "FAIL" in text.upper()

    def test_it_says_what_would_run(self):
        from crossbar.preflight import render_preflight

        assert "2 attempts" in render_preflight(preflight(roster(), [a_test()]))


class TestRenderingWidth:
    def test_every_line_fits_a_terminal(self):
        from crossbar.preflight import render_preflight

        text = render_preflight(preflight(roster(), [a_test()]))
        for line in text.splitlines():
            assert len(line) <= 100, line


class TestSingleModelPreflight:
    def single_roster(self):
        return parse_roster(
            {
                "models": [
                    {"id": "local", "provider": "openai", "model": "q",
                     "base_url": "http://localhost:1/v1"},
                    {"id": "grader", "provider": "anthropic", "model": "big"},
                ],
                "roles": {"candidate": "local", "judge": "grader"},
            },
            source="<test>",
        )

    def test_a_single_model_setup_passes(self):
        assert preflight(self.single_roster(), [a_test()]).ok is True

    def test_the_roles_check_says_it_is_a_single_model_run(self):
        roles = next(c for c in preflight(self.single_roster(), [a_test()]).checks
                     if c.name == "roles")
        assert roles.status is Status.OK
        assert "local" in roles.detail
        assert "baseline" not in roles.detail.lower()

    def test_no_judge_conflict_is_warned_about(self):
        judge = next(c for c in preflight(self.single_roster(), [a_test()]).checks
                     if c.name == "judge")
        assert judge.status is Status.OK

    def test_half_the_attempts_are_planned(self):
        """One role executing means one attempt per task per repeat."""
        assert preflight(self.single_roster(), [a_test()]).planned_attempts == 1


class TestAgentCliPreflight:
    """A missing CLI, or a --bare run with no API key, fails every attempt."""

    def cli_roster(self, command="claude"):
        return parse_roster(
            {
                "models": [
                    {"id": "local", "provider": "openai", "model": "q",
                     "base_url": "http://localhost:1/v1"},
                    {"id": "cc", "provider": "claude-cli", "model": "opus",
                     "command": command},
                ],
                "roles": {"candidate": "local", "baseline": "cc"},
            },
            source="<test>",
        )

    def test_a_missing_cli_binary_is_a_failure(self, monkeypatch):
        report = preflight(self.cli_roster("no-such-claude-xyz"), [a_test()],
                           start_environments=False)
        assert report.ok is False
        assert any("no-such-claude-xyz" in f.detail for f in report.failures)

    def test_a_present_binary_with_a_key_passes(self, monkeypatch, tmp_path):
        import stat

        script = tmp_path / "claude"
        script.write_text("#!/bin/sh\nexit 0\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        report = preflight(self.cli_roster(str(script)), [a_test()], start_environments=False)
        check = next(c for c in report.checks if c.name == "agent-cli")
        assert check.status is Status.OK

    def test_a_missing_anthropic_key_is_a_failure_for_a_bare_run(self, monkeypatch, tmp_path):
        """--bare reads credentials only from ANTHROPIC_API_KEY, never OAuth."""
        import stat

        script = tmp_path / "claude"
        script.write_text("#!/bin/sh\nexit 0\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        report = preflight(self.cli_roster(str(script)), [a_test()], start_environments=False)
        assert any("ANTHROPIC_API_KEY" in f.detail for f in report.failures)

    def test_a_roster_with_no_cli_models_skips_the_check(self):
        names = {c.name for c in preflight(roster(), [a_test()], start_environments=False).checks}
        assert "agent-cli" not in names


class TestSubscriptionCli:
    """A subscription login is a legitimate way to connect the Claude CLI, but
    it costs the isolation `--bare` buys, and the report must not pretend
    otherwise."""

    def _roster(self, auth):
        return parse_roster(
            {
                "models": [
                    {"id": "cc", "provider": "claude-cli", "model": "opus",
                     "auth": auth},
                    {"id": "other", "provider": "openai", "model": "m",
                     "base_url": "http://localhost:1/v1"},
                ],
                "roles": {"candidate": "other", "judge": "cc"},
            }
        )

    def test_a_subscription_cli_does_not_need_an_api_key(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        report = preflight(self._roster("subscription"), [], start_environments=False)
        check = next(c for c in report.checks if c.name == "agent-cli")
        assert check.status is not Status.FAIL

    def test_an_api_key_cli_still_needs_one(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        report = preflight(self._roster("api-key"), [], start_environments=False)
        check = next(c for c in report.checks if c.name == "agent-cli")
        assert check.status is Status.FAIL
        assert "ANTHROPIC_API_KEY" in check.detail

    def test_a_subscription_cli_is_warned_about(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        report = preflight(self._roster("subscription"), [], start_environments=False)
        check = next(c for c in report.checks if c.name == "agent-cli")
        assert check.status is Status.WARN
        assert "CLAUDE.md" in check.detail

    def test_an_unknown_auth_is_refused_by_the_loader(self):
        with pytest.raises(RosterError, match="auth"):
            self._roster("sorcery")
