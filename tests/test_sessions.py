"""Sessions and multi-round transcripts.

What is verifiable at level 1, and what is not:

* **Stored and replayed faithfully.** Turns accumulate in order, a session can be
  listed, reopened and deleted, and one session never leaks into another.
* **Not used as context.** The answer to turn 3 is computed from turn 3 alone.
  That is level 2 (`docs/LEVELS.md`), and `test_a_follow_up_does_not_see_the_subject`
  below pins the current behaviour so that raising the level is a deliberate,
  visible change rather than a silent one.
"""

from __future__ import annotations

import unittest

from personal_chatbots.serve import create_app
from tests.support import BuiltCase

try:
    from fastapi.testclient import TestClient

    HAVE_FASTAPI = True
except Exception:  # noqa: BLE001  # pragma: no cover
    # Deliberately broad. fastapi.testclient needs httpx and raises RuntimeError,
    # not ImportError, when it is missing -- so an ImportError guard lets the
    # module fail to import and takes the whole discovery run down with it. That
    # is exactly what happened on a fresh clone, and in CI.
    HAVE_FASTAPI = False


class SessionStoreTest(BuiltCase):
    def test_transcript_records_both_directions_in_order(self):
        for question in ("what is dsh-review about?", "which projects use Kotlin?", "hello?"):
            self.runtime.ask_and_record(question, session_id="s-order")
        rows = self.store.session_messages("s-order")
        self.assertEqual([r["role"] for r in rows], ["user", "assistant"] * 3)
        self.assertEqual([r["turn"] for r in rows], [1, 1, 2, 2, 3, 3])
        self.assertEqual(rows[0]["content"], "what is dsh-review about?")
        self.assertEqual(rows[1]["resolution_source"], "knowledge")
        self.assertEqual(rows[5]["resolution_source"], "refuse")

    def test_sessions_do_not_leak_into_each_other(self):
        self.runtime.ask_and_record("what is dsh-review about?", session_id="a")
        self.runtime.ask_and_record("which projects use Kotlin?", session_id="b")
        self.assertEqual(len(self.store.session_messages("a")), 2)
        self.assertEqual(len(self.store.session_messages("b")), 2)
        self.assertEqual(self.store.session_messages("a")[0]["content"], "what is dsh-review about?")

    def test_session_title_is_the_first_question(self):
        self.runtime.ask_and_record("what is dsh-review about?", session_id="titled")
        self.runtime.ask_and_record("and Kotlin?", session_id="titled")
        self.assertEqual(self.store.session("titled")["title"], "what is dsh-review about?")

    def test_listing_is_most_recent_first(self):
        for name in ("first", "second", "third"):
            self.runtime.ask_and_record("what is dsh-review about?", session_id=name)
        ids = [row["session_id"] for row in self.store.list_sessions(10)]
        self.assertEqual(ids[:3], ["third", "second", "first"])

    def test_session_counts_turns_and_refusals(self):
        self.runtime.ask_and_record("what is dsh-review about?", session_id="counted")
        self.runtime.ask_and_record("do you do weddings?", session_id="counted")
        meta = self.store.session("counted")
        self.assertEqual(meta["turns"], 2)
        self.assertEqual(meta["messages"], 4)
        self.assertEqual(meta["unresolved"], 1)

    def test_delete_removes_only_that_session(self):
        self.runtime.ask_and_record("what is dsh-review about?", session_id="keep")
        self.runtime.ask_and_record("what is dsh-review about?", session_id="drop")
        self.assertEqual(self.store.delete_session("drop"), 2)
        self.assertIsNone(self.store.session("drop"))
        self.assertEqual(len(self.store.session_messages("keep")), 2)

    def test_unknown_session_has_no_rows(self):
        self.assertIsNone(self.store.session("never-existed"))
        self.assertEqual(self.store.session_messages("never-existed"), [])

    def test_a_follow_up_does_not_see_the_subject(self):
        """The honest L1 boundary, pinned so that L2 changes it on purpose.

        A visitor asks about a repository, then asks a follow-up that only makes
        sense with the subject carried over. At level 1 the second turn refuses,
        and that refusal lands in the inbox -- which is precisely the evidence
        that level 2 is worth its live tables.
        """
        first = self.runtime.ask_and_record("what is dsh-review about?", session_id="follow")
        second = self.runtime.ask_and_record("how many stars does it have?", session_id="follow")
        self.assertEqual(first.source, "knowledge")
        self.assertTrue(second.refused)

        inbox = [row["content"] for row in self.store.unresolved_inbox(50)]
        self.assertIn("how many stars does it have?", inbox)


@unittest.skipUnless(HAVE_FASTAPI, "fastapi not installed")
class SessionApiTest(BuiltCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.client = TestClient(create_app(cls.cfg))

    def _ask(self, question, session=None):
        payload = {"question": question}
        if session:
            payload["session_id"] = session
        return self.client.post("/api/ask", json=payload).json()

    def test_ask_returns_the_session_and_turn(self):
        first = self._ask("what is dsh-review about?")
        self.assertTrue(first["session_id"])
        self.assertEqual(first["turn"], 1)
        self.assertFalse(first["context_used"])

        second = self._ask("which projects use Kotlin?", first["session_id"])
        self.assertEqual(second["session_id"], first["session_id"])
        self.assertEqual(second["turn"], 2)

    def test_transcript_endpoint_replays_the_conversation(self):
        session = self._ask("what is dsh-review about?")["session_id"]
        self._ask("do you do weddings?", session)
        body = self.client.get(f"/api/sessions/{session}").json()
        self.assertEqual(body["turns"], 2)
        self.assertEqual(len(body["messages"]), 4)
        self.assertEqual(body["messages"][0]["role"], "user")
        self.assertEqual(body["messages"][0]["content"], "what is dsh-review about?")
        self.assertEqual(body["messages"][1]["source"], "knowledge")
        # The flag lives on the *user* row, so the inbox cannot double-count a
        # turn. The assistant row says how it answered instead.
        self.assertTrue(body["messages"][2]["unresolved"])
        self.assertEqual(body["messages"][3]["source"], "refuse")
        self.assertFalse(body["messages"][3]["unresolved"])
        # Citations survive the round trip, which is what makes a reopened
        # conversation as traceable as a live one.
        self.assertTrue(body["messages"][1]["citations"][0]["url"])

    def test_session_listing_carries_what_the_sidebar_needs(self):
        session = self._ask("what is your most starred repo?")["session_id"]
        body = self.client.get("/api/sessions").json()
        entry = next(s for s in body["sessions"] if s["id"] == session)
        self.assertEqual(entry["title"], "what is your most starred repo?")
        self.assertEqual(entry["turns"], 1)
        self.assertEqual(entry["unresolved"], 0)

    def test_two_sessions_stay_separate(self):
        a = self._ask("what is dsh-review about?")["session_id"]
        b = self._ask("which projects use Kotlin?")["session_id"]
        self.assertNotEqual(a, b)
        self.assertEqual(len(self.client.get(f"/api/sessions/{a}").json()["messages"]), 2)
        self.assertEqual(len(self.client.get(f"/api/sessions/{b}").json()["messages"]), 2)

    def test_unknown_session_is_404(self):
        self.assertEqual(self.client.get("/api/sessions/nope").status_code, 404)
        self.assertEqual(self.client.delete("/api/sessions/nope").status_code, 404)

    def test_delete_endpoint(self):
        session = self._ask("what is dsh-review about?")["session_id"]
        self.assertEqual(self.client.delete(f"/api/sessions/{session}").json()["deleted"], 2)
        self.assertEqual(self.client.get(f"/api/sessions/{session}").status_code, 404)

    def test_refusal_is_a_recorded_turn_not_an_error(self):
        session = self._ask("do you do weddings?")["session_id"]
        body = self.client.get(f"/api/sessions/{session}").json()
        self.assertEqual(body["unresolved"], 1)
        question, answer = body["messages"]
        self.assertEqual(answer["source"], "refuse")
        self.assertEqual(answer["content"], self.cfg.refuse_template)
        self.assertEqual(answer["citations"], [])
        # `unresolved` marks the *question* the structure could not answer, which
        # is what `v_unresolved_inbox` reads. Marking the answer as well would
        # count the same failure twice.
        self.assertTrue(question["unresolved"])
        self.assertFalse(answer["unresolved"])


if __name__ == "__main__":
    unittest.main()


class SchemaGuardTest(BuiltCase):
    """A database built by an older schema must say so, not 500."""

    def test_a_stale_database_names_the_fix(self):
        from personal_chatbots.store import SCHEMA_VERSION, SchemaTooOld, Store

        stale = self.tmp / "stale.db"
        with Store(stale) as store:
            store.conn.executescript(
                "CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT);"
            )
            store.conn.execute("PRAGMA user_version = 1")
            store.conn.commit()
            self.assertEqual(store.schema_version(), 1)
            with self.assertRaises(SchemaTooOld) as caught:
                store.require_schema()
        message = str(caught.exception)
        self.assertIn("pc build", message)
        self.assertIn(str(SCHEMA_VERSION), message)

    def test_a_freshly_built_database_passes_the_guard(self):
        from personal_chatbots.store import SCHEMA_VERSION

        self.store.require_schema()
        self.assertEqual(self.store.schema_version(), SCHEMA_VERSION)

    def test_the_session_views_exist_after_a_build(self):
        names = {
            row[0]
            for row in self.store.conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'view'"
            )
        }
        self.assertIn("v_sessions", names)
        self.assertIn("v_unresolved_inbox", names)


class ConcurrencyTest(BuiltCase):
    """The serving path is not single-threaded.

    FastAPI runs sync endpoints in a worker pool, so a cached Store gets used
    from whichever thread picks up the request. This has now caught three
    different bugs, each one invisible on a single thread:

    1. "SQLite objects created in a thread can only be used in that same thread"
       -- fixed with `check_same_thread=False`.
    2. "InterfaceError: bad parameter or other API misuse" on 3.12, because
       serializing writes still left concurrent reads inside one connection.
    3. "IndexError: tuple index out of range" on 3.12, because
       `execute(...).fetchone()` is two calls and the cursor it returns belongs
       to the connection's *last* prepared statement -- another thread's
       `execute` in between re-pointed it at a different query.

    The mix below matters. Reading a single value, iterating a result set and
    writing all exercise different lifetimes, and a fix for one of them left
    the others broken.
    """

    def test_parallel_asks_and_reads(self):
        import threading

        errors: list[str] = []
        threads_per_run = 16

        def worker(n: int) -> None:
            try:
                for i in range(6):
                    self.runtime.ask_and_record("what is dsh-review about?", session_id=f"c{n}")
                    # reads whose cursor is fetched immediately
                    self.store.session(f"c{n}")
                    self.store.entity_by_key("plae-tljg/dsh-review")
                    self.store.stats()
                    # reads that consume a whole result set, and iterate it
                    self.store.list_sessions(20)
                    self.store.session_messages(f"c{n}")
                    self.store.knowledge_coverage()
                    self.store.unresolved_inbox(20)
                    self.store.resolution_mix()
                    # a write in the middle of all that reading
                    if i % 3 == 0:
                        self.store.delete_session(f"gone-{n}-{i}")
            except Exception as exc:  # noqa: BLE001 - the point is to catch anything
                errors.append(f"{type(exc).__name__}: {exc}")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(threads_per_run)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        self.assertEqual(len(self.store.session_messages("c0")), 12)
        self.assertEqual(
            {row["session_id"] for row in self.store.list_sessions(50)}
            >= {f"c{i}" for i in range(threads_per_run)},
            True,
        )
