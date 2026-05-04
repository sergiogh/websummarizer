import csv
import logging
import os
from datetime import datetime, timedelta
from io import StringIO
from typing import Dict, List

import requests


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class SpreadsheetConnector:
    def __init__(self):
        self.urls: List[str] = []
        self.titles: List[str] = []
        self.tags: List[str] = []
        self.published_at: List[str] = []
        self.summaries: List[str] = []
        self.results: List[Dict] = []

    def _parse_row_datetime(self, value: str):
        if not value:
            return None

        candidates = [
            "%B %d, %Y at %I:%M%p",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
        ]
        for fmt in candidates:
            try:
                return datetime.strptime(value.strip(), fmt)
            except Exception:
                continue
        return None

    def _row_value(self, row, index, default=""):
        return row[index] if len(row) > index else default

    def get_content(self, delta=7, start_date=None, end_date=None) -> None:
        try:
            spreadsheet_url = os.getenv("GOOGLE_SHEET")
            if not spreadsheet_url:
                raise ValueError("GOOGLE_SHEET environment variable is not set")

            logger.info(f"Fetching content from: {spreadsheet_url}")
            response = requests.get(spreadsheet_url)

            if response.status_code == 403:
                raise Exception(
                    "Access denied to Google Sheet. Please check if the sheet is public or if you have the correct permissions."
                )

            response.raise_for_status()
            csv_content = StringIO(response.text)
            reader = csv.reader(csv_content)
            all_rows = list(reader)

            if not all_rows:
                raise Exception("The spreadsheet is empty")

            date_column_index = 3
            if start_date is not None or end_date is not None:
                if start_date is None or end_date is None:
                    raise ValueError("Both start_date and end_date are required.")
                if isinstance(start_date, str):
                    start_date = datetime.strptime(start_date, "%Y-%m-%d")
                if isinstance(end_date, str):
                    end_date = datetime.strptime(end_date, "%Y-%m-%d")
            else:
                end_date = datetime.now()
                start_date = end_date - timedelta(days=delta)

            logger.info(f"Filtering rows between {start_date} and {end_date}")
            filtered_rows = []
            for row in all_rows[1:]:
                if len(row) <= date_column_index:
                    logger.warning(f"Skipping row due to insufficient columns: {row}")
                    continue

                row_dt = self._parse_row_datetime(row[date_column_index])
                if row_dt is None:
                    logger.warning(f"Skipping row due to invalid date: {row[date_column_index]}")
                    continue

                if start_date <= row_dt <= end_date:
                    filtered_rows.append(row)

            logger.info(f"Found {len(filtered_rows)} rows in the date range")
            for row in filtered_rows:
                self.titles.append(self._row_value(row, 0))
                self.urls.append(self._row_value(row, 1))
                self.tags.append(self._row_value(row, 2))
                self.published_at.append(self._row_value(row, 3))
                self.summaries.append(self._row_value(row, 4))

            if not self.urls:
                raise Exception("No stories found in the selected date range")

        except requests.exceptions.RequestException as e:
            logger.error(f"Error downloading content from spreadsheet: {e}")
            raise Exception(f"Failed to access Google Sheet: {str(e)}")
        except ValueError as e:
            logger.error(f"Value error: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            raise
