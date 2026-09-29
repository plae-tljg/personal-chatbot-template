"""The frame: cross-turn reference without cross-turn state.

The scenario these tests encode is the one that motivated the module:

    › what projects does LKM have?
      • Finance-Management-App … • Anime-Webview … • domain-ops-agent … etc
    › tell me about the second one      -> Anime-Webview
    › and the third?                    -> domain-ops-agent
    › it                                -> whatever was last discussed

No table, no state machine, no live variable: the list is the previous answer's
citations, which were already being stored to make answers traceable.
"""

from __future__ import annotations

import unittest

from personal_chatbots.frame import DEFAULT_WINDOW, Frame, apply
from personal_chatbots.textnorm import tokenize
from tests.support import BuiltCase

LIST_QUESTION = "what projects does LKM have?"


class FrameTest(BuiltCase):
    def test_a_list_becomes_the_items(self):
        self.runtime.ask_and_record(LIST_QUESTION, session_id="f1")
        frame = Frame.from_transcript(self.store.session_messages("f1"), self.store)
        self.assertGreaterEqual(len(frame.items), 5)
        self.assertEqual(frame.items[0].key, "plae-tljg/Finance-Management-App")
        self.assertIsNone(frame.subject, "a list is not a single subject")

    def test_a_single_answer_becomes_the_subject(self):
        self.runtime.ask_and_record("what is dsh-review about?", session_id="f2")
        frame = Frame.from_transcript(self.store.session_messages("f2"), self.store)
        self.assertIsNotNone(frame.subject)
        self.assertEqual(frame.subject.key, "plae-tljg/dsh-review")
        self.assertEqual(frame.items, [])

    def test_a_list_blocks_an_older_subject(self):
        # Answer about one repo, then list many: "it" must not point back past
        # the list at whatever was discussed before it.
        self.runtime.ask_and_record("what is dsh-review about?", session_id="f3")
        self.runtime.ask_and_record(LIST_QUESTION, session_id="f3")
        frame = Frame.from_transcript(self.store.session_messages("f3"), self.store)
        self.assertIsNone(frame.subject)
        self.assertTrue(frame.items)

    def test_no_history_is_an_empty_frame(self):
        self.assertTrue(Frame().empty)
        self.assertIsNone(Frame().resolve_ordinal(0))

    def test_the_window_is_bounded(self):
        # A list from long ago must not hijack today's question.
        self.runtime.ask_and_record(LIST_QUESTION, session_id="f4")
        for i in range(DEFAULT_WINDOW):
            self.runtime.ask_and_record(f"what is dsh-review about? {i}", session_id="f4")
        frame = Frame.from_transcript(self.store.session_messages("f4"), self.store)
        self.assertEqual(frame.items, [], "a stale list leaked into the frame")

    def test_an_out_of_range_ordinal_does_not_resolve(self):
        self.runtime.ask_and_record(LIST_QUESTION, session_id="f5")
        frame = Frame.from_transcript(self.store.session_messages("f5"), self.store)
        self.assertIsNone(frame.resolve_ordinal(99))

    def test_last_is_the_final_item(self):
        self.runtime.ask_and_record(LIST_QUESTION, session_id="f6")
        frame = Frame.from_transcript(self.store.session_messages("f6"), self.store)
        self.assertEqual(frame.resolve_ordinal(-1).key, frame.items[-1].key)


class RewriteTest(BuiltCase):
    def test_ordinals_rewrite_to_a_name(self):
        self.runtime.ask_and_record(LIST_QUESTION, session_id="r1")
        frame = Frame.from_transcript(self.store.session_messages("r1"), self.store)
        for phrase in ("the second one", "the 2nd", "second one", "the second"):
            tokens, refs = apply(tokenize(f"tell me about {phrase}"), frame)
            self.assertIn("anime", tokens, phrase)
            self.assertEqual(list(refs.values()), ["ellkaimu/Anime-Webview"], phrase)

    def test_pronouns_rewrite_to_the_subject(self):
        self.runtime.ask_and_record("what is dsh-review about?", session_id="r2")
        frame = Frame.from_transcript(self.store.session_messages("r2"), self.store)
        tokens, refs = apply(tokenize("tell me about it"), frame)
        self.assertEqual(refs, {"it": "plae-tljg/dsh-review"})
        self.assertIn("dsh", tokens)

    def test_nothing_resolves_without_context(self):
        tokens, refs = apply(tokenize("tell me about the second one"), Frame())
        self.assertEqual(refs, {})
        self.assertEqual(tokens, tokenize("tell me about the second one"))

    def test_a_phrase_that_is_not_a_reference_is_left_alone(self):
        self.runtime.ask_and_record(LIST_QUESTION, session_id="r3")
        frame = Frame.from_transcript(self.store.session_messages("r3"), self.store)
        tokens, refs = apply(tokenize("what is the second world war"), frame)
        # "the second" does resolve; "world war" is not a reference at all.
        self.assertEqual(set(refs), {"the second"})

    def test_rewriting_is_recorded(self):
        self.runtime.ask_and_record(LIST_QUESTION, session_id="r4")
        answer = self.runtime.ask_and_record("tell me about the second one", session_id="r4")
        self.assertEqual(answer.refs, {"the second one": "ellkaimu/Anime-Webview"})
        self.assertIn("Anime-Webview", answer.text)

    def test_the_stored_question_is_what_the_visitor_typed(self):
        self.runtime.ask_and_record(LIST_QUESTION, session_id="r5")
        self.runtime.ask_and_record("tell me about the second one", session_id="r5")
        rows = self.store.session_messages("r5")
        self.assertEqual(rows[2]["content"], "tell me about the second one")
        self.assertEqual(rows[3]["refs_json"], '{"the second one": "ellkaimu/Anime-Webview"}')


class ScenarioTest(BuiltCase):
    """The full walk-through, end to end, exactly as a visitor would drive it."""

    def test_the_whole_scenario(self):
        session = "scenario"
        first = self.runtime.ask_and_record(LIST_QUESTION, session_id=session)
        self.assertEqual(first.source, "knowledge")

        second = self.runtime.ask_and_record("tell me about the second one", session_id=session)
        self.assertIn("Anime-Webview", second.text)

        third = self.runtime.ask_and_record("and the third?", session_id=session)
        self.assertIn("domain-ops-agent", third.text)

        fourth = self.runtime.ask_and_record("what about the last one?", session_id=session)
        self.assertIn("PIKE-RAG_Verbose", fourth.text)

        # Switching subject mid-conversation is the case a flow would get stuck on.
        fifth = self.runtime.ask_and_record("it", session_id=session)
        self.assertFalse(fifth.refused, "the subject should still be reachable")
        self.assertEqual(fifth.refs, {"it": "LKM-Repo/PIKE-RAG_Verbose"})

    def test_a_reference_to_nothing_still_refuses(self):
        # "the second one" with no list behind it is not a guess -- it is unknown.
        answer = self.runtime.ask_and_record("tell me about the second one", session_id="fresh")
        self.assertTrue(answer.refused)
        self.assertEqual(answer.refs, {})

    def test_the_inbox_keeps_shape_after_a_rewrite(self):
        self.runtime.ask_and_record(LIST_QUESTION, session_id="shape")
        self.runtime.ask_and_record("does it use Kotlin?", session_id="shape")
        inbox = [row["content"] for row in self.store.unresolved_inbox(50)]
        # The visitor's own wording is what lands in the inbox, not the rewrite:
        # the maintainer needs the phrasing people actually use.
        self.assertIn("does it use Kotlin?", inbox)


if __name__ == "__main__":
    unittest.main()


class FramePurityTest(BuiltCase):
    """Only answers the visitor could point at may feed the frame.

    A fallback answer carries the entities it was given as context. Reading those
    as a displayed list made "the second one" resolve to an entity that was never
    on screen -- a confident answer about something nobody had mentioned.
    """

    def test_a_model_answer_does_not_become_a_list(self):
        from personal_chatbots.frame import Frame
        from personal_chatbots.store import Citation

        # stand in for a model turn: several citations, source="fallback"
        self.store.record_turn(
            session_id="pure", question="tell me about things",
            normalized="tell me about things", answer="Here is what I know.",
            source="fallback", matched_slug="model",
            citations=[
                Citation(key="language:python", label="Python", url=""),
                Citation(key="language:kotlin", label="Kotlin", url=""),
            ],
            unresolved=True, latency_ms=10.0,
        )
        frame = Frame.from_transcript(self.store.session_messages("pure"), self.store)
        self.assertEqual(frame.items, [], "a model answer is not a list the visitor saw")
        self.assertIsNone(frame.subject)

    def test_a_table_list_still_becomes_one(self):
        self.runtime.ask_and_record("what projects does LKM have?", session_id="pure2")
        from personal_chatbots.frame import Frame

        frame = Frame.from_transcript(self.store.session_messages("pure2"), self.store)
        self.assertTrue(frame.items, "a real list must still be referable")
