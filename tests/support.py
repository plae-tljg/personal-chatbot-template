"""Shared test scaffolding.

Every test builds a throwaway database from the real ``content/`` plus a
snapshot of real GitHub responses in ``tests/fixtures/github``. No network, no
flakiness, and the data is genuinely messy: snake_case and CamelCase names,
forks mixed with originals, empty descriptions, one repository with no language.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
#: A snapshot of real API responses. Layout matches data/cache, which is why
#: this points at the parent: the cache root contains a github/ subdirectory.
FIXTURE_CACHE = ROOT / "tests" / "fixtures"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from personal_chatbots.build import build  # noqa: E402
from personal_chatbots.config import Config  # noqa: E402
from personal_chatbots.engine import Runtime  # noqa: E402
from personal_chatbots.store import Store  # noqa: E402


class BuiltCase(unittest.TestCase):
    """A TestCase with one freshly built database per class."""

    tmp: Path
    cfg: Config
    store: Store
    runtime: Runtime

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="pc-test-"))
        cls.cfg = Config.load(
            ROOT, db_path=cls.tmp / "bot.db", cache_dir=FIXTURE_CACHE
        )
        build(cls.cfg, offline=True)
        cls.store = Store(cls.cfg.db_path)
        cls.runtime = Runtime.from_store(cls.store, cls.cfg)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.store.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def ask(self, question: str):
        return self.runtime.ask(question)
