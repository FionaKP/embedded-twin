"""Claude API client for the model factory.

Uses the official Anthropic SDK with claude-opus-5, adaptive thinking
(the model's default), streaming (drafts can be long), and server-side
refusal fallbacks. Credentials resolve from the environment or an
`ant auth login` profile — the zero-arg client handles both.
"""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

MODEL = "claude-opus-5"


class ClaudeLLM:
    def __init__(self, model: str = MODEL):
        import anthropic  # lazy: the rest of twin never needs it
        self._anthropic = anthropic
        self.client = anthropic.Anthropic()
        self.model = model

    def complete(self, system: str, user_content: list[dict[str, Any]] | str,
                 max_tokens: int = 64000) -> str:
        if isinstance(user_content, str):
            user_content = [{"type": "text", "text": user_content}]
        try:
            with self.client.beta.messages.stream(
                model=self.model,
                max_tokens=max_tokens,
                output_config={"effort": "high"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                system=system,
                messages=[{"role": "user", "content": user_content}],
            ) as stream:
                response = stream.get_final_message()
        except self._anthropic.AuthenticationError:
            raise RuntimeError(
                "No usable Anthropic credentials. Run `ant auth login` or set "
                "ANTHROPIC_API_KEY, then retry.") from None
        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            raise RuntimeError(f"model declined the request: "
                               f"{getattr(details, 'explanation', 'no detail')}")
        return "".join(b.text for b in response.content if b.type == "text")


def datasheet_content(path: str | Path) -> list[dict[str, Any]]:
    """Datasheet file -> user content blocks (PDF as document, else text)."""
    path = Path(path)
    if path.suffix.lower() == ".pdf":
        data = base64.standard_b64encode(path.read_bytes()).decode()
        return [{"type": "document",
                 "source": {"type": "base64", "media_type": "application/pdf",
                            "data": data}}]
    return [{"type": "text", "text": path.read_text(errors="replace")}]


def parse_json_reply(text: str) -> dict:
    """Parse a JSON object from a model reply, tolerating code fences."""
    import json
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.rsplit("```", 1)[0] if "```" in text else text
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < 0:
        raise ValueError(f"no JSON object in reply: {text[:200]!r}")
    return json.loads(text[start:end + 1])
