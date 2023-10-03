## main.py
import argparse
from csv_handler import CsvHandler
from url_processor import UrlProcessor
from summary_generator import SummaryGenerator
from image_extractor import ImageExtractor

# Comment
def main(input_file: str, output_file: str) -> None:
    csv_handler = CsvHandler(input_file, output_file)
    try:
        csv_handler.read_urls()
    except FileNotFoundError:
        print(f"Input file {input_file} does not exist.")
        return
    except Exception as e:
        print(f"Input file {input_file} is not a valid CSV file. Error: {e}")
        return

    total_content = ""
    results = []
    print(csv_handler.titles)
    print(csv_handler.urls)
    for i, url in enumerate(csv_handler.urls):
        url_processor = UrlProcessor(url)
        url_processor.download_content()
        url_processor.strip_html()

        summary_generator = SummaryGenerator(str(csv_handler.titles[i])+" - "+str(url_processor.content))
        prompt = "Act like an expert technology journalist specialized in quantum computing. Generate a concise and accurate executive summary of the following text of 150 words maximum. Ensure you highlight and emphasize people, key nunbers, scientific findings institutions or companies. If there is a quote, mention it as well as the author. End with a sentece with the key take away from the text. Use precise language and avoid irrelevant words."
        summary_generator.generate_summary(prompt)

        if len(str(csv_handler.titles[i])) < 1:
            title_generator = SummaryGenerator(summary_generator.summary)
            prompt = "Act like an expert technology journalist specialized in quantum computing. Summarize this text into a single headline that catches the eye and the reader wants to continue reading. This will be the subject on an email."
            title_generator.generate_summary(prompt)
            csv_handler.titles[i] = title_generator.summary

        total_content += str(summary_generator.summary)

        image_extractor = ImageExtractor(url)
        image_extractor.extract_image()

        result = {
            'url': url,
            'title' : csv_handler.titles[i],
            'summary': summary_generator.summary,
            'image_url': image_extractor.image_url
        }
        results.append(result)
        csv_handler.results.append(result)
        print(result)

    csv_handler.write_results()

    global_summarizer = SummaryGenerator(total_content)
    prompt = "Act like an expert technology journalist specialized in quantum computing. The following are a collection of news that have happened during this week. Generate a concise and clear executive summary for them in 2 paragraphs. Ensure you highlight and relate people, key nunbers, institutions or companies. Join together relevant topics if required. Use scientific and clear language. Then end with an outlook and conclusions for the week."
    global_summarizer.generate_summary(prompt)

    micro_summary = SummaryGenerator(global_summarizer.summary)
    prompt = "Act like an expert technology journalist specialized in quantum computing. Summarize these text into a single headline that catches the eye and the reader wants to continue reading. This will be the subject on an email."
    micro_summary.generate_summary(prompt)

    print("-------------------------")
    print("Global summary:")
    print("-------------------------")
    print(global_summarizer.summary)

    print("-------------------------")
    print("Newsletter:")
    print("-------------------------")
    print("<h2>Quick Recap</h2><p>"+global_summarizer.summary+"</p>")
    print("</br></br>")
    print("<h2>The Week in Quantum Computing</h2>")
    print(micro_summary.summary)
    for result in results:
        if 'url' in result and 'summary' in result:
            print("<div><h3><a href='"+str(result['url'])+"'>"+str(result['title'])+"</a></h3>")
            if 'image_url' in result and len(str(result['image_url'])) > 0 and result['image_url'] is not None:
                print("<img src='"+str(result['image_url'])+"' />")
            print("<p>"+str(result['summary'])+"</p>")
            print("<p><a href='"+str(result['url'])+"'>"+str(result['url'])+"</a></p></div>")
    

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Summarize websites.')
    parser.add_argument('input_file', type=str, help='The input CSV file containing the URLs.')
    parser.add_argument('output_file', type=str, help='The output CSV file to write the results to.')
    args = parser.parse_args()

    main(args.input_file, args.output_file)
