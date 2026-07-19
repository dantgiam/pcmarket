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


async def download_attachment(session: aiohttp.ClientSession, token: str, url: str) -> bytes:
    async with session.get(url, headers={"Authorization": token}) as resp:
        resp.raise_for_status()
        return await resp.read()


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
