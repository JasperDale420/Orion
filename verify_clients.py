import os
import sys

# Add src to pythonpath
sys.path.append(os.path.join(os.path.dirname(__file__), "../src"))

try:
    from orion.unusualwhales.client import UnusualWhalesClient

    print("✅ UnusualWhalesClient imported successfully")
except ImportError as e:
    print(f"❌ Failed to import UnusualWhalesClient: {e}")


print("Verification complete.")
