import json
import os

_PROMPTS = None

_QUANTUM_PIRATES_STYLE_KEYS = {
    "summary.newsletter",
    "title.paper",
    "title.story",
    "global.summary",
    "newsletter.headline",
    "podcast.summary",
    "paper.analysis",
    "summary.api",
    "title.paper.api",
    "title.story.api",
    "global.summary.api",
    "newsletter.headline.api",
    "podcast.summary.api",
    "review.newsletter",
    "apply.review",
    "apply.review.instructions",
    "review.newsletter.api",
    "apply.review.api",
    "apply.review.instructions.api",
    "year.summary",
}

_QUANTUM_PIRATES_STYLE_GUIDE = """

Quantum Pirates Style Guide (Sergio Gago voice)

Core voice:
- Write like a smart skeptic with a pirate grin: technically sharp, commercially aware, allergic to hype.
- Confident, witty, slightly irreverent, never sloppy.

What it must feel like:
- Skeptical, not cynical.
- Technically literate.
- Commercially grounded.
- Playful but precise.
- Forward-looking.

Writing principles:
- Lead with the punch.
- Call hype by its name.
- Separate signal from noise (science vs engineering vs product vs revenue vs PR).
- Translate complexity without dumbing it down.
- Keep momentum and avoid fluff.
- Always land a concrete takeaway.

Sentence style:
- Prefer short to medium sentences.
- Use direct language.
- Use contrast lines when useful (example: "Impressive science. Dubious timeline.").

Signature moves (sparingly):
- "Here is the important part."
- "Now for the bit people conveniently ignore."
- "This is where the press release fog gets thick."
- "Let us cut through it."
- "Zoom out."
- "The real game is this."

Structure:
1) Hook (clear thesis)
2) What happened
3) Why it matters
4) What is real vs what is theater
5) Bottom line

Humor rules:
- Dry smirk from someone who did the homework.
- No clowning, no meme voice.

Favor words like:
- signal, noise, real, credible, fragile, sober, sharp, brutal, interesting, inconvenient, serious, theatrical, meaningful, speculative

Avoid:
- breathless optimism
- empty futurism
- TED Talk fog
- overexplaining basics
- generic corporate filler
- uncritical use of "synergy", "paradigm shift", or "game-changing" (unless used ironically)

Before finalizing, verify:
- What is the real claim?
- What evidence supports it?
- What is missing?
- Is this science, engineering, product, or PR?
- Why should a smart reader care?
"""


def _load_prompts():
    global _PROMPTS
    if _PROMPTS is None:
        prompt_path = os.path.join(os.path.dirname(__file__), "prompts", "prompts.json")
        with open(prompt_path, "r", encoding="utf-8") as file:
            _PROMPTS = json.load(file)
    return _PROMPTS


def get_prompt(key: str, **kwargs) -> str:
    prompts = _load_prompts()
    if key not in prompts:
        raise KeyError(f"Prompt key not found: {key}")

    prompt = prompts[key]
    if kwargs:
        try:
            prompt = prompt.format(**kwargs)
        except KeyError as exc:
            raise KeyError(f"Missing format key for prompt {key}: {exc}") from exc

    if key in _QUANTUM_PIRATES_STYLE_KEYS:
        prompt = f"{prompt}\n{_QUANTUM_PIRATES_STYLE_GUIDE}"
    return prompt
