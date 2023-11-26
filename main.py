## main.py
from spreadsheet_connector import SpreadsheetConnector
from url_processor import UrlProcessor
from summary_generator import SummaryGenerator
from image_extractor import ImageExtractor

def main() -> None:
    
    spreadsheet_handler = SpreadsheetConnector()
    spreadsheet_handler.get_content()
    
    total_content = ""
    results = []
    
    for i, url in enumerate(spreadsheet_handler.urls):
        print("Summarizing: "+spreadsheet_handler.titles[i]+" - "+url)
        url_processor = UrlProcessor(url)
        url_processor.download_content()
        if(url_processor.content is None):
            print("We could not download the website, rolling back to extracted summary.")
            url_processor.content = spreadsheet_handler.summaries[i]
        else:
            url_processor.strip_html()

        summary_generator = SummaryGenerator(str(spreadsheet_handler.titles[i])+" - "+str(url_processor.content))
        prompt = "Act like an expert technology journalist specialized in quantum computing. Generate a concise and accurate executive summary of the following text of 120 words maximum. Ensure you highlight and emphasize people, key nunbers, scientific findings institutions or companies. If there is a quote, mention it as well as the author. End with a sentece that represents the main take away from the text but do not explicitly call out 'take away'. Use precise language and avoid irrelevant words. Don't say what the text is about, write what the text says. Think about the summary step by step and write it in a very understandable way. Do not use superflous words."
        summary_generator.generate_summary(prompt)

        if len(str(spreadsheet_handler.titles[i])) < 1:
            title_generator = SummaryGenerator(summary_generator.summary)
            prompt = "Act like an expert quantum computing engineer talking to a college graduate. Summarize this text into a single headline that catches the eye and the reader wants to continue reading. This will be the main headline of the article referenced below. Be concise. Call out the companies or key people mentioned."
            title_generator.generate_summary(prompt)
            spreadsheet_handler.titles[i] = title_generator.summary

        total_content += str(summary_generator.summary)

        image_extractor = ImageExtractor(url)
        image_extractor.extract_image()

        result = {
            'url': url,
            'title' : spreadsheet_handler.titles[i],
            'summary': summary_generator.summary,
            'image_url': image_extractor.image_url
        }
        results.append(result)


    global_summarizer = SummaryGenerator(total_content)
    prompt = "Act like an expert quantum computing engineer talking to a college graduate or business executive. The following are a collection of news that have happened during this week. Your job is to generate a concise and clear executive summary for them in 2 paragraphs so they know what has happend. Ensure you highlight and relate people, key nunbers, institutions or companies. Join together relevant topics if required business news, investment or research. Make sure you reference to all news in the context. Use clear and precise language and avoid superflous words like 'this week in quantum computing' or 'in the world of quantum'. You can use an informal style. Do not say 'the article talks about...' but diretly and explicitly say the content of the article. Then end with an outlook and conclusions for the week."
    global_summarizer.generate_summary(prompt)

    micro_summary = SummaryGenerator(global_summarizer.summary)
    prompt = "Act like an expert technology journalist specialized in quantum computing. Summarize these text into a single headline that catches the eye and the reader wants to continue reading. This will be the subject on an email. Call out the companies or key people mentioned."
    micro_summary.generate_summary(prompt)

    newsletter = ""

    print("-------------------------")
    print("Global summary:")
    print("-------------------------")
    print(global_summarizer.summary)

    newsletter += "<h1>"+micro_summary.summary+"</h1>"

    print("-------------------------")
    print("Newsletter:")
    print("-------------------------")
    print("<h2>Quick Recap</h2><p>"+global_summarizer.summary+"</p>")
    newsletter += "<h2>Quick Recap</h2><p>"+global_summarizer.summary+"</p>"
    print("</br></br>")
    print("<h2>The Week in Quantum Computing</h2>")
    newsletter += "</br></br><h2>The Week in Quantum Computing</h2>"
    print(micro_summary.summary)
    for result in results:
        if 'url' in result and 'summary' in result:
            print("<div><h3><a href='"+str(result['url'])+"'>"+str(result['title'])+"</a></h3>")
            newsletter += "<div><h3><a href='"+str(result['url'])+"'>"+str(result['title'])+"</a></h3>"
            if 'image_url' in result and len(str(result['image_url'])) > 0 and result['image_url'] is not None:
                print("<img src='"+str(result['image_url'])+"' />")
                newsletter += "<img src='"+str(result['image_url'])+"' />"
            print("<p>"+str(result['summary'])+"</p>")
            newsletter += "<p>"+str(result['summary'])+"</p>"
            print("<p><a href='"+str(result['url'])+"'>"+str(result['url'])+"</a></p></div>")
            newsletter += "<p><a href='"+str(result['url'])+"'>"+str(result['url'])+"</a></p></div>"
    
    with open("newsletter.html", 'w') as file:
        file.write(newsletter)

if __name__ == "__main__":
    main()
