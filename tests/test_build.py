"""Build invariants.

These are the properties that make "run a build at any time" safe. If any of
them breaks, the whole offline/online split stops being trustworthy.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from personal_chatbots.build import build
from personal_chatbots.config import Config
from personal_chatbots.store import Store
from tests.support import FIXTURE_CACHE, ROOT


class BuildInvariantTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="pc-build-"))
        self.cfg = Config.load(ROOT, db_path=self.tmp / "bot.db", cache_dir=FIXTURE_CACHE)

    def _build(self):
        return build(self.cfg, offline=True, cache_dir=FIXTURE_CACHE)

    def test_ingest_is_idempotent(self):
        self._build()
        second = self._build()
        entities = second["entities"]
        self.assertEqual(entities["created"], 0)
        self.assertEqual(entities["updated"], 0)
        self.assertGreater(entities["unchanged"], 0)

    def test_build_never_touches_live_state(self):
        self._build()
        with Store(self.cfg.db_path) as store:
            store.record_turn(
                session_id="s1",
                question="do you do weddings?",
                normalized="do you do weddings",
                answer="I don't know.",
                source="refuse",
                matched_slug="",
                citations=[],
                unresolved=True,
                latency_ms=1,
            )
            self.assertEqual(store.count_messages(), 2)

        report = self._build()
        self.assertEqual(report["messages"], 2)
        with Store(self.cfg.db_path) as store:
            self.assertEqual(store.count_messages(), 2)

    def test_removing_a_knowledge_row_archives_it(self):
        self._build()
        with Store(self.cfg.db_path) as store:
            before = store.conn.execute(
                "SELECT COUNT(*) AS n FROM knowledge WHERE status = 'live'"
            ).fetchone()["n"]
            archived = store.archive_missing_knowledge(keep_slugs=["repo.about"])
            self.assertEqual(archived, before - 1)
            rows = store.conn.execute(
                "SELECT COUNT(*) AS n FROM knowledge WHERE status = 'archived'"
            ).fetchone()["n"]
            # Archived, never deleted: past answers still reference these slugs.
            self.assertEqual(rows, before - 1)

    def test_knowledge_survives_a_rebuild_by_slug(self):
        self._build()
        with Store(self.cfg.db_path) as store:
            ids_before = {
                r["slug"]: r["id"]
                for r in store.conn.execute("SELECT id, slug FROM knowledge WHERE status='live'")
            }
        self._build()
        with Store(self.cfg.db_path) as store:
            ids_after = {
                r["slug"]: r["id"]
                for r in store.conn.execute("SELECT id, slug FROM knowledge WHERE status='live'")
            }
        self.assertEqual(ids_before, ids_after)

    def test_curation_beats_ingest_without_losing_ingest_facts(self):
        self._build()
        with Store(self.cfg.db_path) as store:
            repo = store.entity_by_key("plae-tljg/MaaFwPhoneAI")
        self.assertIsNotNone(repo)
        self.assertIn("Android GUI", repo.summary)          # curation's wording
        self.assertEqual(repo.attrs["language"], "Kotlin")   # ingest's fact
        self.assertTrue(repo.attrs["fork"])

    def test_every_live_knowledge_slot_has_a_live_entity_type(self):
        self._build()
        with Store(self.cfg.db_path) as store:
            types = {e.entity_type for e in store.entities()}
            for row in store.knowledge_rows():
                for entity_type in row.slots.values():
                    self.assertIn(entity_type, types, f"{row.slug} -> {entity_type}")

    def test_no_dangling_links(self):
        report = self._build()
        self.assertEqual(report["dangling_links"], [])

    def test_offline_build_without_cache_fails_loudly(self):
        empty = self.tmp / "empty-cache"
        cfg = Config.load(ROOT, db_path=self.tmp / "other.db", cache_dir=empty)
        report = build(cfg, offline=True, cache_dir=empty)
        # A missing source is noted and skipped, not silently treated as "no repos".
        self.assertTrue(any("offline" in note for note in report["notes"]))


if __name__ == "__main__":
    unittest.main()
