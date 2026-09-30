"""The published database must not contain anyone's conversations.

`pc build` writes two different things into one file: rows synced from
``content/`` and public GitHub metadata, which are publishable, and ``messages``,
which is what visitors asked. Those are one `cp` apart, and a release is exactly
the moment someone makes that mistake.

These tests exist because the command's whole job is a negative: the artifact is
correct when something is *absent*. Nothing about running it normally would
reveal that it stopped emptying `messages`.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from personal_chatbots.config import Config
from personal_chatbots.store import SCHEMA_VERSION, Store
from tests.support import BuiltCase, FIXTURE_CACHE, ROOT


class ReleaseTest(BuiltCase):
    def test_release_empties_live_state(self):
        """The visitor's question is in the built db and must not be in the release."""
        secret = "is my ssh key rotation story on the blog"
        with Store(self.cfg.db_path) as store:
            store.record_turn(
                session_id="visitor-1",
                question=secret,
                normalized="is my ssh key rotation story on the blog",
                answer="I don't have that in my tables.",
                source="refuse",
                matched_slug="",
                citations=[],
                unresolved=True,
                latency_ms=0.2,
            )
            # record_turn writes the question and the answer: two rows, and
            # the assertion is only that the turn is really in the built db.
            self.assertEqual(
                store.conn.execute("SELECT COUNT(*) AS n FROM messages").fetchone()["n"], 2
            )

        out = self.tmp / "published.db"
        code = _release(self.cfg, out)
        self.assertEqual(code, 0, "pc release returned non-zero")

        with Store(out) as published:
            published.require_schema()
            self.assertEqual(
                published.conn.execute("SELECT COUNT(*) AS n FROM messages").fetchone()["n"], 0
            )
        self.assertNotIn(secret.encode(), out.read_bytes())

    def test_release_keeps_the_synced_rows_and_answers(self):
        """Emptying live state must not touch anything the bot answers from."""
        out = self.tmp / "published.db"
        self.assertEqual(_release(self.cfg, out), 0)

        with Store(out) as published:
            published.require_schema()
            for table in ("entities", "entity_links", "documents", "knowledge"):
                built = self.store.conn.execute(
                    f"SELECT COUNT(*) AS n FROM {table}"
                ).fetchone()["n"]
                released = published.conn.execute(
                    f"SELECT COUNT(*) AS n FROM {table}"
                ).fetchone()["n"]
                self.assertEqual(released, built, f"{table} changed during release")

        # The artifact is a working database, not just a valid one: the same
        # question answered by the same rung, with no build and no network.
        page = subprocess.run(
            [
                sys.executable, "-m", "personal_chatbots",
                "--db", str(out), "ask", "what is dsh-review about?",
            ],
            capture_output=True, text=True, cwd=ROOT, timeout=120,
        )
        self.assertEqual(page.returncode, 0, page.stderr)
        self.assertIn("knowledge:repo.about", page.stdout)

    def test_release_refuses_a_stale_schema(self):
        """Publishing a database this code cannot read is worse than failing."""
        stale = self.tmp / "stale.db"
        shutil.copyfile(self.cfg.db_path, stale)
        con = sqlite3.connect(stale)
        con.execute(f"PRAGMA user_version = {SCHEMA_VERSION - 1}")
        con.commit()
        con.close()

        cfg = Config.load(ROOT, db_path=stale, cache_dir=FIXTURE_CACHE)
        out = self.tmp / "should-not-exist.db"
        self.assertNotEqual(_release(cfg, out), 0)
        # Nothing published is the whole assertion. A command that fails loudly
        # and leaves a half-empty database behind would be worse than one that
        # silently ships the messages.
        self.assertFalse(out.exists(), "a refused release still wrote a file")

    def test_manifest_says_what_the_file_is(self):
        out = self.tmp / "published.db"
        self.assertEqual(_release(self.cfg, out), 0)
        manifest = out.with_suffix(out.suffix + ".manifest.json")
        self.assertTrue(manifest.exists(), "release wrote no manifest")
        data = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual(data["schema_version"], SCHEMA_VERSION)
        self.assertIn("messages", data["live_tables_emptied"][0])
        self.assertEqual(len(data["sha256"]), 64)
        # The hash must describe the file that was actually written.
        import hashlib

        self.assertEqual(data["sha256"], hashlib.sha256(out.read_bytes()).hexdigest())


def _release(cfg: Config, out: Path) -> int:
    """Run `pc release` through main(), so this tests the CLI as invoked.

    Calling cmd_release directly would skip the exception handling in main(),
    and a stale schema raises SchemaTooOld rather than returning a code -- the
    real entry point is the thing whose behaviour matters here.

    `main()` also loads `.env` into ``os.environ``, which outlives the call and
    is shared with every other test in the process. Once OPENCODE_API_KEY is
    set, the fallback rung turns on for every Runtime built afterwards, and
    tests that pin the no-model default (`test_sessions`) start failing in a
    full run while passing on their own. So the entry point is fenced: the
    environment going in is the environment coming out.
    """
    import os

    from personal_chatbots.cli import main

    saved = dict(os.environ)
    try:
        return main(["--root", str(ROOT), "--db", str(cfg.db_path), "release", str(out)])
    finally:
        os.environ.clear()
        os.environ.update(saved)


if __name__ == "__main__":
    unittest.main()
