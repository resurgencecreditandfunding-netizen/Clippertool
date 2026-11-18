import os
import sys
import tempfile
from pathlib import Path

# Isolated data dir per test run — must be set before clipcore modules import.
os.environ["CLIPCORE_DATA_DIR"] = tempfile.mkdtemp(prefix="clipcore-test-")
os.environ["TWITCH_CLIENT_ID"] = ""
os.environ["TWITCH_CLIENT_SECRET"] = ""
# Local test server must not go through any HTTP proxy.
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
