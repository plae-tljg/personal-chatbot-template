"""Text normalisation and alias generation."""

from __future__ import annotations

import unittest

from personal_chatbots.textnorm import aliases_for, normalize, split_camel, tokenize


class NormalizeTest(unittest.TestCase):
    def test_separators_become_the_same_tokens(self):
        forms = [
            "Finance-Management-App",
            "finance management app",
            "Finance_Management_App",
            "FinanceManagementApp",
        ]
        self.assertEqual(len({normalize(f) for f in forms}), 1)

    def test_camel_case_is_split(self):
        self.assertEqual(normalize("ThreeBodySystemAnimation"), "three body system animation")
        self.assertEqual(normalize("MaaFwPhoneAI"), "maa fw phone ai")

    def test_punctuation_and_symbols_are_dropped(self):
        self.assertEqual(normalize("what is the price of dsh-review?"), "what is the price of dsh review")
        self.assertEqual(normalize("dsh-review (3★)"), "dsh review 3")

    def test_placeholders_survive_untouched(self):
        self.assertIn("{repo}", tokenize("what is {repo} about"))
        self.assertIn("{repo.summary}", tokenize("{repo.summary}"))

    def test_cjk_splits_per_character(self):
        # No segmentation dictionary at this size; per-character tokens are enough
        # for a Chinese question to match a Chinese pattern.
        self.assertEqual(normalize("营业时间"), "营 业 时 间")

    def test_split_camel(self):
        self.assertEqual(split_camel("Type_As_If_You_Are_Working"), "Type As If You Are Working")


class AliasTest(unittest.TestCase):
    def test_short_and_long_forms(self):
        aliases = aliases_for("dsh-review", "plae-tljg/dsh-review")
        self.assertIn("dsh review", aliases)
        self.assertIn("dshreview", aliases)
        self.assertIn("plae tljg dsh review", aliases)

    def test_very_short_names_get_no_alias(self):
        # A one- or two-character alias would match half the corpus. Names this
        # short need a curation alias instead -- that is a documented limitation.
        self.assertEqual(aliases_for("C"), [])

    def test_aliases_are_deduplicated(self):
        aliases = aliases_for("Finance-Management-App", "plae-tljg/Finance-Management-App")
        self.assertEqual(len(aliases), len(set(aliases)))


if __name__ == "__main__":
    unittest.main()
