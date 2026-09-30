# Matching — how a question reaches a row

Three layers decide whether a question is answered. They run in this order, and
knowing which one a given phrasing needs is most of the maintenance work.

```
1. normalisation     case, underscores, hyphens, camelCase   — free, nobody maintains it
2. slot resolution   "dsh review" -> repo:plae-tljg/dsh-review
3. pattern match     the skeleton "{repo} 是什么" vs a row's patterns
                     a. exact
                     b. one edit (two on patterns of eight tokens or more)
```

Understanding is not one of the layers, and that is the point: a wrong answer is
worse than a refusal, so every layer refuses when it is not sure.

## Layer 2: aliases — same thing, different name

`curation.yaml` gives an entity extra names it is known by:

```yaml
- entity_type: repo
  key: plae-lkm/ThreeBodySystemAnimation
  add_aliases: ["three body simulation", "three body problem"]
```

Use an alias when the visitor means **the same entity** but calls it something
else — a nickname, a translation, an abbreviation, the name without its suffix.
This is finding, not interpreting: the alias maps one noun to one row, and
ambiguity between two entities still refuses.

## Layer 3a: patterns — same question, different words

`knowledge.yaml` patterns are the phrasings a row answers:

```yaml
- slug: repo.about
  patterns:
    - "what is {repo} about"
    - "tell me about {repo}"
```

Use a pattern when the wording changes but the **question** is the same. Patterns
are exact, which is what makes them predictable and cheap.

## Layer 3b: fuzzy — same question, misspelled

A question one edit from a pattern matches it. This exists because the exact
matcher was rejecting things any reader would call the same question:

| the visitor wrote | why it used to refuse |
|---|---|
| `what is dsh-review abotu?` | transposed letters — two edits to plain Levenshtein |
| `what's dsh-review about?` | contraction |
| `which project use Kotlin?` | grammar drift |
| `what is the dsh-review abuot` | a stray word *and* a typo |

It is **on by preference**, not by necessity — `runtime.fuzzy.enabled` in
`bot.json` turns it off when you want to test whether a phrasing is genuinely
covered rather than merely close to something covered.

### What it will not do

Four guards, each of which exists because a test caught it being necessary:

- **Slots must already have resolved.** Fuzzy excuses a typo in the framing; it
  can never invent, widen or choose an entity. A question whose subject is
  misspelled enough that layer 2 fails refuses outright — tolerance lives in
  layer 3, not layer 2.
- **A pattern must declare exactly the slots the question resolved.** Otherwise
  "what is the weather today" sits one edit from "what is {repo} about", because
  a skeleton with no placeholder is not far from one with a placeholder.
- **A placeholder only accepts the same entity type.** `{project}` and `{repo}`
  are one edit apart as text, and `curation.yaml` makes
  "Finance-Management" a project alias — so without this, a question about an
  ambiguous family was answered from a repository's phrasing. That was a real
  false positive.
- **Equally close is ambiguous.** Two patterns at the same distance refuse. A
  coin toss about which question was asked is how a bot lies.

### Thresholds

| pattern length | tolerated |
|---|---|
| fewer than 4 tokens | nothing — an edit is a third of the question |
| 4–7 tokens | one edit |
| 8+ tokens | two edits |

They live in `personal_chatbots/similarity.py`, not in `bot.json`, because they
are a judgement about this matcher rather than a per-deployment setting.
`runtime.fuzzy.enabled` is the switch that matters to a deployment.

The distance is Damerau-Levenshtein (adjacent transpositions), so a swap costs
one rather than two. That distinction is the difference between a matcher that
tolerates typos and one that only tolerates missing letters.

## Which layer for which request

| the visitor's problem | the fix | where |
|---|---|---|
| calls the repo by another name | alias | `curation.yaml` |
| asks a question no row covers ("how many stars") | a new pattern, or a new row | `knowledge.yaml` |
| misspells a word in a question a row already answers | nothing — fuzzy handles it | — |
| misspells the *entity name* | an alias, if it is a real alternative name | `curation.yaml` |
| asks in another language | patterns in that language, once the locale question is settled | `knowledge.yaml` |
| two things match | **refuse** — that is correct, and it is a test case | `tests.yaml` |

## Adding tolerance is adding risk

Everything here is a lever for answering questions nobody asked. When you widen
something, the case to add to `tests.yaml` is the one that must still refuse, not
the one that now works — the working one will be obvious in the diff, and the
dangerous one is what nobody thought to check.

`git log --grep=fuzzy` on this repository is the record of getting that wrong:
a threshold of two edits on five-token patterns answered an ambiguity test case,
and a boundary rule demanding exact last words rejected every typo it was written
to excuse. Both were found by tests, not by reading the code.
