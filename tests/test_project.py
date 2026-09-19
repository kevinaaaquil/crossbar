"""The .crossbar project folder: how an installed binary finds its setup."""
import textwrap

import pytest

from crossbar.project import (
    CONFIG_NAME,
    PROJECT_DIR,
    ProjectError,
    find_project,
    init_project,
    load_project,
)

CONFIG = textwrap.dedent(
    """
    models:
      - id: local
        provider: openai
        model: qwen
        base_url: http://localhost:8000/v1
      - id: frontier
        provider: anthropic
        model: big
    roles:
      candidate: local
      baseline: frontier
    tests:
      - tests/support-triage
    run:
      results_dir: runs
      judge_tests: 1
    """
)


@pytest.fixture
def project(tmp_path):
    from crossbar.demo.scripted import example_test_path
    import shutil

    root = tmp_path / "work"
    (root / PROJECT_DIR).mkdir(parents=True)
    (root / PROJECT_DIR / CONFIG_NAME).write_text(CONFIG)
    shutil.copytree(example_test_path(), root / PROJECT_DIR / "tests" / "support-triage")
    return root


class TestDiscovery:
    def test_it_finds_the_project_folder_in_the_current_directory(self, project, monkeypatch):
        monkeypatch.chdir(project)
        assert find_project() == project / PROJECT_DIR

    def test_it_walks_up_from_a_subdirectory(self, project, monkeypatch):
        nested = project / "a" / "b"
        nested.mkdir(parents=True)
        monkeypatch.chdir(nested)
        assert find_project() == project / PROJECT_DIR

    def test_it_stops_at_the_filesystem_root(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert find_project() is None

    def test_an_explicit_start_can_be_given(self, project):
        assert find_project(project) == project / PROJECT_DIR


class TestLoading:
    def test_the_roster_comes_from_the_config(self, project):
        loaded = load_project(project)
        assert [m.id for m in loaded.roster.models] == ["local", "frontier"]
        assert loaded.roster.roles["candidate"] == "local"

    def test_test_paths_resolve_relative_to_the_project_folder(self, project):
        loaded = load_project(project)
        assert len(loaded.tests) == 1
        assert loaded.tests[0].name == "Support triage"

    def test_run_settings_are_read(self, project):
        loaded = load_project(project)
        assert loaded.judge_tests == 1
        assert loaded.results_dir.name == "runs"

    def test_the_results_directory_sits_inside_the_project_folder(self, project):
        """Results belong with the config, not scattered in the user's repo."""
        loaded = load_project(project)
        assert loaded.results_dir.is_relative_to(project / PROJECT_DIR)

    def test_a_missing_project_is_reported_with_what_to_do(self, tmp_path):
        with pytest.raises(ProjectError, match="crossbar init"):
            load_project(tmp_path)

    def test_a_malformed_config_names_the_file(self, project):
        (project / PROJECT_DIR / CONFIG_NAME).write_text("models: [oops\n")
        with pytest.raises(ProjectError, match=CONFIG_NAME):
            load_project(project)

    def test_a_config_with_no_tests_is_reported(self, project):
        (project / PROJECT_DIR / CONFIG_NAME).write_text(
            CONFIG.replace("tests:\n  - tests/support-triage", "tests: []")
        )
        with pytest.raises(ProjectError, match="tests"):
            load_project(project)

    def test_a_test_path_that_does_not_exist_is_reported(self, project):
        (project / PROJECT_DIR / CONFIG_NAME).write_text(
            CONFIG.replace("tests/support-triage", "tests/nope")
        )
        with pytest.raises(ProjectError, match="nope"):
            load_project(project)

    def test_bad_roster_errors_surface_intact(self, project):
        (project / PROJECT_DIR / CONFIG_NAME).write_text(
            CONFIG.replace("candidate: local", "candidate: ghost")
        )
        with pytest.raises(ProjectError, match="ghost"):
            load_project(project)

    def test_unknown_connectors_are_caught_when_the_project_loads(self, project):
        env = project / PROJECT_DIR / "tests" / "support-triage" / "env.yaml"
        env.write_text("kind: local\nconnectors:\n  telepathy: {}\n")
        with pytest.raises(ProjectError, match="telepathy"):
            load_project(project)


class TestInit:
    def test_it_creates_the_project_folder(self, tmp_path):
        init_project(tmp_path)
        assert (tmp_path / PROJECT_DIR / CONFIG_NAME).exists()

    def test_it_copies_the_example_test_in(self, tmp_path):
        init_project(tmp_path)
        example = tmp_path / PROJECT_DIR / "tests" / "support-triage"
        assert (example / "test.yaml").exists()
        assert len(list(example.glob("*.task.yaml"))) == 4

    def test_what_it_writes_loads(self, tmp_path):
        init_project(tmp_path)
        loaded = load_project(tmp_path)
        assert loaded.tests[0].name == "Support triage"

    def test_the_config_it_writes_is_commented_for_a_human(self, tmp_path):
        init_project(tmp_path)
        text = (tmp_path / PROJECT_DIR / CONFIG_NAME).read_text()
        assert "#" in text
        assert "api_key_env" in text

    def test_it_refuses_to_overwrite_an_existing_project(self, tmp_path):
        init_project(tmp_path)
        with pytest.raises(ProjectError, match="exists"):
            init_project(tmp_path)

    def test_it_reports_what_it_created(self, tmp_path):
        created = init_project(tmp_path)
        assert any(CONFIG_NAME in str(p) for p in created)

    def test_a_fresh_project_needs_editing_before_it_runs(self, tmp_path):
        """The scaffold points at placeholder endpoints on purpose — it must not
        look like a working setup that silently does nothing."""
        init_project(tmp_path)
        text = (tmp_path / PROJECT_DIR / CONFIG_NAME).read_text().lower()
        assert "edit" in text or "replace" in text or "your" in text
