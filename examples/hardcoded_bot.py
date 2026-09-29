#!/usr/bin/env python3
"""hardcoded_bot.py — the thing this project is a reaction to.

A small electronics-shop bot written the way such bots actually get written:
prices and FAQs live inside this one Python file, matching is a chain of
`if phrase in question`, and anything the rules miss goes to an agent fallback.

**This is not a strawman.** It is the reasonable version of the thing. It was
written by someone competent under a deadline, it handles the common questions,
and the `# keep prices here so they are easy to find` comment at the top is the
kind of decision a good engineer makes at 5pm. Nothing in this file is stupid.

That is the point. The problem is structural, not a matter of care:

1. **A price is data pretending to be prose.** It appears in the price answer,
   in the product blurb, and in the budget answer. Change one and you must find
   the other two, and nothing tells you if you missed one — the bot will simply
   contradict itself, confidently.

2. **Matching is order-dependent and nothing detects it.** `phone` is checked
   before `earbuds`, so "do the earbuds work with my phone" is answered with
   the phone blurb. The rules are individually fine; the interaction is wrong,
   and there is no test that could have caught it.

3. **Failure is invisible.** The fallback returns a sentence. It does not record
   the question, so nobody can ever learn what to add — the same three
   questions can fail for a year without anyone knowing.

4. **Every change is a code change.** Adding one FAQ means editing Python, which
   means a syntax risk and a diff that mixes data with control flow — so the
   agent maintaining it has to be trusted with the whole program, not with a
   row.

5. **Nothing is testable.** There is no assertion anywhere in this file, because
   there is nothing to assert *against*: the knowledge and the control flow are
   the same thing.

What this file does **not** get wrong: it has no RAG and no model in the fast
path, and that is a *correct* design choice — the same choice `personal-chatbots`
makes. A rule-based bot with an agent behind it is not the problem. The problem
is that the rules are unreviewable, untestable, and unmeasurable, so the agent
behind it has to be trusted rather than checked.

Run it:

    python examples/hardcoded_bot.py "how much is the apex laptop"
    python examples/hardcoded_bot.py                # small REPL
    python examples/hardcoded_bot.py --demo         # show it contradict itself
"""

from __future__ import annotations

import sys

# ---------------------------------------------------------------------------
# Prices. Keep prices here so they are easy to find.
# ---------------------------------------------------------------------------

APEX_LAPTOP = 1299
NIMBUS_PHONE = 699
VOLT_EARBUDS = 149
COBALT_MONITOR = 329

# ---------------------------------------------------------------------------
# The rules. Newest ones go at the top because they keep getting shadowed.
# ---------------------------------------------------------------------------


def answer(question: str) -> str:
    q = question.lower().strip()

    # --- shipping, returns, warranty ---
    if "shipping" in q or "deliver" in q:
        return "Free shipping over $50. Standard delivery is 3-5 working days."
    if "return" in q or "refund" in q:
        return "Returns accepted within 30 days, unused and in the original box."
    if "warranty" in q or "guarantee" in q:
        return "Every device carries a 24-month warranty."
    if "pay" in q or "payment" in q or "card" in q:
        return "We take cards, PayPal, and bank transfer."

    # --- prices ---
    if "apex" in q:
        return (
            "The Apex Laptop 14 is $1299. A 14-inch ultrabook with 16GB RAM and a "
            "1TB SSD. Free shipping, 24-month warranty."
        )
    if "nimbus" in q:
        return (
            "The Nimbus Phone 5 is $699. A 6.1-inch OLED phone with a 50MP camera. "
            "Free shipping, 24-month warranty."
        )
    if "volt" in q:
        return (
            "The Volt Earbuds are $149. Active noise cancelling, 30-hour battery. "
            "Free shipping, 24-month warranty."
        )
    if "cobalt" in q:
        return (
            "The Cobalt Monitor 27 is $329. 27-inch 1440p at 165Hz. "
            "Free shipping, 24-month warranty."
        )

    # --- budget lists: the same prices again, in prose ---
    if "cheap" in q or "budget" in q or "under" in q:
        return (
            "Under $200 we have the Volt Earbuds at $149. "
            "Under $400 the Cobalt Monitor 27 at $329. "
            "Our cheapest laptop is the Apex Laptop 14 at $1299."
        )
    if "most expensive" in q or "premium" in q:
        return "The Apex Laptop 14 at $1299 is our top model. The Nimbus Phone 5 is $699."
    if "how much" in q or "price" in q or "cost" in q:
        return (
            "Prices: Apex Laptop 14 $1299, Nimbus Phone 5 $699, "
            "Volt Earbuds $149, Cobalt Monitor 27 $329."
        )

    # --- product questions ---
    # Note the order. "phone" is checked before "earbuds", so a question that
    # mentions both is answered as if it were only about the phone.
    if "phone" in q or "nimbus" in q:
        return "The Nimbus Phone 5 is a 6.1-inch OLED phone with a 50MP camera."
    if "laptop" in q or "apex" in q:
        return "The Apex Laptop 14 is a 14-inch ultrabook with 16GB RAM."
    if "earbuds" in q or "volt" in q:
        return "The Volt Earbuds have active noise cancelling and 30-hour battery."
    if "monitor" in q or "cobalt" in q:
        return "The Cobalt Monitor 27 is 27-inch 1440p at 165Hz."

    # --- the shop itself ---
    if "open" in q or "hours" in q:
        return "Our online shop is always open; support replies 9-6 on weekdays."
    if "contact" in q or "email" in q or "support" in q:
        return "Email support@example-shop.invalid and we will get back to you."
    if "who are you" in q or "what can you do" in q:
        return "I answer questions about our laptops, phones, earbuds and monitors."

    return fallback(question)


# ---------------------------------------------------------------------------
# The agent fallback. This is the part that made the original project
# feel maintainable: anything the rules miss, a model handles.
# ---------------------------------------------------------------------------


def fallback(question: str) -> str:
    # In the original this shells out to an agent:
    #     return subprocess.run(["hermes", "-z", question], ...).stdout
    # The answer is returned and then forgotten -- nothing records that the
    # rules missed, so this list never shortens.
    return "Sorry, I do not know about that. Please contact support."


# ---------------------------------------------------------------------------
# demo: how the price duplication fails
# ---------------------------------------------------------------------------


def demo() -> None:
    """Raise a price in one place and show the bot contradicting itself."""
    knowledge, _, _ = _SOURCE.partition("def demo(")
    places = [line.strip() for line in knowledge.splitlines() if str(VOLT_EARBUDS) in line]

    print(f"The Volt Earbuds price ({VOLT_EARBUDS}) is written out in "
          f"{len(places)} places in this file:\n")
    for line in places:
        shown = line if len(line) <= 72 else line[:69] + "..."
        print(f"    {shown}")

    print("\nNow say the earbuds go from $149 to $129, and someone updates the")
    print("obvious answer -- the product sentence a customer is most likely to hit:\n")
    print("    changed : The Volt Earbuds are $129. Active noise cancelling, ...")
    print(f"\nThe other {len(places) - 1} places still say 149. So the bot now answers")
    print("both of these, and both sound equally confident:\n")
    print("    \"how much are the volt earbuds\" -> $129  (correct)")
    print("    \"what is cheap\"                -> $149  (wrong, and nobody knows)")
    print("\nNothing raises. No test fails, because no test could: the price is not")
    print("a value the program can compare -- it is a substring inside three")
    print("separate sentences written by three different people on three days.")
    print("\nIn personal-chatbots the same change is one row:")
    print("    entities.attrs_json = {\"price_cents\": 12900}")
    print("and the sentence says {price}. `pc build && pc test` then fails loudly if")
    print("a currency amount ever appears in content/knowledge.yaml, so the")
    print("duplication cannot come back.")


_SOURCE = ""


def main(argv: list[str]) -> int:
    global _SOURCE
    _SOURCE = open(__file__, encoding="utf-8").read()

    if argv and argv[0] == "--demo":
        demo()
        return 0

    if argv:
        print(answer(" ".join(argv)))
        return 0

    print("hardcoded-shop-bot. ctrl-d to quit.")
    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if line:
            print(answer(line))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
