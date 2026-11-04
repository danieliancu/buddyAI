"""What is running: the release number and the Git commit, for the System page and /healthz.

The commit comes from the environment, because the image has no .git folder:
  BUDDYAI_BUILD_COMMIT  set in the image from the SOURCE_COMMIT build argument (deploy/server.Dockerfile);
  SOURCE_COMMIT         set by Coolify in the running container, from its generated .env file.
Anything that does not look like a commit id (empty, "HEAD", "unknown") is reported as "unknown", so nothing
else from the environment can ever reach the public /healthz answer.
"""

from __future__ import annotations

import os
import re

VERSION = "0.1.0"  # keep in step with server/pyproject.toml
UNKNOWN_COMMIT = "unknown"

_COMMIT_ID = re.compile(r"[0-9a-f]{7,40}")


def commit() -> str:
    for name in ("BUDDYAI_BUILD_COMMIT", "SOURCE_COMMIT"):
        value = os.environ.get(name, "").strip().lower()
        if _COMMIT_ID.fullmatch(value):
            return value
    return UNKNOWN_COMMIT
