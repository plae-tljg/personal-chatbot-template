"""The frame: cross-turn context that is *derived*, not stored.

This module exists because of a hole in the original taxonomy. `docs/LEVELS.md`
sorted everything by two axes -- does it need cross-turn state, does it produce
an outside effect -- and concluded that anything cross-turn must be a Flow with
a `flow_states` table. That is wrong, and the counter-example is everyday:

    › what projects does LKM have?
      • Finance-Management-App … • Anime-Webview … • domain-ops-agent … (10 items)
    › tell me about the second one

Nothing here needs a state machine. It needs the *previous answer*, which we
already keep. So:

    **Reference is cross-turn without being stateful.**

The frame is a projection of the transcript:

    items    the citations of the most recent answer that listed several things
    subject  the single entity of the most recent answer that discussed one

Both come out of `messages.citations_json`, which exists to make answers
traceable. Reference resolution is a second use for data we were keeping anyway,
which is why this costs no tables, no migration, and no live state.

Two rules keep it honest:

* **No context, no resolution.** An ordinal with no list behind it resolves to
  nothing and the question falls through to the normal ladder. It never guesses.
* **The frame is bounded.** Only the last few answers are consulted, so a list
  from twenty turns ago cannot hijack the current question.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Sequence

from .store import Entity, Store
from .textnorm import tokenize
from .vocabulary import ORDINAL_WORDS, PRONOUN_PHRASES

#: How many recent answers may contribute context. Small on purpose: a reference
#: is almost always to the immediately preceding answer, and a wide window turns
#: a wrong guess into a confident wrong answer.
DEFAULT_WINDOW = 6


@dataclass(frozen=True)
class Reference:
    start: int
    end: int
    phrase: str
    entity: Entity
    kind: str  # "ordinal" | "subject"

    def as_dict(self) -> dict[str, str]:
        return {"phrase": self.phrase, "key": self.entity.key, "kind": self.kind}


@dataclass
class Frame:
    subject: Entity | None = None
    items: list[Entity] = field(default_factory=list)

    # -- construction ---------------------------------------------------------

    @classmethod
    def from_transcript(
        cls,
        rows: Sequence[Any],
        store: Store,
        window: int = DEFAULT_WINDOW,
    ) -> "Frame":
        """Read the last few answers and reconstruct what is being discussed.

        Scans newest first. A list answer claims `items` and blocks older single
        answers from becoming the subject -- otherwise, after a list, "it" would
        silently point at whatever was discussed two turns before the list.
        """
        frame = cls()
        blocked_subject = False

        answers = [row for row in rows if row["role"] == "assistant"]
        for row in reversed(answers[-window:] if window else answers):
            keys = [
                str(citation.get("key", ""))
                for citation in json.loads(row["citations_json"] or "[]")
                if citation.get("key")
            ]
            entities = [e for e in (store.entity_by_key(key) for key in keys) if e is not None]
            if not entities:
                continue

            if len(entities) >= 2:
                if not frame.items:
                    frame.items = entities
                blocked_subject = True
            elif frame.subject is None and not blocked_subject:
                frame.subject = entities[0]

            if frame.items and frame.subject is not None:
                break

        return frame

    @property
    def empty(self) -> bool:
        return self.subject is None and not self.items

    # -- resolution -----------------------------------------------------------

    def resolve_ordinal(self, index: int) -> Entity | None:
        if not self.items:
            return None
        try:
            return self.items[index]
        except IndexError:
            return None

    def references(self, tokens: Sequence[str]) -> list[Reference]:
        """Every referring expression in the token list that can be resolved.

        Longest phrase wins at each position, so "the second one" is one
        reference rather than an ordinal plus a stray "one".
        """
        out: list[Reference] = []
        cursor = 0
        while cursor < len(tokens):
            found = self._match_at(tokens, cursor)
            if found is None:
                cursor += 1
                continue
            out.append(found)
            cursor = found.end
        return out

    def _match_at(self, tokens: Sequence[str], start: int) -> Reference | None:
        # Phrases are tried longest first so a two-word ordinal is not shadowed
        # by the single word inside it.
        for length, phrase in _candidate_phrases():
            end = start + length
            if end > len(tokens):
                continue
            window = tuple(tokens[start:end])
            if window != phrase:
                continue

            entity = self._resolve_phrase(phrase)
            if entity is None:
                continue
            return Reference(
                start=start,
                end=end,
                phrase=" ".join(window),
                entity=entity,
                kind="ordinal" if _ordinal_index(phrase) is not None else "subject",
            )
        return None

    def _resolve_phrase(self, phrase: tuple[str, ...]) -> Entity | None:
        ordinal = _ordinal_index(phrase)
        if ordinal is not None:
            return self.resolve_ordinal(ordinal)
        if phrase in PRONOUN_PHRASES:
            return self.subject
        return None


# ---------------------------------------------------------------------------
# word tables
# ---------------------------------------------------------------------------


def _ordinal_index(phrase: tuple[str, ...]) -> int | None:
    """Pull an ordinal index out of a phrase, or None if it is not an ordinal.

    Recognised shapes, with the connector words optional:

        first | 1st | last
        the first | the first one | first one
    """
    words = [w for w in phrase if w not in {"the", "one"}]
    if len(words) != 1:
        return None
    return ORDINAL_WORDS.get(words[0])


def _candidate_phrases() -> list[tuple[int, tuple[str, ...]]]:
    """Every phrase shape, longest first. Built once and cached."""
    global _PHRASES
    if _PHRASES is not None:
        return _PHRASES

    phrases: set[tuple[str, ...]] = set()
    for word in ORDINAL_WORDS:
        phrases.add((word,))
        phrases.add(("the", word))
        phrases.add((word, "one"))
        phrases.add(("the", word, "one"))
    phrases.update(PRONOUN_PHRASES)

    _PHRASES = sorted(((len(p), p) for p in phrases), key=lambda item: (-item[0], item[1]))
    return _PHRASES


_PHRASES: list[tuple[int, tuple[str, ...]]] | None = None


def apply(tokens: Sequence[str], frame: Frame | None) -> tuple[list[str], dict[str, str]]:
    """Rewrite referring expressions into the entities they point at.

    A rewrite, not a rung: once "the second one" becomes
    "Finance-Management-Web", every rung below works unchanged, and the answer
    records *how* the reference was read rather than hiding it.
    """
    if frame is None or frame.empty:
        return list(tokens), {}

    references = frame.references(tokens)
    if not references:
        return list(tokens), {}

    rewritten = list(tokens)
    for reference in sorted(references, key=lambda r: r.start, reverse=True):
        rewritten[reference.start : reference.end] = tokenize(reference.entity.name)

    return rewritten, {reference.phrase: reference.entity.key for reference in references}
