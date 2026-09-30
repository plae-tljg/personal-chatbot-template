"""Typo tolerance for knowledge patterns -- and the guardrails that keep it honest.

The matcher in `engine.rung_knowledge` was exact set membership:

    if skeleton not in patterns: continue

which is cheap and completely predictable, and which also means a visitor who
writes "what is dsh-reveiw about?" gets "I don't have that in my tables" about a
repository the bot knows perfectly well. Six shapes fell through that way:
misspelled entity names, misspelled ordinary words, chit-chat wrapped around a
good question ("hi, can you tell me what is ..."), contractions ("what's"),
small grammar drift ("which project use Kotlin?"), and wrong pronoun
("who made your X").

This module adds the smallest thing that fixes those: edit distance between the
question's skeleton and each pattern, accepted only when it is close and
unambiguous.

**Why it is dangerous, and what stops it.** The rule this project is built on is
*a rung never guesses* (`resolve.py`). Edit distance is exactly the mechanism by
which a bot begins guessing: at a loose threshold, "what is the weather today"
is two edits from "what is the {repo} about" and the bot answers about a
repository nobody asked about. Four things prevent that here:

1. **The threshold is tight** (default distance 1, or 2 for patterns of five
   tokens or more) and is configuration, not a constant buried in code.
2. **The runner-up test.** If two patterns are equally close, the row does not
   match at all -- the same answer to ambiguity that entity resolution already
   gives, for the same reason.
3. **Same slots, always.** A pattern is only ever compared against a skeleton
   that already resolved every declared slot to a real entity, unambiguously.
   Fuzzy matching can excuse a typo in the framing; it can never invent, widen
   or choose an entity.
4. **It says so.** A fuzzy match is recorded (`Answer.fuzzy`, `messages
   .refs_json`) and shown in the widget, so a wrong turn is visible rather than
   silent. A guess you can see is a bug report; a guess you cannot see is a lie.

Matching on two levels -- tokens and characters -- is deliberate. Token distance
catches a misspelled word ("abotu" -> "about"), and character distance catches a
misspelled name inside a single token ("dsh-reveiw" -> "dsh review" is one token
versus two, which token distance scores badly and character distance scores
well). The lower of the two wins, so neither shape of typo needs its own rule.
"""

from __future__ import annotations

#: Distance 1 is "one insertion, deletion or substitution". Distance 2 is
#: allowed only past ``LONG_PATTERN_TOKENS``. It was 5 until a seeded case
#: objected: "what is Finance-Management about?" sat two edits from
#: "what is the {project} project" -- inserting "the" and swapping
#: "about" for "project" -- so a question about ambiguity got answered as a
#: curated grouping. Two edits is a large fraction of a short pattern; past
#: eight tokens it is a small one.
LONG_PATTERN_TOKENS = 8

#: Below this, a question is too short for edit distance to mean anything:
#: "who is X" and "what is X" are one substitution apart and ask different
#: things, and with three tokens a single edit is a third of the question. Four
#: is the shortest pattern in `knowledge.yaml` that tolerates anything, and it
#: is the shortest one where an edit leaves the shape recognisable.
MIN_TOKENS_FOR_FUZZ = 4


def _levenshtein(a: list, b: list) -> int:
    """Damerau-Levenshtein: insert, delete, substitute, or swap two neighbours.

    The transposition rule is the whole reason this is not plain
    Wagner-Fischer. "abotu" for "about" is one slip of the fingers, and to a
    reader it is one mistake -- but to plain Levenshtein it is two edits
    (delete, insert), so every threshold that called one typo acceptable
    rejected it. Counting a swap as one is what makes a typo-tolerant matcher
    actually tolerate typos rather than only missing letters.

    Restricted Damerau (the neighbouring-pair form): memory is O(len(b)), and
    it is exact for the single-typo cases this is used for.
    """
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    two_back: list[int] = []
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            cost = min(
                previous[j] + 1,               # delete from a
                current[j - 1] + 1,            # insert into a
                previous[j - 1] + (ca != cb),  # substitute
            )
            if (
                i > 1
                and j > 1
                and ca == b[j - 2]
                and a[i - 2] == cb
            ):
                cost = min(cost, two_back[j - 2] + 1)  # swap the pair
            current.append(cost)
        two_back, previous = previous, current
    return previous[-1]


def _pair_cost(left: str, right: str) -> int:
    """Substituting one word for another costs their character distance, capped.

    The cap matters: without it an unrelated word looks like an enormous edit
    and the alignment stops preferring to substitute at all. Capping at the
    longer word's length keeps "abotu"/"about" cheap and makes unrelated words
    cost what deleting one and inserting the other would.
    """
    return min(_levenshtein(list(left), list(right)), max(len(left), len(right)))


def _levenshtein_words(a: list[str], b: list[str]) -> int:
    """Word-level distance where substituting a word costs its character edits.

    Comparing two sentences as flat character strings -- the obvious
    implementation -- fails the case this module was written for. In
    ``"what is dsh review abotu"`` against ``"what is {repo} about"`` the typo
    is one transposition, but as characters it also shifts everything after it,
    scoring three and refusing. Aligning word to word first and measuring only
    inside each pair charges the one edit that was really made.
    """
    if not a:
        return sum(len(w) for w in b)
    if not b:
        return sum(len(w) for w in a)
    previous = [0]
    for word in b:
        previous.append(previous[-1] + len(word))
    for left in a:
        current = [previous[0] + len(left)]
        for j, right in enumerate(b, start=1):
            current.append(
                min(
                    previous[j] + len(left),                    # this word was added
                    current[j - 1] + len(right),                # this word is missing
                    previous[j - 1] + _pair_cost(left, right),  # a different word
                )
            )
        previous = current
    return previous[-1]


def distance(skeleton: str, pattern: str) -> int:
    """How far a question's skeleton is from a pattern. Lower is closer.

    Takes the better of two views, because there are two shapes of typo and each
    is easy in one view and hard in the other:

    * word-aligned character distance -- ``abotu`` for ``about``, where the words
      line up and the mistake is inside one of them;
    * plain token distance -- ``dsh-reveiw`` for ``dsh review``, where one word
      became two and the alignment itself is what changed.
    """
    sk_tokens, pat_tokens = skeleton.split(), pattern.split()
    return min(
        _levenshtein_words(sk_tokens, pat_tokens),
        _levenshtein(sk_tokens, pat_tokens),
    )


def allowed(pattern: str) -> int:
    """The largest distance this pattern may be matched at. 0 means exact only."""
    tokens = pattern.split()
    if len(tokens) < MIN_TOKENS_FOR_FUZZ:
        return 0
    return 2 if len(tokens) >= LONG_PATTERN_TOKENS else 1


def _slots(text: str) -> list[str]:
    return [w for w in text.split() if w.startswith("{")]


def _slot_names(text: str) -> set[str]:
    return {w.strip("{}") for w in _slots(text)}


def best_match(
    skeleton: str,
    patterns: list[str],
    *,
    exclude: list[str] | None = None,
    slot_types: dict[str, str] | None = None,
) -> tuple[str, int] | None:
    """The one pattern this skeleton may be matched to, or None.

    Returns ``(pattern, distance)``. None when nothing is close enough, when the
    closest candidate is identical to a pattern already rejected by ``exclude``
    (a row's own `exclude` list still has to win), or when two patterns are
    equally close -- a tie is ambiguity, and ambiguity refuses.

    Two guards keep tolerance from becoming guessing, and both were added
    because a test caught them:

    *A pattern must declare exactly the slots the skeleton resolved.* Without
    it, "what is the weather today" -- which resolves nothing, so its skeleton
    has no placeholder -- sits one edit from "what is the {repo} about", and two
    repositories' rows both claim it.

    *Those slots must have the same **types**.* `curation.yaml` gives the
    Finance-Management family a `project` alias, so "what is Finance-Management
    about?" resolves the slot as a project -- and "{project}" and "{repo}" are
    one edit apart as placeholder text. Borrowing a repository's pattern to
    answer a curated grouping answers a different question than the one asked,
    so a placeholder only satisfies a placeholder of the same entity type.
    """
    blocked = {p for p in (exclude or []) if p}
    wanted_types = slot_types or {}
    wanted = _slots(skeleton)
    wanted_names = _slot_names(skeleton)
    scored: list[tuple[int, str]] = []
    for pattern in patterns:
        if pattern in blocked:
            continue
        if _slots(pattern) != wanted:
            continue
        # A placeholder is satisfied only by the same *kind* of placeholder.
        if any(wanted_types.get(name, "") for name in _slot_names(pattern)) and (
            {wanted_types.get(name, "") for name in _slot_names(pattern)}
            != {wanted_types.get(name, "") for name in wanted_names}
        ):
            continue
        limit = allowed(pattern)
        if limit == 0:
            continue
        # The first and last tokens are the question's shape: what kind of
        # question it is ("what", "which", "who") and what it is aimed at.
        # Requiring them to match stops "what is X about" from being read as
        # "does X work offline", which shares most of its words.
        #
        # The last token is compared with one character of tolerance, not for
        # equality: "abotu" is a misspelling of the *boundary* word -- precisely
        # the typo this module exists to excuse -- and demanding an exact match
        # there rejected every word-level typo in testing. One character
        # tolerates a transposition or a doubled letter while still rejecting a
        # genuinely different word, which is the distinction that matters.
        sk_parts, pat_parts = skeleton.split(), pattern.split()
        if sk_parts[0] != pat_parts[0]:
            continue
        # Measured with `_pair_cost`, not plain Levenshtein: a transposition
        # ("abotu" for "about") is one mistake to a reader and two edits to
        # Levenshtein, so a `> 1` test here rejected every typo it was written
        # to allow. The word-level cost counts it as the single slip it is.
        if _pair_cost(sk_parts[-1], pat_parts[-1]) > 1:
            continue
        # Cheap rejection first: a length gap alone can exceed the limit.
        if abs(len(skeleton.split()) - len(pattern.split())) > limit:
            if abs(len(skeleton) - len(pattern)) > limit:
                continue
        d = distance(skeleton, pattern)
        if d <= limit:
            scored.append((d, pattern))

    if not scored:
        return None
    scored.sort()
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        # Two patterns equally close. Picking either would be a coin toss, and a
        # coin toss about which question was asked is how a bot lies.
        return None
    return scored[0][1], scored[0][0]
