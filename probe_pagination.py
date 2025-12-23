import asyncio
import os
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv

# Ensure env vars are loaded
load_dotenv()

UW_API_KEY = os.getenv("UW_API_KEY")
UW_BASE_URL = os.getenv("UW_BASE_URL", "https://api.unusualwhales.com/api")


async def probe_flow_limit():
    print("\n--- Probing Flow Alerts Limit ---")
    url = f"{UW_BASE_URL}/option-trades/flow-alerts"
    headers = {"Authorization": f"Bearer {UW_API_KEY}"}

    # Test Date: Dec 19
    params = {"date": "2025-12-19", "limit": 1000}  # Try high limit

    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=headers, params=params)
        print(f"Limit 1000: {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json().get("data", [])
            print(f"Count: {len(data)}")


async def probe_alerts_older_than():
    print("\n--- Probing Alerts older_than ---")
    url = f"{UW_BASE_URL}/alerts"
    headers = {"Authorization": f"Bearer {UW_API_KEY}"}

    # Start from Now
    cursor_ts = datetime.now(timezone.utc).isoformat()

    async with httpx.AsyncClient() as client:
        # Page 1
        params = {"older_than": cursor_ts}
        resp = await client.get(url, headers=headers, params=params)
        print(f"Page 1 (older_than={cursor_ts}): {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json().get("data", [])
            print(f"Count: {len(data)}")
            if data:
                dates = sorted([x.get("created_at") for x in data])
                print(f"Range: {dates[0]} - {dates[-1]}")
                cursor_ts = dates[0]  # Oldest becomes new cursor

                # Page 2
                params = {"older_than": cursor_ts}
                resp = await client.get(url, headers=headers, params=params)
                print(f"Page 2 (older_than={cursor_ts}): {resp.status_code}")
                if resp.status_code == 200:
                    data2 = resp.json().get("data", [])
                    print(f"Count: {len(data2)}")
                    if data2:
                        dates2 = sorted([x.get("created_at") for x in data2])
                        print(f"Range: {dates2[0]} - {dates2[-1]}")


if __name__ == "__main__":
    asyncio.run(probe_flow_limit())
    asyncio.run(probe_alerts_older_than())
