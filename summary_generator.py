import openai
from typing import Optional
import os

class SummaryGenerator:
    def __init__(self, content: str):
        self.content: str = content
        self.summary: Optional[str] = None

    def generate_summary(self, prompt) -> None:
        """Generate a summary of the content."""

        openai.api_key = os.getenv('OPENAI_API_KEY')
        try:
            response = openai.ChatCompletion.create(
                model='o1',
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
