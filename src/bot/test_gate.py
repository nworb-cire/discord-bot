from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Final

from loguru import logger


DEFAULT_COVERAGE_FAIL_UNDER: Final[int] = 90
PRE_COMMIT_MODE: Final[str] = "pre-commit"
PRE_PUSH_MODE: Final[str] = "pre-push"
CI_MODE: Final[str] = "ci"
VALID_MODES: Final[set[str]] = {PRE_COMMIT_MODE, PRE_PUSH_MODE, CI_MODE}


@dataclass(frozen=True)
class TestGateConfig:
    __test__ = False

    mode: str = PRE_COMMIT_MODE
    coverage_fail_under: int = DEFAULT_COVERAGE_FAIL_UNDER
    junit_xml: str | None = None
    coverage_xml: str | None = None
    durations: int | None = None

    @classmethod
    def from_env(cls) -> "TestGateConfig":
        mode = os.getenv("BOT_TEST_GATE_MODE", PRE_COMMIT_MODE)
        if mode not in VALID_MODES:
            raise ValueError(
                f"Unsupported BOT_TEST_GATE_MODE={mode!r}. Expected one of {sorted(VALID_MODES)}."
            )

        coverage_fail_under = int(
            os.getenv("BOT_TEST_COVERAGE_FAIL_UNDER", str(DEFAULT_COVERAGE_FAIL_UNDER))
        )
        durations = os.getenv("BOT_TEST_DURATIONS")
        return cls(
            mode=mode,
            coverage_fail_under=coverage_fail_under,
            junit_xml=os.getenv("BOT_TEST_JUNIT_XML"),
            coverage_xml=os.getenv("BOT_TEST_COVERAGE_XML"),
            durations=int(durations) if durations else None,
        )


class TestGate:
    __test__ = False

    def __init__(self, config: TestGateConfig):
        self.config = config

    def run(self) -> int:
        args = self._pytest_args()
        command = [sys.executable, *args]
        logger.info("Running {} test gate: {}", self.config.mode, " ".join(command))
        completed = subprocess.run(command, check=False)
        return completed.returncode

    def _pytest_args(self) -> list[str]:
        if self.config.mode == PRE_COMMIT_MODE:
            return self._pre_commit_args()
        return self._coverage_args()

    def _pre_commit_args(self) -> list[str]:
        return ["-m", "pytest", "tests", "--testmon", "-q"]

    def _coverage_args(self) -> list[str]:
        args = [
            "-m",
            "pytest",
            "tests",
            "--cov=src/bot",
            "--cov-report=term-missing:skip-covered",
            f"--cov-fail-under={self.config.coverage_fail_under}",
        ]
        if self.config.junit_xml:
            args.append(f"--junitxml={self.config.junit_xml}")
        if self.config.coverage_xml:
            args.append(f"--cov-report=xml:{self.config.coverage_xml}")
        if self.config.durations is not None:
            args.append(f"--durations={self.config.durations}")
        return args


def main() -> int:
    try:
        config = TestGateConfig.from_env()
    except ValueError as exc:
        logger.error("{}", exc)
        return 2

    return TestGate(config).run()


if __name__ == "__main__":
    raise SystemExit(main())
