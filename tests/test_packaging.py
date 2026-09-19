"""Packaging: crossbar is installed, not cloned.

Anything reachable only from the repository root does not exist for the people
who will actually run this.
"""
from pathlib import Path

import pytest


class TestBundledExample:
    def test_the_example_test_resolves_through_the_package(self):
        from crossbar.demo.scripted import example_test_path

        path = example_test_path()
        assert path.is_dir()
        assert (path / "test.yaml").exists()

    def test_it_lives_inside_the_package_not_beside_it(self):
        import crossbar
        from crossbar.demo.scripted import example_test_path

        package_root = Path(crossbar.__file__).parent
        assert example_test_path().is_relative_to(package_root)

    def test_it_loads(self):
        from crossbar.connectors import known_connectors
        from crossbar.demo.scripted import example_test_path
        from crossbar.domain import load_test

        test = load_test(example_test_path(), known_connectors=known_connectors())
        assert len(test.tasks) == 4

    def test_its_servers_are_launched_through_the_installed_package(self):
        """`python -m crossbar.demo.tickets_server`, not a path into a checkout."""
        from crossbar.demo.scripted import example_test_path
        from crossbar.domain import load_test

        servers = load_test(example_test_path()).environment.connectors[0].options["servers"]
        assert servers[0]["args"] == ["-m", "crossbar.demo.tickets_server"]

    def test_the_demo_runs_without_a_repository(self, tmp_path, monkeypatch):
        """Nothing may depend on the current directory being a checkout."""
        from crossbar.demo import run_demo

        monkeypatch.chdir(tmp_path)
        assert run_demo(str(tmp_path / "out")).attempts


class TestPackageData:
    def test_every_example_yaml_is_declared_as_package_data(self):
        """A file inside the package is not shipped unless it is declared."""
        import tomllib

        root = Path(__file__).resolve().parent.parent
        config = tomllib.load(open(root / "pyproject.toml", "rb"))
        patterns = config["tool"]["setuptools"]["package-data"]["crossbar"]
        assert any("examples" in p and p.endswith(".yaml") for p in patterns)
