## csv_handler.py
import csv
from typing import List, Dict

class CsvHandler:
    def __init__(self, input_file: str, output_file: str):
        self.input_file: str = input_file
        self.output_file: str = output_file
        self.urls: List[str] = []
        self.titles: List[str] = []
        self.results: List[Dict] = []

    def read_urls(self) -> None:
        """Read URLs from the input CSV file."""
        with open(self.input_file, 'r', encoding='utf-8') as file:
            reader = csv.reader(file, delimiter=',', quotechar='"')
        
            for i, row in enumerate(reader):
                print(i)
                print(row)
                self.titles.append(row[1])
                if len(str(row[1])) < 1:
                    self.urls.append(row[4])
                else:
                    self.urls.append(row[5])

    def write_results(self) -> None:
        """Write the results to the output CSV file."""
        with open(self.output_file, 'w', newline='') as file:
            writer = csv.writer(file)
            writer.writerow(['URL', 'Summary', 'Image_URL'])
            for result in self.results:
                writer.writerow([result['url'], result['summary'], result['image_url']])
