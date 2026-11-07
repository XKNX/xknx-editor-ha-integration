"""Fixtures of every test."""

import asyncio
from collections.abc import Generator
from unittest.mock import patch

import pytest

from custom_components.xknx_editor.link import EditorLink


@pytest.fixture(autouse=True)
def no_link(request: pytest.FixtureRequest) -> Generator[None]:
    """The link to the editor stays closed where a test sets up the integration: it would sign in
    to the test's stand-in for the editor too. test_link.py tests it on its own."""
    if request.node.module.__name__.endswith("test_link"):
        yield
        return

    async def idle(_self: EditorLink) -> None:
        await asyncio.Event().wait()

    with patch.object(EditorLink, "async_run", idle):
        yield
