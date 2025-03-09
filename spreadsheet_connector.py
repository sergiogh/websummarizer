import requests
from io import StringIO
from datetime import datetime, timedelta
from typing import List, Dict
import csv
import os


class SpreadsheetConnector:
    def __init__(self):
        self.urls: List[str] = []
        self.titles: List[str] = []
        self.summaries: List[str] = []
        self.results: List[Dict] = []

    def get_content(self, delta = 7) -> None:
        try:
            spreadsheet_url = os.getenv('GOOGLE_SHEET')
            response = requests.get(spreadsheet_url)
            csv_content = StringIO(response.text)
            reader = csv.reader(csv_content)
            all_rows = list(reader)
            date_column_index = 0

            end_date = datetime.now()
            start_date = end_date - timedelta(days=delta)

            filtered_rows = [
                row for row in all_rows
                if start_date <= datetime.strptime(row[date_column_index], '%B %d, %Y at %I:%M%p') <= end_date
            ]
            print(filtered_rows)
            for row in filtered_rows:
                self.titles.append(row[1])
                self.urls.append(row[5])
                self.summaries.append(row[2])


        except requests.exceptions.RequestException as e:
            print(f"Error downloading content from spreadsheet: {e}")
            self.content = None

   


