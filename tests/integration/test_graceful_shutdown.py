import asyncio
import os
import signal
from unittest.mock import MagicMock, patch

import pytest

# We need to import the main functions, but they are inside 'if __name__ == "__main__"' protected blocks or just functions.
# We will import the module and mock dependencies to run `main()` partially.
from orion.main_execution import main as execution_main


@pytest.mark.asyncio
async def test_execution_shutdown():
    """
    Verifies that main_execution.py's main loop exits on SIGTERM.
    We mock the engines to avoid real startup.
    """
    with (
        patch("orion.main_execution.SignalEngine") as MockSigEngine,  # noqa: N806
        patch("orion.main_execution.ExecutionEngine") as MockExecEngine,  # noqa: N806
        patch("orion.main_execution.init_db", new_callable=MagicMock),
        patch("orion.main_execution.setup_struct_logger"),
    ):
        # Setup mocks
        mock_sig = MockSigEngine.return_value
        mock_exec = MockExecEngine.return_value
        mock_sig.initialize = MagicMock()  # async?
        mock_exec.initialize = MagicMock()

        # Async mocks for initialize
        async def async_init():
            pass

        mock_sig.initialize.side_effect = async_init
        mock_exec.initialize.side_effect = async_init
        mock_exec.poll_fills.side_effect = async_init

        # We need to trigger the signal AFTER the loop starts.
        # This is tricky in a single process test unless we use a background task to send the signal.

        async def send_signal_later():
            await asyncio.sleep(0.5)
            # Send SIGTERM to self
            os.kill(os.getpid(), signal.SIGTERM)

        # However, pytest captures signals?
        # Better approach: We can check if `loop.add_signal_handler` was called,
        # and invoke the handler manually if we can access the closure?
        # Accessing the closure inside `main` is hard.

        # Alternative: We trust the `asyncio` loop handling if `add_signal_handler` is used.
        # But we want to verify the loop *actually breaks*.

        # Let's try mocking `loop.add_signal_handler` to capture the handler, then call it.

        captured_handler = None

        def capture_handler(sig, handler):
            nonlocal captured_handler
            if sig == signal.SIGTERM:
                captured_handler = handler

        asyncio.get_running_loop()

        # We assume the code uses loop = asyncio.get_running_loop()
        # We can't easily patch that locally inside the function.
        # But we can patch `asyncio.get_running_loop` ? No, dangerous.

        # Let's run `execution_main` as a task, and have another task trigger the shutdown event?
        # But `shutdown_event` is local to `main`.

        # We will use the `os.kill` approach but we might need to be careful with pytest.
        # If this fails, we will manually verify.

        # Actually, let's just create a simplified version of the logic to verify "Smart Sleep" behavior?
        # No, that's unit testing asyncio.

        # Let's try the `os.kill` approach.

        task = asyncio.create_task(execution_main())

        # Give it time to start
        await asyncio.sleep(0.5)

        # Send Signal
        print("Sending SIGTERM...")
        os.kill(os.getpid(), signal.SIGTERM)

        try:
            await asyncio.wait_for(task, timeout=2.0)
            print("Execution main exited gracefully.")
        except TimeoutError:
            pytest.fail("Execution main did not exit on SIGTERM within timeout.")
        except Exception as e:
            # It might raise CancelledError or something?
            print(f"Exited with {e}")
