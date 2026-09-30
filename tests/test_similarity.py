"""Typo tolerance: what it must heal, and what it must never do.

Fuzzy matching is the one mechanism in this project that can make the bot answer
a question nobody asked. Every test here is either a healed typo (the point) or a
trap that must still refuse (the price). The traps are not hypothetical -- two of
them were real false positives found while building this:

* "what is the weather today" matched *two* repository rows, because its skeleton
  has no placeholder and "{repo}" is not far from nothing;
* "what is Finance-Management about" was answered from a curated grouping's row,
  because "{project}" and "{repo}" are one edit apart as placeholder text.

Both were caught by tests rather than by reasoning, which is why they are pinned
here with the reasoning that fixed them.
"""

from __future__ import annotations

import unittest

from personal_chatbots import similarity as sim
from tests.support import BuiltCase


class DistanceTest(unittest.TestCase):
    def test_a_transposition_is_one_mistake(self):
        """Plain Levenshtein says two, which rejected every typo it was for."""
        self.assertEqual(sim.distance("what is {repo} abotu", "what is {repo} about"), 1)
        self.assertEqual(sim.distance("what is {repo} abuot", "what is {repo} about"), 1)

    def test_a_substitution_is_one_mistake(self):
        self.assertEqual(sim.distance("which project use {language}", "which projects use {language}"), 1)

    def test_identical_is_zero(self):
        self.assertEqual(sim.distance("what is {repo} about", "what is {repo} about"), 0)

    def test_an_unrelated_question_is_far(self):
        self.assertGreater(sim.distance("what is the weather today", "what is {repo} about"), 1)

    def test_short_patterns_are_exact_only(self):
        """"who is X" and "what is X" are one edit apart and ask different things.

        Three tokens is too short to tolerate anything: one edit is a third of
        the question, and the shape stops being recognisable.
        """
        self.assertEqual(sim.allowed("who is {person}"), 0)
        self.assertEqual(sim.allowed("what is {repo} about"), 1)

    def test_two_edits_need_a_long_pattern(self):
        """Distance 2 was 5 tokens until it answered an ambiguity case wrongly."""
        self.assertEqual(sim.allowed("does the {repo} work offline"), 1)  # 5 tokens: one edit
        long_pattern = "which of the projects that you have written use {language}"
        self.assertEqual(len(long_pattern.split()), 10)
        self.assertEqual(sim.allowed(long_pattern), 2)


class GuardTest(unittest.TestCase):
    def test_a_skeleton_with_no_slots_cannot_borrow_a_slotted_pattern(self):
        self.assertIsNone(sim.best_match("what is the weather today", ["what is {repo} about"]))

    def test_placeholders_only_satisfy_the_same_type(self):
        """{project} must not borrow a repository's phrasing."""
        self.assertIsNone(
            sim.best_match(
                "what is {project} about",
                ["what is the {project} project"],
                slot_types={"project": "project"},
            )
        )

    def test_the_same_type_is_allowed_through(self):
        self.assertIsNotNone(
            sim.best_match(
                "what is {repo} abotu",
                ["what is {repo} about"],
                slot_types={"repo": "repo"},
            )
        )

    def test_a_different_opening_word_is_a_different_question(self):
        self.assertIsNone(
            sim.best_match("does {repo} work offline", ["what is {repo} about"], slot_types={"repo": "repo"})
        )

    def test_equally_close_patterns_refuse(self):
        """A tie is ambiguity. Picking one would be a coin toss."""
        self.assertIsNone(
            sim.best_match(
                "who wrote {repo}",
                ["who wrote the {repo}", "who wrote a {repo}"],
                slot_types={"repo": "repo"},
            )
        )

    def test_an_excluded_pattern_stays_excluded(self):
        """A row's own `exclude` must beat tolerance, or `exclude` stops working."""
        self.assertIsNone(
            sim.best_match(
                "what is {repo} abotu",
                ["what is {repo} about"],
                exclude=["what is {repo} about"],
                slot_types={"repo": "repo"},
            )
        )


class EngineTest(BuiltCase):
    def test_a_typo_is_answered_and_flagged(self):
        answer = self.ask("what is dsh-review abotu?")
        self.assertEqual(answer.source, "knowledge")
        self.assertEqual(answer.fuzzy, 1, "a tolerated typo must say that it was tolerated")
        self.assertEqual(answer.matched_slug, "repo.about")
        # The typo was in the framing, never in which repository was meant.
        self.assertEqual(answer.slots["repo"].key, "plae-tljg/dsh-review")

    def test_an_exact_match_is_not_flagged(self):
        answer = self.ask("what is dsh-review about?")
        self.assertEqual(answer.source, "knowledge")
        self.assertIsNone(answer.fuzzy, "an exact match is not a fuzzy match")

    def test_a_typo_does_not_become_an_inbox_entry(self):
        """Tolerance is not a hole in the data, so it is not a gap to report.

        Measured by what a maintainer would see: the inbox grows for questions
        the tables could not answer. A question they *did* answer, even
        approximately, is not one of those.
        """
        answer = self.ask("what is dsh-review abotu?")
        self.assertEqual(answer.source, "knowledge")
        self.assertFalse(answer.unresolved)

    def test_the_dangerous_shapes_still_refuse(self):
        for question in (
            "what is the weather today?",
            "how many stars does dsh-review have?",
            "what is Finance-Management about?",
            "why is the sky blue?",
            "is python good?",
        ):
            with self.subTest(question=question):
                self.assertEqual(self.ask(question).source, "refuse")

    def test_disabling_fuzzy_restores_exact_matching(self):
        """The escape hatch: prove a phrasing is covered, not merely close."""
        from personal_chatbots.config import Config
        from personal_chatbots.engine import Runtime
        from personal_chatbots.store import Store

        cfg = Config.load(self.cfg.root, db_path=self.cfg.db_path, cache_dir=self.cfg.cache_dir)
        runtime_data = {**cfg.data["runtime"], "fuzzy": {"enabled": False}}
        object.__setattr__(cfg, "data", {**cfg.data, "runtime": runtime_data})
        with Store(cfg.db_path) as store:
            strict = Runtime.from_store(store, cfg)
            self.assertEqual(strict.ask("what is dsh-review abotu?").source, "refuse")
            self.assertEqual(strict.ask("what is dsh-review about?").source, "knowledge")


if __name__ == "__main__":
    unittest.main()
