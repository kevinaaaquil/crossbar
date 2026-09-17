"""Task pack format: parsing, validation, and the integrity rules."""
import textwrap

import pytest

from crossbar.tasks import (
    Check,
    Task,
    TaskPack,
    TaskValidationError,
    load_pack,
    load_task_file,
    parse_task,
)

MINIMAL = {
    "id": "notes-001",
    "prompt": "Create a note called hello.",
    "environment": {
        "kind": "local",
        "servers": [{"name": "notes", "command": "python", "args": ["-m", "srv"]}],
    },
    "checks": [{"type": "tool_called", "server": "notes", "tool": "create_note"}],
}


def _task(**overrides):
    data = {**MINIMAL, **overrides}
    return parse_task(data, source="<test>")


class TestParseTask:
    def test_parses_a_minimal_task(self):
        task = _task()
        assert isinstance(task, Task)
        assert task.id == "notes-001"
        assert task.prompt == "Create a note called hello."

    def test_defaults_are_applied(self):
        task = _task()
        assert task.timeout_s == 120
        assert task.max_steps == 20
        assert task.budget_tokens == 100_000
        assert task.security.forbidden_tools == ()

    def test_explicit_limits_override_defaults(self):
        task = _task(timeout_s=30, max_steps=5, budget_tokens=1000)
        assert (task.timeout_s, task.max_steps, task.budget_tokens) == (30, 5, 1000)

    def test_server_spec_is_parsed(self):
        task = _task()
        server = task.environment.servers[0]
        assert server.name == "notes"
        assert server.command == "python"
        assert server.args == ("-m", "srv")
        assert server.env == {}

    def test_checks_are_parsed_into_check_objects(self):
        task = _task()
        assert len(task.checks) == 1
        assert isinstance(task.checks[0], Check)
        assert task.checks[0].type == "tool_called"

    def test_security_block_is_parsed(self):
        task = _task(security={"forbidden_tools": ["notes.delete_all"], "max_tool_calls": 7})
        assert task.security.forbidden_tools == ("notes.delete_all",)
        assert task.security.max_tool_calls == 7


class TestTaskValidation:
    def test_missing_id_is_rejected(self):
        data = {k: v for k, v in MINIMAL.items() if k != "id"}
        with pytest.raises(TaskValidationError, match="id"):
            parse_task(data, source="<test>")

    def test_missing_prompt_is_rejected(self):
        data = {k: v for k, v in MINIMAL.items() if k != "prompt"}
        with pytest.raises(TaskValidationError, match="prompt"):
            parse_task(data, source="<test>")

    def test_task_without_checks_is_rejected(self):
        with pytest.raises(TaskValidationError, match="checks"):
            _task(checks=[])

    def test_unknown_check_type_is_rejected(self):
        with pytest.raises(TaskValidationError, match="quantum_entanglement"):
            _task(checks=[{"type": "quantum_entanglement"}])

    def test_check_referencing_unknown_server_is_rejected(self):
        with pytest.raises(TaskValidationError, match="ghost"):
            _task(checks=[{"type": "tool_called", "server": "ghost", "tool": "x"}])

    def test_environment_without_servers_is_rejected(self):
        with pytest.raises(TaskValidationError, match="server"):
            _task(environment={"kind": "local", "servers": []})

    def test_unknown_environment_kind_is_rejected(self):
        with pytest.raises(TaskValidationError, match="kind"):
            _task(environment={"kind": "vm", "servers": [{"name": "n", "command": "x"}]})

    def test_docker_environment_requires_an_image(self):
        with pytest.raises(TaskValidationError, match="image"):
            _task(environment={"kind": "docker", "servers": [{"name": "n", "command": "x"}]})

    def test_duplicate_server_names_are_rejected(self):
        with pytest.raises(TaskValidationError, match="duplicate"):
            _task(
                environment={
                    "kind": "local",
                    "servers": [
                        {"name": "notes", "command": "a"},
                        {"name": "notes", "command": "b"},
                    ],
                }
            )

    def test_error_message_names_the_source_file(self):
        with pytest.raises(TaskValidationError, match=r"tasks/bad\.yaml"):
            parse_task({}, source="tasks/bad.yaml")

    def test_mcp_state_check_requires_a_tool(self):
        with pytest.raises(TaskValidationError, match="tool"):
            _task(checks=[{"type": "mcp_state", "server": "notes", "expect": {}}])

    def test_final_text_check_requires_a_value(self):
        with pytest.raises(TaskValidationError, match="value"):
            _task(checks=[{"type": "final_text", "match": "contains"}])


class TestLoadFromDisk:
    def test_loads_a_task_from_a_yaml_file(self, tmp_path):
        path = tmp_path / "t.task.yaml"
        path.write_text(
            textwrap.dedent(
                """
                id: disk-001
                prompt: do the thing
                environment:
                  kind: local
                  servers:
                    - name: notes
                      command: python
                checks:
                  - type: final_text
                    match: contains
                    value: done
                """
            )
        )
        task = load_task_file(path)
        assert task.id == "disk-001"
        assert task.source.endswith("t.task.yaml")

    def test_loads_every_task_in_a_pack_directory(self, tmp_path):
        for i in (1, 2):
            (tmp_path / f"t{i}.task.yaml").write_text(
                f"id: pack-00{i}\nprompt: p\nenvironment:\n  kind: local\n  servers:\n"
                f"    - name: notes\n      command: python\nchecks:\n  - type: final_text\n"
                f"    match: contains\n    value: x\n"
            )
        pack = load_pack(tmp_path)
        assert isinstance(pack, TaskPack)
        assert [t.id for t in pack.tasks] == ["pack-001", "pack-002"]

    def test_pack_metadata_is_read_from_pack_yaml(self, tmp_path):
        (tmp_path / "pack.yaml").write_text("name: Claims triage\ndescription: demo pack\n")
        (tmp_path / "a.task.yaml").write_text(
            "id: a\nprompt: p\nenvironment:\n  kind: local\n  servers:\n    - name: n\n"
            "      command: c\nchecks:\n  - type: final_text\n    match: contains\n    value: x\n"
        )
        pack = load_pack(tmp_path)
        assert pack.name == "Claims triage"
        assert pack.description == "demo pack"

    def test_duplicate_task_ids_across_the_pack_are_rejected(self, tmp_path):
        body = (
            "id: dup\nprompt: p\nenvironment:\n  kind: local\n  servers:\n    - name: n\n"
            "      command: c\nchecks:\n  - type: final_text\n    match: contains\n    value: x\n"
        )
        (tmp_path / "a.task.yaml").write_text(body)
        (tmp_path / "b.task.yaml").write_text(body)
        with pytest.raises(TaskValidationError, match="dup"):
            load_pack(tmp_path)

    def test_empty_directory_is_rejected(self, tmp_path):
        with pytest.raises(TaskValidationError, match="no tasks"):
            load_pack(tmp_path)

    def test_missing_directory_is_rejected(self, tmp_path):
        with pytest.raises(TaskValidationError, match="not a directory"):
            load_pack(tmp_path / "nope")

    def test_malformed_yaml_reports_the_file(self, tmp_path):
        (tmp_path / "bad.task.yaml").write_text("id: [unclosed\n")
        with pytest.raises(TaskValidationError, match="bad.task.yaml"):
            load_pack(tmp_path)
