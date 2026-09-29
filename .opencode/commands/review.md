---
description: Check the current state without changing anything — inbox, kappa, dead rows
agent: reviewer
---

Report the current state of this bot. **Change nothing** — no edits, no builds.

!`python -m personal_chatbots stats 2>&1 | head -40`

!`python -m personal_chatbots inbox 2>&1 | head -40`

Recent conversations, if any:

!`python -m personal_chatbots sessions --limit 5 2>&1 | head -40`

Then answer, briefly:

1. **What is the bot failing at right now?** Cluster the inbox by shape. Name the
   two or three clusters that would pay for themselves if fixed.
2. **Which live knowledge rows have never matched?** (`stats` lists them.) For
   each, say whether it needs archiving, a better pattern, or simply traffic.
3. **Is anything at risk?** A row whose pattern is greedy enough to steal another
   row's questions; a slot with no live entity behind it; a currency amount that
   would fail the build.
4. **What would you change first, and what would it cost?** One sentence each.

Do not propose a new table, a new level, or a new mechanism unless the evidence
in the inbox demands it. `docs/LEVELS.md` explains why: raising a level adds live
state that has to be maintained forever.
