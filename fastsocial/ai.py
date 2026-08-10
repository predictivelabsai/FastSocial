from __future__ import annotations

import json

from openai import AsyncOpenAI

from fastsocial.config import settings


async def generate_variants(prompt: str, tone: str = "clear and useful") -> dict:
    cfg = settings()
    if not cfg.xai_api_key:
        raise RuntimeError("XAI_API_KEY is not configured")
    client = AsyncOpenAI(api_key=cfg.xai_api_key, base_url=cfg.xai_base_url)
    response = await client.chat.completions.create(
        model=cfg.xai_model,
        temperature=0.7,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": (
                    "You are FastSocial's editorial assistant. Return JSON with keys text, x, "
                    "linkedin, bluesky, and hashtags. Preserve factual claims; never invent URLs or results. "
                    "Respect limits: X 280 characters and Bluesky 300 characters."
                ),
            },
            {"role": "user", "content": f"Tone: {tone}\nDraft or notes:\n{prompt}"},
        ],
    )
    content = response.choices[0].message.content or "{}"
    return json.loads(content)
