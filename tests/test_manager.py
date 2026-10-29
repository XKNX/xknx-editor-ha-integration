"""Keeping the editor running: status, and the waits before the next start."""

from collections import deque
from pathlib import Path
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from custom_components.xknx_editor import manager as manager_module
from custom_components.xknx_editor.const import DATA_FOLDER, DOMAIN, EDITOR_VERSION
from custom_components.xknx_editor.dotnet import DotnetError
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


async def test_mcp_address_is_home_assistants(hass: HomeAssistant) -> None:
    manager = EditorManager(hass, password="x", requirements="r.txt")
    await hass.config.async_update(external_url="https://ha.example:8123")
    assert manager._mcp_url() == "https://ha.example:8123/api/xknx_editor/mcp"
    with patch.object(manager_module, "get_url", side_effect=manager_module.NoURLAvailableError):
        assert manager._mcp_url() is None
    await manager.client.close()


async def test_a_long_run_resets_the_waits(hass: HomeAssistant) -> None:
    manager = EditorManager(hass, password="x", requirements="r.txt")
    delays: list[float] = []

    async def start() -> None:
        manager.process = EndingProcess(1)  # type: ignore[assignment]

    async def sleep(delay: float) -> None:
        delays.append(delay)
        if len(delays) == 3:
            manager._stopping = True

    # Every run counts as long enough: the editor ran stably before it ended.
    with (
        patch.object(manager, "_async_start", start),
        patch.object(manager_module.asyncio, "sleep", sleep),
        patch.object(manager_module, "_STABLE_RUN", -1.0),
    ):
        await manager.async_run()
    assert delays == [5, 5, 5]
    await manager.client.close()


async def test_start_installs_and_starts_the_editor(hass: HomeAssistant, tmp_path: Path) -> None:
    manager = EditorManager(hass, password="pw", requirements="https://example.com/r.txt")
    steps: list[str] = []
    started: list[dict[str, object]] = []

    class FakeProcess:
        def __init__(self, python: Path, **kwargs: object) -> None:
            started.append({"python": python, **kwargs})

        async def async_start(self, client: object) -> None:
            assert client is manager.client
            steps.append("started")

    async def requirements(session: object, source: str) -> str:
        assert source == "https://example.com/r.txt"
        return "numpy==2.5.4\n"

    async def ensure(text: str, progress: object) -> Path:
        assert text == "numpy==2.5.4\n"
        return tmp_path / "python"

    async def dotnet(session: object, text: str) -> None:
        steps.append(manager.status)

    await hass.config.async_update(external_url="https://ha.example")
    with (
        patch.object(manager.environment, "async_requirements", requirements),
        patch.object(manager.environment, "async_ensure", ensure),
        patch.object(manager, "_async_ensure_dotnet", dotnet),
        patch.object(manager.dotnet, "environment", return_value={"DOTNET_ROOT": "/d"}),
        patch.object(manager_module, "EditorProcess", FakeProcess),
    ):
        await manager._async_start()
    # Installed first, then started.
    assert steps == [f"Installing XKNX Editor {EDITOR_VERSION}", "started"]
    assert manager.status == "Starting"
    assert manager.run == 1
    options = started[0]
    assert options["python"] == tmp_path / "python"
    assert options["password"] == "pw"
    assert options["data"] == Path(hass.config.path(DATA_FOLDER))
    assert options["files"] == Path(hass.config.path(DATA_FOLDER, "files"))
    assert options["cache"] == manager.environment.root / "cache"
    assert options["mcp_url"] == "https://ha.example/api/xknx_editor/mcp"
    assert options["environment"] == {"DOTNET_ROOT": "/d"}
    # A token of its own for every run.
    token = options["mcp_token"]
    assert isinstance(token, str) and len(token) >= 32
    await manager.client.close()


async def test_dotnet_runtime_and_helpers(hass: HomeAssistant) -> None:
    manager = EditorManager(hass, password="x", requirements="https://example.com/r.txt")
    with (
        patch.object(manager.dotnet, "async_ensure_runtime", AsyncMock()) as runtime,
        patch.object(manager.dotnet, "async_ensure_helpers", AsyncMock()) as helpers,
    ):
        await manager._async_ensure_dotnet("session", "requirements text")  # type: ignore[arg-type]
    runtime.assert_awaited_once_with("session")
    helpers.assert_awaited_once_with("session", "https://example.com/r.txt", "requirements text")
    assert manager.status == "Installing the .NET runtime"
    await manager.client.close()


async def test_editor_starts_without_dotnet(hass: HomeAssistant, caplog) -> None:
    manager = EditorManager(hass, password="x", requirements="r.txt")
    with patch.object(
        manager.dotnet,
        "async_ensure_runtime",
        side_effect=DotnetError("no .NET runtime for armv7l"),
    ):
        await manager._async_ensure_dotnet(None, "")  # type: ignore[arg-type]
    assert "will not read keys from assemblies: no .NET runtime for armv7l" in caplog.text
    await manager.client.close()
