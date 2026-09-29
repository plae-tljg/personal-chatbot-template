"""The test runner: ``content/tests.yaml`` against a built database.

This is the ``Runner`` seam. Its v1 implementation runs assertions in-process
against the live rows; a hosted CI or a shadow-evaluation runner would be a
different implementation of the same function.

The baseline for "regression" is git: green on ``main``, red on the branch. That
is the whole regression-protection mechanism, and it needs no tables.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any

from .config import Config
from .engine import Runtime
from .store import Store


@dataclass
class Outcome:
    slug: str
    question: str
    passed: bool
    notes: list[str] = field(default_factory=list)
    got: dict[str, Any] = field(default_factory=dict)


def _check(expect: dict[str, Any], answer: Any, outcome: Outcome) -> None:
    text_lower = answer.text.lower()
    got = {
        "source": answer.source,
        "slots": {k: v.key for k, v in answer.slots.items()},
        "citations": [c.key for c in answer.citations],
        "text": answer.text,
    }
    outcome.got = got

    if "refuses" in expect:
        if answer.refused != bool(expect["refuses"]):
            outcome.notes.append(
                f"expected refuses={bool(expect['refuses'])}, got {answer.source!r}"
            )

    if "source" in expect and answer.source != expect["source"]:
        outcome.notes.append(f"expected source={expect['source']!r}, got {answer.source!r}")

    for slot, key in (expect.get("resolves") or {}).items():
        actual = got["slots"].get(slot)
        if actual != key:
            outcome.notes.append(f"expected {slot} -> {key!r}, got {actual!r}")

    for needle in expect.get("answer_contains") or []:
        if str(needle).lower() not in text_lower:
            outcome.notes.append(f"answer missing {needle!r}")

    for needle in expect.get("answer_excludes") or []:
        if str(needle).lower() in text_lower:
            outcome.notes.append(f"answer unexpectedly contains {needle!r}")

    for needle in expect.get("cites") or []:
        if not any(c.matches(str(needle)) for c in answer.citations):
            outcome.notes.append(f"no citation matching {needle!r}")


def structural_only(cfg: Config) -> Config:
    """The config with the model rung removed.

    The frozen cases assert what the **structure** does. A model rung is not part
    of that contract, and leaving it in has two failure modes, both of which
    happened the moment the fallback was enabled in this repository:

    * the suite needs a network call and a valid key to be green, so CI and every
      offline run become flaky;
    * worse, a fluent model answer *hides* a broken arrangement row -- a
      `refuses: true` case passes for the wrong reason and nobody notices the
      structure regressed.

    So the suite runs what the tables alone can do, on purpose and visibly.
    """
    ladder = [rung for rung in cfg.ladder if rung != "fallback"]
    if ladder == cfg.ladder:
        return cfg
    runtime_data = {**cfg.data.get("runtime", {}), "ladder": ladder}
    return dataclasses.replace(cfg, data={**cfg.data, "runtime": runtime_data})


def run_tests(cfg: Config, tests: list[dict[str, Any]], store: Store) -> list[Outcome]:
    runtime = Runtime.from_store(store, structural_only(cfg))
    outcomes: list[Outcome] = []
    for case in tests:
        if case.get("status", "active") != "active":
            continue
        answer = runtime.ask(case["question"])  # deliberately not recorded
        outcome = Outcome(slug=case["slug"], question=case["question"], passed=True)
        _check(case["expect"], answer, outcome)
        outcome.passed = not outcome.notes
        outcomes.append(outcome)
    return outcomes
