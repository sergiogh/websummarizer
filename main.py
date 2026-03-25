from dotenv import load_dotenv
from spreadsheet_connector import SpreadsheetConnector
from url_processor import UrlProcessor
from summary_generator import SummaryGenerator
from image_extractor import ImageExtractor
from scientific_paper_processor import ScientificPaperProcessor
from datetime import datetime
import random
from openai import OpenAI
import os
import base64
import html
import io
import requests
import sys

from artifact_store import ArtifactStore
from prompt_loader import get_prompt
from qa_checks import qa_title_summary
from quantum_bits_comic import fetch_latest_quantum_bits_comic, resolve_comic_for_render
from story_organizer import (
    STORY_BUCKET_DESCRIPTIONS,
    STORY_BUCKET_LABELS,
    build_story_digest,
    curate_stories,
    group_stories,
)
from title_utils import sanitize_story_title

def load_spreadsheet_data(days = 7):
    spreadsheet_handler = SpreadsheetConnector()
    spreadsheet_handler.get_content(days)
    return spreadsheet_handler

def process_url(url, title, fallback_summary):
    # Check if this is a scientific paper
    paper_processor = ScientificPaperProcessor(url)
    is_paper = paper_processor.is_scientific_paper()
    
    if is_paper:
        print(f"Detected scientific paper from {paper_processor.paper_type}: {url}")
        # Download the full paper
        full_text = paper_processor.download_paper()
        if full_text:
            print(f"Successfully downloaded paper, length: {len(full_text)} characters")
            return {
                "raw": full_text,
                "clean": full_text,
                "is_paper": True,
                "paper_type": paper_processor.paper_type
            }
        else:
            print(f"Could not download full paper, falling back to standard processing")
            # Fall through to standard processing
    
    # Standard URL processing for non-papers or if paper download failed
    url_processor = UrlProcessor(url)
    url_processor.download_content()
    if url_processor.content is None:
        print(f"Could not download {url}, using fallback summary.")
        url_processor.content = fallback_summary
        raw_content = fallback_summary
    else:
        raw_content = url_processor.content
        url_processor.strip_html()
    return {
        "raw": raw_content or "",
        "clean": url_processor.content or "",
        "is_paper": False,
        "paper_type": None
    }

def generate_summary(title, content, url=None):
    # Check if this is a scientific paper and use specialized analysis
    if url:
        paper_processor = ScientificPaperProcessor(url)
        if paper_processor.is_scientific_paper():
            print(f"Generating concise summary for scientific paper: {title}")
            analysis = paper_processor.analyze_paper(title, content)
            if analysis:
                return analysis
            else:
                print("Paper analysis failed, falling back to standard summary")
    
    # Standard summary generation for non-papers
    prompt = get_prompt("summary.newsletter")
    summary_generator = SummaryGenerator(f"{title} - {content}")
    summary_generator.generate_summary(prompt)
    return summary_generator.summary

def generate_title(summary, url=None, is_paper=False):
    if is_paper:
        prompt = get_prompt("title.paper")
    else:
        prompt = get_prompt("title.story")
    title_generator = SummaryGenerator(summary)
    title_generator.generate_summary(prompt)
    title = title_generator.summary
    if is_paper and title:
        # Add [PAPER] prefix
        title = f"[PAPER] {title}"
    return title

def extract_image(url):
    image_extractor = ImageExtractor(url)
    image_extractor.extract_image()
    return image_extractor.image_url

def generate_global_summary(total_content):
    prompt = get_prompt("global.summary")
    global_summarizer = SummaryGenerator(total_content)
    global_summarizer.generate_summary(prompt)
    return global_summarizer.summary

def generate_newsletter_headline(global_summary):
    prompt = get_prompt("newsletter.headline")
    micro_summary = SummaryGenerator(global_summary)
    micro_summary.generate_summary(prompt)
    return micro_summary.summary

def generate_podcast_summary(total_content):
    prompt = get_prompt("podcast.summary")
    podcast_summarizer = SummaryGenerator(total_content)
    podcast_summarizer.generate_summary(prompt)
    return podcast_summarizer.summary

def extract_key_facts(clean_content):
    prompt = get_prompt("provenance.why")
    facts_generator = SummaryGenerator(clean_content)
    facts_generator.generate_summary(prompt)
    return facts_generator.summary

def review_newsletter_content(results, global_summary, micro_summary, podcast_summary):
    # Create a text representation of the newsletter for review
    review_content = f"""
NEWSLETTER REVIEW REQUEST

HEADLINE: {micro_summary}

GLOBAL SUMMARY:
{global_summary}

ARTICLES:
"""
    
    for result in results:
        if 'url' in result and 'summary' in result:
            review_content += f"""
Title: {result['title']}
URL: {result['url']}
Summary: {result['summary']}
Image URL: {result.get('image_url', 'No image')}
---
"""

    review_content += f"""
PODCAST CONTENT:
{podcast_summary}

"""

    prompt = get_prompt("review.newsletter")

    reviewer = SummaryGenerator(review_content)
    reviewer.generate_summary(prompt)
    return reviewer.summary

def generate_highlight_image(global_summary, micro_summary):
    client = OpenAI()
    
    # Create a prompt for the image generation
    prompt = get_prompt("image.highlight")
    
    try:
        # Generate the image using DALL-E
        response = client.images.generate(
            model="dall-e-3",
            prompt=prompt,
            size="1024x1024",
            quality="standard",
            n=1,
        )
        
        # Get the image URL
        image_url = response.data[0].url
        
        # Download the image
        image_response = requests.get(image_url)
        image_data = image_response.content
        
        # Save the image
        timestamp = datetime.now().strftime("%d%m%Y")
        image_filename = f"newsletter_{timestamp}_highlight.png"
        
        with open(image_filename, 'wb') as f:
            f.write(image_data)
            
        return image_filename
        
    except Exception as e:
        print(f"Error generating highlight image: {e}")
        return None

def render_comic_section(comic):
    if not comic:
        return ""

    image_src = comic.get("image_src") or comic.get("image_url")
    published_label = comic.get("published_label")
    published_text = f"Latest strip published {published_label}" if published_label else "Latest strip"
    comic_summary = html.escape(comic.get("summary", ""))
    comic_link = html.escape(comic.get("link", "#"), quote=True)
    comic_title = html.escape(comic.get("title", "Latest comic strip"))
    comic_series = html.escape(comic.get("series", "Quantum Bits with Quantessa & Atomique"))
    comic_creator = html.escape(comic.get("creator", "Yuval Boger"))
    image_src_attr = html.escape(image_src, quote=True) if image_src else ""

    section = f"""
    <div class="comic-section">
        <div class="comic-eyebrow">{comic_series}</div>
        <h2>{comic_title}</h2>
        <p class="comic-meta">{published_text} · by {comic_creator}</p>"""

    if image_src:
        section += f"""
        <a href="{comic_link}" target="_blank" rel="noopener noreferrer">
            <img src="{image_src_attr}" alt="{comic_title}" class="comic-image">
        </a>"""

    if comic_summary:
        section += f"""
        <p>{comic_summary}</p>"""

    section += f"""
        <p class="source-link"><a href="{comic_link}" target="_blank" rel="noopener noreferrer">Read the full comic on Quantum Bits Comics</a></p>
    </div>"""
    return section


def render_summary_html(summary):
    rendered = html.escape(summary or "")
    for label in ("What happened:", "Key detail:", "Why this matters:", "Finding:", "Evidence:"):
        rendered = rendered.replace(html.escape(label), f"<strong>{label}</strong>")
    return rendered.replace("\n", "<br>")


def render_article_groups(results):
    sections = []
    for bucket, stories in group_stories(results):
        bucket_label = html.escape(STORY_BUCKET_LABELS.get(bucket, "Other Developments"))
        bucket_description = html.escape(STORY_BUCKET_DESCRIPTIONS.get(bucket, ""))
        anchor_id = f"channel-{bucket}"
        sections.append(f"""
        <div class="story-group" id="{anchor_id}">
            <div class="story-group-heading">{bucket_label} ({len(stories)})</div>
            <p class="story-group-intent">{bucket_description}</p>""")
        for result in stories:
            if 'url' not in result or 'summary' not in result:
                continue
            article_url = html.escape(result.get('url', ''), quote=True)
            article_title = html.escape(result.get('title', ''))
            article_summary = render_summary_html(result.get('summary', ''))
            sections.append(f"""
        <div class="article">
            <h3><a href="{article_url}">{article_title}</a></h3>""")
            if result.get('image_url'):
                image_url = html.escape(result['image_url'], quote=True)
                sections.append(f"""
            <img src="{image_url}" alt="Article image" />""")
            sections.append(f"""
            <p>{article_summary}</p>
            <p class="source-link"><a href="{article_url}">Read original article</a></p>
        </div>""")
        sections.append("""
        </div>""")
    return "".join(sections)

def render_topic_navigation(results):
    links = []
    for bucket, stories in group_stories(results):
        bucket_label = html.escape(STORY_BUCKET_LABELS.get(bucket, "Other Developments"))
        links.append(
            f'<a href="#channel-{bucket}">{bucket_label} ({len(stories)})</a>'
        )
    if not links:
        return ""
    return f"""
    <div class="topic-navigation">
        <div class="topic-navigation-title">Topics in this issue</div>
        <div class="topic-navigation-links">{''.join(links)}</div>
    </div>"""


def render_overflow_links(overflow_results):
    if not overflow_results:
        return ""
    items = []
    for story in overflow_results:
        article_url = html.escape(story.get('url', ''), quote=True)
        article_title = html.escape(story.get('title', ''))
        items.append(
            f'<li><a href="{article_url}" target="_blank" rel="noopener noreferrer">{article_title}</a></li>'
        )
    return f"""
    <div class="overflow-section" id="more-links">
        <h2>More links this week ({len(overflow_results)})</h2>
        <ul>{''.join(items)}</ul>
    </div>"""


def create_newsletter(results, global_summary, micro_summary, podcast_summary, comic=None, overflow_results=None):
    overflow_results = overflow_results or []
    
    # Generate highlight image
    highlight_image = generate_highlight_image(global_summary, micro_summary)
    
    # Review the content before generating the final HTML
    review_result = review_newsletter_content(
        results, global_summary, micro_summary, podcast_summary
    )
    
    print("\nNewsletter Review Results:")
    print("-------------------------")
    print(review_result)
    print("-------------------------")
    
    # Apply review recommendations to correct any issues
    print("\nApplying review recommendations...")
    corrected_content = apply_review_recommendations(
        review_result, results, global_summary, micro_summary, podcast_summary
    )
    
    # Use corrected content if available, otherwise use original
    final_headline = corrected_content.get('headline', micro_summary)
    final_global_summary = corrected_content.get('global_summary', global_summary)
    final_podcast_summary = corrected_content.get('podcast_summary', podcast_summary)
    
    # Update article summaries if corrections were provided
    final_results = results.copy()
    if 'articles' in corrected_content and corrected_content['articles']:
        for i, corrected_summary in enumerate(corrected_content['articles']):
            if i < len(final_results):
                final_results[i] = final_results[i].copy()
                final_results[i]['summary'] = corrected_summary
    
    print("\nFinal verification complete. Generating newsletter HTML...")
    
    newsletter = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{final_headline}</title>
    <style>
        body {{
            font-family: Arial, sans-serif;
            line-height: 1.6;
            max-width: 800px;
            margin: 0 auto;
            padding: 20px;
            color: #333;
        }}
        h1 {{
            color: #2c3e50;
            border-bottom: 2px solid #3498db;
            padding-bottom: 10px;
        }}
        h2 {{
            color: #2c3e50;
            margin-top: 30px;
        }}
        h3 {{
            color: #34495e;
        }}
        .highlight-image {{
            width: 100%;
            max-height: 400px;
            object-fit: cover;
            border-radius: 8px;
            margin: 20px 0;
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
        }}
        .quote-of-week {{
            background-color: #f8f9fa;
            border-left: 4px solid #3498db;
            padding: 20px;
            margin: 30px 0;
            border-radius: 0 8px 8px 0;
        }}
        .quote-text {{
            font-size: 1.2em;
            font-style: italic;
            color: #2c3e50;
            margin-bottom: 10px;
        }}
        .quote-attribution {{
            color: #7f8c8d;
            font-size: 0.9em;
        }}
        .article {{
            margin: 30px 0;
            padding: 20px;
            border: 1px solid #eee;
            border-radius: 5px;
        }}
        .article img {{
            max-width: 100%;
            height: auto;
            margin: 15px 0;
            border-radius: 5px;
        }}
        a {{
            color: #3498db;
            text-decoration: none;
        }}
        a:hover {{
            text-decoration: underline;
        }}
        .source-link {{
            font-size: 0.9em;
            color: #7f8c8d;
        }}
        .podcast-section {{
            background-color: #f8f9fa;
            padding: 20px;
            border-radius: 5px;
            margin-top: 30px;
        }}
        .comic-section {{
            background: linear-gradient(135deg, #f7fbff 0%, #edf7f4 100%);
            border: 1px solid #d6eaf8;
            border-radius: 12px;
            padding: 24px;
            margin: 30px 0;
        }}
        .comic-eyebrow {{
            color: #0f766e;
            font-size: 0.85em;
            font-weight: bold;
            letter-spacing: 0.08em;
            margin-bottom: 10px;
            text-transform: uppercase;
        }}
        .comic-meta {{
            color: #4b5563;
            font-size: 0.95em;
            margin-bottom: 16px;
        }}
        .comic-image {{
            width: 100%;
            height: auto;
            border-radius: 10px;
            margin: 10px 0 18px;
            box-shadow: 0 10px 30px rgba(15, 23, 42, 0.12);
        }}
        .story-group {{
            margin-top: 30px;
        }}
        .story-group-heading {{
            color: #0f766e;
            font-size: 0.82em;
            font-weight: bold;
            letter-spacing: 0.08em;
            margin: 0 0 12px;
            text-transform: uppercase;
        }}
        .story-group-intent {{
            margin: 0 0 12px;
            color: #4b5563;
            font-size: 0.92em;
            line-height: 1.5;
        }}
        .issue-stats {{
            margin-top: 12px;
            color: #4b5563;
            font-size: 0.95em;
        }}
        .topic-navigation {{
            margin-top: 20px;
            border: 1px solid #d6eaf8;
            border-radius: 10px;
            padding: 14px 16px;
            background: #f7fbff;
        }}
        .topic-navigation-title {{
            color: #0f766e;
            font-size: 0.8em;
            font-weight: bold;
            letter-spacing: 0.08em;
            margin-bottom: 10px;
            text-transform: uppercase;
        }}
        .topic-navigation-links {{
            display: flex;
            flex-wrap: wrap;
            gap: 8px 14px;
        }}
        .overflow-section {{
            margin-top: 30px;
            padding-top: 18px;
            border-top: 1px dashed #d6eaf8;
        }}
        .overflow-section ul {{
            margin: 0;
            padding-left: 20px;
            line-height: 1.7;
        }}
        .action-links {{
            display: flex;
            justify-content: space-between;
            margin: 20px 0;
            font-style: italic;
            color: #666;
        }}
        .action-link {{
            flex: 1;
            text-align: center;
            padding: 10px;
            border-radius: 5px;
            transition: all 0.2s;
        }}
        .action-link:hover {{
            background-color: #f8f9fa;
            transform: translateY(-2px);
        }}
        .share-link {{
            border-right: 1px solid #eee;
        }}
        .verification-badge {{
            background-color: #27ae60;
            color: white;
            padding: 5px 10px;
            border-radius: 15px;
            font-size: 0.8em;
            display: inline-block;
            margin-bottom: 10px;
        }}
    </style>
</head>
<body>
    <div class="verification-badge">✓ Verified Content - All quotes and data sourced from provided materials</div>
    <h1>{final_headline}</h1>
    <p class="issue-stats">This week: {len(final_results)} selected stories</p>"""
    
    if highlight_image:
        newsletter += f"""
    <img src="{highlight_image}" alt="Quantum Computing News Highlight" class="highlight-image">"""
    
    newsletter += f"""
    <h2>Quick Recap</h2>
    <p>{final_global_summary}</p>"""

    newsletter += render_comic_section(comic)
    newsletter += render_topic_navigation(final_results)
    
    newsletter += f"""
    <h2>The Week in Quantum Computing</h2>
    <div class="articles">"""

    newsletter += render_article_groups(final_results)
    newsletter += render_overflow_links(overflow_results)

    newsletter += f"""
    </div>
    
    <div class="podcast-section">
        <h2>Podcast Research Content</h2>
        <p>{final_podcast_summary}</p>
    </div>

    <script>
        function shareNewsletter() {{
            if (navigator.share) {{
                navigator.share({{
                    title: '{final_headline}',
                    text: 'Check out this week\'s quantum computing newsletter!',
                    url: window.location.href
                }})
                .catch(console.error);
            }} else {{
                // Fallback for browsers that don't support Web Share API
                const url = window.location.href;
                const text = 'Check out this week\'s quantum computing newsletter!';
                window.open(`https://twitter.com/intent/tweet?text=${{encodeURIComponent(text)}}&url=${{encodeURIComponent(url)}}`);
            }}
        }}
        
        function subscribeNewsletter() {{
            alert('Thank you for your interest! Please visit our website to subscribe to the newsletter.');
        }}
    </script>
</body>
</html>"""
    
    return newsletter

def apply_review_recommendations(review_result, results, global_summary, micro_summary, podcast_summary):
    """
    Apply review recommendations to ensure quotes and data are verified and outlook/implications are context-based.
    """
    prompt = get_prompt(
        "apply.review",
        review_result=review_result,
        micro_summary=micro_summary,
        global_summary=global_summary
    )
    
    for i, result in enumerate(results):
        if 'url' in result and 'summary' in result:
            prompt += f"\n{i+1}. {result['title']}: {result['summary']}"
    
    prompt += f"\n\nPodcast Summary: {podcast_summary}"
    prompt += get_prompt("apply.review.instructions")
    
    corrector = SummaryGenerator(prompt)
    corrector.generate_summary("Apply the corrections based on the review recommendations.")
    
    # Parse the corrected content
    corrected_content = {}
    lines = corrector.summary.split('\n')
    current_section = None
    
    for line in lines:
        line = line.strip()
        if line.startswith('CORRECTED_HEADLINE:'):
            corrected_content['headline'] = line.replace('CORRECTED_HEADLINE:', '').strip()
        elif line.startswith('CORRECTED_GLOBAL_SUMMARY:'):
            corrected_content['global_summary'] = line.replace('CORRECTED_GLOBAL_SUMMARY:', '').strip()
        elif line.startswith('CORRECTED_ARTICLES:'):
            current_section = 'articles'
            corrected_content['articles'] = []
        elif line.startswith('CORRECTED_PODCAST:'):
            corrected_content['podcast_summary'] = line.replace('CORRECTED_PODCAST:', '').strip()
        elif current_section == 'articles' and line and not line.startswith('CORRECTED_'):
            corrected_content['articles'].append(line)
    
    return corrected_content

def main() -> None:
    load_dotenv()
    
    # Parse command-line arguments
    days = 7  # default value
    for arg in sys.argv[1:]:
        if arg.startswith('days='):
            try:
                days = int(arg.split('=')[1])
            except (ValueError, IndexError):
                print(f"Warning: Invalid days parameter '{arg}'. Using default value of 7.")
                days = 7
    
    spreadsheet_handler = load_spreadsheet_data(days=days)
    artifact_store = ArtifactStore()
    run_id = artifact_store.new_run({"days": days, "source": "cli"})
    results = []
    print("URLs:")
    print(spreadsheet_handler.urls)
    for i, url in enumerate(spreadsheet_handler.urls):
        story_id = str(i)
        print(f"Processing: {spreadsheet_handler.titles[i]} - {url}")
        
        # Check if this is a scientific paper
        paper_processor = ScientificPaperProcessor(url)
        is_paper = paper_processor.is_scientific_paper()
        
        content_bundle = process_url(url, spreadsheet_handler.titles[i], "")
        artifact_store.save_text(run_id, story_id, "raw", content_bundle["raw"])
        artifact_store.save_text(run_id, story_id, "clean", content_bundle["clean"])
        artifact_store.save_json(
            run_id,
            story_id,
            "fetch",
            {
                "url": url,
                "is_paper": content_bundle["is_paper"],
                "paper_type": content_bundle["paper_type"],
                "raw_length": len(content_bundle["raw"] or ""),
                "clean_length": len(content_bundle["clean"] or "")
            }
        )

        summary = generate_summary(spreadsheet_handler.titles[i], content_bundle["clean"], url)
        
        # Skip if summary generation failed
        if summary is None:
            print(f"Warning: Could not generate summary for {url}")
            continue

        artifact_store.save_json(run_id, story_id, "summary", {"summary": summary})
        
        # Handle title generation/formatting
        if not spreadsheet_handler.titles[i] or spreadsheet_handler.titles[i].strip() == "":
            # Generate new title
            new_title = generate_title(summary, url, is_paper)
            if new_title is not None:
                spreadsheet_handler.titles[i] = new_title
                print(f"Generated new title: {spreadsheet_handler.titles[i]}")
            else:
                print(f"Warning: Could not generate title for {url}")
                continue

        spreadsheet_handler.titles[i] = sanitize_story_title(spreadsheet_handler.titles[i], is_paper=is_paper)
        artifact_store.save_json(run_id, story_id, "title", {"title": spreadsheet_handler.titles[i]})

        qa_result = qa_title_summary(spreadsheet_handler.titles[i], summary)
        artifact_store.save_json(run_id, story_id, "qa", qa_result)

        spreadsheet_handler.titles[i] = qa_result["title_fixed"]
        summary = qa_result["summary_fixed"]

        why_log = extract_key_facts(content_bundle["clean"])
        artifact_store.save_json(
            run_id,
            story_id,
            "provenance",
            {"url": url, "title": spreadsheet_handler.titles[i], "why": why_log}
        )
        
        image_url = extract_image(url)
        
        results.append({
            'story_id': story_id,
            'url': url,
            'title': spreadsheet_handler.titles[i],
            'summary': summary,
            'image_url': image_url,
            'tag': spreadsheet_handler.tags[i],
            'is_paper': bool(content_bundle["is_paper"] or is_paper),
            'paper_type': content_bundle["paper_type"]
        })

    if not results:
        print("Error: No valid content was generated. Exiting.")
        return

    curated = curate_stories(results)
    primary_results = curated["primary"]
    overflow_results = curated["overflow"]

    total_content = build_story_digest(primary_results)

    global_summary = generate_global_summary(total_content)
    if global_summary is None:
        print("Error: Could not generate global summary. Exiting.")
        return

    micro_summary = generate_newsletter_headline(global_summary)
    if micro_summary is None:
        print("Error: Could not generate newsletter headline. Exiting.")
        return

    podcast_summary = generate_podcast_summary(total_content)
    if podcast_summary is None:
        print("Error: Could not generate podcast summary. Exiting.")
        return

    comic = fetch_latest_quantum_bits_comic(artifact_store.run_dir(run_id))

    artifact_store.save_run_json(
        run_id,
        "results.json",
        {
            "results": primary_results,
            "overflow_results": overflow_results,
            "channel_counts": curated["channel_counts"],
            "global_summary": global_summary,
            "micro_summary": micro_summary,
            "podcast_summary": podcast_summary,
            "comic": comic
        }
    )
    
    comic_image_src = None
    if comic and comic.get("image_filename"):
        comic_image_src = os.path.relpath(
            os.path.join(artifact_store.run_dir(run_id), comic["image_filename"]),
            os.getcwd()
        )

    newsletter = create_newsletter(
        primary_results,
        global_summary,
        micro_summary,
        podcast_summary,
        resolve_comic_for_render(comic, comic_image_src),
        overflow_results=overflow_results,
    )
    artifact_store.save_run_json(run_id, "render.json", {"headline": micro_summary})
    
    timestamp = datetime.now().strftime("%d%m%Y")
    filename = f"newsletter_{timestamp}.html"
    with open(filename, 'w', encoding='utf-8') as file:
        file.write(newsletter)
    artifact_store.save_run_text(run_id, "newsletter.html", newsletter)
    print(f"\nNewsletter generated successfully: {filename}")

if __name__ == "__main__":
    main()
