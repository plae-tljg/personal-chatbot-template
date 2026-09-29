"""The runtime: a ladder of rungs over rows, and the actions they execute.

```
knowledge   pattern match, slots resolve to live entities   -> answer + citations
entity      a bare entity name                              -> card + citations
search      FTS5 over that entity's documents               -> passage + citation
refuse      honest "I don't know", recorded                 -> the learning signal
```

Three properties this module is responsible for:

* **A rung never guesses.** Ambiguity falls through to refusal.
* **No model runs here.** Not a disabled flag: no code path. The ladder is read
  from ``bot.json``, so adding a rung later is an append to that list.
* **Everything is recorded, including the failures.** An unanswered question is
  the only learning signal the system has.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from .config import Config
from .frame import Frame, apply as apply_frame
from .resolve import AliasIndex, Match, resolve, single_entity
from .store import Citation, Entity, KnowledgeRow, Store
from .textnorm import normalize, render, tokenize
from .vocabulary import VocabularyError

#: Words that carry no information when deciding what a question is *about*.
STOPWORDS = frozenset(
    """a an and are about do does for from how i in is it me my of on or tell that the
    to what when where which who why you your have has can could would""".split()
)

#: A question that is only filler around one entity name is a request for that
#: entity's card, not for a knowledge row.
#:
#: The connectors matter once references resolve: "and the third?" rewrites to
#: "and domain ops agent", and refusing that would waste a correct resolution on
#: a missing pattern. Two entity names still yields no card, because choosing
#: between them would be a guess.
FILLER = frozenset(
    {
        "what", "is", "who", "about", "the", "tell", "me", "a", "an", "of",
        "do", "you", "know",
        "and", "also", "then", "next", "please",
    }
)


class RungError(RuntimeError):
    pass


@dataclass
class Answer:
    text: str
    source: str
    citations: list[Citation] = field(default_factory=list)
    matched_slug: str = ""
    slots: dict[str, Entity] = field(default_factory=dict)
    latency_ms: float = 0
    session_id: str = ""
    turn: int = 0
    #: referring phrase -> entity key, e.g. {"the second one": "ellkaimu/Anime-Webview"}
    refs: dict[str, str] = field(default_factory=dict)

    @property
    def refused(self) -> bool:
        return self.source == "refuse"


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def _entity_values(slot: str, entity: Entity) -> dict[str, str]:
    values = {
        "{" + slot + "}": entity.name,
        "{" + slot + ".key}": entity.key,
        "{" + slot + ".name}": entity.name,
        "{" + slot + ".summary}": entity.summary,
        "{" + slot + ".url}": entity.url,
        "{" + slot + ".entity_type}": entity.entity_type,
    }
    for key, value in entity.attrs.items():
        values["{" + slot + ".attrs." + key + "}"] = _stringify(value)
    return values


def _stringify(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (list, tuple)):
        return ", ".join(_stringify(v) for v in value)
    if value is None:
        return ""
    return str(value)


def _row_values(entity: Entity) -> dict[str, str]:
    values = {
        "{key}": entity.key,
        "{name}": entity.name,
        "{summary}": entity.summary,
        "{url}": entity.url,
        "{entity_type}": entity.entity_type,
    }
    for key, value in entity.attrs.items():
        values["{attrs." + key + "}"] = _stringify(value)
    return values


# ---------------------------------------------------------------------------
# actions
# ---------------------------------------------------------------------------


@dataclass
class ActionResult:
    text: str
    citations: list[Citation]


def _cite(entity: Entity) -> Citation:
    return Citation(key=entity.key, label=entity.name, url=entity.url)


def run_action(
    row: KnowledgeRow, slots: dict[str, Entity], store: Store, cfg: Config
) -> ActionResult:
    action = row.action
    kind = action.get("kind")

    if kind == "refuse":
        return ActionResult(text=str(action.get("template") or cfg.refuse_template), citations=[])

    if kind == "answer":
        values: dict[str, str] = {}
        cited: list[Entity] = []
        for name, entity in slots.items():
            values.update(_entity_values(name, entity))
            cited.append(entity)
        text = render(str(action["template"]), values)
        citations = [_cite(e) for e in cited]
        for extra in row.citations:
            url = render(extra, values)
            if url and not any(c.url == url for c in citations):
                citations.append(Citation(key=url, label=url, url=url))
        return ActionResult(text=text, citations=citations)

    if kind == "list":
        entities = _list_targets(row, slots, store)
        limit = int(action.get("limit") or 0)
        if limit:
            entities = entities[:limit]
        if not entities:
            return ActionResult(text="", citations=[])
        lines = [render(str(action.get("template", "{name}")), _row_values(e)) for e in entities]
        return ActionResult(text="\n".join(f"• {line}" for line in lines), citations=[_cite(e) for e in entities])

    raise VocabularyError(f"knowledge[{row.slug}]: unknown action kind {kind!r}")


def _list_targets(row: KnowledgeRow, slots: dict[str, Entity], store: Store) -> list[Entity]:
    action = row.action
    entity_type = str(action.get("entity_type", ""))
    link_type = action.get("link_type")

    if link_type:
        slot_name = next(iter(row.slots), None)
        entity = slots.get(slot_name) if slot_name else None
        if entity is None:
            return []
        return store.linked_entities(
            entity.id, str(link_type), str(action.get("direction", "in")), entity_type
        )

    order_by = str(action.get("order_by") or "id")
    desc = order_by.lower().endswith(" desc")
    column = order_by.split()[0]
    if column.startswith("attrs."):
        attr = column.split(".", 1)[1]
        sql_expr = f"json_extract(attrs_json, '$.{attr}')"
    else:
        sql_expr = column
    return store.list_entities(entity_type, order_by=sql_expr, desc=desc)


# ---------------------------------------------------------------------------
# rungs
# ---------------------------------------------------------------------------


@dataclass
class Runtime:
    store: Store
    cfg: Config
    index: AliasIndex
    rows: list[KnowledgeRow]

    @classmethod
    def from_store(cls, store: Store, cfg: Config) -> "Runtime":
        return cls(
            store=store,
            cfg=cfg,
            index=AliasIndex(store.entities()),
            rows=store.knowledge_rows(),
        )

    # -- rung 1: knowledge ----------------------------------------------------

    def rung_knowledge(self, tokens: list[str]) -> Answer | None:
        for row in self.rows:
            if row.locale and row.locale != self.cfg.default_locale:
                continue
            resolved = resolve(tokens, row.slots, self.index)
            if resolved is None:
                continue
            skeleton, slots = resolved

            require = [normalize(str(w)) for w in row.match.get("require", [])]
            exclude = [normalize(str(w)) for w in row.match.get("exclude", [])]
            joined = " ".join(tokens)
            if any(word and word not in joined for word in require):
                continue
            if any(word and word in joined for word in exclude):
                continue

            patterns = {normalize(p) for p in row.patterns}
            if skeleton not in patterns:
                continue

            result = run_action(row, slots, self.store, self.cfg)
            if not result.text:
                continue
            return Answer(
                text=result.text,
                source="knowledge",
                # Never truncated. `citations_json` is the record of what an
                # answer was built from -- the frame reads it to resolve "the
                # second one", so a display cap here silently shortens the list
                # a visitor can refer back to. Capping is a rendering decision.
                citations=result.citations,
                matched_slug=row.slug,
                slots=slots,
            )
        return None

    # -- rung 2: entity -------------------------------------------------------

    def rung_entity(self, tokens: list[str]) -> Answer | None:
        entity = single_entity(tokens, self.index)
        if entity is None:
            return None
        leftover = set(_leftover(tokens, entity, self.index))
        if leftover - FILLER:
            return None
        if not entity.summary:
            return None
        return Answer(
            text=f"{entity.name} — {entity.summary}",
            source="entity",
            citations=[_cite(entity)],
            matched_slug=entity.key,
            slots={},
        )

    # -- rung 3: search -------------------------------------------------------

    def rung_search(self, tokens: list[str]) -> Answer | None:
        """Full-text search, scoped to the one entity the question is about.

        Deliberately not a corpus-wide search: an unscoped FTS guess answers
        questions it has no business answering, and the ambiguous case is exactly
        where a portfolio bot must refuse.
        """
        entity = single_entity(tokens, self.index)
        if entity is None:
            return None
        words = [
            w for w in _leftover(tokens, entity, self.index)
            if len(w) >= 3 and w not in STOPWORDS and w not in FILLER
        ]
        if not words:
            return None
        hits = self.store.search_documents(entity.id, words[:3])
        if not hits:
            return None
        body = " ".join(hits[0].body.split())
        snippet = body[:280] + ("…" if len(body) > 280 else "")
        return Answer(
            text=f"From the {hits[0].title} README: {snippet}",
            source="search",
            citations=[_cite(entity)],
            matched_slug=hits[0].slug,
            slots={},
        )

    # -- rung 4: refuse -------------------------------------------------------

    def rung_refuse(self, tokens: list[str]) -> Answer:
        return Answer(text=self.cfg.refuse_template, source="refuse", citations=[])

    # -- the ladder -----------------------------------------------------------

    def ladder(self) -> list[tuple[str, Callable[[list[str]], Answer | None]]]:
        table = {
            "knowledge": self.rung_knowledge,
            "entity": self.rung_entity,
            "search": self.rung_search,
            "refuse": self.rung_refuse,
        }
        rungs: list[tuple[str, Callable[[list[str]], Answer | None]]] = []
        for name in self.cfg.ladder:
            if name not in table:
                raise RungError(
                    f"bot.json: rung {name!r} is not implemented at level {self.cfg.level} "
                    f"(implemented: {', '.join(sorted(table))}). See docs/LEVELS.md."
                )
            rungs.append((name, table[name]))
        return rungs

    def ask(self, question: str, frame: Frame | None = None) -> Answer:
        """Answer one question.

        ``frame`` carries what earlier turns were about. It only ever *rewrites*
        the question -- referring expressions become the entities they point at --
        so every rung below stays exactly what it was. No rung reads the frame,
        which is why adding context did not add a state machine.
        """
        started = time.perf_counter()
        tokens, refs = apply_frame(tokenize(question), frame)
        answer: Answer | None = None
        for _, rung in self.ladder():
            answer = rung(tokens)
            if answer is not None and (answer.text or answer.refused):
                break
        if answer is None or not (answer.text or answer.refused):
            answer = self.rung_refuse(tokens)
        answer.latency_ms = round((time.perf_counter() - started) * 1000, 3)
        answer.refs = refs
        return answer

    def ask_and_record(self, question: str, session_id: str | None = None) -> Answer:
        """Answer and append the turn to a transcript.

        Earlier turns now matter in exactly one narrow way: a referring
        expression ("the second one", "it") is rewritten into the entity it
        points at before the ladder runs. Nothing else is carried over -- there
        is still no state machine, and a question that needs a value collected
        across several turns still refuses.

        That is the whole point of the frame: it *reads* the transcript rather
        than maintaining a parallel state, so it added no tables and no live
        variables.
        """
        session = session_id or str(uuid.uuid4())
        # A brand-new session has no history, so a first-turn "the second one"
        # refuses rather than guessing at a list that was never shown.
        frame = (
            Frame.from_transcript(self.store.session_messages(session), self.store)
            if session_id
            else Frame()
        )
        answer = self.ask(question, frame)
        answer.session_id = session
        answer.turn = self.store.record_turn(
            session_id=session,
            question=question,
            normalized=normalize(question),
            answer=answer.text,
            source=answer.source,
            matched_slug=answer.matched_slug,
            citations=answer.citations,
            unresolved=answer.refused,
            latency_ms=answer.latency_ms,
            refs=answer.refs,
        )
        return answer


def _leftover(tokens: list[str], entity: Entity, index: AliasIndex) -> list[str]:
    """Tokens of the question that are not part of the matched entity name.

    Returns a list, not a set: the search rung takes the first few words, and set
    ordering over strings is not stable across processes.
    """
    matches = [m for m in index.matches(tokens, entity.entity_type) if m.entity.id == entity.id]
    if not matches:
        return [t for t in tokens if not t.startswith("{")]
    best = max(matches, key=lambda m: m.length)
    remaining = tokens[: best.start] + tokens[best.end :]
    return [t for t in remaining if not t.startswith("{")]
