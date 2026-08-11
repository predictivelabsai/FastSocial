from __future__ import annotations

import asyncio
import base64
import inspect
import json
import re
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import select

from fastsocial.config import settings
from fastsocial.models import AIProviderCredential, ModelProfile
from fastsocial.security import decrypt_text

SUPPORTED_PROVIDERS = {"xai", "openai"}
MODEL_PURPOSES = {"text", "image", "video"}


class ModelAccessError(RuntimeError):
    pass


@dataclass(frozen=True)
class ResolvedModel:
    provider: str
    model_name: str
    api_key: str
    base_url: str
    credential_source: str


def default_model(provider: str, purpose: str) -> str:
    cfg = settings()
    if purpose == "text" and provider == cfg.model_provider.strip().lower():
        return cfg.model_name
    if provider == "openai":
        return {
            "text": cfg.openai_model,
            "image": cfg.openai_image_model,
            "video": cfg.openai_video_model,
        }[purpose]
    return {
        "text": cfg.xai_model,
        "image": cfg.image_model,
        "video": cfg.video_model,
    }[purpose]


def resolve_model(
    session,
    *,
    workspace_id,
    user_email: str,
    provider: str,
    purpose: str,
) -> ResolvedModel:
    provider = provider.strip().lower()
    purpose = purpose.strip().lower()
    if provider not in SUPPORTED_PROVIDERS or purpose not in MODEL_PURPOSES:
        raise ModelAccessError("Unsupported model provider or purpose")

    profile = session.scalar(
        select(ModelProfile).where(
            ModelProfile.workspace_id == workspace_id,
            ModelProfile.provider == provider,
            ModelProfile.purpose == purpose,
        )
    )
    credential = session.scalar(
        select(AIProviderCredential).where(
            AIProviderCredential.workspace_id == workspace_id,
            AIProviderCredential.provider == provider,
        )
    )
    if credential:
        api_key = decrypt_text(credential.api_key_encrypted)
        source = "workspace"
    elif settings().server_model_access_allowed(user_email):
        api_key = settings().xai_api_key if provider == "xai" else settings().openai_api_key
        source = "server"
    else:
        raise ModelAccessError(
            "Add your own xAI or OpenAI API key in Integrations to use model features."
        )
    if not api_key:
        raise ModelAccessError(f"No {provider} API key is configured")
    base_url = settings().xai_base_url if provider == "xai" else settings().openai_base_url
    return ResolvedModel(
        provider=provider,
        model_name=profile.model_name if profile else default_model(provider, purpose),
        api_key=api_key,
        base_url=base_url.rstrip("/"),
        credential_source=source,
    )


def build_chat_model(resolved: ResolvedModel, *, temperature: float = 0.4):
    from langchain_openai import ChatOpenAI

    kwargs: dict[str, Any] = {
        "model": resolved.model_name,
        "api_key": resolved.api_key,
        "temperature": temperature,
        "timeout": settings().model_request_timeout,
        "max_retries": 2,
    }
    if resolved.provider == "xai":
        kwargs["base_url"] = resolved.base_url
    else:
        kwargs["use_responses_api"] = True
    return ChatOpenAI(**kwargs)


def _message_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("text"):
                parts.append(str(item["text"]))
        return "\n".join(parts)
    return str(value or "")


def parse_json_response(content: Any) -> dict[str, Any]:
    text = _message_text(content).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S)
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            raise RuntimeError("The model did not return a structured post draft") from exc
        value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise RuntimeError("The model returned an invalid post draft")
    return value


async def invoke_json(resolved: ResolvedModel, *, system_prompt: str, user_prompt: str) -> dict:
    model = build_chat_model(resolved)
    response = await model.ainvoke(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
    )
    return parse_json_response(response.content)


async def test_model_connection(resolved: ResolvedModel) -> list[str]:
    endpoint = "/language-models" if resolved.provider == "xai" else "/models"
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            f"{resolved.base_url}{endpoint}",
            headers={"Authorization": f"Bearer {resolved.api_key}"},
        )
        response.raise_for_status()
        body = response.json()
    values = body.get("models") or body.get("data") or []
    return sorted(
        {str(item.get("id")) for item in values if isinstance(item, dict) and item.get("id")}
    )


async def generate_image_bytes(resolved: ResolvedModel, prompt: str) -> tuple[bytes, str]:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=resolved.api_key, base_url=resolved.base_url)
    arguments = {"model": resolved.model_name, "prompt": prompt}
    if resolved.provider == "xai":
        arguments["response_format"] = "b64_json"
    response = await client.images.generate(**arguments)
    item = response.data[0]
    if getattr(item, "b64_json", None):
        return base64.b64decode(item.b64_json), "image/png"
    if not getattr(item, "url", None):
        raise RuntimeError("The image provider returned no image")
    async with httpx.AsyncClient(timeout=settings().model_request_timeout) as http:
        downloaded = await http.get(item.url)
        downloaded.raise_for_status()
        return downloaded.content, downloaded.headers.get("content-type", "image/png").split(
            ";", 1
        )[0]


async def _download_video(url: str) -> tuple[bytes, str]:
    async with httpx.AsyncClient(timeout=settings().model_request_timeout) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.content, response.headers.get("content-type", "video/mp4").split(";", 1)[0]


async def generate_video_bytes(
    resolved: ResolvedModel,
    prompt: str,
    *,
    duration: int = 8,
) -> tuple[bytes, str, str]:
    if resolved.provider == "xai":
        headers = {
            "Authorization": f"Bearer {resolved.api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=settings().model_request_timeout) as client:
            started = await client.post(
                f"{resolved.base_url}/videos/generations",
                headers=headers,
                json={
                    "model": resolved.model_name,
                    "prompt": prompt,
                    "duration": max(2, min(duration, 15)),
                    "aspect_ratio": "16:9",
                    "resolution": "720p",
                },
            )
            started.raise_for_status()
            request_id = str(started.json().get("request_id") or "")
            if not request_id:
                raise RuntimeError("The video provider returned no request ID")
            for _ in range(90):
                result = await client.get(
                    f"{resolved.base_url}/videos/{request_id}",
                    headers={"Authorization": f"Bearer {resolved.api_key}"},
                )
                result.raise_for_status()
                payload = result.json()
                if payload.get("status") == "done":
                    video_url = (payload.get("video") or {}).get("url")
                    if not video_url:
                        raise RuntimeError("The completed video had no download URL")
                    body, mime = await _download_video(video_url)
                    return body, mime, request_id
                if payload.get("status") in {"failed", "expired"}:
                    raise RuntimeError(str(payload.get("error") or "Video generation failed"))
                await asyncio.sleep(2)
            raise RuntimeError("Video generation timed out")

    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=resolved.api_key, base_url=resolved.base_url)
    videos = getattr(client, "videos", None)
    if videos is None:
        raise RuntimeError("The installed OpenAI SDK does not expose the video API")
    job = await videos.create(
        model=resolved.model_name,
        prompt=prompt,
        seconds=str(max(4, min(duration, 20))),
        size="1280x720",
    )
    request_id = str(job.id)
    for _ in range(90):
        current = await videos.retrieve(request_id)
        if current.status == "completed":
            content = await videos.download_content(request_id)
            body = content.read()
            if inspect.isawaitable(body):
                body = await body
            return body, "video/mp4", request_id
        if current.status in {"failed", "cancelled"}:
            raise RuntimeError("OpenAI video generation failed")
        await asyncio.sleep(2)
    raise RuntimeError("OpenAI video generation timed out")
