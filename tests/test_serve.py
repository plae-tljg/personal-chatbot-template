"""The serving path. Skipped when fastapi is not installed."""

from __future__ import annotations

import unittest

try:
    from fastapi.testclient import TestClient

    HAVE_FASTAPI = True
except ImportError:  # pragma: no cover
    HAVE_FASTAPI = False

from tests.support import FIXTURE_CACHE, ROOT, BuiltCase


@unittest.skipUnless(HAVE_FASTAPI, "fastapi not installed")
class ApiTest(BuiltCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        from personal_chatbots.serve import create_app

        cls.client = TestClient(create_app(cls.cfg))

    def test_config_endpoint(self):
        body = self.client.get("/api/config").json()
        self.assertEqual(body["level"], 1)
        self.assertIn("refuse", body["ladder"])

    def test_ask_endpoint_reports_how_it_answered(self):
        body = self.client.post("/api/ask", json={"question": "what is dsh-review about?"}).json()
        self.assertEqual(body["source"], "knowledge")
        self.assertEqual(body["tokens"], 0)
        self.assertTrue(body["citations"])

    def test_refusal_is_recorded(self):
        body = self.client.post("/api/ask", json={"question": "do you sell insurance?"}).json()
        self.assertEqual(body["source"], "refuse")
        self.assertEqual(body["text"], self.cfg.refuse_template)

    def test_empty_question_is_rejected(self):
        self.assertEqual(self.client.post("/api/ask", json={"question": "   "}).status_code, 400)

    def test_a_stale_database_returns_503_not_500(self):
        import sqlite3

        from personal_chatbots.config import Config
        from personal_chatbots.serve import create_app

        stale = self.tmp / "stale-api.db"
        sqlite3.connect(stale).close()  # an empty file: no tables at all
        client = TestClient(create_app(Config.load(ROOT, db_path=stale, cache_dir=FIXTURE_CACHE)))
        response = client.get("/api/sessions")
        self.assertEqual(response.status_code, 503)
        self.assertIn("pc build", response.json()["detail"])

    def test_widget_is_served(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Ask LKM", response.text)


if __name__ == "__main__":
    unittest.main()
