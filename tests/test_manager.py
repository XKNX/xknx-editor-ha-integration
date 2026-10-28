"""Keeping the editor running: status, and the waits before the next start."""

from collections import deque
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from custom_components.xknx_editor import manager as manager_module
from custom_components.xknx_editor.const import DOMAIN
from custom_components.xknx_editor.environment import InstallError
from custom_components.xknx_editor.manager import ISSUE_NOT_RUNNING, EditorManager


class EndingProcess:
    """An editor run that ends with ``code`` as soon as it is watched."""

    def __init__(self, code: int) -> None:
        self.code = code
        self.output = deque(["last words"])

    async def async_wait(self) -> int:
        return self.code

    async def async_stop(self) -> None:
        pass


async def test_failed_starts_wait_longer_each_time(hass: HomeAssistant) -> None:
    manager = EditorManager(hass, password="x", requirements="r.txt")
    delays: list[float] = []
    statuses: list[str] = []

    async def start() -> None:
        raise InstallError("no wheel for this platform")

    async def sleep(delay: float) -> None:
        delays.append(delay)
        statuses.append(manager.status)
        if len(delays) == 5:
            manager._stopping = True

    with (
        patch.object(manager, "_async_start", start),
        patch.object(manager_module.asyncio, "sleep", sleep),
    ):
        await manager.async_run()
    assert delays == [5, 30, 120, 600, 600]
    assert statuses[0] == "Failed to start (next try in 5 s)"
    assert manager.detail == "no wheel for this platform"
    await manager.client.close()


async def test_an_ended_editor_is_started_again(hass: HomeAssistant) -> None:
    manager = EditorManager(hass, password="x", requirements="r.txt")
    starts = 0
    details: list[str] = []

    async def start() -> None:
        nonlocal starts
        starts += 1
        manager.process = EndingProcess(1)  # type: ignore[assignment]

    async def sleep(delay: float) -> None:
        details.append(manager.detail)
        if starts == 2:
            manager._stopping = True

    with (
        patch.object(manager, "_async_start", start),
        patch.object(manager_module.asyncio, "sleep", sleep),
    ):
        await manager.async_run()
    assert starts == 2
    assert manager.status.startswith("Failed: the editor ended")
    assert details[0] == "Exit code 1\nlast words"
    await manager.client.close()


async def test_stop_ends_the_watch(hass: HomeAssistant) -> None:
    manager = EditorManager(hass, password="x", requirements="r.txt")
    stopped = False

    class StoppedProcess(EndingProcess):
        async def async_wait(self) -> int:
            await manager.async_stop()
            return 0

        async def async_stop(self) -> None:
            nonlocal stopped
            stopped = True

    async def start() -> None:
        manager.process = StoppedProcess(0)  # type: ignore[assignment]

    with patch.object(manager, "_async_start", start):
        await manager.async_run()
    assert stopped
    assert manager.client.closed


async def test_repair_issue_while_not_running(hass: HomeAssistant) -> None:
    manager = EditorManager(hass, password="x", requirements="r.txt")
    registry = ir.async_get(hass)
    attempts = 0
    seen: list[ir.IssueEntry | None] = []

    async def start() -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise InstallError("x" * 2000)
        manager.process = EndingProcess(0)  # type: ignore[assignment]

        async def wait() -> int:
            # Running: the issue is gone.
            seen.append(registry.async_get_issue(DOMAIN, ISSUE_NOT_RUNNING))
            await manager.async_stop()
            return 0

        manager.process.async_wait = wait  # type: ignore[method-assign]

    async def sleep(delay: float) -> None:
        seen.append(registry.async_get_issue(DOMAIN, ISSUE_NOT_RUNNING))

    with (
        patch.object(manager, "_async_start", start),
        patch.object(manager_module.asyncio, "sleep", sleep),
    ):
        await manager.async_run()
    issue = seen[0]
    assert issue is not None
    assert issue.severity is ir.IssueSeverity.ERROR
    assert issue.translation_placeholders is not None
    assert issue.translation_placeholders["status"] == "Failed to start"
    # A long cause is shortened to its end.
    assert issue.translation_placeholders["detail"] == "…" + "x" * 500
    assert seen[1] is None
