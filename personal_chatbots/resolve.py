"""Slot resolution: finding entities inside a question.

The rule this module exists to enforce:

    **A rung never guesses.**

If a placeholder matches two different entities, the row does not match. Falling
through to refusal is correct behaviour; picking a favourite between
``Finance-Management-App`` and ``Finance-Management-Web`` is how a bot starts
lying about somebody's CV.

Matching is token-sequence equality over the normalised forms (see textnorm), so
"Finance-Management-App", "finance management app" and "financemanagementapp"
all resolve to the same row, and "dsh review" does not match inside "dsh reviews".
"""

from __future__ import annotations

from dataclasses import dataclass

from .store import Entity
from .textnorm import normalize, tokenize


@dataclass(frozen=True)
class Match:
    start: int
    end: int
    entity: Entity

    @property
    def length(self) -> int:
        return self.end - self.start


class AliasIndex:
    """Alias -> entity, grouped by entity type. Built once per process."""

    def __init__(self, entities: list[Entity]):
        # entity_type -> alias tokens -> list of entities
        self._by_type: dict[str, dict[tuple[str, ...], list[Entity]]] = {}
        for entity in entities:
            bucket = self._by_type.setdefault(entity.entity_type, {})
            for alias in entity.aliases:
                tokens = tuple(tokenize(alias))
                if not tokens:
                    continue
                bucket.setdefault(tokens, []).append(entity)
        self._lengths: dict[str, list[int]] = {
            etype: sorted({len(k) for k in bucket}, reverse=True)
            for etype, bucket in self._by_type.items()
        }

    @property
    def entity_types(self) -> list[str]:
        return sorted(self._by_type)

    def matches(self, tokens: list[str], entity_type: str) -> list[Match]:
        """Every alias occurrence of ``entity_type`` in the token list."""
        bucket = self._by_type.get(entity_type)
        if not bucket:
            return []
        out: list[Match] = []
        for size in self._lengths.get(entity_type, []):
            for start in range(0, len(tokens) - size + 1):
                entities = bucket.get(tuple(tokens[start:start + size]))
                if not entities:
                    continue
                for entity in entities:
                    out.append(Match(start=start, end=start + size, entity=entity))
        return out

    def matches_any(self, tokens: list[str]) -> list[Match]:
        out: list[Match] = []
        for entity_type in self._by_type:
            out.extend(self.matches(tokens, entity_type))
        return out


def _select(matches: list[Match], taken: list[tuple[int, int]]) -> list[Match]:
    """Longest non-overlapping matches, ignoring anything already claimed."""
    chosen: list[Match] = []
    for match in sorted(matches, key=lambda m: (-m.length, m.start)):
        if any(match.start < end and start < match.end for start, end in taken):
            continue
        # Two *different* entities matching the same span is ambiguity, not a
        # duplicate. Collapsing them here would let dictionary order pick a
        # winner, which is exactly the guess this module exists to prevent.
        if any(
            match.start < c.end and c.start < match.end and (match.start, match.end) != (c.start, c.end)
            for c in chosen
        ):
            continue
        chosen.append(match)
    return chosen


def resolve(
    tokens: list[str], slots: dict[str, str], index: AliasIndex
) -> tuple[str, dict[str, Entity]] | None:
    """Resolve every declared slot, then return the question's skeleton.

    Returns ``(skeleton, {slot: entity})`` or ``None`` when any slot fails or is
    ambiguous. The skeleton is the question with each resolved span replaced by
    its placeholder, which is what gets compared against a pattern.
    """
    taken: list[tuple[int, int]] = []
    assignments: list[tuple[str, Match]] = []
    chosen: dict[str, Entity] = {}

    for slot, entity_type in slots.items():
        candidates = _select(index.matches(tokens, entity_type), taken)
        if not candidates:
            return None
        distinct = {c.entity.id for c in candidates}
        if len(distinct) > 1:
            # Two different things could fill this slot. Refuse rather than pick.
            return None
        best = candidates[0]
        taken.append((best.start, best.end))
        assignments.append((slot, best))
        chosen[slot] = best.entity

    by_start = {match.start: slot for slot, match in assignments}
    by_end = {match.start: match.end for _, match in assignments}
    parts: list[str] = []
    cursor = 0
    while cursor < len(tokens):
        slot = by_start.get(cursor)
        if slot is not None:
            parts.append("{" + slot + "}")
            cursor = by_end[cursor]
        else:
            parts.append(tokens[cursor])
            cursor += 1
    return " ".join(parts), chosen


def skeleton_of(text: str) -> str:
    """The normalised token form of a pattern, placeholders preserved."""
    return normalize(text)


def single_entity(tokens: list[str], index: AliasIndex) -> Entity | None:
    """The one entity a question is about, or None if it is zero or several."""
    matches = _select(index.matches_any(tokens), [])
    distinct: dict[int, Entity] = {}
    for match in matches:
        distinct.setdefault(match.entity.id, match.entity)
    if len(distinct) != 1:
        return None
    return next(iter(distinct.values()))
