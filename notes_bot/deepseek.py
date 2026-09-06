# ---------------- Тонкий клиент DeepSeek ----------------
# Тот же подход, что в bot2/moderation.py: aiohttp, temperature=0, ретраи на
# 429/5xx. Отличие — здесь всегда ждём JSON, поэтому разбор ответа вынесен
# в отдельную функцию и сделан устойчивым: модель периодически заворачивает
# JSON в ```-блок или добавляет фразу до/после.

import asyncio
import json

import aiohttp

from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL, DEEPSEEK_URL

ATTEMPTS = 3

# Результаты одной попытки: продолжать, повторить, сдаться.
_RETRY = object()
_FAIL = object()


def _extract_json(content: str):
    """Достаёт JSON из ответа модели. Возвращает dict/list или None."""
    if not content:
        return None

    text = content.strip()

    # ```json ... ``` или просто ``` ... ```
    if text.startswith("```"):
        text = text[3:]
        if text.lower().startswith("json"):
            text = text[4:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    try:
        return json.loads(text)
    except Exception:
        pass

    # Последняя попытка: вырезать самый внешний объект или массив.
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except Exception:
                continue

    return None


async def _post(session, headers, payload, timeout, attempt):
    async with session.post(DEEPSEEK_URL, headers=headers, json=payload,
                            timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
        if resp.status != 200:
            body = (await resp.text())[:300]
            print(f"⚠️ DeepSeek статус {resp.status} (попытка {attempt}): {body}")
            # 4xx кроме 429 повторять бессмысленно — запрос кривой.
            return _RETRY if (resp.status >= 500 or resp.status == 429) else _FAIL

        data = await resp.json()
        return (data["choices"][0]["message"]["content"] or "").strip()


async def chat(system: str, user: str, *, temperature: float = 0.0,
               max_tokens: int | None = None, timeout: int = 90,
               session: aiohttp.ClientSession | None = None) -> str | None:
    """Сырой ответ модели строкой. None — если не удалось получить ответ.

    session можно передать снаружи (импорт истории гоняет сотни запросов
    подряд — там переиспользование соединения заметно экономит время)."""
    if not DEEPSEEK_API_KEY:
        print("⚠️ DEEPSEEK_API_KEY не задан — классификация и отчёты работать не будут")
        return None

    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": DEEPSEEK_MODEL,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if max_tokens:
        payload["max_tokens"] = max_tokens

    for attempt in range(1, ATTEMPTS + 1):
        try:
            if session is not None:
                result = await _post(session, headers, payload, timeout, attempt)
            else:
                async with aiohttp.ClientSession() as own:
                    result = await _post(own, headers, payload, timeout, attempt)
        except Exception as e:
            print(f"⚠️ DeepSeek: ошибка запроса (попытка {attempt}): {type(e).__name__}: {e}")
            await asyncio.sleep(attempt * 2)
            continue

        if result is _FAIL:
            return None
        if result is _RETRY:
            await asyncio.sleep(attempt * 2)
            continue
        return result

    print("⚠️ DeepSeek: все попытки исчерпаны")
    return None


async def chat_json(system: str, user: str, **kwargs):
    """Ответ модели, разобранный в dict/list. None — если не получилось."""
    content = await chat(system, user, **kwargs)
    if content is None:
        return None

    parsed = _extract_json(content)
    if parsed is None:
        print(f"⚠️ DeepSeek: ответ не разобрался как JSON: {content[:300]}")
    return parsed
