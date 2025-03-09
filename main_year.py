from dotenv import load_dotenv

from spreadsheet_connector import SpreadsheetConnector
from url_processor import UrlProcessor
from summary_generator import SummaryGenerator
from image_extractor import ImageExtractor


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
    prompt = "Act like an expert technology journalist specialized in quantum computing. Read carefully all the stories and titles and create 3 lists. Each list contains: The title within an html link with the URL (example: <a href='https://thepiratecto.com'>Quantum grows</a>), and the summary for each story. Each list has exactly 10 articles. Pick the 10 (ten) most relevant articles for each list. List one has the most relevant company and investment funding stories. Pick 10 stories by the biggest investment amount and relevance. The second list is 10 stories about government and public funds. Pick only stories about countries, governments and institutions. The third list 10 stories about scientific discoveries, papers and breakthroughs. Pick the 5 most relevant stories for the advancement of the quantum computing field. Add a <br> after each story."
    summary_generator.generate_summary(prompt)

    print(summary_generator.summary)

if __name__ == "__main__":
    main()
