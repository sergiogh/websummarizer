from dotenv import load_dotenv

from spreadsheet_connector import SpreadsheetConnector
from url_processor import UrlProcessor
from summary_generator import SummaryGenerator
from image_extractor import ImageExtractor
from prompt_loader import get_prompt


def main() -> None:

    load_dotenv()
    
    spreadsheet_handler = SpreadsheetConnector()
    spreadsheet_handler.get_content(365)
    
    total_content = ""
    results = []
    
    print("Articles: ")
    print(len(spreadsheet_handler.urls))
    for i, url in enumerate(spreadsheet_handler.urls):

        total_content += spreadsheet_handler.titles[i]+" "+spreadsheet_handler.summaries[i]+" URL: "+spreadsheet_handler.urls[i]+"\n"

    print(total_content)
    
    summary_generator = SummaryGenerator(total_content)
    prompt = get_prompt("year.summary")
    summary_generator.generate_summary(prompt)

    print(summary_generator.summary)

if __name__ == "__main__":
    main()
