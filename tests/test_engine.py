"""The ladder and the actions it executes."""

from __future__ import annotations

import unittest

from personal_chatbots.store import Store
from personal_chatbots.textnorm import tokenize
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

    def test_the_inbox_clusters_by_shape_not_by_message(self):
        # Asking the same thing three times is ONE item with a count of three.
        # A window function here returned one row per message, which is a wall of
        # duplicates as soon as there is any traffic.
        # its own wording, so another test in this class cannot inflate the count
        question = "do you rent out the rooftop for bar mitzvahs?"
        for _ in range(3):
            self.runtime.ask_and_record(question, session_id="dup")
        with Store(self.cfg.db_path) as store:
            rows = store.unresolved_inbox(200)
        matching = [r for r in rows if "bar mitzvahs" in (r["content"] or "")]
        self.assertEqual(len(matching), 1, "the inbox should hold one row per shape")
        self.assertEqual(matching[0]["same_shape_count"], 3)

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


class FallbackRungTest(BuiltCase):
    """The model rung is off by default, and answering does not close the gap.

    Two properties matter and both are easy to get wrong: it must be invisible
    unless the ladder asks for it, and when it does answer, the turn must still
    be recorded as unresolved -- otherwise the fallback hides the only signal the
    maintenance loop runs on.
    """

    def test_no_model_runs_when_the_rung_is_not_in_the_ladder(self):
        """The contract, not this repo's current config.

        This repository enables the fallback (docs/CONCERNS.md C1); a fresh
        adopter should start with it off, and either way the property that
        matters is that a ladder without the rung never reaches a model.
        """
        from personal_chatbots.config import Config
        from personal_chatbots.engine import Runtime

        cfg = Config.load(self.cfg.root, db_path=self.cfg.db_path, cache_dir=self.cfg.cache_dir)
        object.__setattr__(cfg, "data", {**cfg.data, "runtime": {
            **cfg.data["runtime"], "ladder": ["knowledge", "entity", "search", "refuse"],
        }})
        runtime = Runtime.from_store(self.store, cfg)
        self.assertNotIn("fallback", cfg.ladder)
        self.assertNotIn("fallback", [name for name, _ in runtime.ladder()])
        for question in ("do you do weddings?", "hello there"):
            self.assertEqual(runtime.ask(question).source, "refuse")

    def test_disabled_returns_none(self):
        self.assertIsNone(self.runtime.rung_fallback(["do", "you", "sell", "insurance"]))

    def test_enabled_without_a_key_returns_none(self):
        import os

        from personal_chatbots.config import Config
        from personal_chatbots.engine import Runtime

        cfg = Config.load(self.cfg.root, db_path=self.cfg.db_path, cache_dir=self.cfg.cache_dir)
        object.__setattr__(cfg, "data", {**cfg.data, "runtime": {
            **cfg.data["runtime"],
            "ladder": ["knowledge", "entity", "search", "fallback", "refuse"],
            "fallback": {**cfg.fallback, "enabled": True, "api_key_env": "PC_TEST_MISSING_KEY"},
        }})
        os.environ.pop("PC_TEST_MISSING_KEY", None)
        runtime = Runtime.from_store(self.store, cfg)
        self.assertIsNone(runtime.rung_fallback(["do", "you", "sell", "insurance"]))

    def test_an_answer_does_not_clear_the_unresolved_flag(self):
        import os

        from personal_chatbots.config import Config
        from personal_chatbots.engine import Answer, Runtime

        cfg = Config.load(self.cfg.root, db_path=self.cfg.db_path, cache_dir=self.cfg.cache_dir)
        object.__setattr__(cfg, "data", {**cfg.data, "runtime": {
            **cfg.data["runtime"],
            "ladder": ["knowledge", "entity", "search", "fallback", "refuse"],
            "fallback": {**cfg.fallback, "enabled": True, "api_key_env": "PC_TEST_KEY"},
        }})
        os.environ["PC_TEST_KEY"] = "test"

        runtime = Runtime.from_store(self.store, cfg)
        # A stub assigned on the instance shadows the bound method, so the ladder
        # exercises the real ordering while the network stays out of the test.
        runtime.rung_fallback = lambda tokens: Answer(
            text="I do not have that, but I have passed it on.",
            source="fallback", citations=[], matched_slug="model",
        )

        answer = runtime.ask_and_record("do you sell insurance?", session_id="fb")
        self.assertEqual(answer.source, "fallback")
        self.assertIn("passed it on", answer.text)

        rows = self.store.session_messages("fb")
        self.assertEqual(
            rows[0]["unresolved"], 1,
            "the tables still do not know this -- the fallback answers the visitor, "
            "it does not close the gap",
        )
        self.assertEqual(rows[1]["resolution_source"], "fallback")

    def test_the_system_prompt_forbids_invention_and_bounds_the_context(self):
        joined = self.runtime._fallback_system_prompt(["- dsh-review (repo): a thing https://x"])
        self.assertIn("do not know", joined)
        self.assertIn("Never guess", joined)
        self.assertIn("dsh-review", joined)

        # an empty context must not produce a prompt that invites a guess
        bare = self.runtime._fallback_system_prompt([])
        self.assertNotIn("Facts you may use", bare)

    def test_context_is_bounded_to_what_the_question_touched(self):
        from personal_chatbots.textnorm import tokenize

        lines, cited = self.runtime._fallback_context(tokenize("what is dsh-review about"), 6)
        self.assertEqual(len(lines), 1)
        self.assertEqual(cited[0].key, "plae-tljg/dsh-review")


class ReasoningStripTest(unittest.TestCase):
    """Reasoning models return their chain of thought; it must not reach a visitor.

    Found by pointing the fallback at a real model: MiniMax-M2.7 inlined
    <think>...</think> in `message.content`, and the first thing the CLI printed
    was the model thinking out loud.
    """

    def strip(self, text):
        from personal_chatbots.engine import _strip_reasoning

        return _strip_reasoning(text)

    def test_closed_think_block(self):
        self.assertEqual(self.strip("<think>hmm</think>The answer is 4."), "The answer is 4.")

    def test_thinking_variant_and_case(self):
        self.assertEqual(self.strip("<THINKING>a</THINKING>done"), "done")

    def test_unterminated_block_drops_everything_after_it(self):
        # The model was cut off mid-thought: shipping a fragment would be worse
        # than shipping nothing.
        self.assertEqual(self.strip("<think>I should consider"), "")

    def test_reasoning_tag_and_fenced_form(self):
        self.assertEqual(self.strip("<reasoning>x</reasoning>ok"), "ok")
        self.assertEqual(self.strip("```thinking\nnoise\n```answer"), "answer")

    def test_a_plain_answer_is_untouched(self):
        text = "dsh-review is a review tab for DeepSeek Harness."
        self.assertEqual(self.strip(text), text)


class SuggestionTest(BuiltCase):
    """Follow-ups must be answerable, and must not repeat the row that answered.

    "Better chatflow" at level 1 is not a state machine; it is telling the
    visitor what this bot can do. The rule that keeps it honest: a suggestion
    that would itself refuse is worse than no suggestion, so a row is only
    offered when every slot it declares can be filled by an entity already in
    play.
    """

    def test_suggestions_are_answerable(self):
        for question in ("what is dsh-review about?", "do you do weddings?",
                         "what projects does LKM have?", "which projects use Kotlin?"):
            answer = self.runtime.ask(question)
            for suggestion in answer.suggestions:
                follow_up = self.runtime.ask(suggestion)
                self.assertFalse(
                    follow_up.refused,
                    f"{question!r} suggested {suggestion!r}, which refuses",
                )

    def test_the_answering_row_is_not_suggested_back(self):
        answer = self.runtime.ask("what is dsh-review about?")
        self.assertNotIn("what is dsh-review about", answer.suggestions)
        self.assertTrue(all("dsh-review about" not in s for s in answer.suggestions))

    def test_a_resolved_slot_drives_the_follow_ups(self):
        # The repo resolved, so the follow-ups are about that repo.
        answer = self.runtime.ask("what is dsh-review about?")
        self.assertIn("who made dsh-review", answer.suggestions)

    def test_a_refusal_still_offers_something(self):
        answer = self.runtime.ask("do you do weddings?")
        self.assertTrue(answer.suggestions, "a refusal with no way forward reads as broken")

    def test_at_most_three(self):
        self.assertLessEqual(len(self.runtime.ask("hello").suggestions), 3)


class NearMissTest(BuiltCase):
    """A question one word away from a row should name the row and the word.

    "what accounts does LKM publish under" reached the model twice while
    `owner.accounts` sat there reading "which accounts does {person} publish
    under". The failure was silent, and the bot was even *suggesting* the correct
    phrasing in its follow-ups -- it knew the answer and could not match the
    question. Reporting the gap is the difference between a maintainer adding a
    row and a maintainer widening one.
    """

    def test_a_one_word_gap_is_named(self):
        found = self.runtime.near_misses(tokenize("which projects need Kotlin?"))
        slugs = [slug for slug, _ in found]
        self.assertIn("repo.by_language", slugs)
        note = dict(found)["repo.by_language"]
        self.assertIn("use", note)   # what the row says
        self.assertIn("need", note)  # what was said

    def test_a_matched_question_is_not_a_near_miss(self):
        # it answers, so there is nothing to report
        self.assertTrue(self.runtime.ask("which projects use Kotlin?").suggestions is not None)
        found = self.runtime.near_misses(tokenize("which projects use Kotlin?"))
        self.assertNotIn("repo.by_language", [slug for slug, _ in found])

    def test_a_distant_question_reports_nothing(self):
        # an unrelated question must not produce noise
        self.assertEqual(self.runtime.near_misses(tokenize("do you do weddings?")), [])

    def test_the_gap_this_repository_actually_hit_still_answers(self):
        answer = self.runtime.ask("what accounts does LKM publish under")
        self.assertEqual(answer.source, "knowledge")
        self.assertEqual(answer.matched_slug, "owner.accounts")
        self.assertFalse(answer.unresolved)
