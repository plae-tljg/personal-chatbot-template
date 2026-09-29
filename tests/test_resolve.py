"""Slot resolution: the "a rung never guesses" rule, and the ways it can break."""

from __future__ import annotations

import unittest

from personal_chatbots.resolve import AliasIndex, resolve, single_entity
from personal_chatbots.store import Entity
from personal_chatbots.textnorm import aliases_for, tokenize


def entity(entity_type: str, key: str, name: str, extra_aliases: list[str] | None = None) -> Entity:
    return Entity(
        id=abs(hash((entity_type, key))) % 10_000,
        entity_type=entity_type,
        key=key,
        name=name,
        summary=f"{name} summary",
        aliases=aliases_for(name, key, extra=extra_aliases),
        attrs={},
        url=f"https://example.invalid/{key}",
    )


APP = entity("repo", "plae-tljg/Finance-Management-App", "Finance-Management-App")
WEB = entity("repo", "ellkaimu/Finance-Management-Web", "Finance-Management-Web")
DSH = entity("repo", "plae-tljg/dsh-review", "dsh-review")
TOPIC = entity("topic", "topic:finance-management", "finance-management")
PROJECT = entity("project", "project:finance-management", "Finance Management")
INDEX = AliasIndex([APP, WEB, DSH, TOPIC, PROJECT])


class ResolutionTest(unittest.TestCase):
    def test_separator_variants_all_resolve(self):
        for question in (
            "what is Finance-Management-App about",
            "what is finance management app about",
            "what is financemanagementapp about",
        ):
            resolved = resolve(tokenize(question), {"repo": "repo"}, INDEX)
            self.assertIsNotNone(resolved, question)
            skeleton, slots = resolved
            self.assertEqual(skeleton, "what is {repo} about")
            self.assertEqual(slots["repo"].key, APP.key)

    def test_partial_word_does_not_match(self):
        # Token equality, not substring: "dsh reviews" is not "dsh review".
        self.assertIsNone(resolve(tokenize("what is dsh reviews about"), {"repo": "repo"}, INDEX))

    def test_two_entities_sharing_an_alias_is_ambiguous(self):
        # Ambiguity is only meaningful *within* a slot's declared type: the slot
        # says which kind of thing it wants, so a topic and a project with the
        # same name never compete for the same slot.
        first = entity("repo", "x/one", "One", extra_aliases=["shared name"])
        second = entity("repo", "x/two", "Two", extra_aliases=["shared name"])
        index = AliasIndex([first, second])
        self.assertIsNone(resolve(tokenize("what is shared name about"), {"r": "repo"}, index))

    def test_a_slot_type_narrows_the_choice(self):
        # "finance management" names both a topic and a project, but a slot of
        # type topic only ever considers topics.
        resolved = resolve(tokenize("tell me about finance management"), {"x": "topic"}, INDEX)
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved[1]["x"].key, TOPIC.key)

    def test_same_span_from_different_types_is_ambiguous_for_a_bare_name(self):
        # No slot to narrow it: the entity rung must not pick between a topic and
        # a project that match the identical span.
        self.assertIsNone(single_entity(tokenize("finance management"), INDEX))

    def test_unresolvable_slot_returns_none(self):
        self.assertIsNone(resolve(tokenize("what is nothing here about"), {"repo": "repo"}, INDEX))

    def test_single_entity_needs_exactly_one(self):
        self.assertEqual(single_entity(tokenize("dsh-review"), INDEX).key, DSH.key)
        # The same entity twice is still one entity.
        self.assertEqual(single_entity(tokenize("dsh-review and dsh review"), INDEX).key, DSH.key)
        # Two different entities is not a card request.
        self.assertIsNone(single_entity(tokenize("dsh-review and Finance-Management-App"), INDEX))
        self.assertIsNone(single_entity(tokenize("nothing at all"), INDEX))

    def test_multi_slot(self):
        index = AliasIndex([DSH, entity("language", "language:javascript", "JavaScript")])
        resolved = resolve(
            tokenize("is dsh-review written in JavaScript"),
            {"repo": "repo", "language": "language"},
            index,
        )
        self.assertIsNotNone(resolved)
        skeleton, slots = resolved
        self.assertEqual(skeleton, "is {repo} written in {language}")
        self.assertEqual(slots["language"].key, "language:javascript")


if __name__ == "__main__":
    unittest.main()
