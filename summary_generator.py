import openai
from typing import Optional

class SummaryGenerator:
    def __init__(self, content: str):
        self.content: str = content
        self.summary: Optional[str] = None

    def generate_summary(self, prompt) -> None:
        """Generate a summary of the content using the GPT4 API."""

        openai.api_key = "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"
        try:
            response = openai.ChatCompletion.create(
                model='gpt-4',
                temperature=0.5,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "system", "content": self.content[:8000]},
                    {"role": "user", "content": ""}
                ]
            )
            
            self.summary = response['choices'][0]['message']['content']

        except Exception as e:
            print(f"Error generating summary: {e}")
            self.summary = None
