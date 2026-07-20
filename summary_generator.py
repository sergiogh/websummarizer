from openai import OpenAI
from typing import Dict, Optional
import os
import html
import json
import re
import unicodedata

def normalize_text(text: Optional[str]) -> Optional[str]:
    if not text:
        return text

    cleaned = html.unescape(text)
    cleaned = cleaned.replace("\u00a0", " ").strip()

    def mojibake_score(value: str) -> int:
        return len(re.findall(r"[ÃÂâ€�]", value))

    try:
        fixed = cleaned.encode("latin1").decode("utf-8")
        if mojibake_score(fixed) < mojibake_score(cleaned):
            cleaned = fixed
    except UnicodeEncodeError:
        pass
    except UnicodeDecodeError:
        pass

    return unicodedata.normalize("NFC", cleaned).strip()

class SummaryGenerator:
    def __init__(self, content: str):
        self.content: str = content
        self.summary: Optional[str] = None
        self.client = OpenAI(
            api_key=os.getenv('OPENAI_API_KEY'),
            timeout=float(os.getenv("OPENAI_REQUEST_TIMEOUT_SECONDS", "45")),
            max_retries=int(os.getenv("OPENAI_REQUEST_MAX_RETRIES", "0")),
        )
        self.model = os.getenv('OPENAI_MODEL', 'gpt-4.1')

    def generate_summary(self, prompt) -> None:
        """Generate a summary of the content."""
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "system", "content": self.content[:8000]},
                    {"role": "user", "content": ""}
                ]
            )
            
            self.summary = response.choices[0].message.content
            self.summary = normalize_text(self.summary)
            
        except Exception as e:
            print(f"Error generating summary: {e}")
            self.summary = None

    def generate_json_summary(self, prompt: str, user_payload: str) -> Dict[str, object]:
        """Generate and parse a JSON response.

        Some configured models may not support strict JSON mode in the Chat
        Completions API, so the parser also tolerates fenced or prefaced JSON.
        """
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": user_payload},
                ],
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content or "{}"
            self.summary = normalize_text(content)
            return _parse_json_object(content)
        except Exception as e:
            print(f"Error generating structured summary: {e}")
            self.summary = None
            return {}


def _parse_json_object(content: str) -> Dict[str, object]:
    text = (content or "").strip()
    if not text:
        return {}
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE | re.DOTALL).strip()
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            return {}
        try:
            parsed = json.loads(match.group(0))
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
