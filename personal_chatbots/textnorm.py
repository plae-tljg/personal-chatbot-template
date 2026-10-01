"""Text normalisation and alias generation.

The whole matching engine rests on one decision: a question and a pattern are
compared as **token sequences**, not as strings. Tokens are lowercase words,
single CJK characters, or `{placeholders}`. Everything else -- punctuation,
underscores, dashes, slashes -- disappears.

That gives clean word boundaries for free (no regex escaping, no substring
false positives) and makes "Finance-Management-App", "finance management app"
and "Finance Management App" the same thing.
"""

from __future__ import annotations

import re

# A placeholder, a CJK character, or a run of word characters.
_TOKEN = re.compile(r"\{[^}]*\}|\w+", re.UNICODE)
_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff]")
_CJK_OR_ASCII = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff]|[A-Za-z0-9_]+"
)
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")

#: A pattern placeholder, e.g. ``{repo}`` or ``{repo.summary}``.
PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)(?:\.([A-Za-z_][A-Za-z0-9_]*))?\}")


def tokenize(text: str) -> list[str]:
    """Split text into comparable tokens.

    Three expansions happen here, and all three are needed for GitHub naming:

    * separators split a token -- ``dsh-review`` becomes ``dsh``, ``review``;
    * camelCase splits a token -- ``ThreeBodySystemAnimation`` becomes four;
    * CJK runs split per character, so a Chinese question can match a Chinese
      pattern without a segmentation dictionary.
    """
    out: list[str] = []
    for match in _TOKEN.finditer(text):
        token = match.group(0)
        if token.startswith("{"):
            out.append(token.lower())
        elif _CJK.search(token):
            out.extend(_CJK_OR_ASCII.findall(token.lower()))
        else:
            # Split the *original* casing: lowercasing first destroys the camel
            # boundaries, which is how "MaaFwPhoneAI" ends up as one token.
            for part in token.split("_"):
                for piece in _CAMEL_BOUNDARY.sub(" ", part).split():
                    out.append(piece.lower())
    return out


def normalize(text: str) -> str:
    """The canonical string form used for comparison and storage."""
    return " ".join(tokenize(text))


def split_camel(name: str) -> str:
    """``Type_As_If_You_Are_Working`` -> ``Type As If You Are Working``."""
    spaced = re.sub(r"[_\-/.]+", " ", name)
    return _CAMEL_BOUNDARY.sub(" ", spaced)


def compact(name: str) -> str:
    return _NON_ALNUM.sub("", name.lower())


def aliases_for(name: str, key: str = "", extra: list[str] | None = None) -> list[str]:
    """Every normalized form a visitor might type for one entity.

    Generated mechanically at ingest, because GitHub naming is hostile to
    matching: ``Type_As_If_You_Are_Working``, ``Finance-Management-App``,
    ``i_love_you_web``. The compact forms (``financemanagementapp``) catch
    people who drop the separators.

    Aliases shorter than three characters after compaction are dropped: a
    two-letter alias matches half the corpus. CJK is exempt from that rule,
    because one character is already a word here -- 你 is "you", not half an
    abbreviation, and it is how "你的网站是什么" reaches the person slot.
    """
    candidates: list[str] = []

    short = key.split("/", 1)[-1] if key else name
    for base in (name, split_camel(name), short, split_camel(short)):
        candidates.append(base)
    if key:
        candidates.append(key)
        candidates.append(key.replace("/", " ").replace("-", " ").replace("_", " "))
    for base in (name, short):
        candidates.append(compact(base))
    candidates.extend(extra or [])

    out: list[str] = []
    for candidate in candidates:
        normalized = normalize(candidate)
        if not normalized:
            continue
        if len(compact(normalized)) < 3 and not _CJK.search(normalized):
            continue
        if normalized not in out:
            out.append(normalized)
    return out


def render(template: str, values: dict[str, str]) -> str:
    """Substitute ``{key}`` in a template, leaving unknown placeholders alone.

    Deliberately not ``str.format``: templates contain ``★`` and arbitrary
    punctuation, and a missing key should be visible rather than an exception
    thrown at a visitor.
    """

    def replace(match: re.Match[str]) -> str:
        whole = match.group(0)
        return values.get(whole, whole)

    return re.sub(r"\{[^}]*\}", replace, template)


def placeholders(text: str) -> list[str]:
    """The distinct ``{...}`` occurrences in a template, in order."""
    out: list[str] = []
    for match in re.finditer(r"\{[^}]*\}", text):
        if match.group(0) not in out:
            out.append(match.group(0))
    return out
