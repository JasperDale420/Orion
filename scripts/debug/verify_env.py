import os

from dotenv import load_dotenv

# Try loading .env explicitely
load_dotenv()

key = os.getenv("UW_API_KEY")

if key:
    print(f"UW_API_KEY found: {key[:4]}...{key[-4:]} (Length: {len(key)})")
else:
    print("UW_API_KEY NOT FOUND in environment.")

from orion.config import system_settings  # noqa: E402

print(f"Config SystemSettings Key: {str(system_settings.uw_api_key)[:4]}...")
