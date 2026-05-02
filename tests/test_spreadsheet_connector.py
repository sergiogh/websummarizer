import os
import unittest
from unittest.mock import patch

from spreadsheet_connector import SpreadsheetConnector


class FakeResponse:
    status_code = 200
    text = (
        "Title,URL,Tag,Date,Summary\n"
        "IonQ story,https://example.com/ionq,industry,2026-05-01,Selected summary\n"
        "Old story,https://example.com/old,other,2026-04-01,Old summary\n"
    )

    def raise_for_status(self):
        return None


class SpreadsheetConnectorTests(unittest.TestCase):
    @patch.dict(os.environ, {"GOOGLE_SHEET": "https://example.com/sheet.csv"})
    @patch("spreadsheet_connector.requests.get", return_value=FakeResponse())
    def test_get_content_accepts_date_range(self, _mock_get):
        connector = SpreadsheetConnector()

        connector.get_content(start_date="2026-05-01", end_date="2026-05-02")

        self.assertEqual(connector.titles, ["IonQ story"])
        self.assertEqual(connector.urls, ["https://example.com/ionq"])
        self.assertEqual(connector.tags, ["industry"])
        self.assertEqual(connector.published_at, ["2026-05-01"])
        self.assertEqual(connector.summaries, ["Selected summary"])


if __name__ == "__main__":
    unittest.main()
