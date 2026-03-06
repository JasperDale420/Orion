import asyncio
import os
import sys

# Add src to path if needed (assuming we're running from Orion root or it's installed)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "src")))

from orion.agents.codex_client import run_codex_completion
from orion.config import agent_settings


async def main():
    print("Testing AI Gateway code editing tools via run_codex_completion...")

    # Create a dummy file to read and write
    test_filepath = "dummy_test_file.txt"
    with open(test_filepath, "w") as f:
        f.write("Initial content: Need to fix this bug.")

    messages = [
        {
            "role": "system",
            "content": "You have tools to read_file, write_file, and run_command. Your task is to read the file 'dummy_test_file.txt', change its contents to 'Bug fixed successfully.', and then run the command 'ls -la'.",
        },
        {"role": "user", "content": "Please complete your task now."},
    ]

    print(f"Using model: {agent_settings.model_name}")
    print(f"Gateway URL: {agent_settings.ai_gateway_url}")

    try:
        response = await run_codex_completion(messages=messages, model=agent_settings.model_name, timeout_seconds=60)
        print("\n=== FINAL LLM RESPONSE ===")
        print(response)

        # Verify the file was changed
        with open(test_filepath) as f:
            new_content = f.read()
        print("\n=== VERIFICATION ===")
        print(f"File content is now: {new_content}")
        if "Bug fixed successfully" in new_content:
            print("SUCCESS: File was edited correctly by the LLM.")
        else:
            print("FAILED: File was not edited as expected.")

    except Exception as e:
        print(f"\nERROR running test: {e}")

    finally:
        # Cleanup
        if os.path.exists(test_filepath):
            os.remove(test_filepath)


if __name__ == "__main__":
    asyncio.run(main())
