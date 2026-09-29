"""Read a `.env` file into the environment.

Deliberately tiny and stdlib-only. One `KEY=value` per line, `#` comments, blank
lines ignored, surrounding quotes stripped, and an optional leading `export`
tolerated because that is what people paste. Anything more elaborate belongs in
the shell.

Why it exists: the fallback rung needs an API key, the key must not be committed,
and "remember to export it before every command" is how a feature ends up
looking broken. `.env` is already in `.gitignore`.

Values already present in the environment win. That is the principle of least
surprise for anyone running `OPENCODE_API_KEY=... pc serve`.
"""

from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(root: Path, filename: str = ".env") -> int:
    """Load ``root/filename`` into ``os.environ``. Returns how many keys were set."""
    path = Path(root) / filename
    if not path.exists():
        return 0

    loaded = 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip().strip("'\"")
        if not key or key in os.environ:
            continue
        os.environ[key] = value
        loaded += 1
    return loaded
