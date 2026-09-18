"""Evidence: what gets captured from an Attempt for the judge to examine."""
import json
import sys

import pytest

from crossbar.connectors import McpConnector
from crossbar.domain import ConnectorConfig, EnvironmentSpec
from crossbar.environment import LocalEnvironment
from crossbar.evidence import (
    Evidence,
    EvidenceItem,
    EvidenceRequest,
    capture,
    load_evidence,
)

PY = sys.executable

MCP = ConnectorConfig(
    name="mcp",
    options={"servers": [{"name": "tickets", "command": "${CROSSBAR_PYTHON}",
                          "args": ["-m", "tests.fixtures.tickets_server"]}]},
)


@pytest.fixture
def connectors():
    env = LocalEnvironment(EnvironmentSpec(kind="local", connectors=(MCP,)))
    connector = McpConnector(MCP)
    connector.setup(env.start())
    yield [connector]
    connector.teardown()
    env.stop()


def request(**overrides) -> EvidenceRequest:
    data = {
        "label": "the whole ticket store",
        "connector": "mcp",
        "probe": "tickets__dump_db",
        "args": {},
    }
    data.update(overrides)
    return EvidenceRequest(**data)


class TestCapture:
    def test_a_probe_is_captured(self, connectors):
        evidence = capture([request()], connectors, final_answer="done")
        assert len(evidence.items) == 1
        assert "T-1001" in evidence.items[0].content

    def test_the_final_answer_is_kept(self, connectors):
        assert capture([], connectors, final_answer="I escalated two.").final_answer == (
            "I escalated two."
        )

    def test_captured_content_reflects_what_the_agent_did(self, connectors):
        connectors[0].call("tickets__set_priority", {"id": "T-1001", "priority": "urgent"})
        evidence = capture([request()], connectors, final_answer="")
        tickets = json.loads(evidence.items[0].content)["tickets"]
        assert next(t for t in tickets if t["id"] == "T-1001")["priority"] == "urgent"

    def test_several_requests_are_captured_in_order(self, connectors):
        requests = [
            request(label="one", probe="tickets__get_ticket", args={"id": "T-1001"}),
            request(label="two", probe="tickets__get_ticket", args={"id": "T-1002"}),
        ]
        evidence = capture(requests, connectors, final_answer="")
        assert [i.request.label for i in evidence.items] == ["one", "two"]

    def test_the_label_survives_capture(self, connectors):
        evidence = capture([request(label="after the run")], connectors, final_answer="")
        assert evidence.items[0].request.label == "after the run"


class TestUnavailableEvidence:
    """Anything the judge needs but cannot get becomes a reason, never a crash."""

    def test_an_unknown_connector_is_recorded_as_an_error(self, connectors):
        evidence = capture([request(connector="browser")], connectors, final_answer="")
        item = evidence.items[0]
        assert item.content is None
        assert "browser" in item.error

    def test_an_unknown_probe_is_recorded_as_an_error(self, connectors):
        evidence = capture([request(probe="tickets__imaginary")], connectors, final_answer="")
        assert "imaginary" in evidence.items[0].error

    def test_a_tool_that_is_not_read_only_is_refused(self, connectors):
        evidence = capture([request(probe="tickets__delete_all")], connectors, final_answer="")
        assert "read-only" in evidence.items[0].error

    def test_refusing_a_mutating_probe_leaves_state_untouched(self, connectors):
        capture([request(probe="tickets__delete_all")], connectors, final_answer="")
        tickets = connectors[0].probe("tickets__dump_db", {}).structured["tickets"]
        assert len(tickets) == 6

    def test_a_probe_that_errors_records_the_error(self, connectors):
        evidence = capture(
            [request(probe="tickets__get_ticket", args={"id": "T-9999"})],
            connectors,
            final_answer="",
        )
        assert evidence.items[0].error
        assert "T-9999" in evidence.items[0].error

    def test_one_unavailable_item_does_not_stop_the_others(self, connectors):
        requests = [request(connector="ghost"), request(label="good")]
        evidence = capture(requests, connectors, final_answer="")
        assert evidence.items[0].content is None
        assert evidence.items[1].content is not None

    def test_missing_items_are_reportable(self, connectors):
        evidence = capture([request(connector="ghost"), request()], connectors, final_answer="")
        assert [i.request.label for i in evidence.missing] == ["the whole ticket store"]
        assert evidence.is_complete is False

    def test_complete_evidence_says_so(self, connectors):
        assert capture([request()], connectors, final_answer="x").is_complete is True


class TestBlinding:
    """The judge must not be able to tell whose Attempt it is grading."""

    def test_evidence_carries_no_model_identity(self, connectors):
        evidence = capture([request()], connectors, final_answer="done")
        serialised = json.dumps(evidence.to_dict())
        for leak in ("model", "agent", "candidate", "baseline", "role", "provider"):
            assert leak not in serialised.lower(), f"{leak!r} leaked into the evidence"

    def test_the_dataclass_has_no_identity_fields(self):
        """Structural, not a convention to remember: the payload cannot leak
        what it does not have."""
        fields = set(Evidence.__dataclass_fields__)
        assert not fields & {"model", "model_id", "agent", "agent_id", "role"}


class TestPersistence:
    def test_evidence_round_trips_through_disk(self, connectors, tmp_path):
        original = capture([request()], connectors, final_answer="done")
        path = original.write(tmp_path / "evidence.json")
        restored = load_evidence(path)
        assert restored.final_answer == "done"
        assert restored.items[0].content == original.items[0].content
        assert restored.items[0].request.label == original.items[0].request.label

    def test_errors_survive_the_round_trip(self, connectors, tmp_path):
        original = capture([request(connector="ghost")], connectors, final_answer="")
        restored = load_evidence(original.write(tmp_path / "e.json"))
        assert restored.items[0].error == original.items[0].error
        assert restored.is_complete is False

    def test_it_is_judgeable_with_no_live_environment(self, connectors, tmp_path):
        """Re-judging happens long after the container is gone."""
        connectors[0].call("tickets__set_priority", {"id": "T-1001", "priority": "urgent"})
        path = capture([request()], connectors, final_answer="done").write(tmp_path / "e.json")

        connectors[0].teardown()  # the environment is now gone

        restored = load_evidence(path)
        tickets = json.loads(restored.items[0].content)["tickets"]
        assert next(t for t in tickets if t["id"] == "T-1001")["priority"] == "urgent"

    def test_parent_directories_are_created(self, connectors, tmp_path):
        path = capture([], connectors, final_answer="").write(tmp_path / "a" / "b" / "e.json")
        assert path.exists()

    def test_the_file_is_plain_json(self, connectors, tmp_path):
        path = capture([request()], connectors, final_answer="done").write(tmp_path / "e.json")
        assert json.loads(path.read_text())["final_answer"] == "done"

    def test_loading_a_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_evidence(tmp_path / "nope.json")

    def test_very_large_content_is_truncated_with_a_marker(self, connectors, tmp_path):
        item = EvidenceItem(request=request(), content="x" * 900_000)
        evidence = Evidence(final_answer="", items=(item,))
        restored = load_evidence(evidence.write(tmp_path / "e.json"))
        assert len(restored.items[0].content) < 900_000
        assert "truncated" in restored.items[0].content


class TestIntegrationWithAnAgentRun:
    def test_capture_after_a_real_agent_run(self, connectors):
        from crossbar.agents import ModelAgent
        from crossbar.domain import load_test
        from crossbar.providers import ScriptedProvider, scripted_step

        task = load_test("tests/fixtures/tests/support-triage").task("escalate-outage")
        provider = ScriptedProvider([
            scripted_step(tool_calls=[
                ("tickets__set_priority", {"id": "T-1001", "priority": "urgent"}),
                ("tickets__set_priority", {"id": "T-1004", "priority": "urgent"}),
            ]),
            scripted_step(text="Escalated T-1001 and T-1004."),
        ])
        trajectory = ModelAgent("local", provider).run(task, connectors)

        evidence = capture(
            [request(label="every ticket after the run")],
            connectors,
            final_answer=trajectory.final_text,
        )

        assert evidence.is_complete
        assert evidence.final_answer == "Escalated T-1001 and T-1004."
        tickets = json.loads(evidence.items[0].content)["tickets"]
        urgent = {t["id"] for t in tickets if t["priority"] == "urgent"}
        assert urgent == {"T-1001", "T-1004"}
