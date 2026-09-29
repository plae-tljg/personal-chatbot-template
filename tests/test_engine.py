"""The ladder and the actions it executes."""

from __future__ import annotations

import unittest

from personal_chatbots.store import Store
from tests.support import BuiltCase


class LadderTest(BuiltCase):
    def test_knowledge_beats_entity(self):
        answer = self.ask("what is dsh-review about?")
        self.assertEqual(answer.source, "knowledge")
        self.assertEqual(answer.matched_slug, "repo.about")

    def test_a_bare_name_is_a_card(self):
        answer = self.ask("dsh-review")
        self.assertEqual(answer.source, "entity")
        self.assertIn("DeepSeek Harness", answer.text)

    def test_unknown_question_refuses(self):
        answer = self.ask("do you do weddings?")
        self.assertTrue(answer.refused)
        self.assertEqual(answer.citations, [])

    def test_search_is_scoped_to_the_named_entity(self):
        # A question about a real repo, asking something only its README knows.
        answer = self.ask("does dsh-review track changes?")
        self.assertIn(answer.source, {"search", "knowledge", "refuse"})
        if answer.source == "search":
            self.assertEqual(answer.citations[0].key, "plae-tljg/dsh-review")

    def test_unrelated_full_text_hit_does_not_answer(self):
        # "price" appears in no README, and there is no price row: refuse, do not
        # rummage through the corpus looking for something to say.
        answer = self.ask("what is the price of dsh-review?")
        self.assertTrue(answer.refused)


class ActionTest(BuiltCase):
    def test_list_orders_by_a_json_attr(self):
        answer = self.ask("what is your most starred repo?")
        self.assertEqual(answer.source, "knowledge")
        first = answer.text.splitlines()[0]
        self.assertIn("Finance-Management-App", first)

    def test_list_follows_a_link(self):
        answer = self.ask("which accounts does LKM publish under?")
        keys = {c.key for c in answer.citations}
        self.assertIn("LKM-Repo", keys)
        self.assertIn("ellkaimu", keys)

    def test_linked_list_needs_the_slot(self):
        answer = self.ask("which projects use Kotlin?")
        self.assertIn("MaaFwPhoneAI", answer.text)

    def test_curated_project_grouping_is_reachable(self):
        answer = self.ask("what is the Finance Management project?")
        for name in ("Finance-Management-App", "Finance-Management-Web", "Finance_Lux_Web"):
            self.assertIn(name, answer.text)

    def test_citations_come_from_the_entities_used(self):
        answer = self.ask("what is MaaFwPhoneAI about?")
        self.assertEqual([c.key for c in answer.citations], ["plae-tljg/MaaFwPhoneAI"])

    def test_ambiguity_refuses_rather_than_picking(self):
        answer = self.ask("what is Finance-Management about?")
        self.assertTrue(answer.refused)


class RecordingTest(BuiltCase):
    def test_refusals_are_recorded_as_unresolved(self):
        with Store(self.cfg.db_path) as store:
            before = store.stats()["unresolved"]
            runtime_before = store.count_messages()
        self.runtime.ask_and_record("do you do weddings?", session_id="rec-1")
        with Store(self.cfg.db_path) as store:
            self.assertEqual(store.count_messages(), runtime_before + 2)
            self.assertEqual(store.stats()["unresolved"], before + 1)
            inbox = [r["content"] for r in store.unresolved_inbox(10)]
        self.assertIn("do you do weddings?", inbox)

    def test_answers_are_not_unresolved(self):
        with Store(self.cfg.db_path) as store:
            before = store.count_messages()
        self.runtime.ask_and_record("what is dsh-review about?", session_id="rec-2")
        with Store(self.cfg.db_path) as store:
            row = store.conn.execute(
                "SELECT resolution_source, unresolved, matched_slug FROM messages"
                " WHERE session_id = 'rec-2' AND role = 'assistant'"
            ).fetchone()
            self.assertEqual(row["resolution_source"], "knowledge")
            self.assertEqual(row["unresolved"], 0)
            self.assertEqual(row["matched_slug"], "repo.about")
            self.assertEqual(store.count_messages(), before + 2)


if __name__ == "__main__":
    unittest.main()
