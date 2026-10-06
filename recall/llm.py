"""AI providers. Each takes a system prompt and neutral content blocks and yields text deltas.

Neutral blocks: {"type": "text", "text": str} | {"type": "image", "media_type": str, "data": base64-str}
"""

from __future__ import annotations

from collections.abc import Iterator

from .config import Settings

# Models that accept the server-side refusal fallback ("default" routing by refusal category).
FALLBACK_MODELS = {"claude-opus-5-5", "claude-fable-5-1", "claude-opus-5", "claude-sonnet-5-5"}


class LLMError(Exception):
    pass


def stream_answer(settings: Settings, system: str, blocks: list[dict], max_tokens: int) -> Iterator[str]:
    if settings.provider == "anthropic":
        yield from _stream_anthropic(settings, system, blocks, max_tokens)
    elif settings.provider == "openai":
        yield from _stream_openai(settings, system, blocks, max_tokens)
    else:
        raise LLMError("No AI provider configured")


def _stream_anthropic(settings: Settings, system: str, blocks: list[dict], max_tokens: int) -> Iterator[str]:
    import anthropic

    content = []
    for b in blocks:
        if b["type"] == "text":
            content.append({"type": "text", "text": b["text"]})
        else:
            content.append(
                {"type": "image", "source": {"type": "base64", "media_type": b["media_type"], "data": b["data"]}}
            )
    client = anthropic.Anthropic(api_key=settings.effective_api_key() or None)
    kwargs: dict = dict(
        model=settings.model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": content}],
    )
    if not settings.model.startswith("claude-haiku"):
        kwargs["output_config"] = {"effort": settings.effort}
    try:
        if settings.model in FALLBACK_MODELS:
            # If the primary model declines, the API re-runs the request on a fallback model.
            ctx = client.beta.messages.stream(
                **kwargs, betas=["server-side-fallback-2026-07-01"], fallbacks="default"
            )
        else:
            ctx = client.messages.stream(**kwargs)
        with ctx as stream:
            for text in stream.text_stream:
                yield text
            final = stream.get_final_message()
        if final.stop_reason == "refusal":
            yield "\n\n> The model declined to answer this request."
        elif final.stop_reason == "max_tokens":
            yield "\n\n> _Answer truncated: the output token limit was reached._"
    except anthropic.AuthenticationError:
        raise LLMError("Anthropic rejected the API key. Check it in Settings.")
    except anthropic.PermissionDeniedError as e:
        raise LLMError(f"Permission denied by Anthropic API: {e.message}")
    except anthropic.NotFoundError:
        raise LLMError(f"Model '{settings.model}' was not found. Check the model name in Settings.")
    except anthropic.RateLimitError:
        raise LLMError("Rate limited by the Anthropic API. Try again shortly.")
    except anthropic.BadRequestError as e:
        raise LLMError(f"Anthropic API rejected the request: {e.message}")
    except anthropic.APIStatusError as e:
        raise LLMError(f"Anthropic API error {e.status_code}: {e.message}")
    except anthropic.APIConnectionError:
        raise LLMError("Could not reach the Anthropic API. Check your network connection.")


def _stream_openai(settings: Settings, system: str, blocks: list[dict], max_tokens: int) -> Iterator[str]:
    import openai

    content = []
    for b in blocks:
        if b["type"] == "text":
            content.append({"type": "text", "text": b["text"]})
        else:
            content.append({"type": "image_url", "image_url": {"url": f"data:{b['media_type']};base64,{b['data']}"}})
    client = openai.OpenAI(api_key=settings.effective_api_key() or "not-needed", base_url=settings.base_url or None)
    try:
        stream = client.chat.completions.create(
            model=settings.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": content}],
            max_tokens=max_tokens,
            stream=True,
        )
        for ev in stream:
            if ev.choices and ev.choices[0].delta and ev.choices[0].delta.content:
                yield ev.choices[0].delta.content
    except openai.AuthenticationError:
        raise LLMError("The AI endpoint rejected the API key. Check it in Settings.")
    except openai.NotFoundError:
        raise LLMError(f"Model '{settings.model}' was not found at the configured endpoint.")
    except openai.APIStatusError as e:
        raise LLMError(f"AI endpoint error {e.status_code}: {e.message}")
    except openai.APIConnectionError:
        raise LLMError(f"Could not reach the AI endpoint {settings.base_url or 'api.openai.com'}.")


CAPTION_PROMPT = (
    "Describe this image from someone's notes in one or two sentences so it can be found by search later. "
    "Say what kind of image it is (diagram, screenshot, chart, photo…), what it shows, and include any "
    "important visible labels, names or numbers. Reply with the description only."
)


def caption_image(settings: Settings, data_b64: str, media_type: str) -> str:
    """Short searchable description of one image, using the configured provider."""
    model = settings.caption_model or settings.model
    blocks = [{"type": "image", "media_type": media_type, "data": data_b64}, {"type": "text", "text": CAPTION_PROMPT}]
    s = Settings(**{**settings.__dict__, "model": model, "effort": "low"})
    text = "".join(stream_answer(s, "You write concise, factual image descriptions.", blocks, 1024))
    # drop the refusal / truncation notes that stream_answer appends as blockquotes
    return "\n".join(line for line in text.splitlines() if not line.startswith("> ")).strip()
