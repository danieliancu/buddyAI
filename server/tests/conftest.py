import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
_tmp = tempfile.mkdtemp(prefix="buddyai-test-")
os.environ.setdefault("BUDDYAI_DATA_DIR", _tmp)
os.environ.setdefault("BUDDYAI_MOCK_PROVIDERS", "true")
os.environ.setdefault("BUDDYAI_MDNS_ENABLED", "false")
