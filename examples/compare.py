#!/usr/bin/env python3
"""compare.py — measure the two bots instead of asserting which is better.

The claim this project makes is *not* "a bot without RAG is bad". `hardcoded_bot.py`
has no model in its fast path either, and that is the right call in both designs.
The claim is narrower and measurable:

    the hardcoded bot is not bad because of how it answers.
    It is bad because a change to it cannot be reviewed, tested, or measured —
    so the agent maintaining it has to be trusted rather than checked.

So this script does not compare answers. It compares the five properties that
decide whether an AI can maintain something safely, and it computes every number
from the files rather than hardcoding a figure that will rot.

    python examples/compare.py
"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import hardcoded_bot  # noqa: E402  (the foil, next to this file)
from personal_chatbots.build import build  # noqa: E402
from personal_chatbots.config import Config  # noqa: E402
from personal_chatbots.content import CURRENCY_LITERAL, load_test_cases  # noqa: E402
from personal_chatbots.engine import Runtime  # noqa: E402
from personal_chatbots.store import Store  # noqa: E402

FIXTURE_CACHE = ROOT / "tests" / "fixtures"
HARDCODED_SOURCE = Path(__file__).resolve().parent / "hardcoded_bot.py"
KNOWLEDGE = ROOT / "content" / "knowledge.yaml"
TESTS = ROOT / "content" / "tests.yaml"

UNKNOWN = [
    "do you ship to Portugal?",
    "is the apex laptop repairable at home?",
    "can i pay in instalments?",
]


BOLD = "\033[1m" if sys.stdout.isatty() else ""
RESET = "\033[0m" if sys.stdout.isatty() else ""


def rule(title: str) -> None:
    print(f"\n{BOLD}{title}{RESET}")


def measure_price_duplication() -> tuple[int, int]:
    """How many places must agree about one price, in each design.

    Both sides are counted from the files, not asserted, so neither number can
    rot into a claim the repo no longer honours.
    """
    # The foil counts its own duplication the same way `--demo` does.
    hardcoded = len(hardcoded_bot._duplication_places("volt")) + 1
    in_templates = len(CURRENCY_LITERAL.findall(KNOWLEDGE.read_text(encoding="utf-8")))
    return hardcoded, in_templates


def measure_assertions() -> tuple[int, int, int]:
    hardcoded = len(re.findall(r"^\s*assert\b", HARDCODED_SOURCE.read_text(encoding="utf-8"), re.M))
    unit = sum(
        len(re.findall(r"self\.assert\w+", path.read_text(encoding="utf-8")))
        for path in (ROOT / "tests").glob("test_*.py")
    )
    cases = load_test_cases(TESTS)
    return hardcoded, unit, len(cases)


def main() -> int:
    print("hardcoded_bot.py  vs  personal-chatbots")
    print("=" * 62)
    print("Not a comparison of answers. A comparison of what happens when")
    print("somebody has to change one.")

    # ---- 1 ---------------------------------------------------------------
    hardcoded_places, template_literals = measure_price_duplication()
    rule("1. One price changes")
    print(f"   hardcoded      the Volt price appears in {hardcoded_places} places in one .py")
    print(f"                  file, in two languages -> edit 1, leave {hardcoded_places - 1} stale,")
    print("                  no error raised, no test able to notice")
    print(f"   parameterised  currency amounts in content/knowledge.yaml: {template_literals}")
    print("                  the validator refuses one outright, so the duplication")
    print("                  cannot come back. One row on an entity; 0 knowledge edits.")

    # ---- 2 ---------------------------------------------------------------
    rule("2. A rule that steals another rule's questions")
    question = "do the earbuds work with my phone"
    stolen = hardcoded_bot.answer(question)
    print(f'   hardcoded      "{question}"')
    print(f'                  -> {stolen[:58]}...')
    print("                  silently wrong: the `phone` branch is checked first")
    print("   parameterised  a confusable negative is a test *kind* — a question that")
    print("                  must be REFUSED. It is seeded by a human, not by whoever")
    print("                  wrote the rule, so it cannot be fitted to the same evidence.")

    # ---- 3 ---------------------------------------------------------------
    rule("3. What an unanswerable question produces")
    print("   hardcoded      the fallback mirrors the question back in a friendly")
    print("                  frame. It never says 'I don't know':\n")
    for q in UNKNOWN:
        print(f"                    {q!r:44} -> {hardcoded_bot.fallback(q)!r}")
    print("\n                  The first and third are commitments invented from the")
    print("                  customer's own wording; the shop never made them. The")
    print("                  second is fluent and empty. All three got a reply, and")
    print("                  none of the three left a trace.")
    print("\n                  The damage is not the wrong answers. It is that the")
    print("                  failure signal is gone: no question is ever *admitted*")
    print("                  to be unanswerable, so nothing records what the rules")
    print("                  are missing, and the gap list can never shrink.")

    tmp = Path(tempfile.mkdtemp(prefix="pc-compare-"))
    cfg = Config.load(ROOT, db_path=tmp / "bot.db", cache_dir=FIXTURE_CACHE)
    build(cfg, offline=True)
    with Store(cfg.db_path) as store:
        runtime = Runtime.from_store(store, cfg)
        for q in UNKNOWN:
            runtime.ask_and_record(q, session_id="compare")
        inbox = store.unresolved_inbox(20)
    print(f"\n   parameterised  the same {len(UNKNOWN)} questions -> {len(inbox)} rows in the inbox,")
    print("                  each one a to-do with a count:")
    for row in inbox[:3]:
        print(f"                    x{row['same_shape_count']}  {row['content']}")

    # ---- 4 ---------------------------------------------------------------
    rule("4. Whether a change can be reviewed as data")
    print("   hardcoded      +1 FAQ = edit control flow. Its position in the elif chain")
    print("                  is part of its meaning, so the diff is a program change.")
    print("   parameterised  +1 FAQ = one list item in YAML. The diff is the sentence")
    print("                  a visitor will read, and CI runs the cases before a merge.")

    # ---- 5 ---------------------------------------------------------------
    hardcoded_asserts, unit_asserts, cases = measure_assertions()
    refusals = sum(1 for case in load_test_cases(TESTS) if case["expect"].get("refuses"))
    rule("5. Whether it is testable at all")
    print(f"   hardcoded      assertions in the project: {hardcoded_asserts}")
    print(f"   parameterised  {unit_asserts} assertions in unit tests, plus {cases} frozen")
    print(f"                  cases in content/tests.yaml ({refusals} of which assert a refusal)")

    print("\n" + "=" * 62)
    print("Both bots answer the common question without a model, which is the")
    print("right call. The difference is that one of them can be changed by an")
    print("agent and *checked*, and the other can only be changed by an agent")
    print("and *hoped for*.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
