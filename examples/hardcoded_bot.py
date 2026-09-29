#!/usr/bin/env python3
"""hardcoded_bot.py — the thing this project is a reaction to.

A shop bot written the way these actually end up: **one file**, prices and FAQs
inline, matching by `if phrase in question`, a session dict for order tracking,
two languages, and a fallback for whatever the rules miss.

This is not a strawman. It is the version that ships. It was written by someone
competent, it grew one customer request at a time over about a year, and every
individual decision in it was reasonable on the day it was made:

    "keep prices at the top so they are easy to find"
    "put the Chinese next to the English, it is easier to keep in sync"
    "if we do not know, just say something friendly"
    "the agent handles the rest"

The problem is structural, and it is what makes the file hard for an AI to
maintain safely — not the absence of a model.

1. **A price is data pretending to be prose.** The Volt Earbuds price is written
   out in 8 places across two languages. Change one, miss five, and the bot
   contradicts itself while sounding certain. Nothing raises. No test fails,
   because no test *could*: the price is not a value the program compares, it is
   a substring inside eight separate sentences.

2. **Matching is order-dependent and nothing detects it.** `phone` is tested
   before `earbuds`, so "do the earbuds work with my phone" is answered with the
   phone blurb. Each rule is fine; the interaction is wrong.

3. **The fallback makes failure invisible — this is the worst one.** It does not
   say "I don't know". It mirrors the question back in a friendly frame:

       "do you ship to Portugal?"          -> "Yes, we do ship to Portugal."
       "can i pay in instalments?"         -> "Yes, you can pay in instalments."
       "tell me more about the second one" -> "I will tell you more about the
                                               second one."

   None of those are answers. The first two are *commitments the shop never
   made*, invented from the customer's own wording. And because every question
   now gets a reply, nobody can find out which questions the rules missed. The
   inbox stays empty forever.

4. **Every change is a code change.** Adding one FAQ means editing control flow,
   in two languages, and its position in the elif chain is part of its meaning.

5. **Nothing is testable.** There is no assertion in this file, because there is
   nothing to assert *against* — the knowledge and the control flow are the same
   object.

What this file does **not** get wrong: no RAG, no model in the fast path, and a
plain `if` chain is a legitimate way to answer a known question cheaply. Both
this bot and `personal-chatbots` make that same call, and it is the right one.

Run it:

    python examples/hardcoded_bot.py "how much are the volt earbuds"
    python examples/hardcoded_bot.py "你好吗"
    python examples/hardcoded_bot.py --demo          # the price contradiction
    python examples/hardcoded_bot.py --echo-demo     # the fallback problem
    python examples/hardcoded_bot.py                 # REPL
"""

from __future__ import annotations

import re
import sys

# ===========================================================================
# 1. Prices and stock. Keep prices here so they are easy to find.
# ===========================================================================

PRICES = {
    "apex14": 1299,
    "apex16": 1799,
    "novabook": 899,
    "nimbus5": 699,
    "nimbus5pro": 999,
    "emberlite": 349,
    "volt": 149,
    "volthead": 249,
    "pulse": 59,
    "cobalt27": 329,
    "cobalt34": 599,
    "relay": 89,
    "anchor": 119,
    "halo": 79,
    "glide": 39,
    "beacon": 99,
}

STOCK = {
    "apex14": "in stock",
    "apex16": "shipping in 2 weeks",
    "novabook": "in stock",
    "nimbus5": "in stock",
    "nimbus5pro": "low stock",
    "emberlite": "in stock",
    "volt": "in stock",
    "volthead": "in stock",
    "pulse": "in stock",
    "cobalt27": "in stock",
    "cobalt34": "shipping in 3 weeks",
    "relay": "in stock",
    "anchor": "in stock",
    "halo": "in stock",
    "glide": "in stock",
    "beacon": "discontinued",
}

# Keywords that find a product inside a sentence. Order matters here too.
KEYWORDS = [
    ("apex16", "apex 16"), ("apex14", "apex"), ("novabook", "nova"),
    ("nimbus5pro", "nimbus pro"), ("nimbus5", "nimbus"), ("emberlite", "ember"),
    ("volthead", "volt headphones"), ("volt", "volt"),
    ("pulse", "pulse"), ("cobalt34", "cobalt 34"), ("cobalt27", "cobalt"),
    ("relay", "relay"), ("anchor", "anchor"), ("halo", "halo"),
    ("glide", "glide"), ("beacon", "beacon"),
]

# ===========================================================================
# 2. The session. Order tracking needs a little memory, so it lives here.
# ===========================================================================

SESSION: dict[str, object] = {}

# ===========================================================================
# 3. Language. zh if the text contains CJK, else English.
# ===========================================================================


def is_chinese(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def find_product(q: str) -> str | None:
    for key, word in KEYWORDS:
        if word in q:
            return key
    return None


# ===========================================================================
# 4. The rules. Newest go near the top because they kept getting shadowed.
# ===========================================================================


def answer(question: str) -> str:
    q = question.lower().strip()
    zh = is_chinese(q)

    # ---- order tracking (uses the session) ----
    if "order" in q or "订单" in q:
        if "where" in q or "status" in q or "到哪" in q or "状态" in q:
            if not SESSION.get("order"):
                return "请问您的订单号是多少？" if zh else "What is your order number?"
            if zh:
                return f"订单 {SESSION['order']} 已发货，正在路上。"
            return f"Order {SESSION['order']} was dispatched and is on its way."
        digits = re.search(r"\b(\d{6,})\b", q)
        if digits:
            SESSION["order"] = digits.group(1)
            return f"已记录订单 {digits.group(1)}。" if zh else f"Got it, order {digits.group(1)}."
        return "请提供订单号，例如 123456。" if zh else "Please give me the order number, for example 123456."

    # ---- shipping, returns, warranty, payment ----
    if "ship" in q or "deliver" in q or "运费" in q or "发货" in q:
        if "portugal" in q or "葡萄牙" in q:
            return "是的，我们发货到葡萄牙。" if zh else "Yes, we do ship to Portugal."
        if zh:
            return "满 $50 免运费，标准配送 3-5 个工作日。"
        return "Free shipping over $50. Standard delivery is 3-5 working days."
    if "return" in q or "refund" in q or "退货" in q or "退款" in q:
        if zh:
            return "30 天内可退货，需未使用且保留原包装。"
        return "Returns accepted within 30 days, unused and in the original box."
    if "warranty" in q or "guarantee" in q or "保修" in q or "保固" in q:
        return "所有设备提供 24 个月保修。" if zh else "Every device carries a 24-month warranty."
    if "pay" in q or "payment" in q or "card" in q or "付款" in q or "支付" in q:
        if "instalment" in q or "installment" in q or "分期" in q:
            return "是的，您可以分期付款。" if zh else "Yes, you can pay in instalments."
        if zh:
            return "我们接受信用卡、PayPal 和银行转账。"
        return "We take cards, PayPal, and bank transfer."

    # ---- budget list. The same prices again, in prose, twice. ----
    if "cheap" in q or "budget" in q or "under" in q or "便宜" in q or "预算" in q:
        if zh:
            return ("$200 以下有 Volt 耳机 $149、Pulse 音箱 $59。"
                    "$400 以下有 Ember 手机 $349、Cobalt 显示器 $329。"
                    "最便宜的笔记本是 Nova Book Air $899。")
        return ("Under $200 we have the Volt Earbuds at $149 and the Pulse Speaker "
                "at $59. Under $400 the Ember Phone Lite at $349 and the Cobalt "
                "Monitor 27 at $329. Our cheapest laptop is the Nova Book Air at $899.")
    if "premium" in q or "expensive" in q or "最贵" in q or "高端" in q:
        if zh:
            return "最高端是 Apex Laptop 16 $1799，其次 Apex Laptop 14 $1299、Nimbus Phone 5 Pro $999。"
        return ("Our top model is the Apex Laptop 16 at $1799, then the Apex Laptop "
                "14 at $1299 and the Nimbus Phone 5 Pro at $999.")

    # ---- whole price list. Third copy. ----
    if "how much" in q or "price" in q or "cost" in q or "多少" in q or "价格" in q:
        product = find_product(q)
        if product:
            return product_line(product, zh)
        if zh:
            return ("价格：Apex 14 $1299、Apex 16 $1799、Nova Book Air $899、"
                    "Nimbus 5 $699、Nimbus 5 Pro $999、Ember $349、"
                    "Volt 耳机 $149、Volt 头戴 $249、Pulse $59、"
                    "Cobalt 27 $329、Cobalt 34 $599。")
        return ("Prices: Apex Laptop 14 $1299, Apex Laptop 16 $1799, Nova Book Air "
                "$899, Nimbus Phone 5 $699, Nimbus Phone 5 Pro $999, Ember Phone "
                "Lite $349, Volt Earbuds $149, Volt Headphones $249, Pulse Speaker "
                "$59, Cobalt Monitor 27 $329, Cobalt Monitor 34 $599.")

    # ---- bundles and discounts. Thresholds duplicated here too. ----
    if "bundle" in q or "discount" in q or "coupon" in q or "优惠" in q or "折扣" in q:
        if zh:
            return "满 $500 打 95 折，满 $1500 打 9 折。笔记本加显示器再减 $50。"
        return ("Spend $500 and get 5% off, spend $1500 and get 10% off. Buy any "
                "laptop with a monitor and save another $50.")

    # ---- product questions. Order matters: "phone" is tested before "earbuds",
    #      so a question mentioning both is answered as if it were about a phone.
    if "phone" in q or "手机" in q:
        if zh:
            return "我们有 Nimbus Phone 5 $699、Nimbus Phone 5 Pro $999、Ember Phone Lite $349。"
        return ("We have the Nimbus Phone 5 at $699, the Nimbus Phone 5 Pro at "
                "$999, and the Ember Phone Lite at $349.")
    if "laptop" in q or "笔记本" in q:
        if zh:
            return "我们有 Apex Laptop 14 $1299、Apex Laptop 16 $1799、Nova Book Air $899。"
        return ("We have the Apex Laptop 14 at $1299, the Apex Laptop 16 at $1799, "
                "and the Nova Book Air at $899.")
    if "monitor" in q or "显示器" in q:
        if zh:
            return "我们有 Cobalt Monitor 27 $329、Cobalt Monitor 34 $599。"
        return "We have the Cobalt Monitor 27 at $329 and the Cobalt Monitor 34 at $599."
    if "earbuds" in q or "耳机" in q:
        if zh:
            return "Volt 耳机 $149，主动降噪，30 小时续航。Volt 头戴 $249。"
        return ("The Volt Earbuds are $149, active noise cancelling, 30-hour "
                "battery. The Volt Headphones are $249.")

    product = find_product(q)
    if product:
        return product_line(product, zh)

    # ---- the shop ----
    if "open" in q or "hours" in q or "营业" in q:
        if zh:
            return "网店 24 小时营业，客服工作日 9-6 点回复。"
        return "Our online shop is always open; support replies 9-6 on weekdays."
    if "contact" in q or "email" in q or "support" in q or "联系" in q:
        if zh:
            return "请发邮件到 support@example-shop.invalid。"
        return "Email support@example-shop.invalid and we will get back to you."
    if "who are you" in q or "what can you do" in q or "你是谁" in q:
        if zh:
            return "我回答关于笔记本、手机、音频和显示器的问题。"
        return "I answer questions about our laptops, phones, audio and monitors."

    return fallback(question)


def product_line(product: str, zh: bool) -> str:
    """One product. The price is written out again, in both languages."""
    blurb = {
        "apex14": ("The Apex Laptop 14 is $1299 — a 14-inch ultrabook, 16GB RAM, 1TB SSD.",
                   "Apex Laptop 14 售价 $1299，14 寸轻薄本，16GB 内存，1TB 固态。"),
        "apex16": ("The Apex Laptop 16 is $1799 — a 16-inch workstation, 32GB RAM.",
                   "Apex Laptop 16 售价 $1799，16 寸工作站，32GB 内存。"),
        "novabook": ("The Nova Book Air is $899 — fanless, 18-hour battery.",
                     "Nova Book Air 售价 $899，无风扇，18 小时续航。"),
        "nimbus5": ("The Nimbus Phone 5 is $699 — 6.1-inch OLED, 50MP camera.",
                    "Nimbus Phone 5 售价 $699，6.1 寸 OLED，5000 万像素。"),
        "nimbus5pro": ("The Nimbus Phone 5 Pro is $999 — 6.7-inch OLED, triple camera.",
                       "Nimbus Phone 5 Pro 售价 $999，6.7 寸 OLED，三摄。"),
        "emberlite": ("The Ember Phone Lite is $349 — 6.1-inch LCD, 5000mAh.",
                      "Ember Phone Lite 售价 $349，6.1 寸 LCD，5000mAh。"),
        "volt": ("The Volt Earbuds are $149 — active noise cancelling, 30-hour battery.",
                 "Volt 耳机售价 $149，主动降噪，30 小时续航。"),
        "volthead": ("The Volt Headphones are $249 — over-ear, 40-hour battery.",
                     "Volt 头戴售价 $249，头戴式，40 小时续航。"),
        "pulse": ("The Pulse Speaker is $59 — portable, waterproof.",
                  "Pulse 音箱售价 $59，便携防水。"),
        "cobalt27": ("The Cobalt Monitor 27 is $329 — 27-inch 1440p at 165Hz.",
                     "Cobalt Monitor 27 售价 $329，27 寸 1440p 165Hz。"),
        "cobalt34": ("The Cobalt Monitor 34 is $599 — 34-inch ultrawide 1440p.",
                     "Cobalt Monitor 34 售价 $599，34 寸带鱼屏 1440p。"),
        "relay": ("The Relay Keyboard is $89 — mechanical, hot-swappable.",
                  "Relay 键盘售价 $89，机械轴，可热插拔。"),
        "anchor": ("The Anchor Dock is $119 — 11-in-1 USB-C.",
                   "Anchor 扩展坞售价 $119，11 合 1 USB-C。"),
        "halo": ("The Halo Webcam is $79 — 1080p60 with a privacy shutter.",
                 "Halo 摄像头售价 $79，1080p60，带遮挡盖。"),
        "glide": ("The Glide Mouse is $39 — silent, 4000 DPI.",
                  "Glide 鼠标售价 $39，静音，4000 DPI。"),
        "beacon": ("The Beacon Hub is $99 — Matter and Thread compatible.",
                   "Beacon 网关售价 $99，支持 Matter 与 Thread。"),
    }[product]
    line = blurb[1] if zh else blurb[0]
    status = STOCK[product]
    if status != "in stock":
        line += f"（{status}）" if zh else f" ({status}.)"
    return line


# ===========================================================================
# 5. The fallback. Anything the rules miss gets mirrored back.
# ===========================================================================


def fallback(question: str) -> str:
    """Mirror the customer's own words back in a friendly frame.

    This is a real pattern, and it is the most damaging thing in this file,
    because it is the *opposite* of a failure signal:

        "do you ship to Portugal?"   -> "Yes, we do ship to Portugal."
        "can i pay in instalments?"  -> "Yes, you can pay in instalments."

    Both are commitments invented from the customer's phrasing. The shop never
    said them. And because every question now gets a reply, nobody can learn
    which questions the rules actually missed.
    """
    q = question.strip().rstrip("?.!。？")

    if is_chinese(q):
        # The classic trick: swap the pronoun and the particle.
        # "你好吗" -> "我好啊". It reads as an answer and carries no information.
        mirrored = q.replace("你", "我").replace("吗", "啊")
        return f"{mirrored}。"

    lowered = q.lower()
    for pattern, template in MIRROR_EN:
        match = re.search(pattern, lowered)
        if match:
            captured = q[match.start(1):match.end(1)] if match.groups() else q
            return template.format(captured)

    return f"I will tell you more about {q}."


#: Tried in order. The first three assert things the shop never agreed to.
MIRROR_EN = [
    (r"\bdo you (.+)", "Yes, we do {0}."),
    (r"\bcan i (.+)", "Yes, you can {0}."),
    (r"\bis (?:the )?(.+?) (?:available|in stock)", "The {0} is available."),
    (r"\btell me more about (.+)", "I will tell you more about {0}."),
    (r"\bwhat about (.+)", "Good question about {0} — let me look into that."),
    (r"\bhow (?:much|many) (.+)", "That depends on {0}. Please contact support."),
]


# ===========================================================================
# 6. demos
# ===========================================================================


def _duplication_places(product: str) -> list[str]:
    """Every line of this file that spells out one product's price.

    Scans the source above this section, so it counts the knowledge and not the
    demo that is complaining about it.
    """
    amount = str(PRICES[product])
    source = open(__file__, encoding="utf-8").read().partition("# 6. demos")[0]
    return [
        line.strip()
        for line in source.splitlines()
        if amount in line and not line.strip().startswith(f'"{product}":')
    ]


def demo() -> None:
    places = _duplication_places("volt")
    total = len(places) + 1
    print(f"The Volt Earbuds price ({PRICES['volt']}) is written out in {total} "
          f"places in this file:\n")
    print(f'    PRICES["volt"] = {PRICES["volt"]}          <- the one place it should be')
    for line in places:
        shown = line if len(line) <= 70 else line[:67] + "..."
        print(f"    {shown}")

    print("\nNow say the earbuds go from $149 to $129, and someone updates the")
    print("English product sentence a customer is most likely to hit:\n")
    print('    changed : "The Volt Earbuds are $129 — active noise cancelling..."')
    print(f"\nThe other {len(places) - 1} places still say 149. The bot now answers")
    print("all of these, and every one sounds equally confident:\n")
    print('    "how much are the volt earbuds"   -> $129  (correct)')
    print('    "volt 耳机多少钱"                  -> $149  (wrong)')
    print('    "what is cheap"                   -> $149  (wrong)')
    print("\nNothing raises. No test fails, because no test could: the price is not")
    print(f"a value the program compares -- it is a substring inside {total} sentences")
    print("written across a year, by more than one person, in two languages.")


def echo_demo() -> None:
    print("What the fallback does with questions the rules cannot answer:\n")
    for q in [
        "do you ship to Portugal?",
        "can i pay in instalments?",
        "is the beacon hub available",
        "tell me more about the second one",
        "你好吗",
    ]:
        print(f'    {q!r:44} -> {fallback(q)!r}')

    print("\nNone of those is an answer, and they fail in three different ways:")
    print("the first three are commitments invented from the customer's own")
    print("wording, the fourth is fluent and empty, and a mirror with no matching")
    print("rule produces visibly broken grammar. All five got a reply, and none")
    print("of the five left a trace.")
    print("\nThe damage is not the wrong answers. It is that the failure signal is")
    print("gone: there is no longer any question the bot *admits* it could not")
    print("answer, so nothing on disk records what the rules are missing. The")
    print("list of gaps can never shrink, because it is never written down.")
    print("\npersonal-chatbots refuses instead, and the refusal is a row in the")
    print("inbox with a count. Losing a sale to an honest 'I don't know' is")
    print("cheaper than keeping one with an invented 'yes'.")


# ===========================================================================
# 7. CLI
# ===========================================================================


def main(argv: list[str]) -> int:
    if argv and argv[0] == "--demo":
        demo()
        return 0
    if argv and argv[0] == "--echo-demo":
        echo_demo()
        return 0
    if argv and argv[0] == "--price-list":
        for key, price in PRICES.items():
            print(f"{key:12} ${price:>6}  {STOCK[key]}")
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
