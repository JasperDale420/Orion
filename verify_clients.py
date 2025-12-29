import os
import sys

# Add src to pythonpath
sys.path.append(os.path.join(os.path.dirname(__file__), "../src"))

import importlib.util

spec = importlib.util.find_spec("orion.unusualwhales.client")
if spec:
    print("✅ UnusualWhalesClient imported successfully")
else:
    print("❌ Failed to find module 'orion.unusualwhales.client'")


print("Verification complete.")
