"""Domain objects: Test, Task, Golden, limits, environment specs."""
import textwrap

import pytest

from crossbar.domain import (
    ConnectorConfig,
    DomainError,
    EnvironmentSpec,
    Limits,
    Role,
    Task,
    Test,
    load_test,
    parse_task,
)

ENV_YAML = textwrap.dedent(
    """
    kind: local
    connectors:
      mcp:
        servers:
          - name: tickets
            command: python
            args: ["-m", "tickets_server"]
    """
)

TASK_YAML = textwrap.dedent(
    """
    id: escalate-outage
    name: Escalate the outage tickets
    prompt: |
      Escalate every open ticket mentioning an outage to urgent.
    golden: |
      Tickets T-1001 and T-1004 both have priority "urgent".
      No other ticket's priority has changed.
    limits:
      timeout_s: 90
      max_steps: 10
      max_tokens: 50000
    """
)

TEST_YAML = textwrap.dedent(
    """
    name: Support triage
    description: Four support-desk workflows.
    repeats: 3
    environment: env.yaml
    """
)


def write_test_dir(tmp_path, task_yaml=TASK_YAML, test_yaml=TEST_YAML, env_yaml=ENV_YAML):
    (tmp_path / "test.yaml").write_text(test_yaml)
    (tmp_path / "env.yaml").write_text(env_yaml)
    (tmp_path / "01-first.task.yaml").write_text(task_yaml)
    return tmp_path


MINIMAL_TASK = {
    "id": "t1",
    "prompt": "Do the thing.",
    "golden": "The thing is done.",
}


def task(**overrides):
    return parse_task({**MINIMAL_TASK, **overrides}, source="<test>")


class TestTaskParsing:
    def test_parses_the_required_fields(self):
        parsed = task()
        assert isinstance(parsed, Task)
        assert parsed.id == "t1"
        assert parsed.prompt == "Do the thing."
        assert parsed.golden == "The thing is done."

    def test_the_golden_is_prose_and_is_not_interpreted(self):
        parsed = task(golden="Anything at all: the DB has rows, the file exists.")
        assert parsed.golden.startswith("Anything at all")

    def test_limits_have_defaults(self):
        assert task().limits == Limits()

    def test_limits_can_be_overridden(self):
        parsed = task(limits={"timeout_s": 30, "max_steps": 5, "max_tokens": 1000})
        assert parsed.limits == Limits(timeout_s=30, max_steps=5, max_tokens=1000)

    def test_partial_limits_keep_the_other_defaults(self):
        parsed = task(limits={"max_steps": 4})
        assert parsed.limits.max_steps == 4
        assert parsed.limits.timeout_s == Limits().timeout_s

    def test_name_falls_back_to_the_id(self):
        assert task().title == "t1"
        assert task(name="A nice name").title == "A nice name"


class TestTaskValidation:
    @pytest.mark.parametrize("missing", ["id", "prompt", "golden"])
    def test_required_fields_are_required(self, missing):
        data = {k: v for k, v in MINIMAL_TASK.items() if k != missing}
        with pytest.raises(DomainError, match=missing):
            parse_task(data, source="<test>")

    def test_an_empty_golden_is_rejected(self):
        with pytest.raises(DomainError, match="golden"):
            task(golden="   ")

    def test_the_source_file_is_named_in_the_error(self):
        with pytest.raises(DomainError, match=r"tasks/bad\.yaml"):
            parse_task({}, source="tasks/bad.yaml")

    def test_non_positive_limits_are_rejected(self):
        with pytest.raises(DomainError, match="timeout_s"):
            task(limits={"timeout_s": 0})

    def test_unknown_keys_are_rejected(self):
        with pytest.raises(DomainError, match="checks"):
            task(checks=[{"type": "mcp_state"}])

    def test_a_non_mapping_task_is_rejected(self):
        with pytest.raises(DomainError, match="mapping"):
            parse_task(["not", "a", "mapping"], source="<test>")


class TestEnvironmentSpec:
    def test_local_environments_need_no_image(self, tmp_path):
        test = load_test(write_test_dir(tmp_path))
        assert test.environment.kind == "local"
        assert test.environment.image is None

    def test_docker_environments_require_an_image(self, tmp_path):
        with pytest.raises(DomainError, match="image"):
            load_test(write_test_dir(tmp_path, env_yaml="kind: docker\nconnectors: {mcp: {}}\n"))

    def test_docker_environments_parse_their_image(self, tmp_path):
        env = "kind: docker\nimage: demo:latest\nconnectors: {mcp: {}}\n"
        assert load_test(write_test_dir(tmp_path, env_yaml=env)).environment.image == "demo:latest"

    def test_unknown_kinds_are_rejected(self, tmp_path):
        with pytest.raises(DomainError, match="carrier-pigeon"):
            load_test(write_test_dir(tmp_path, env_yaml="kind: carrier-pigeon\nconnectors: {}\n"))

    def test_reset_policy_defaults_to_recreate(self, tmp_path):
        assert load_test(write_test_dir(tmp_path)).environment.reset == "recreate"

    def test_unknown_reset_policies_are_rejected(self, tmp_path):
        env = "kind: local\nreset: pray\nconnectors: {mcp: {}}\n"
        with pytest.raises(DomainError, match="pray"):
            load_test(write_test_dir(tmp_path, env_yaml=env))

    def test_an_environment_with_no_connectors_is_rejected(self, tmp_path):
        with pytest.raises(DomainError, match="connector"):
            load_test(write_test_dir(tmp_path, env_yaml="kind: local\nconnectors: {}\n"))


class TestConnectorConfig:
    def test_connectors_are_parsed_by_name(self, tmp_path):
        env = load_test(write_test_dir(tmp_path)).environment
        assert [c.name for c in env.connectors] == ["mcp"]
        assert isinstance(env.connectors[0], ConnectorConfig)

    def test_connector_options_are_passed_through_untouched(self, tmp_path):
        options = load_test(write_test_dir(tmp_path)).environment.connectors[0].options
        assert options["servers"][0]["name"] == "tickets"

    def test_read_only_tools_can_be_declared(self, tmp_path):
        env_yaml = textwrap.dedent(
            """
            kind: local
            connectors:
              mcp:
                servers: []
                read_only_tools: ["tickets__list_tickets"]
            """
        )
        env = load_test(write_test_dir(tmp_path, env_yaml=env_yaml)).environment
        assert env.connectors[0].read_only_tools == ("tickets__list_tickets",)

    def test_read_only_tools_default_to_empty(self, tmp_path):
        assert load_test(write_test_dir(tmp_path)).environment.connectors[0].read_only_tools == ()

    def test_unknown_connectors_are_rejected_when_a_registry_is_supplied(self, tmp_path):
        env_yaml = "kind: local\nconnectors:\n  telepathy: {}\n"
        with pytest.raises(DomainError, match="telepathy"):
            load_test(write_test_dir(tmp_path, env_yaml=env_yaml), known_connectors={"mcp"})

    def test_connectors_are_not_validated_without_a_registry(self, tmp_path):
        env_yaml = "kind: local\nconnectors:\n  future-thing: {}\n"
        assert load_test(write_test_dir(tmp_path, env_yaml=env_yaml)).environment.connectors


class TestLoadingATest:
    def test_a_test_directory_loads(self, tmp_path):
        test = load_test(write_test_dir(tmp_path))
        assert isinstance(test, Test)
        assert test.name == "Support triage"
        assert test.repeats == 3
        assert [t.id for t in test.tasks] == ["escalate-outage"]

    def test_tasks_load_in_filename_order(self, tmp_path):
        write_test_dir(tmp_path)
        (tmp_path / "02-second.task.yaml").write_text("id: second\nprompt: p\ngolden: g\n")
        (tmp_path / "00-zeroth.task.yaml").write_text("id: zeroth\nprompt: p\ngolden: g\n")
        assert [t.id for t in load_test(tmp_path).tasks] == [
            "zeroth",
            "escalate-outage",
            "second",
        ]

    def test_every_task_carries_the_tests_environment(self, tmp_path):
        test = load_test(write_test_dir(tmp_path))
        assert test.tasks[0].environment is test.environment

    def test_repeats_default_to_one(self, tmp_path):
        write_test_dir(tmp_path, test_yaml="name: N\nenvironment: env.yaml\n")
        assert load_test(tmp_path).repeats == 1

    def test_duplicate_task_ids_are_rejected(self, tmp_path):
        write_test_dir(tmp_path)
        (tmp_path / "02-dupe.task.yaml").write_text("id: escalate-outage\nprompt: p\ngolden: g\n")
        with pytest.raises(DomainError, match="escalate-outage"):
            load_test(tmp_path)

    def test_a_test_with_no_tasks_is_rejected(self, tmp_path):
        (tmp_path / "test.yaml").write_text(TEST_YAML)
        (tmp_path / "env.yaml").write_text(ENV_YAML)
        with pytest.raises(DomainError, match="no tasks"):
            load_test(tmp_path)

    def test_a_missing_directory_is_rejected(self, tmp_path):
        with pytest.raises(DomainError, match="not a directory"):
            load_test(tmp_path / "nope")

    def test_a_missing_test_yaml_is_rejected(self, tmp_path):
        (tmp_path / "01-a.task.yaml").write_text("id: a\nprompt: p\ngolden: g\n")
        with pytest.raises(DomainError, match="test.yaml"):
            load_test(tmp_path)

    def test_a_missing_environment_file_is_rejected(self, tmp_path):
        write_test_dir(tmp_path)
        (tmp_path / "env.yaml").unlink()
        with pytest.raises(DomainError, match="env.yaml"):
            load_test(tmp_path)

    def test_malformed_yaml_names_the_file(self, tmp_path):
        write_test_dir(tmp_path)
        (tmp_path / "01-first.task.yaml").write_text("id: [unclosed\n")
        with pytest.raises(DomainError, match="01-first.task.yaml"):
            load_test(tmp_path)

    def test_a_task_may_override_the_environment(self, tmp_path):
        write_test_dir(tmp_path, task_yaml=TASK_YAML + "environment: other.yaml\n")
        (tmp_path / "other.yaml").write_text("kind: docker\nimage: x\nconnectors: {mcp: {}}\n")
        assert load_test(tmp_path).tasks[0].environment.kind == "docker"

    def test_non_positive_repeats_are_rejected(self, tmp_path):
        with pytest.raises(DomainError, match="repeats"):
            load_test(write_test_dir(tmp_path, test_yaml="name: N\nrepeats: 0\nenvironment: env.yaml\n"))


class TestRoles:
    def test_the_three_roles_exist(self):
        assert {Role.CANDIDATE, Role.BASELINE, Role.JUDGE}

    def test_candidate_sorts_before_baseline(self):
        """Execution order depends on this: candidate attempts run first."""
        assert Role.CANDIDATE.order < Role.BASELINE.order

    def test_the_judge_has_no_execution_order(self):
        assert Role.JUDGE.order is None


class TestIntegrationWithARealTestDirectory:
    """The fixture Test directory that the rest of the suite also uses."""

    FIXTURE = "tests/fixtures/tests/support-triage"

    def test_the_fixture_test_loads(self):
        test = load_test(self.FIXTURE)
        assert test.name == "Support triage"
        assert test.repeats == 2
        assert [t.id for t in test.tasks] == ["escalate-outage", "route-billing"]

    def test_every_task_has_a_prose_golden(self):
        for task in load_test(self.FIXTURE):
            assert len(task.golden.strip()) > 20
            assert task.golden != task.prompt

    def test_the_environment_declares_an_mcp_connector(self):
        env = load_test(self.FIXTURE).environment
        assert [c.name for c in env.connectors] == ["mcp"]
        assert env.connectors[0].options["servers"][0]["name"] == "tickets"

    def test_limits_are_per_task_with_defaults_where_unset(self):
        test = load_test(self.FIXTURE)
        assert test.task("escalate-outage").limits.timeout_s == 60
        assert test.task("route-billing").limits == Limits()

    def test_it_validates_against_a_connector_registry(self):
        assert load_test(self.FIXTURE, known_connectors={"mcp"})

    def test_it_is_rejected_by_a_registry_without_mcp(self):
        with pytest.raises(DomainError, match="mcp"):
            load_test(self.FIXTURE, known_connectors={"browser"})
