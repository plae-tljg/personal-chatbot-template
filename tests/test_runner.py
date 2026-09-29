"""The seeded suite in content/tests.yaml, and the runner's own assertions.

The baseline for "regression" is git: this file must be green on ``main``. A
change that turns one of these red is a regression, and that is the entire
mechanism -- no tables, no history to maintain.
"""

from __future__ import annotations

import dataclasses
import unittest

from personal_chatbots.content import load_test_cases
from personal_chatbots.runner import Outcome, _check, run_tests
from tests.support import BuiltCase, ROOT


class _Answer:
    def __init__(self, text="", source="refuse", citations=(), slots=None):
        self.text = text
        self.source = source
        self.citations = list(citations)
        self.slots = slots or {}

    @property
    def refused(self):
        return self.source == "refuse"


class CheckerTest(unittest.TestCase):
    def outcome(self, expect, answer):
        outcome = Outcome(slug="t", question="q", passed=True)
        _check(expect, answer, outcome)
        return outcome.notes

    def test_refuses_mismatch_is_reported(self):
        notes = self.outcome({"refuses": True}, _Answer(text="ok", source="knowledge"))
        self.assertTrue(any("refuses" in n for n in notes))

    def test_source_mismatch_is_reported(self):
        notes = self.outcome({"source": "knowledge"}, _Answer(source="entity"))
        self.assertTrue(any("source" in n for n in notes))

    def test_answer_contains_is_case_insensitive(self):
        self.assertEqual(self.outcome({"answer_contains": ["deepseek"]}, _Answer(text="DeepSeek Harness")), [])
        self.assertTrue(self.outcome({"answer_contains": ["nope"]}, _Answer(text="DeepSeek Harness")))


class SeededSuiteTest(BuiltCase):
    def test_every_seeded_case_passes(self):
        cases = load_test_cases(ROOT / "content" / "tests.yaml")
        outcomes = run_tests(self.cfg, cases, self.store)
        failures = [o for o in outcomes if not o.passed]
        detail = "\n".join(
            f"  {o.slug}: {o.question!r} -> [{o.got.get('source')}] {o.notes}" for o in failures
        )
        self.assertEqual(failures, [], f"{len(failures)} seeded case(s) failed:\n{detail}")

    def test_the_suite_covers_more_than_happy_paths(self):
        cases = load_test_cases(ROOT / "content" / "tests.yaml")
        kinds = {"positive": 0, "refusal": 0}
        for case in cases:
            if case["expect"].get("refuses"):
                kinds["refusal"] += 1
            else:
                kinds["positive"] += 1
        # A suite of only happy paths cannot catch over-broad rows.
        self.assertGreaterEqual(kinds["refusal"], 3)
        self.assertGreaterEqual(kinds["positive"], 3)

    def test_every_case_declares_an_origin(self):
        for case in load_test_cases(ROOT / "content" / "tests.yaml"):
            self.assertTrue(case["origin"], f"{case['slug']} has no origin")

    def test_the_suite_ignores_the_model_rung(self):
        """A fluent answer must never be able to pass a frozen case.

        Enabling the fallback in this repository turned three `refuses: true`
        cases green-to-red in one command, because the model answered where the
        structure had refused. The suite now runs structurally by construction.
        """
        from personal_chatbots.runner import structural_only

        enabled = dataclasses.replace(
            self.cfg,
            data={**self.cfg.data, "runtime": {
                **self.cfg.data["runtime"],
                "ladder": ["knowledge", "entity", "search", "fallback", "refuse"],
            }},
        )
        self.assertIn("fallback", enabled.ladder)
        self.assertNotIn("fallback", structural_only(enabled).ladder)

        cases = load_test_cases(ROOT / "content" / "tests.yaml")
        outcomes = run_tests(enabled, cases, self.store)
        self.assertEqual([o for o in outcomes if not o.passed], [])

    def test_run_tests_does_not_write_messages(self):
        cases = load_test_cases(ROOT / "content" / "tests.yaml")
        before = self.store.count_messages()
        run_tests(self.cfg, cases, self.store)
        self.assertEqual(self.store.count_messages(), before)


if __name__ == "__main__":
    unittest.main()
