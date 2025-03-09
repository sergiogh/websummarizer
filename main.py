from dotenv import load_dotenv
from spreadsheet_connector import SpreadsheetConnector
from url_processor import UrlProcessor
from summary_generator import SummaryGenerator
from image_extractor import ImageExtractor
from datetime import datetime

def load_spreadsheet_data(days = 7):
    spreadsheet_handler = SpreadsheetConnector()
    spreadsheet_handler.get_content(days)
    return spreadsheet_handler

def process_url(url, title, fallback_summary):
    url_processor = UrlProcessor(url)
    url_processor.download_content()
    if url_processor.content is None:
        print(f"Could not download {url}, using fallback summary.")
        url_processor.content = fallback_summary
    else:
        url_processor.strip_html()
    return url_processor.content

def generate_summary(title, content):
    prompt = (
        "Act as an expert technology journalist specialized in quantum computing. Your audience knows about quantum computing and its potential but also shortcomings"
        "You can be skeptical about the news but you need to be accurate and concise. "
        "Generate a concise and accurate executive summary of the following text in 120 words maximum. "
        "Highlight people, key numbers, scientific findings, institutions, or companies and why it is important, specifically within the context of quantum computing in this year 2025"
        "Mention quotes and their authors. End with a sentence that represents the main takeaway considering "
        "the latest news in quantum computing but do not say 'the main takeway' or 'the conclusion' or 'the outlook', make it embedded with the story. "
        "Only write a takeaway or conclusion if it is really relevant. Otherwise don't write anything."
        "Use precise language and avoid irrelevant words. Do not assume any company positions in the market or their revenue. Use the provided context only"
    )
    summary_generator = SummaryGenerator(f"{title} - {content}")
    summary_generator.generate_summary(prompt)
    return summary_generator.summary

def generate_title(summary):
    prompt = (
        "Act as an expert quantum computing engineer talking to a college graduate. "
        "Summarize this text into a single headline that catches the eye. "
        "Be concise and call out companies or key people mentioned. Avoid quotes or superfluous words."
    )
    title_generator = SummaryGenerator(summary)
    title_generator.generate_summary(prompt)
    return title_generator.summary

def extract_image(url):
    image_extractor = ImageExtractor(url)
    image_extractor.extract_image()
    return image_extractor.image_url

def generate_global_summary(total_content):
    prompt = (
        "Act as an expert quantum computing engineer talking to a college graduate or business executive. Your audience knows about quantum computing and its potential but also shortcomings"
        "Generate a concise and clear executive summary of the week's news in 2 paragraphs.  Make sure you use all the context provided, and reference to every single article."
        "Highlight and relate people, key numbers, institutions, or companies. "
        "Join relevant topics if required. For example, investment news first, then research and papers, then company news. Use clear and precise language and avoid superfluous words. Don't use words like 'in the world of'"
        "Don't write more than one or two sentences referring to each article. Remember that this is a summary of the more detailed news that will be showed to the user later on."
        "End with a brief outlook and conclusions for the week."
    )
    global_summarizer = SummaryGenerator(total_content)
    global_summarizer.generate_summary(prompt)
    return global_summarizer.summary

def generate_newsletter_headline(global_summary):
    prompt = (
        "Act as an expert technology journalist specialized in quantum computing. "
        "Summarize this text into a single headline that catches the eye. "
        "This will be the subject of an email. Call out companies or key people mentioned. Mention every company"
        "Example: 'MIT new paper on error correction, QCtrl, IBM, Google, Rigetti, IonQ, Honeywell, D-Wave, Xanadu, Zapata make the news'"
    )
    micro_summary = SummaryGenerator(global_summary)
    micro_summary.generate_summary(prompt)
    return micro_summary.summary

def generate_podcast_summary(total_content):
    prompt = (
        "Act as a research assistant for a podcast on quantum computing. "
        "Generate a very detailed and thorough article from the following text. Your audience is already an expert audience in quantum computing. Do not use superfluos words nor explain quantum concepts. Only why they are important"
        "Include every single detail, mentioning people, numbers, affiliations, dates, money involved, parties or governments. "
        "Include quotes and their authors. Be as detailed as possible and avoid irrelevant information. "
        "You can expand with your own knowledge of what has happened in quantum computing and communications in the past."
        "Make it at least 15.000 words long."
        "This text will be used as research content for a podcast, so it needs to be comprehensive and complete."
        "The afterwards generate two headlines with a list of summary bulletpoints. One for business related news and one for research and academia"
    )
    podcast_summarizer = SummaryGenerator(total_content)
    podcast_summarizer.generate_summary(prompt)
    return podcast_summarizer.summary

def create_newsletter(results, global_summary, micro_summary, podcast_summary):
    newsletter = f"<h1>{micro_summary}</h1>"
    newsletter += f"<h2>Quick Recap</h2><p>{global_summary}</p>"
    newsletter += "</br></br><h2>The Week in Quantum Computing</h2>"
    for result in results:
        if 'url' in result and 'summary' in result:
            newsletter += f"<div><h3><a href='{result['url']}'>{result['title']}</a></h3>"
            if 'image_url' in result and result['image_url']:
                newsletter += f"<img src='{result['image_url']}' />"
            newsletter += f"<p>{result['summary']}</p>"
            newsletter += f"<p><a href='{result['url']}'>{result['url']}</a></p></div>"
    newsletter += f"</br></br><h2>Podcast Research Content</h2><p>{podcast_summary}</p>"
    return newsletter

def main() -> None:
    load_dotenv()
    
    spreadsheet_handler = load_spreadsheet_data(days=7)
    total_content = ""
    results = []
    print("URLs:")
    print(spreadsheet_handler.urls)
    for i, url in enumerate(spreadsheet_handler.urls):
        print(f"Summarizing: {spreadsheet_handler.titles[i]} - {url}")
        content = process_url(url, spreadsheet_handler.titles[i], spreadsheet_handler.summaries[i])
        summary = generate_summary(spreadsheet_handler.titles[i], content)
        
        if not spreadsheet_handler.titles[i]:
            spreadsheet_handler.titles[i] = generate_title(summary)
        
        total_content += summary
        image_url = extract_image(url)
        
        results.append({
            'url': url,
            'title': spreadsheet_handler.titles[i],
            'summary': summary,
            'image_url': image_url
        })

    global_summary = generate_global_summary(total_content)
    micro_summary = generate_newsletter_headline(global_summary)
    podcast_summary = generate_podcast_summary(total_content)
    newsletter = create_newsletter(results, global_summary, micro_summary, podcast_summary)

    timestamp = datetime.now().strftime("%d%m%Y")
    filename = f"newsletter_{timestamp}.html"
    with open(filename, 'w') as file:
        file.write(newsletter)

if __name__ == "__main__":
    main()
