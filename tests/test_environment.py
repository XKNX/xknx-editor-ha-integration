"""The editor's environment: when it is created and filled, and with what."""

from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant.test_util.aiohttp import AiohttpClientMocker

from custom_components.xknx_editor import environment
from custom_components.xknx_editor.environment import EditorEnvironment, InstallError


class FakeUv:
    """Records the uv calls; ``venv`` creates the environment's Python."""

    def __init__(self, env: EditorEnvironment) -> None:
        self.env = env
        self.calls: list[str] = []

    async def __call__(self, *args: str) -> None:
        self.calls.append(args[0])
        if args[0] == "venv":
            self.env.python.parent.mkdir(parents=True, exist_ok=True)
            self.env.python.write_text("")
        if args[0] == "pip":
            assert args[1] == "sync"
            assert Path(args[-1]).read_text() == self.requirements  # noqa: ASYNC240
            assert "--no-build" in args

    requirements = ""
    # What the import check of the environment reports, one answer per check (None: fine).
    problems: list[str | None]

    async def check(self, python: Path) -> str | None:
        self.calls.append("check")
        return self.problems.pop(0) if getattr(self, "problems", None) else None


@pytest.fixture
def env(tmp_path: Path) -> EditorEnvironment:
    return EditorEnvironment(tmp_path / "cache")


def test_the_environments_python(env: EditorEnvironment) -> None:
    with patch.object(environment.sys, "platform", "linux"):
        assert env.python == env.venv / "bin" / "python"
    with patch.object(environment.sys, "platform", "win32"):
        assert env.python == env.venv / "Scripts" / "python.exe"


async def ensure(env: EditorEnvironment, uv: FakeUv, requirements: str) -> list[str]:
    uv.calls.clear()
    uv.requirements = requirements
    steps: list[str] = []
    with patch.object(environment, "_uv", uv), patch.object(environment, "_check", uv.check):
        assert await env.async_ensure(requirements, steps.append) == env.python
    return uv.calls


async def test_installs_once_per_requirements(env: EditorEnvironment) -> None:
    uv = FakeUv(env)
    assert await ensure(env, uv, "a==1\n") == ["venv", "pip", "check"]
    # Nothing to install while nothing changed; only the import check.
    assert await ensure(env, uv, "a==1\n") == ["check"]
    # Other requirements: installed into the same environment.
    assert await ensure(env, uv, "a==2\n") == ["pip", "check"]


async def test_new_environment_for_another_python(env: EditorEnvironment) -> None:
    uv = FakeUv(env)
    await ensure(env, uv, "a==1\n")
    with patch.object(environment, "python_tag", return_value="3.15 /usr/local/bin/python3"):
        assert await ensure(env, uv, "a==1\n") == ["venv", "pip", "check"]


async def test_new_environment_when_it_is_gone(env: EditorEnvironment) -> None:
    uv = FakeUv(env)
    await ensure(env, uv, "a==1\n")
    env.python.unlink()
    assert await ensure(env, uv, "a==1\n") == ["venv", "pip", "check"]


async def test_failed_install_is_tried_again(env: EditorEnvironment) -> None:
    uv = FakeUv(env)

    async def failing(*args: str) -> None:
        await uv(*args)
        if args[0] == "pip":
            raise InstallError("no wheel")

    with patch.object(environment, "_uv", failing), pytest.raises(InstallError):
        uv.requirements = "a==1\n"
        await env.async_ensure("a==1\n")
    assert await ensure(env, uv, "a==1\n") == ["pip", "check"]


async def test_damaged_environment_is_created_again(env: EditorEnvironment) -> None:
    """An environment whose packages cannot be imported (an install cut off by a restart left
    numpy half written) is created again, though its requirements did not change."""
    uv = FakeUv(env)
    await ensure(env, uv, "a==1\n")
    uv.problems = ["AttributeError: module 'numpy' has no attribute 'dtype'", None]
    assert await ensure(env, uv, "a==1\n") == ["check", "venv", "pip", "check"]
    assert await ensure(env, uv, "a==1\n") == ["check"]


async def test_damaged_install_is_created_again(env: EditorEnvironment) -> None:
    uv = FakeUv(env)
    await ensure(env, uv, "a==1\n")
    uv.problems = ["ImportError: numpy", None]
    assert await ensure(env, uv, "a==2\n") == ["pip", "check", "venv", "pip", "check"]


async def test_still_damaged_after_creating_it_again(env: EditorEnvironment) -> None:
    uv = FakeUv(env)
    await ensure(env, uv, "a==1\n")
    uv.problems = ["ImportError: numpy", "ImportError: numpy"]
    uv.requirements = "a==1\n"
    with (
        patch.object(environment, "_uv", uv),
        patch.object(environment, "_check", uv.check),
        pytest.raises(InstallError, match="cannot be imported: ImportError: numpy"),
    ):
        await env.async_ensure("a==1\n")
    # Not recorded as done: the next start tries again.
    assert env.state() == {"python": environment.python_tag()}


async def test_check_names_why_the_packages_cannot_be_imported(tmp_path: Path) -> None:
    # A Python that is not there, and one without the editor's packages.
    assert await environment._check(tmp_path / "python") is not None
    problem = await environment._check(Path(environment.sys.executable))
    assert problem is None or "Error" in problem


async def test_uv_failure_names_the_output() -> None:
    with pytest.raises(InstallError, match="uv definitely-not-a-command failed"):
        await environment._uv("definitely-not-a-command")


async def test_requirements_from_a_file(hass: HomeAssistant, env: EditorEnvironment) -> None:
    source = env.root.parent / "requirements.txt"
    source.write_text("b==2\n")
    session = async_get_clientsession(hass)
    assert await env.async_requirements(session, str(source)) == "b==2\n"
    with pytest.raises(InstallError, match="Could not read"):
        await env.async_requirements(session, str(source) + ".missing")


async def test_requirements_from_a_url(
    hass: HomeAssistant, env: EditorEnvironment, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get("https://example.com/req.txt", text="c==3\n")
    aioclient_mock.get("https://example.com/missing.txt", status=404)
    session = async_get_clientsession(hass)
    assert await env.async_requirements(session, "https://example.com/req.txt") == "c==3\n"
    with pytest.raises(InstallError, match="Could not download"):
        await env.async_requirements(session, "https://example.com/missing.txt")
