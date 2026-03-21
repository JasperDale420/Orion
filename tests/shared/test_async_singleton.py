import pytest

from orion.shared.patterns import AsyncSingleton


class _AsyncInitSingleton(AsyncSingleton):
    async_init_calls = 0

    async def _async_init(self) -> None:
        type(self).async_init_calls += 1


@pytest.mark.asyncio
async def test_async_singleton_calls_async_init_once() -> None:
    _AsyncInitSingleton._reset_instance()
    _AsyncInitSingleton.async_init_calls = 0

    first = await _AsyncInitSingleton.get_instance()
    second = await _AsyncInitSingleton.get_instance()

    assert first is second
    assert _AsyncInitSingleton.async_init_calls == 1
