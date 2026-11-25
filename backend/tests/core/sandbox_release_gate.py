"""Require all collected sandbox acceptance cases to pass."""

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from _pytest.terminal import TerminalReporter


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if exitstatus != pytest.ExitCode.OK:
        return
    reporter: TerminalReporter | None = session.config.pluginmanager.get_plugin('terminalreporter')
    if reporter is None:
        session.exitstatus = pytest.ExitCode.INTERNAL_ERROR
        return
    passed = len(reporter.stats.get('passed', ()))
    if not session.testscollected or passed != session.testscollected or reporter.stats.get('deselected'):
        reporter.write_line('Workspace sandbox acceptance requires every selected case to pass.', red=True)
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
