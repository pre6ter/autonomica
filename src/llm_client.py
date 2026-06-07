"""Клиент к LM Studio (OpenAI-совместимый API) с поддержкой зрения."""
from __future__ import annotations

import json
import re
from typing import Any

from openai import OpenAI


class LLMClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float = 0.1,
        max_tokens: int = 1024,
    ) -> None:
        self.client = OpenAI(base_url=base_url, api_key=api_key)
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

    def complete(self, messages: list[dict[str, Any]]) -> str:
        """Запрос в чат-комплишн. Возвращает сырой текст ответа модели."""
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=messages,  # type: ignore[arg-type]
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        return resp.choices[0].message.content or ""


def build_user_message(text: str, image_data_url: str | None) -> dict[str, Any]:
    """Формирует user-сообщение. Если есть картинка — мультимодальный контент."""
    if image_data_url:
        return {
            "role": "user",
            "content": [
                {"type": "text", "text": text},
                {"type": "image_url", "image_url": {"url": image_data_url}},
            ],
        }
    return {"role": "user", "content": text}


_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def extract_json(raw: str) -> dict[str, Any]:
    """Извлекает JSON-объект из ответа модели, устойчиво к обёрткам и ```json```.

    Многие локальные модели добавляют рассуждения, markdown-блоки или теги
    <think>...</think>. Здесь мы вырезаем их и берём первый валидный JSON-объект.
    """
    text = raw.strip()

    # Убираем reasoning-теги, если модель их выводит
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)

    # Снимаем markdown-ограждение
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    if fence:
        candidate = fence.group(1)
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    # Прямая попытка
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Берём самый «жадный» блок { ... } и пытаемся распарсить, отрезая хвост
    match = _JSON_BLOCK.search(text)
    if match:
        snippet = match.group(0)
        for end in range(len(snippet), 1, -1):
            chunk = snippet[:end]
            if chunk.count("{") <= chunk.count("}"):
                try:
                    return json.loads(chunk)
                except json.JSONDecodeError:
                    continue
    raise ValueError(f"Не удалось извлечь JSON из ответа модели: {raw[:300]!r}")
