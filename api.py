from flask import Flask, request, jsonify, redirect, send_from_directory, url_for
from flask_cors import CORS
from spreadsheet_connector import SpreadsheetConnector
from summary_generator import SummaryGenerator
from url_processor import UrlProcessor
from image_extractor import ImageExtractor
from scientific_paper_processor import ScientificPaperProcessor
from datetime import datetime
import os
from dotenv import load_dotenv
import requests
import json
import html

from artifact_store import ArtifactStore
from prompt_loader import get_prompt
from qa_checks import (
    qa_title_summary,
    validate_aggregate_grounding,
    validate_story_grounding,
    validate_summary_claims,
)
from quantum_bits_comic import (
    build_comic_asset_url,
    fetch_latest_quantum_bits_comic,
    resolve_comic_for_render,
)
from story_organizer import STORY_BUCKET_LABELS, build_story_digest, group_stories, order_stories
from story_grounding import (
    failure_summary,
    filter_passed_stories,
    generate_grounded_summary,
    generate_grounded_title,
)
from title_utils import sanitize_story_title

app = Flask(__name__)
CORS(app)

artifact_store = ArtifactStore()

def _load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)
    except Exception:
        return default

def _escape(value):
    return html.escape(value or "")


def _story_id_for_result(result, fallback_index):
    return str(result.get("story_id", fallback_index))

def load_spreadsheet_data(days=7, start_date=None, end_date=None):
    spreadsheet_handler = SpreadsheetConnector()
    spreadsheet_handler.get_content(days, start_date=start_date, end_date=end_date)
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
            paper_sections = paper_processor.extract_paper_sections(full_text)
            return {
                "raw": full_text,
                "clean": full_text,
                "metadata": {
                    "source_url": url,
                    "html_title": title,
                    "h1": title,
                    "extraction_status": "paper",
                    "clean_text_length": len(full_text),
                    "paper_sections": paper_sections,
                },
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
        "metadata": getattr(url_processor, "metadata", {}) or {},
        "is_paper": False,
        "paper_type": None
    }

def generate_summary(title, content, url=None, source_metadata=None):
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
    grounded = generate_grounded_summary(
        title,
        content,
        url or "",
        source_metadata or {},
        prompt_key="summary.story.grounded.api",
    )
    return grounded["summary"]

def generate_summary_result(title, content, url=None, source_metadata=None):
    if url:
        paper_processor = ScientificPaperProcessor(url)
        if paper_processor.is_scientific_paper():
            analysis = paper_processor.analyze_paper(title, content)
            if analysis:
                return {
                    "status": "ok",
                    "summary": analysis,
                    "matched_title": title,
                    "evidence": [],
                    "confidence": 0.85,
                }

    return generate_grounded_summary(
        title,
        content,
        url or "",
        source_metadata or {},
        prompt_key="summary.story.grounded.api",
    )

def generate_title(summary, url=None, is_paper=False, source_title=""):
    if is_paper:
        prompt = get_prompt("title.paper.api")
    else:
        prompt = get_prompt("title.story.api")
    title_context = summary or ""
    source_title = (source_title or "").strip()
    if source_title:
        title_context = (
            f"SOURCE TITLE CONTEXT: {source_title}\n"
            f"SUMMARY CONTEXT: {summary or ''}"
        )
    title_generator = SummaryGenerator(title_context)
    title_generator.generate_summary(prompt)
    title = title_generator.summary
    if is_paper and title:
        # Add [PAPER] prefix
        title = f"[PAPER] {title}"
    return title

def generate_title_result(source_title, content, url=None, is_paper=False, source_metadata=None, summary=""):
    if is_paper:
        title = generate_title(summary or content[:2000], url, is_paper=True, source_title=source_title)
        return {"status": "ok" if title else "insufficient_content", "title": title or "", "evidence": [], "confidence": 0.8}

    return generate_grounded_title(
        source_title or "",
        content or "",
        url or "",
        source_metadata or {},
        prompt_key="title.story.grounded.api",
    )

# Image extraction removed for newsletter-only text output

def generate_global_summary(total_content):
    prompt = get_prompt("global.summary.api")
    global_summarizer = SummaryGenerator(total_content)
    global_summarizer.generate_summary(prompt)
    return global_summarizer.summary

def generate_newsletter_headline(global_summary):
    prompt = get_prompt("newsletter.headline.api")
    micro_summary = SummaryGenerator(global_summary)
    micro_summary.generate_summary(prompt)
    return micro_summary.summary

def generate_podcast_summary(total_content):
    prompt = get_prompt("podcast.summary.api")
    podcast_summarizer = SummaryGenerator(total_content)
    podcast_summarizer.generate_summary(prompt)
    return podcast_summarizer.summary

def build_aggregate_outputs(results):
    passed_results = filter_passed_stories(results)
    aggregate_results = passed_results or []
    if not aggregate_results:
        return {
            "passed_results": [],
            "global_summary": "No stories passed source-grounding checks. Review flagged stories before publishing this issue.",
            "micro_summary": "Newsletter needs source review",
            "podcast_summary": "No podcast research content was generated because no selected stories passed source-grounding checks.",
            "aggregate_qa": {
                "global_summary": {"passed": False, "flags": ["no_passed_stories"]},
                "headline": {"passed": False, "flags": ["no_passed_stories"]},
                "podcast_summary": {"passed": False, "flags": ["no_passed_stories"]},
            },
        }

    total_content = build_story_digest(aggregate_results)
    global_summary = generate_global_summary(total_content)
    micro_summary = generate_newsletter_headline(global_summary)
    podcast_summary = generate_podcast_summary(total_content)
    aggregate_qa = {
        "global_summary": validate_aggregate_grounding(global_summary or "", aggregate_results),
        "headline": validate_aggregate_grounding(micro_summary or "", aggregate_results),
        "podcast_summary": validate_aggregate_grounding(podcast_summary or "", aggregate_results),
    }
    return {
        "passed_results": aggregate_results,
        "global_summary": global_summary,
        "micro_summary": micro_summary,
        "podcast_summary": podcast_summary,
        "aggregate_qa": aggregate_qa,
    }

def extract_key_facts(clean_content):
    prompt = get_prompt("provenance.why")
    facts_generator = SummaryGenerator(clean_content)
    facts_generator.generate_summary(prompt)
    return facts_generator.summary

def render_comic_section(comic):
    if not comic:
        return ""

    image_src = comic.get("image_src") or comic.get("image_url")
    published_label = comic.get("published_label")
    published_text = f"Latest strip published {published_label}" if published_label else "Latest strip"
    comic_title = _escape(comic.get("title", "Latest comic strip"))
    comic_series = _escape(comic.get("series", "Quantum Bits with Quantessa & Atomique"))
    comic_creator = _escape(comic.get("creator", "Yuval Boger"))
    comic_link = _escape(comic.get("link", "#"))
    comic_summary = _escape(comic.get("summary", ""))
    image_src_attr = _escape(image_src) if image_src else ""

    section = (
        "<div style='margin:24px 0; padding:20px; border:1px solid #d6eaf8; border-radius:12px; "
        "background:linear-gradient(135deg, #f7fbff 0%, #edf7f4 100%);'>"
        f"<div style='font-size:0.8em; font-weight:700; letter-spacing:0.08em; text-transform:uppercase; color:#0f766e; margin-bottom:8px;'>{comic_series}</div>"
        f"<h2>{comic_title}</h2>"
        f"<p style='color:#4b5563; margin-bottom:16px;'>{published_text} · by {comic_creator}</p>"
    )

    if image_src:
        section += (
            f"<a href='{comic_link}' target='_blank' rel='noopener noreferrer'>"
            f"<img src='{image_src_attr}' alt='{comic_title}' style='width:100%; height:auto; border-radius:10px; margin:10px 0 18px;' />"
            "</a>"
        )

    if comic_summary:
        section += f"<p>{comic_summary}</p>"

    section += (
        f"<p><a href='{comic_link}' target='_blank' rel='noopener noreferrer'>Read the full comic on Quantum Bits Comics</a></p>"
        "</div>"
    )
    return section


def render_summary_html(summary):
    rendered = _escape(summary)
    for label in ("Finding:", "Evidence:", "Why it matters:"):
        rendered = rendered.replace(_escape(label), f"<strong>{label}</strong>")
    return rendered.replace("\n", "<br>")


def render_article_groups(results):
    sections = []
    for bucket, stories in group_stories(results):
        bucket_label = _escape(STORY_BUCKET_LABELS.get(bucket, "Other Developments"))
        sections.append(
            "<div style='margin-top:24px;'>"
            f"<div style='font-size:0.8em; font-weight:700; letter-spacing:0.08em; text-transform:uppercase; color:#0f766e; margin-bottom:8px;'>{bucket_label}</div>"
        )
        for result in stories:
            if 'url' not in result or 'summary' not in result:
                continue
            article_url = _escape(result.get('url', ''))
            article_title = _escape(result.get('title', ''))
            article_summary = render_summary_html(result.get('summary', ''))
            sections.append(
                f"<div><h3><a href='{article_url}'>{article_title}</a></h3>"
                f"<p>{article_summary}</p>"
                f"<p><a href='{article_url}'>{article_url}</a></p></div>"
            )
        sections.append("</div>")
    return "".join(sections)

def create_newsletter(results, global_summary, micro_summary, podcast_summary, comic=None):
    # Review the content before generating the final HTML
    review_result = review_newsletter_content_api(
        results, global_summary, micro_summary, podcast_summary
    )

    # Do not auto-rewrite generated stories after review unless the original
    # source text is included in the correction pass. Rewriting from newsletter
    # text alone can reintroduce hallucinations.
    final_headline = micro_summary
    final_global_summary = global_summary
    final_podcast_summary = podcast_summary
    final_results = results.copy()
    all_grounded = all(not result.get("qa_flags") for result in final_results)
    
    newsletter = f"<h1>{final_headline}</h1>"
    badge_text = "Verified Content - source-grounding checks passed" if all_grounded else "Needs Review - one or more stories failed source-grounding checks"
    badge_color = "#27ae60" if all_grounded else "#b45309"
    newsletter += f"<div style='background-color: {badge_color}; color: white; padding: 5px 10px; border-radius: 15px; font-size: 0.8em; display: inline-block; margin-bottom: 10px;'>{_escape(badge_text)}</div>"
    newsletter += f"<h2>Quick Recap</h2><p>{final_global_summary}</p>"
    newsletter += render_comic_section(comic)
    newsletter += "</br></br><h2>The Week in Quantum Computing</h2>"
    newsletter += render_article_groups(final_results)
    newsletter += f"</br></br><h2>Podcast Research Content</h2><p>{final_podcast_summary}</p>"
    return newsletter

def apply_overrides(results, overrides):
    if not overrides:
        return results
    updated = []
    for idx, result in enumerate(results):
        story_id = _story_id_for_result(result, idx)
        override = overrides.get(story_id, {})
        if override:
            updated_result = result.copy()
            updated_result["title"] = override.get("title", result["title"])
            updated_result["summary"] = override.get("summary", result["summary"])
            updated.append(updated_result)
        else:
            updated.append(result)
    return updated

def load_run_results(run_id):
    results_path = os.path.join(artifact_store.run_dir(run_id), "results.json")
    data = _load_json(results_path, {})
    return {
        "results": data.get("results", []),
        "global_summary": data.get("global_summary", ""),
        "micro_summary": data.get("micro_summary", ""),
        "podcast_summary": data.get("podcast_summary", ""),
        "comic": data.get("comic")
    }

def load_story_details(run_id, story_id):
    story_dir = artifact_store.story_dir(run_id, story_id)
    qa = _load_json(os.path.join(story_dir, "qa.json"), {})
    provenance = _load_json(os.path.join(story_dir, "provenance.json"), {})
    return qa, provenance

def load_story_artifacts(run_id, story_id):
    story_dir = artifact_store.story_dir(run_id, story_id)
    clean_path = os.path.join(story_dir, "clean.txt")
    raw_path = os.path.join(story_dir, "raw.txt")
    try:
        with open(clean_path, "r", encoding="utf-8") as file:
            clean_text = file.read()
    except Exception:
        clean_text = ""
    try:
        with open(raw_path, "r", encoding="utf-8") as file:
            raw_text = file.read()
    except Exception:
        raw_text = ""
    return {
        "qa": _load_json(os.path.join(story_dir, "qa.json"), {}),
        "provenance": _load_json(os.path.join(story_dir, "provenance.json"), {}),
        "fetch": _load_json(os.path.join(story_dir, "fetch.json"), {}),
        "summary": _load_json(os.path.join(story_dir, "summary.json"), {}),
        "title": _load_json(os.path.join(story_dir, "title.json"), {}),
        "clean_text": clean_text,
        "raw_text": raw_text,
    }

def extract_overrides_from_form(form, results):
    new_overrides = {}
    for idx, result in enumerate(results):
        story_id = _story_id_for_result(result, idx)
        title = form.get(f"title_{story_id}", "").strip()
        summary = form.get(f"summary_{story_id}", "").strip()
        if title or summary:
            new_overrides[story_id] = {
                "title": title,
                "summary": summary
            }
    return new_overrides

def regenerate_story(run_id, story_id, overrides):
    story_dir = artifact_store.story_dir(run_id, story_id)
    clean_path = os.path.join(story_dir, "clean.txt")
    fetch = _load_json(os.path.join(story_dir, "fetch.json"), {})
    url = fetch.get("url", "")
    is_paper = bool(fetch.get("is_paper"))

    try:
        with open(clean_path, "r", encoding="utf-8") as file:
            clean_content = file.read()
    except Exception:
        clean_content = ""

    title_seed = overrides.get(story_id, {}).get("title", "")
    source_metadata = fetch.get("metadata", {})
    summary_result = generate_summary_result(title_seed, clean_content, url, source_metadata)
    summary = summary_result["summary"]

    if not title_seed:
        title_result = generate_title_result(
            fetch.get("input_title", ""),
            clean_content,
            url,
            is_paper,
            source_metadata,
            summary,
        )
        title_seed = title_result.get("title") or generate_title(summary, url, is_paper, source_title=fetch.get("title", ""))

    title_seed = sanitize_story_title(title_seed, is_paper=is_paper)
    qa_result = qa_title_summary(title_seed, summary)
    grounding_result = validate_story_grounding(title_seed, summary, clean_content, source_metadata)
    claim_result = validate_summary_claims(summary, clean_content)
    if not claim_result["passed"]:
        grounding_result["flags"] = list(dict.fromkeys(grounding_result["flags"] + ["summary_claims_not_supported"]))
        grounding_result["passed"] = False
        grounding_result["claim_check"] = claim_result
    if not grounding_result["passed"]:
        summary = failure_summary(summary_result.get("status", ""), grounding_result["flags"])
        qa_result["summary_fixed"] = summary
        qa_result["flags"] = list(dict.fromkeys(qa_result["flags"] + grounding_result["flags"]))
    final_title = qa_result["title_fixed"]
    final_summary = qa_result["summary_fixed"]

    why_log = extract_key_facts(clean_content)

    artifact_store.save_json(run_id, story_id, "summary", {"summary": final_summary, "grounded": summary_result})
    artifact_store.save_json(run_id, story_id, "title", {"title": final_title})
    artifact_store.save_json(run_id, story_id, "qa", qa_result)
    artifact_store.save_json(
        run_id,
        story_id,
        "provenance",
        {"url": url, "title": final_title, "why": why_log, "grounding": grounding_result}
    )

    overrides[story_id] = {"title": final_title, "summary": final_summary}
    artifact_store.save_overrides(run_id, overrides)
    return overrides

def apply_review_recommendations_api(review_result, results, global_summary, micro_summary, podcast_summary):
    """
    Apply review recommendations to ensure quotes and data are verified and outlook/implications are context-based.
    """
    prompt = get_prompt(
        "apply.review.api",
        review_result=review_result
    )
    prompt += (
        "\n\nCURRENT CONTENT:"
        f"\nHeadline: {micro_summary}"
        f"\nGlobal Summary: {global_summary}"
        "\n\nArticles:"
    )
    
    for i, result in enumerate(results):
        if 'url' in result and 'summary' in result:
            prompt += f"\n{i+1}. {result['title']}: {result['summary']}"
    
    prompt += f"\n\nPodcast Summary: {podcast_summary}"
    
    prompt += get_prompt("apply.review.instructions.api")
    
    corrector = SummaryGenerator(prompt)
    corrector.generate_summary("Apply the corrections based on the review recommendations.")
    
    # Parse the corrected content
    if not corrector.summary:
        return {}
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

def review_newsletter_content_api(results, global_summary, micro_summary, podcast_summary):
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
Extracted Page Title: {result.get('source_metadata', {}).get('html_title', '')}
Extracted Page Headline: {result.get('source_metadata', {}).get('h1', '')}
Extraction Status: {result.get('source_metadata', {}).get('extraction_status', '')}
QA Flags: {', '.join(result.get('qa_flags', [])) or 'none'}
Evidence Snippets: {' | '.join(result.get('summary_evidence', []))}
# Image handling removed for newsletter-only text output
---
"""

    review_content += f"""
PODCAST CONTENT:
{podcast_summary}
"""

    prompt = get_prompt("review.newsletter.api")

    reviewer = SummaryGenerator(review_content)
    reviewer.generate_summary(prompt)
    return reviewer.summary

@app.route('/fetch-news', methods=['POST'])
def fetch_news():
    try:
        data = request.json
        days = data.get('days', 7)
        
        if not os.getenv('GOOGLE_SHEET'):
            return jsonify({'error': 'GOOGLE_SHEET environment variable is not set'}), 500
            
        spreadsheet_handler = load_spreadsheet_data(days)
        stories = []
        
        for i, url in enumerate(spreadsheet_handler.urls):
            paper_processor = ScientificPaperProcessor(url)
            is_paper = paper_processor.is_scientific_paper()
            stories.append({
                'id': str(i),
                'title': sanitize_story_title(spreadsheet_handler.titles[i], is_paper=is_paper),
                'url': url,
                'tag': spreadsheet_handler.tags[i]
            })
        
        if not stories:
            return jsonify({'error': 'No stories found for the selected date range'}), 404
            
        return jsonify({'stories': stories})
    except requests.exceptions.RequestException as e:
        print(f"Network error: {e}")
        return jsonify({'error': f'Failed to access Google Sheet: {str(e)}'}), 500
    except ValueError as e:
        print(f"Value error: {e}")
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        print(f"Error fetching news: {e}")
        return jsonify({'error': str(e)}), 500

def run_generation(selected_stories, source="api"):
    run_id = artifact_store.new_run({"source": source})
    results = []

    for index, story in enumerate(selected_stories):
        story_id = str(index)
        paper_processor = ScientificPaperProcessor(story['url'])
        is_paper = paper_processor.is_scientific_paper()

        content_bundle = process_url(story['url'], story.get('title', ''), story.get('summary', ''))
        artifact_store.save_text(run_id, story_id, "raw", content_bundle["raw"])
        artifact_store.save_text(run_id, story_id, "clean", content_bundle["clean"])
        artifact_store.save_json(
            run_id,
            story_id,
            "fetch",
            {
                "url": story["url"],
                "input_title": story.get("title", ""),
                "is_paper": content_bundle["is_paper"],
                "paper_type": content_bundle["paper_type"],
                "metadata": content_bundle.get("metadata", {}),
                "raw_length": len(content_bundle["raw"] or ""),
                "clean_length": len(content_bundle["clean"] or "")
            }
        )

        summary_result = generate_summary_result(
            story.get('title', ''),
            content_bundle["clean"],
            story['url'],
            content_bundle.get("metadata", {}),
        )
        summary = summary_result["summary"]
        artifact_store.save_json(run_id, story_id, "summary", {"summary": summary, "grounded": summary_result})

        if not story.get('title'):
            title_result = generate_title_result(
                story.get('title', ''),
                content_bundle["clean"],
                story['url'],
                is_paper,
                content_bundle.get("metadata", {}),
                summary,
            )
            story['title'] = title_result.get("title") or generate_title(summary, story['url'], is_paper, source_title=story.get('title', ''))
        story['title'] = sanitize_story_title(story['title'], is_paper=is_paper)

        artifact_store.save_json(run_id, story_id, "title", {"title": story["title"]})
        qa_result = qa_title_summary(story["title"], summary)
        grounding_result = validate_story_grounding(
            story["title"],
            summary,
            content_bundle["clean"],
            content_bundle.get("metadata", {}),
        )
        claim_result = validate_summary_claims(summary, content_bundle["clean"])
        if not claim_result["passed"]:
            grounding_result["flags"] = list(dict.fromkeys(grounding_result["flags"] + ["summary_claims_not_supported"]))
            grounding_result["passed"] = False
            grounding_result["claim_check"] = claim_result
        if not grounding_result["passed"]:
            summary = failure_summary(summary_result.get("status", ""), grounding_result["flags"])
            qa_result["summary_fixed"] = summary
            qa_result["flags"] = list(dict.fromkeys(qa_result["flags"] + grounding_result["flags"]))
        artifact_store.save_json(run_id, story_id, "qa", qa_result)
        story["title"] = qa_result["title_fixed"]
        summary = qa_result["summary_fixed"]

        why_log = extract_key_facts(content_bundle["clean"])
        artifact_store.save_json(
            run_id,
            story_id,
            "provenance",
            {
                "url": story["url"],
                "title": story["title"],
                "why": why_log,
                "grounding": grounding_result,
                "summary_evidence": summary_result.get("evidence", []),
            }
        )

        results.append({
            'story_id': story_id,
            'url': story['url'],
            'title': story['title'],
            'summary': summary,
            'tag': story.get('tag', ''),
            'is_paper': bool(content_bundle["is_paper"] or is_paper),
            'paper_type': content_bundle["paper_type"],
            'qa_flags': qa_result.get("flags", []),
            'source_metadata': content_bundle.get("metadata", {}),
            'summary_evidence': summary_result.get("evidence", []),
            'grounding': grounding_result
        })

    results = order_stories(results)
    aggregate_outputs = build_aggregate_outputs(results)
    global_summary = aggregate_outputs["global_summary"]
    micro_summary = aggregate_outputs["micro_summary"]
    podcast_summary = aggregate_outputs["podcast_summary"]
    comic = fetch_latest_quantum_bits_comic(artifact_store.run_dir(run_id))

    artifact_store.save_run_json(
        run_id,
        "results.json",
        {
            "results": results,
            "global_summary": global_summary,
            "micro_summary": micro_summary,
            "podcast_summary": podcast_summary,
            "aggregate_qa": aggregate_outputs["aggregate_qa"],
            "passed_story_ids": [story.get("story_id") for story in aggregate_outputs["passed_results"]],
            "comic": comic
        }
    )

    overrides = artifact_store.load_overrides(run_id)
    results = apply_overrides(results, overrides)
    comic_image_src = None
    if comic and comic.get("image_filename"):
        comic_image_src = build_comic_asset_url(run_id, comic["image_filename"])

    newsletter = create_newsletter(
        results,
        global_summary,
        micro_summary,
        podcast_summary,
        resolve_comic_for_render(comic, comic_image_src)
    )
    artifact_store.save_run_text(run_id, "newsletter.html", newsletter)
    return run_id, newsletter

@app.route('/runs/<run_id>/assets/<path:filename>', methods=['GET'])
def run_asset(run_id, filename):
    return send_from_directory(artifact_store.run_dir(run_id), filename)

@app.route('/generate-newsletter', methods=['POST'])
def generate_newsletter():
    try:
        data = request.json
        selected_stories = data.get('stories', [])
        run_id, newsletter = run_generation(selected_stories, source="api")
        return jsonify({'newsletter': newsletter, 'run_id': run_id})
    except Exception as e:
        print(f"Error generating newsletter: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/admin', methods=['GET'])
def admin_home():
    runs = artifact_store.list_runs()
    items = []
    for run in runs:
        run_id = run.get("run_id", "")
        created_at = run.get("created_at", "")
        run_data = load_run_results(run_id)
        headline = _escape(run_data.get("micro_summary")) or "No headline yet"
        global_summary = _escape(run_data.get("global_summary")) or "No summary yet"
        preview = global_summary[:180] + ("..." if len(global_summary) > 180 else "")
        items.append(
            f"""
            <li class='run-card'>
              <div class='run-meta'>
                <a class='run-link' href='/admin/run/{run_id}'>{run_id}</a>
                <span class='run-date'>{created_at}</span>
              </div>
              <div class='run-headline'>{headline}</div>
              <div class='run-summary'>{preview}</div>
            </li>
            """
        )
    list_html = "\n".join(items) if items else "<li>No runs yet.</li>"
    return f"""
    <html>
      <head>
        <title>Admin - Runs</title>
        <style>
          body {{
            font-family: 'Helvetica Neue', Arial, sans-serif;
            padding: 24px;
            background: #f6f7fb;
            color: #1f2a37;
          }}
          h1 {{ margin-bottom: 6px; }}
          .subtle {{ color: #6b7280; margin-bottom: 18px; }}
          ul {{ padding-left: 0; list-style: none; }}
          .run-card {{
            background: #ffffff;
            border-radius: 12px;
            padding: 16px;
            margin: 12px 0;
            box-shadow: 0 6px 20px rgba(15, 23, 42, 0.08);
          }}
          .run-meta {{ display: flex; justify-content: space-between; align-items: baseline; }}
          .run-link {{ font-weight: 700; color: #1d4ed8; text-decoration: none; }}
          .run-link:hover {{ text-decoration: underline; }}
          .run-date {{ color: #94a3b8; font-size: 0.85em; }}
          .run-headline {{ margin-top: 8px; font-size: 1.05em; font-weight: 600; }}
          .run-summary {{ margin-top: 6px; color: #4b5563; line-height: 1.4; }}
          .actions {{ margin-bottom: 12px; }}
          .actions a {{
            display: inline-block;
            padding: 8px 12px;
            background: #1d4ed8;
            color: white;
            text-decoration: none;
            border-radius: 8px;
          }}
        </style>
      </head>
      <body>
        <h1>Runs</h1>
        <div class="subtle">Manage newsletter runs and review artifacts.</div>
        <div class="actions">
          <a href="/admin/run/new">New run</a>
        </div>
        <ul>{list_html}</ul>
      </body>
    </html>
    """

@app.route('/admin/run/new', methods=['GET', 'POST'])
def admin_run_new():
    if request.method == 'GET':
        return """
        <html>
          <head>
            <title>Admin - New Run</title>
            <style>
              body {
                font-family: 'Helvetica Neue', Arial, sans-serif;
                padding: 24px;
                max-width: 900px;
                margin: 0 auto;
                background: #f6f7fb;
                color: #111827;
              }
              input { padding: 8px; border-radius: 6px; border: 1px solid #e2e8f0; }
              button {
                padding: 10px 16px;
                background: #1d4ed8;
                color: white;
                border: none;
                border-radius: 8px;
                font-weight: 600;
              }
            </style>
          </head>
          <body>
            <h1>New Run</h1>
            <form method="post">
              <label>Days to include:</label>
              <input type="number" name="days" min="1" max="365" value="7" />
              <button type="submit">Load stories</button>
            </form>
          </body>
        </html>
        """

    days = int(request.form.get("days", "7"))
    spreadsheet_handler = load_spreadsheet_data(days)
    rows = []
    for i, url in enumerate(spreadsheet_handler.urls):
        paper_processor = ScientificPaperProcessor(url)
        is_paper = paper_processor.is_scientific_paper()
        title = sanitize_story_title(spreadsheet_handler.titles[i], is_paper=is_paper)
        description = (spreadsheet_handler.summaries[i] if hasattr(spreadsheet_handler, "summaries") else "") or ""
        rows.append(f"""
        <div class='story-card'>
          <label>
            <input type='checkbox' name='story_{i}' checked />
            <strong>{title}</strong>
          </label>
          <div style='font-size:0.85em; color:#666'>{url}</div>
          <label style='display:block; margin-top:8px; font-weight:600;'>Title override</label>
          <input type='text' name='title_{i}' value='{_escape(title)}' />
          <label style='display:block; margin-top:8px; font-weight:600;'>Description</label>
          <textarea name='summary_{i}' rows='3'>{_escape(description)}</textarea>
          <input type='hidden' name='url_{i}' value='{_escape(url)}' />
        </div>
        """)

    rows_html = "\n".join(rows) if rows else "<div class='empty'>No stories found.</div>"
    return f"""
    <html>
      <head>
        <title>Admin - Select Stories</title>
        <style>
          body {{
            font-family: 'Helvetica Neue', Arial, sans-serif;
            padding: 24px;
            max-width: 900px;
            margin: 0 auto;
            background: #f6f7fb;
            color: #111827;
          }}
          .story-card {{
            background: white;
            border-radius: 10px;
            padding: 12px;
            margin: 10px 0;
            box-shadow: 0 4px 12px rgba(15, 23, 42, 0.08);
          }}
          input[type="text"], textarea {{
            width: 100%;
            padding: 8px;
            border-radius: 6px;
            border: 1px solid #e2e8f0;
            font-size: 0.95em;
          }}
          button {{
            padding: 10px 16px;
            background: #1d4ed8;
            color: white;
            border: none;
            border-radius: 8px;
            font-weight: 600;
          }}
          .empty {{ color: #6b7280; }}
          .select-actions {{ margin-bottom: 12px; }}
          .select-actions button {{
            background: #0f172a;
            margin-right: 8px;
          }}
        </style>
      </head>
      <body>
        <h1>Select stories</h1>
        <form method="post" action="/admin/run/execute">
          <input type="hidden" name="days" value="{days}" />
          <div class="select-actions">
            <button type="button" onclick="toggleAll(true)">Select all</button>
            <button type="button" onclick="toggleAll(false)">Select none</button>
          </div>
          {rows_html}
          <button type="submit">Generate newsletter</button>
        </form>
        <script>
          function toggleAll(state) {{
            const boxes = document.querySelectorAll('input[type="checkbox"][name^="story_"]');
            boxes.forEach((box) => {{ box.checked = state; }});
          }}
        </script>
      </body>
    </html>
    """

@app.route('/admin/run/execute', methods=['POST'])
def admin_run_execute():
    selected = []
    index = 0
    while True:
        title_key = f"title_{index}"
        url_key = f"url_{index}"
        summary_key = f"summary_{index}"
        if title_key not in request.form or url_key not in request.form:
            break
        if request.form.get(f"story_{index}") == "on":
            selected.append({
                "title": request.form.get(title_key, ""),
                "url": request.form.get(url_key, ""),
                "summary": request.form.get(summary_key, "")
            })
        index += 1

    if not selected:
        return redirect(url_for("admin_run_new"))

    run_id, _ = run_generation(selected, source="admin")
    return redirect(url_for("admin_run", run_id=run_id))

@app.route('/admin/run/<run_id>', methods=['GET', 'POST'])
def admin_run(run_id):
    run_data = load_run_results(run_id)
    overrides = artifact_store.load_overrides(run_id)

    if request.method == 'POST':
        new_overrides = extract_overrides_from_form(request.form, run_data["results"])
        artifact_store.save_overrides(run_id, new_overrides)
        overrides = new_overrides

    rows = []
    for idx, result in enumerate(run_data["results"]):
        story_id = _story_id_for_result(result, idx)
        qa, provenance = load_story_details(run_id, story_id)
        override = overrides.get(story_id, {})
        title_value = _escape(override.get("title") or result.get("title"))
        summary_value = _escape(override.get("summary") or result.get("summary"))
        qa_flags = _escape(", ".join(qa.get("flags", [])) if qa else "")
        why_log = _escape(provenance.get("why") if provenance else "")

        rows.append(f"""
        <div style='border:1px solid #eee; padding:12px; margin:12px 0;'>
          <div style='font-size:0.9em; color:#666'>Story {idx + 1} · {result.get('url', '')}</div>
          <label style='display:block; font-weight:bold; margin-top:8px;'>Title</label>
          <input type='text' name='title_{story_id}' value='{title_value}' style='width:100%; padding:8px;' />
          <label style='display:block; font-weight:bold; margin-top:8px;'>Summary</label>
          <textarea name='summary_{story_id}' rows='4' style='width:100%; padding:8px;'>{summary_value}</textarea>
          <div style='margin-top:10px;'>
            <button type='submit' name='regen_story' value='{story_id}' formaction='/admin/run/{run_id}/regenerate' formmethod='post'>Regenerate this story</button>
          </div>
          <div style='font-size:0.85em; color:#444; margin-top:8px;'>QA Flags: {qa_flags or 'None'}</div>
          <div style='font-size:0.85em; color:#444; margin-top:6px; white-space:pre-wrap;'>Why log: {why_log or 'None'}</div>
        </div>
        """)

    rows_html = "\n".join(rows)
    headline = _escape(run_data.get("micro_summary")) or "No headline yet"
    global_summary = _escape(run_data.get("global_summary")) or "No summary yet"
    return f"""
    <html>
      <head>
        <title>Admin - Run {run_id}</title>
        <style>
          body {{
            font-family: 'Helvetica Neue', Arial, sans-serif;
            padding: 24px;
            background: #f8fafc;
            color: #111827;
          }}
          .container {{ max-width: 1100px; margin: 0 auto; }}
          h1 {{ margin-bottom: 6px; }}
          .headline {{ font-size: 1.1em; font-weight: 600; margin-top: 6px; }}
          .summary {{ color: #4b5563; margin-top: 6px; line-height: 1.5; }}
          .actions {{ margin: 14px 0 20px; }}
          .actions a {{
            margin-right: 12px;
            text-decoration: none;
            color: #1d4ed8;
            font-weight: 600;
          }}
          .card {{
            background: white;
            border-radius: 12px;
            padding: 14px;
            margin: 12px 0;
            box-shadow: 0 6px 20px rgba(15, 23, 42, 0.08);
          }}
          input[type='text'], textarea {{
            width: 100%;
            padding: 10px;
            border: 1px solid #e2e8f0;
            border-radius: 8px;
            font-size: 0.95em;
          }}
          label {{ display: block; font-weight: 600; margin-top: 8px; }}
          button {{
            padding: 10px 16px;
            background: #1d4ed8;
            color: white;
            border: none;
            border-radius: 8px;
            font-weight: 600;
          }}
        </style>
      </head>
      <body>
        <div class='container'>
          <h1>Run {run_id}</h1>
          <div class='headline'>{headline}</div>
          <div class='summary'>{global_summary}</div>
          <div class='actions'>
            <a href='/admin'>Back to runs</a>
            <a href='/admin/run/{run_id}/review'>Review failed stories</a>
            <a href='/admin/run/{run_id}/preview' target='_blank'>Preview newsletter</a>
          </div>
          <form method='post'>
            {rows_html}
            <button type='submit'>Save overrides</button>
          </form>
        </div>
      </body>
    </html>
    """

@app.route('/admin/run/<run_id>/preview', methods=['GET'])
def admin_preview(run_id):
    run_data = load_run_results(run_id)
    overrides = artifact_store.load_overrides(run_id)
    results = apply_overrides(run_data["results"], overrides)
    comic = run_data.get("comic")
    comic_image_src = None
    if comic and comic.get("image_filename"):
        comic_image_src = build_comic_asset_url(run_id, comic["image_filename"])
    newsletter = create_newsletter(
        results,
        run_data.get("global_summary", ""),
        run_data.get("micro_summary", ""),
        run_data.get("podcast_summary", ""),
        resolve_comic_for_render(comic, comic_image_src)
    )
    return newsletter

@app.route('/admin/run/<run_id>/review', methods=['GET'])
def admin_review_failed_stories(run_id):
    run_data = load_run_results(run_id)
    rows = []
    for idx, result in enumerate(run_data["results"]):
        story_id = _story_id_for_result(result, idx)
        artifacts = load_story_artifacts(run_id, story_id)
        qa = artifacts["qa"]
        provenance = artifacts["provenance"]
        fetch = artifacts["fetch"]
        metadata = fetch.get("metadata", {}) or result.get("source_metadata", {}) or {}
        flags = qa.get("flags") or result.get("qa_flags") or []
        grounding = provenance.get("grounding") or result.get("grounding") or {}
        if not flags and grounding.get("passed", True):
            continue

        evidence = result.get("summary_evidence") or provenance.get("summary_evidence") or []
        claim_check = grounding.get("claim_check") or {}
        unsupported = claim_check.get("unsupported_claims") or []
        clean_preview = _escape(artifacts["clean_text"][:6000])
        raw_preview = _escape(artifacts["raw_text"][:2500])
        evidence_html = "".join(f"<li>{_escape(item)}</li>" for item in evidence) or "<li>No evidence snippets recorded.</li>"
        unsupported_html = "".join(
            f"<li><strong>{_escape(item.get('claim', ''))}</strong><br><span>{_escape(item.get('best_evidence', ''))}</span></li>"
            for item in unsupported
        ) or "<li>No claim-level failures recorded.</li>"

        rows.append(f"""
        <section class='review-card'>
          <div class='story-meta'>Story {idx + 1} · <a href='{_escape(result.get('url', ''))}' target='_blank' rel='noopener noreferrer'>{_escape(result.get('url', ''))}</a></div>
          <h2>{_escape(result.get('title', 'Untitled'))}</h2>
          <div class='flags'>{_escape(', '.join(flags) or 'Grounding failed')}</div>
          <div class='grid'>
            <div>
              <h3>Generated Summary</h3>
              <p>{_escape(result.get('summary', ''))}</p>
              <h3>Evidence Snippets</h3>
              <ul>{evidence_html}</ul>
              <h3>Unsupported Claims</h3>
              <ul>{unsupported_html}</ul>
            </div>
            <div>
              <h3>Source Identity</h3>
              <dl>
                <dt>Submitted URL</dt><dd>{_escape(metadata.get('submitted_url') or fetch.get('url') or result.get('url', ''))}</dd>
                <dt>Final URL</dt><dd>{_escape(metadata.get('final_url') or metadata.get('source_url') or '')}</dd>
                <dt>Page Title</dt><dd>{_escape(metadata.get('html_title', ''))}</dd>
                <dt>Headline</dt><dd>{_escape(metadata.get('h1', ''))}</dd>
                <dt>Extraction</dt><dd>{_escape(metadata.get('extraction_status', ''))} / {_escape(metadata.get('extraction_method', ''))}</dd>
              </dl>
            </div>
          </div>
          <details open>
            <summary>Extracted article text</summary>
            <pre>{clean_preview}</pre>
          </details>
          <details>
            <summary>Raw source preview</summary>
            <pre>{raw_preview}</pre>
          </details>
        </section>
        """)

    rows_html = "\n".join(rows) or "<div class='empty'>No failed stories in this run.</div>"
    return f"""
    <html>
      <head>
        <title>Admin - Review Failed Stories</title>
        <style>
          body {{ font-family: 'Helvetica Neue', Arial, sans-serif; padding: 24px; background: #f8fafc; color: #111827; }}
          main {{ max-width: 1200px; margin: 0 auto; }}
          a {{ color: #1d4ed8; }}
          .review-card {{ background: white; border-radius: 12px; padding: 18px; margin: 16px 0; box-shadow: 0 6px 20px rgba(15, 23, 42, 0.08); }}
          .story-meta {{ color: #64748b; font-size: 0.9em; }}
          .flags {{ display: inline-block; background: #b45309; color: white; padding: 5px 9px; border-radius: 999px; font-size: 0.82em; }}
          .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }}
          dt {{ font-weight: 700; margin-top: 8px; }}
          dd {{ margin-left: 0; color: #334155; overflow-wrap: anywhere; }}
          pre {{ white-space: pre-wrap; background: #0f172a; color: #e2e8f0; padding: 14px; border-radius: 10px; max-height: 460px; overflow: auto; }}
          summary {{ cursor: pointer; font-weight: 700; margin-top: 14px; }}
          .empty {{ background: white; padding: 18px; border-radius: 12px; }}
        </style>
      </head>
      <body>
        <main>
          <p><a href='/admin/run/{run_id}'>Back to run</a></p>
          <h1>Failed Story Review</h1>
          {rows_html}
        </main>
      </body>
    </html>
    """

@app.route('/admin/run/<run_id>/regenerate', methods=['POST'])
def admin_regenerate_story(run_id):
    run_data = load_run_results(run_id)
    overrides = extract_overrides_from_form(request.form, run_data["results"])
    artifact_store.save_overrides(run_id, overrides)

    story_id = request.form.get("regen_story")
    if story_id is None:
        return redirect(url_for("admin_run", run_id=run_id))

    regenerate_story(run_id, story_id, overrides)
    return redirect(url_for("admin_run", run_id=run_id))

if __name__ == '__main__':
    load_dotenv()
    app.run(debug=True, host='127.0.0.1', port=5000) 
