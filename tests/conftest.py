import os
import sys
import tempfile
from pathlib import Path

# Isolated data dir per test run — must be set before clipcore modules import.
os.environ["CLIPCORE_DATA_DIR"] = tempfile.mkdtemp(prefix="clipcore-test-")
os.environ.setdefault("TWITCH_CLIENT_ID", "")
os.environ.setdefault("TWITCH_CLIENT_SECRET", "")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
