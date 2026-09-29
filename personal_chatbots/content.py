"""Load and validate ``content/*.yaml``.

Validation is the offline analogue of the write-time validator in the older
design: it runs at build time, in front of the agent, before anything reaches a
pull request. Bad content fails loudly here rather than quietly at a visitor.

The rules are the ones that catch the two classic failures:

* a row that can **never** fire -- a placeholder with no slot, a slot with no
  entity type, a pattern that is empty or all-placeholder;
* a row that fires on **everything** -- no patterns at all, or a `match` that
  constrains nothing while the pattern is a single placeholder.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import vocabulary as vocab
from .config import Config
from .textnorm import placeholders, tokenize

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")

#: A currency amount written into a template. This is the one literal the
#: validator refuses outright, because it is the change that happens most often
#: and the failure is silent: leave a stale price in a second sentence and the
#: bot contradicts itself while sounding certain (docs/CONCERNS.md C2).
#:
#: Deliberately narrow. "3-5 working days" and "24-month warranty" are values
#: too, but they change once a decade and parameterising them makes the
#: sentences worse. Prices change weekly. The rule targets the case that pays
#: for itself.
CURRENCY_LITERAL = re.compile(
    r"(?:[$£€¥]\s?\d[\d,]*(?:\.\d+)?)|(?:\d[\d,]*(?:\.\d+)?\s*(?:usd|eur|gbp|cny|rmb|jpy|元|美元|欧元))",
    re.IGNORECASE,
)


class ContentError(ValueError):
    """Bad content. Always carries the file and the slug."""


def _vocab(check, term: str, where: str) -> str:
    """Run a vocabulary check, reporting failures as content errors.

    The vocabulary has its own error type because it is also used at runtime, but
    at the content boundary everything a human must fix should look the same, and
    the CLI should not have to catch two exception types for one mistake.
    """
    try:
        return check(term, where)
    except vocab.VocabularyError as exc:
        raise ContentError(str(exc)) from exc


@dataclass
class CurationEntity:
    entity_type: str
    key: str
    name: str
    summary: str = ""
    url: str = ""
    aliases: list[str] = field(default_factory=list)
    attrs: dict[str, Any] = field(default_factory=dict)
    links: list[tuple[str, str]] = field(default_factory=list)  # (link_type, to_key)


@dataclass
class DesiredState:
    knowledge: list[dict[str, Any]] = field(default_factory=list)
    entities: list[CurationEntity] = field(default_factory=list)
    overrides: dict[str, dict[str, Any]] = field(default_factory=dict)
    tests: list[dict[str, Any]] = field(default_factory=list)


def _load_yaml(path: Path) -> Any:
    if not path.exists():
        raise ContentError(f"missing content file: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ContentError(f"{path.name}: invalid YAML: {exc}") from exc
    if data is None:
        return []
    if not isinstance(data, list):
        raise ContentError(f"{path.name}: top level must be a list")
    return data


# ---------------------------------------------------------------------------
# knowledge
# ---------------------------------------------------------------------------


def _check_pattern(pattern: str, slug: str, slots: dict[str, str]) -> None:
    tokens = tokenize(pattern)
    if not tokens:
        raise ContentError(f"knowledge[{slug}]: pattern {pattern!r} normalises to nothing")
    used = {t[1:-1] for t in tokens if t.startswith("{") and t.endswith("}")}
    for name in used:
        if name not in slots:
            raise ContentError(
                f"knowledge[{slug}]: pattern uses {{{name}}} but slots declares {sorted(slots)}"
            )
    literal = [t for t in tokens if not t.startswith("{")]
    if not literal:
        # A pattern that is nothing but a placeholder matches any question that
        # mentions an entity of that type -- including ones it has no answer for.
        raise ContentError(
            f"knowledge[{slug}]: pattern {pattern!r} has no literal words; it would match anything"
        )


def _check_template(template: str, slug: str, slots: dict[str, str], *, is_list: bool) -> None:
    found = CURRENCY_LITERAL.search(template)
    if found:
        raise ContentError(
            f"knowledge[{slug}]: template contains the currency amount {found.group(0)!r}. "
            "A price in a sentence is a value that will change, and a stale one is "
            "indistinguishable from a correct one. Put it on an entity and use a "
            "placeholder (docs/CONCERNS.md C2)."
        )
    for whole in placeholders(template):
        inner = whole[1:-1]
        root, _, rest = inner.partition(".")
        if root in {"attrs"} and rest:
            continue
        if is_list:
            # A list template is rendered once per row, so its placeholders are
            # entity fields, not slot names.
            if root not in vocab.ENTITY_FIELDS and root != "attrs":
                raise ContentError(
                    f"knowledge[{slug}]: list template uses {{{inner}}}; allowed are "
                    f"{sorted(vocab.ENTITY_FIELDS)} or attrs.<key>"
                )
            continue
        if root not in slots:
            raise ContentError(
                f"knowledge[{slug}]: template uses {{{inner}}} but {{{root}}} is not a declared slot "
                f"{sorted(slots)}"
            )
        if rest and rest not in vocab.ENTITY_FIELDS and not rest.startswith("attrs"):
            raise ContentError(
                f"knowledge[{slug}]: template uses {{{inner}}}; {rest!r} is not an entity field "
                f"({sorted(vocab.ENTITY_FIELDS)})"
            )


def _check_action(action: Any, slug: str, slots: dict[str, str]) -> None:
    if not isinstance(action, dict):
        raise ContentError(f"knowledge[{slug}]: action must be a mapping")
    kind = _vocab(vocab.check_action_kind, str(action.get("kind", "")), f"knowledge[{slug}]")
    if kind == "answer":
        template = action.get("template")
        if not isinstance(template, str) or not template.strip():
            raise ContentError(f"knowledge[{slug}]: answer action needs a non-empty template")
        _check_template(template, slug, slots, is_list=False)
    elif kind == "list":
        _vocab(vocab.check_entity_type, str(action.get("entity_type", "")), f"knowledge[{slug}].action")
        if action.get("link_type"):
            _vocab(vocab.check_link_type, str(action["link_type"]), f"knowledge[{slug}].action")
            direction = str(action.get("direction", "in"))
            if direction not in {"in", "out"}:
                raise ContentError(f"knowledge[{slug}]: direction must be 'in' or 'out'")
            if not slots:
                raise ContentError(
                    f"knowledge[{slug}]: a linked list needs a slot to link from"
                )
        template = action.get("template", "{name}")
        _check_template(str(template), slug, slots, is_list=True)
        order = action.get("order_by")
        if order:
            column = str(order).split()[0]
            if column.startswith("attrs."):
                pass
            elif column not in vocab.ORDER_COLUMNS:
                raise ContentError(
                    f"knowledge[{slug}]: order_by {column!r} is not a column "
                    f"({sorted(vocab.ORDER_COLUMNS)}) or attrs.<key>"
                )


def load_knowledge(path: Path) -> list[dict[str, Any]]:
    rows = _load_yaml(path)
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            raise ContentError(f"knowledge.yaml[{index}]: each entry must be a mapping")
        slug = str(raw.get("slug", "")).strip()
        if not SLUG_RE.match(slug):
            raise ContentError(f"knowledge.yaml[{index}]: bad slug {slug!r}")
        if slug in seen:
            raise ContentError(f"knowledge.yaml: duplicate slug {slug!r}")
        seen.add(slug)

        patterns = raw.get("patterns")
        if not isinstance(patterns, list) or not patterns:
            raise ContentError(f"knowledge[{slug}]: needs a non-empty 'patterns' list")

        slots = raw.get("slots") or {}
        if not isinstance(slots, dict):
            raise ContentError(f"knowledge[{slug}]: slots must be a mapping")
        for name, entity_type in slots.items():
            if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", str(name)):
                raise ContentError(f"knowledge[{slug}]: bad slot name {name!r}")
            _vocab(vocab.check_entity_type, str(entity_type), f"knowledge[{slug}].slots.{name}")

        for pattern in patterns:
            _check_pattern(str(pattern), slug, slots)

        match = raw.get("match") or {}
        if not isinstance(match, dict):
            raise ContentError(f"knowledge[{slug}]: match must be a mapping")
        for key in ("require", "exclude"):
            value = match.get(key, [])
            if not isinstance(value, list):
                raise ContentError(f"knowledge[{slug}]: match.{key} must be a list")

        action = raw.get("action")
        _check_action(action, slug, slots)

        citations = raw.get("cites") or []
        if not isinstance(citations, list):
            raise ContentError(f"knowledge[{slug}]: cites must be a list")

        out.append(
            {
                "slug": slug,
                "locale": str(raw.get("locale", "")),
                "patterns": [str(p) for p in patterns],
                "slots": {str(k): str(v) for k, v in slots.items()},
                "match": {"require": list(match.get("require", [])), "exclude": list(match.get("exclude", []))},
                "action": action,
                "citations": [str(c) for c in citations],
            }
        )
    return out


# ---------------------------------------------------------------------------
# curation
# ---------------------------------------------------------------------------


def load_curation(path: Path) -> tuple[list[CurationEntity], list[dict[str, Any]]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    data = data or {}
    if not isinstance(data, dict):
        raise ContentError("curation.yaml: top level must be a mapping")

    entities: list[CurationEntity] = []
    for raw in data.get("entities") or []:
        entity_type = _vocab(vocab.check_entity_type, str(raw.get("type", "")), "curation.entities")
        key = str(raw.get("key", "")).strip()
        if not key:
            raise ContentError("curation.entities: every entry needs a key")
        links: list[tuple[str, str]] = []
        for link in raw.get("links") or []:
            link_type = _vocab(vocab.check_link_type, str(link.get("type", "")), f"curation[{key}]")
            links.append((link_type, str(link.get("to", ""))))
        entities.append(
            CurationEntity(
                entity_type=entity_type,
                key=key,
                name=str(raw.get("name") or key),
                summary=str(raw.get("summary") or ""),
                url=str(raw.get("url") or ""),
                aliases=[str(a) for a in (raw.get("aliases") or [])],
                attrs=dict(raw.get("attrs") or {}),
                links=links,
            )
        )

    # A list, not a dict keyed by string. An override needs to name an entity
    # that may be identified by key alone ("owner/name") or by key *and* type --
    # the login `plae-tljg` is both an account and a person, and an untyped
    # override would resolve by iteration order. Encoding that in a string key
    # means guessing where the prefix ends, and keys legitimately contain colons
    # ("topic:finance"), so the ambiguity is structural, not cosmetic.
    overrides: list[dict[str, Any]] = []
    for raw in data.get("overrides") or []:
        key = str(raw.get("key", "")).strip()
        if not key:
            raise ContentError("curation.overrides: every entry needs a key")
        entity_type = raw.get("type")
        if entity_type:
            _vocab(vocab.check_entity_type, str(entity_type), f"curation.overrides[{key}].type")
        add_links: list[tuple[str, str]] = []
        for link in raw.get("add_links") or []:
            add_links.append(
                (
                    _vocab(vocab.check_link_type, str(link.get("type", "")), f"curation.overrides[{key}]"),
                    str(link.get("to", "")),
                )
            )
        overrides.append(
            {
                "key": key,
                "type": str(entity_type) if entity_type else "",
                "set": dict(raw.get("set") or {}),
                "add_aliases": [str(a) for a in (raw.get("add_aliases") or [])],
                "add_links": add_links,
                "summary": raw.get("summary"),
            }
        )
    return entities, overrides


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


def load_test_cases(path: Path) -> list[dict[str, Any]]:
    rows = _load_yaml(path)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            raise ContentError(f"tests.yaml[{index}]: each entry must be a mapping")
        slug = str(raw.get("slug", "")).strip()
        if not slug or slug in seen:
            raise ContentError(f"tests.yaml[{index}]: missing or duplicate slug {slug!r}")
        seen.add(slug)
        question = str(raw.get("question", "")).strip()
        if not question:
            raise ContentError(f"tests[{slug}]: needs a question")
        expect = raw.get("expect")
        if not isinstance(expect, dict) or not expect:
            raise ContentError(f"tests[{slug}]: needs a non-empty 'expect' mapping")
        out.append(
            {
                "slug": slug,
                "question": question,
                "expect": expect,
                "origin": str(raw.get("origin", "seed")),
                "status": str(raw.get("status", "active")),
            }
        )
    return out


def load_all(cfg: Config) -> DesiredState:
    knowledge = load_knowledge(cfg.content_dir / "knowledge.yaml")
    entities, overrides = load_curation(cfg.content_dir / "curation.yaml")
    tests = load_test_cases(cfg.content_dir / "tests.yaml")
    return DesiredState(knowledge=knowledge, entities=entities, overrides=overrides, tests=tests)
