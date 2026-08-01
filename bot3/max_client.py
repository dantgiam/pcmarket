# ---------------- Клиент MAX Bot API ----------------
# REST поверх aiohttp, без сторонних SDK (см. bot2/moderation.py — тот же подход).

import aiohttp

API_URL = "https://platform-api.max.ru"


async def get_updates(session: aiohttp.ClientSession, token: str, marker, timeout: int = 30) -> dict:
    """Long polling: возвращает {"updates": [...], "marker": ...}."""
    params = {"timeout": timeout, "limit": 100}
    if marker is not None:
        params["marker"] = marker

    poll_timeout = aiohttp.ClientTimeout(total=timeout + 15)
    async with session.get(
        f"{API_URL}/updates",
        params=params,
        headers={"Authorization": token},
        timeout=poll_timeout,
    ) as resp:
        resp.raise_for_status()
        return await resp.json()


def extract_message(update: dict) -> dict | None:
    """MAX message_created update: сообщение лежит либо в update['message'],
    либо в update['payload']['message'] — схема встречается в обоих видах."""
    if update.get("update_type") != "message_created" and update.get("updateType") != "message_created":
        return None
    return update.get("message") or update.get("payload", {}).get("message")


async def get_chat_by_link(session: aiohttp.ClientSession, token: str, link: str) -> dict:
    """Резолвит канал/чат по публичной ссылке или юзернейму (например
    "channel_adygid" из https://max.ru/channel_adygid) — чтобы не хардкодить
    числовой chat_id в конфиге."""
    async with session.get(
        f"{API_URL}/chats/{link}",
        headers={"Authorization": token},
    ) as resp:
        resp.raise_for_status()
        return await resp.json()


async def get_messages(
    session: aiohttp.ClientSession,
    token: str,
    chat_id: int,
    count: int = 100,
    to: int | None = None,
) -> dict:
    """История сообщений чата/канала (новые сначала). Пагинация — через `to`
    (метка времени самого старого уже полученного сообщения минус 1): MAX не
    отдаёт marker для истории сообщений (в отличие от списка чатов)."""
    params: dict = {"chat_id": chat_id, "count": count}
    if to is not None:
        params["to"] = to

    async with session.get(
        f"{API_URL}/messages",
        params=params,
        headers={"Authorization": token},
    ) as resp:
        resp.raise_for_status()
        return await resp.json()


async def download_attachment(session: aiohttp.ClientSession, token: str, url: str) -> bytes:
    async with session.get(url, headers={"Authorization": token}) as resp:
        resp.raise_for_status()
        return await resp.read()


async def get_me(session: aiohttp.ClientSession, token: str) -> dict:
    async with session.get(f"{API_URL}/me", headers={"Authorization": token}) as resp:
        resp.raise_for_status()
        return await resp.json()


async def send_message(
    session: aiohttp.ClientSession,
    token: str,
    chat_id: int,
    text: str,
    attachments: list | None = None,
    reply_to_mid: str | None = None,
) -> str | None:
    """Отправляет сообщение, возвращает mid отправленного сообщения (для reply-threading)."""
    body = {"text": text or ""}
    if attachments:
        body["attachments"] = attachments
    if reply_to_mid:
        body["link"] = {"type": "reply", "mid": reply_to_mid}

    async with session.post(
        f"{API_URL}/messages",
        params={"chat_id": chat_id},
        headers={"Authorization": token},
        json=body,
    ) as resp:
        resp.raise_for_status()
        data = await resp.json()

    mid = data.get("body", {}).get("mid") or data.get("message", {}).get("body", {}).get("mid")
    if not mid:
        print(f"⚠️ Не удалось извлечь mid из ответа на отправку сообщения: {data}")
    return mid


async def upload_image(session: aiohttp.ClientSession, token: str, image_bytes: bytes) -> str:
    """Загружает фото в MAX, возвращает token для вложения в сообщение."""
    async with session.post(
        f"{API_URL}/uploads",
        params={"type": "image"},
        headers={"Authorization": token},
    ) as resp:
        resp.raise_for_status()
        upload = await resp.json()

    form = aiohttp.FormData()
    form.add_field("data", image_bytes, filename="photo.jpg", content_type="image/jpeg")

    async with session.post(upload["url"], data=form) as resp:
        resp.raise_for_status()
        uploaded = await resp.json()

    photos = uploaded.get("photos")
    if photos:
        return next(iter(photos.values()))["token"]
    return uploaded["token"]


async def delete_message(session: aiohttp.ClientSession, token: str, message_id: str) -> None:
    async with session.delete(
        f"{API_URL}/messages",
        params={"message_id": message_id},
        headers={"Authorization": token},
    ) as resp:
        resp.raise_for_status()


async def ban_member(session: aiohttp.ClientSession, token: str, chat_id: int, user_id: int) -> None:
    async with session.delete(
        f"{API_URL}/chats/{chat_id}/members",
        params={"user_id": user_id, "block": "true"},
        headers={"Authorization": token},
    ) as resp:
        resp.raise_for_status()


async def get_admin_ids(session: aiohttp.ClientSession, token: str, chat_id: int) -> set:
    """id админов и владельца чата. При ошибке — пустое множество (VK-релей просто не сработает)."""
    try:
        async with session.get(
            f"{API_URL}/chats/{chat_id}/members/admins",
            headers={"Authorization": token},
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
    except Exception as e:
        print(f"⚠️ Не удалось получить список админов чата {chat_id}: {e}")
        return set()

    # MAX API не всегда единообразен в регистре полей (userId/user_id) —
    # подстраховываемся обоими вариантами.
    return {
        m.get("userId") or m.get("user_id")
        for m in data.get("members", [])
        if m.get("isAdmin") or m.get("is_admin") or m.get("isOwner") or m.get("is_owner")
    }
