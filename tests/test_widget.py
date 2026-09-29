"""Both chatrooms must render every shape of answer without throwing.

Written after the served chatroom threw `ReferenceError: r is not defined` while
building an answer element. The throw meant the reply never rendered and the "…"
placeholder stayed on screen — a crash that read as the bot being slow.

Nothing caught it: `node --check` sees valid syntax, no Python test touches the
page, and the browser was the only place it appeared. The harness in
`widget_harness.mjs` is the smallest thing that would have.
"""

from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

from tests.support import ROOT

HARNESS = ROOT / "tests" / "widget_harness.mjs"
PAGES = {
    "served chatroom": ROOT / "personal_chatbots" / "static" / "index.html",
    "static page": ROOT / "web" / "index.html",
}


@unittest.skipUnless(shutil.which("node"), "node not installed")
class WidgetTest(unittest.TestCase):
    def _render(self, page: Path) -> subprocess.CompletedProcess:
        tmp = page.parent / ".harness.mjs"
        try:
            return subprocess.run(
                ["node", str(HARNESS), str(page), str(tmp)],
                capture_output=True, text=True, timeout=60, cwd=ROOT,
            )
        finally:
            tmp.unlink(missing_ok=True)

    def test_every_answer_shape_renders(self):
        for label, page in PAGES.items():
            if not page.exists():
                self.skipTest(f"{page} missing")
            result = self._render(page)
            self.assertEqual(
                result.returncode, 0,
                f"{label} threw while rendering:\n{result.stdout}\n{result.stderr}",
            )
            self.assertIn("5/5 answer shapes render", result.stdout, label)

    def test_the_harness_would_catch_an_undefined_variable(self):
        """Guard against the harness silently passing on anything.

        A widget that renders an empty string with no badges is a widget that
        threw somewhere and was swallowed -- the exact failure this exists for.
        """
        result = self._render(PAGES["served chatroom"])
        self.assertIn("answer shapes render", result.stdout)
        self.assertNotIn("ReferenceError", result.stderr)


if __name__ == "__main__":
    unittest.main()
