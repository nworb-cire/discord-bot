import sys
from types import SimpleNamespace

import pytest

from bot.test_gate import (
    CI_MODE,
    DEFAULT_COVERAGE_FAIL_UNDER,
    PRE_COMMIT_MODE,
    PRE_PUSH_MODE,
    TestGate,
    TestGateConfig,
    main,
)


def test_config_from_env_defaults(monkeypatch):
    monkeypatch.delenv("BOT_TEST_GATE_MODE", raising=False)
    monkeypatch.delenv("BOT_TEST_COVERAGE_FAIL_UNDER", raising=False)
    monkeypatch.delenv("BOT_TEST_JUNIT_XML", raising=False)
    monkeypatch.delenv("BOT_TEST_COVERAGE_XML", raising=False)
    monkeypatch.delenv("BOT_TEST_DURATIONS", raising=False)

    config = TestGateConfig.from_env()

    assert config == TestGateConfig(
        mode=PRE_COMMIT_MODE,
        coverage_fail_under=DEFAULT_COVERAGE_FAIL_UNDER,
    )


def test_config_from_env_reads_ci_options(monkeypatch):
    monkeypatch.setenv("BOT_TEST_GATE_MODE", CI_MODE)
    monkeypatch.setenv("BOT_TEST_COVERAGE_FAIL_UNDER", "92")
    monkeypatch.setenv("BOT_TEST_JUNIT_XML", "reports/junit.xml")
    monkeypatch.setenv("BOT_TEST_COVERAGE_XML", "reports/coverage.xml")
    monkeypatch.setenv("BOT_TEST_DURATIONS", "10")

    config = TestGateConfig.from_env()

    assert config == TestGateConfig(
        mode=CI_MODE,
        coverage_fail_under=92,
        junit_xml="reports/junit.xml",
        coverage_xml="reports/coverage.xml",
        durations=10,
    )


def test_config_from_env_rejects_invalid_mode(monkeypatch):
    monkeypatch.setenv("BOT_TEST_GATE_MODE", "ship-it")

    with pytest.raises(ValueError):
        TestGateConfig.from_env()


def test_pre_commit_args_use_testmon():
    gate = TestGate(TestGateConfig(mode=PRE_COMMIT_MODE))

    assert gate._pytest_args() == ["-m", "pytest", "tests", "--testmon", "-q"]


def test_pre_push_args_use_source_only_coverage():
    gate = TestGate(
        TestGateConfig(
            mode=PRE_PUSH_MODE,
            coverage_fail_under=91,
            junit_xml="reports/junit.xml",
            coverage_xml="reports/coverage.xml",
            durations=10,
        )
    )

    assert gate._pytest_args() == [
        "-m",
        "pytest",
        "tests",
        "--cov=src/bot",
        "--cov-report=term-missing:skip-covered",
        "--cov-fail-under=91",
        "--junitxml=reports/junit.xml",
        "--cov-report=xml:reports/coverage.xml",
        "--durations=10",
    ]


def test_run_executes_current_python(monkeypatch):
    gate = TestGate(TestGateConfig(mode=PRE_COMMIT_MODE))
    captured = {}

    def fake_run(command, check):
        captured["command"] = command
        captured["check"] = check
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("bot.test_gate.subprocess.run", fake_run)

    result = gate.run()

    assert result == 0
    assert captured["command"][0] == sys.executable
    assert captured["command"][1:] == ["-m", "pytest", "tests", "--testmon", "-q"]
    assert captured["check"] is False


def test_main_returns_error_for_invalid_mode(monkeypatch):
    monkeypatch.setenv("BOT_TEST_GATE_MODE", "bad-mode")

    assert main() == 2
