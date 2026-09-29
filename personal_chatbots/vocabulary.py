"""The vocabulary: the small set of words the engine understands.

In v1 this is a Python dict, not a `vocab_terms` table. That is deliberate: the
maintenance agent does not yet need to invent new entity types, and a table
nobody writes to is a table that rots. When it does need to, the registry is
swapped for a table read -- the interface does not change. That is the seam
(docs/DESIGN.md section 7).
"""

from __future__ import annotations

ENTITY_TYPES: frozenset[str] = frozenset(
    {"repo", "account", "person", "language", "topic", "project", "product"}
)

LINK_TYPES: frozenset[str] = frozenset(
    {"owned_by", "uses_language", "tagged", "includes", "forked_from"}
)

#: What a link type may point at, in priority order.
#:
#: Needed because a natural key is unique only *within* an entity type: the login
#: `plae-tljg` is both an account and the person who owns it. Without this map a
#: link resolves by dictionary order, which is how an account ends up owned by
#: itself. It doubles as validation: a link pointing at the wrong kind of thing is
#: a content bug, and it should be caught at build time rather than at a visitor.
LINK_TARGETS: dict[str, tuple[str, ...]] = {
    "owned_by": ("account", "person"),
    "uses_language": ("language",),
    "tagged": ("topic",),
    "includes": ("repo",),
    "forked_from": ("repo",),
}

# What a knowledge row's action_json.kind may be. A new kind is new Python --
# that is the boundary between "new knowledge is data" and "new primitive is
# code", and it should stay rare.
ACTION_KINDS: frozenset[str] = frozenset({"answer", "list", "refuse"})

# The rungs the ladder may be built from. `composite` is L3 and not implemented;
# naming it here means config can ask for it and get a clear error.
RUNGS: frozenset[str] = frozenset({"knowledge", "entity", "search", "refuse", "composite"})

IMPLEMENTED_RUNGS: frozenset[str] = frozenset({"knowledge", "entity", "search", "refuse"})

# Fields of an entity that a template may interpolate.
ENTITY_FIELDS: frozenset[str] = frozenset({"key", "name", "summary", "url", "entity_type"})

# Columns of `entities` that may appear in a list action's order_by, plus the
# attr escape hatch. Whitelisted because order_by is interpolated into SQL.
ORDER_COLUMNS: frozenset[str] = frozenset(
    {"id", "key", "name", "summary", "entity_type", "updated_at", "url"}
)


#: Words that point at something already said. `-1` means "the most recent one".
#:
#: These live in the registry rather than in the database for the same reason
#: `entity_type` does: they are words the *engine* understands, and a new one is
#: only meaningful once the primitive that consumes it exists. Widening the list
#: is the cheap half; the primitive is the expensive half.
ORDINAL_WORDS: dict[str, int] = {
    "first": 0, "1st": 0,
    "second": 1, "2nd": 1,
    "third": 2, "3rd": 2,
    "fourth": 3, "4th": 3,
    "fifth": 4, "5th": 4,
    "sixth": 5, "6th": 5,
    "seventh": 6, "7th": 6,
    "eighth": 7, "8th": 7,
    "ninth": 8, "9th": 8,
    "tenth": 9, "10th": 9,
    "last": -1,
    # Chinese ordinals arrive as single characters because the tokenizer splits
    # CJK per character, which is why these are spelled out rather than derived.
    "一": 0, "二": 1, "三": 2, "四": 3, "五": 4,
}

#: Phrases that mean "the thing currently under discussion".
PRONOUN_PHRASES: frozenset[tuple[str, ...]] = frozenset(
    {
        ("it",),
        ("that",),
        ("this",),
        ("that", "one"),
        ("this", "one"),
        ("the", "same"),
        ("the", "same", "one"),
    }
)


class VocabularyError(ValueError):
    """Raised when content uses a word the engine does not know."""


def check_entity_type(term: str, where: str) -> str:
    if term not in ENTITY_TYPES:
        raise VocabularyError(
            f"{where}: unknown entity_type {term!r} "
            f"(known: {', '.join(sorted(ENTITY_TYPES))})"
        )
    return term


def check_link_type(term: str, where: str) -> str:
    if term not in LINK_TYPES:
        raise VocabularyError(
            f"{where}: unknown link_type {term!r} "
            f"(known: {', '.join(sorted(LINK_TYPES))})"
        )
    return term


def check_action_kind(term: str, where: str) -> str:
    if term not in ACTION_KINDS:
        raise VocabularyError(
            f"{where}: unknown action kind {term!r} "
            f"(known: {', '.join(sorted(ACTION_KINDS))})"
        )
    return term
