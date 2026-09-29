"""The validator: content mistakes must fail at build time, in front of the agent.

Every case here is one of the two classic failures -- a row that can never fire,
or a row that fires on everything.
"""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from personal_chatbots.content import (
    ContentError,
    load_curation,
    load_knowledge,
    load_test_cases,
)


def write(tmp: Path, name: str, body: str) -> Path:
    path = tmp / name
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


class KnowledgeValidationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="pc-content-"))

    def build(self, body: str):
        return load_knowledge(write(self.tmp, "knowledge.yaml", body))

    def test_accepts_a_well_formed_row(self):
        rows = self.build(
            """
            - slug: repo.about
              patterns: ["what is {repo} about"]
              slots: { repo: repo }
              action: { kind: answer, template: "{repo} — {repo.summary}" }
            """
        )
        self.assertEqual(rows[0]["slug"], "repo.about")

    def test_rejects_placeholder_without_a_slot(self):
        with self.assertRaises(ContentError) as caught:
            self.build(
                """
                - slug: bad
                  patterns: ["what is {repo} about"]
                  slots: {}
                  action: { kind: answer, template: "x" }
                """
            )
        self.assertIn("{repo}", str(caught.exception))

    def test_rejects_unknown_entity_type(self):
        with self.assertRaises(ContentError) as caught:
            self.build(
                """
                - slug: bad
                  patterns: ["what is {thing} about"]
                  slots: { thing: wibble }
                  action: { kind: answer, template: "{thing}" }
                """
            )
        self.assertIn("wibble", str(caught.exception))

    def test_rejects_a_pattern_with_no_literal_words(self):
        # "{repo}" alone would match any question naming any repository.
        with self.assertRaises(ContentError) as caught:
            self.build(
                """
                - slug: greedy
                  patterns: ["{repo}"]
                  slots: { repo: repo }
                  action: { kind: answer, template: "{repo}" }
                """
            )
        self.assertIn("match anything", str(caught.exception))

    def test_rejects_template_placeholder_that_is_not_a_slot(self):
        with self.assertRaises(ContentError) as caught:
            self.build(
                """
                - slug: bad
                  patterns: ["what is {repo} about"]
                  slots: { repo: repo }
                  action: { kind: answer, template: "{language}" }
                """
            )
        self.assertIn("{language}", str(caught.exception))

    def test_rejects_unknown_action_kind(self):
        with self.assertRaises(ContentError):
            self.build(
                """
                - slug: bad
                  patterns: ["hello there"]
                  slots: {}
                  action: { kind: teleport }
                """
            )

    def test_rejects_unsafe_order_by(self):
        with self.assertRaises(ContentError):
            self.build(
                """
                - slug: bad
                  patterns: ["list things"]
                  slots: {}
                  action:
                    kind: list
                    entity_type: repo
                    order_by: "id; drop table entities"
                """
            )

    def test_rejects_duplicate_slug(self):
        with self.assertRaises(ContentError) as caught:
            self.build(
                """
                - slug: same
                  patterns: ["a b c"]
                  slots: {}
                  action: { kind: answer, template: "x" }
                - slug: same
                  patterns: ["d e f"]
                  slots: {}
                  action: { kind: answer, template: "y" }
                """
            )
        self.assertIn("duplicate", str(caught.exception))

    def test_rejects_a_currency_amount_in_a_template(self):
        # The one literal the validator refuses outright: a price written into a
        # sentence is a value that will change, and a stale one reads exactly
        # like a correct one (docs/CONCERNS.md C2).
        with self.assertRaises(ContentError) as caught:
            self.build(
                """
                - slug: product.price
                  patterns: ["how much is {product}"]
                  slots: { product: product }
                  action: { kind: answer, template: "{product} costs $1299." }
                """
            )
        self.assertIn("$1299", str(caught.exception))

    def test_accepts_a_price_that_comes_from_the_entity(self):
        rows = self.build(
            """
            - slug: product.price
              patterns: ["how much is {product}"]
              slots: { product: product }
              action: { kind: answer, template: "{product} costs {product.attrs.price}." }
            """
        )
        self.assertEqual(rows[0]["slug"], "product.price")

    def test_non_monetary_numbers_are_allowed(self):
        # "3-5 working days" is a value too, but it changes once a decade and
        # parameterising it makes the sentence worse. The rule stays narrow.
        self.build(
            """
            - slug: shipping
              patterns: ["how long is shipping"]
              slots: {}
              action: { kind: answer, template: "Standard delivery is 3-5 working days." }
            """
        )

    def test_rejects_empty_patterns(self):
        with self.assertRaises(ContentError):
            self.build(
                """
                - slug: bad
                  patterns: []
                  action: { kind: answer, template: "x" }
                """
            )


class CurationValidationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="pc-curation-"))

    def test_rejects_unknown_link_type(self):
        path = write(
            self.tmp,
            "curation.yaml",
            """
            entities:
              - type: project
                key: project:x
                name: X
                links: [{ type: contains, to: "a/b" }]
            """,
        )
        with self.assertRaises(ContentError) as caught:
            load_curation(path)
        self.assertIn("contains", str(caught.exception))

    def test_typed_override_key_is_kept(self):
        path = write(
            self.tmp,
            "curation.yaml",
            """
            overrides:
              - key: plae-tljg
                type: account
                summary: "Primary account."
            """,
        )
        _, overrides = load_curation(path)
        self.assertEqual(overrides[0]["type"], "account")
        self.assertEqual(overrides[0]["key"], "plae-tljg")


class TestValidationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="pc-tests-"))

    def test_rejects_a_case_with_no_expectation(self):
        path = write(
            self.tmp,
            "tests.yaml",
            """
            - slug: t
              question: "hello?"
              expect: {}
            """,
        )
        with self.assertRaises(ContentError):
            load_test_cases(path)

    def test_rejects_a_case_with_no_question(self):
        path = write(
            self.tmp,
            "tests.yaml",
            """
            - slug: t
              question: ""
              expect: { refuses: true }
            """,
        )
        with self.assertRaises(ContentError):
            load_test_cases(path)


if __name__ == "__main__":
    unittest.main()
