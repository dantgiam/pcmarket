# ---------------- Клиент VK API (публикация на стену группы) ----------------

import aiohttp

API_URL = "https://api.vk.com/method"
API_VERSION = "5.199"


async def _call(session: aiohttp.ClientSession, method: str, token: str, **params) -> dict:
    params.update({"access_token": token, "v": API_VERSION})
    async with session.post(f"{API_URL}/{method}", data=params) as resp:
        resp.raise_for_status()
        data = await resp.json()

    if "error" in data:
        raise RuntimeError(f"VK API {method}: {data['error']}")

    return data["response"]


async def _upload_photo(session: aiohttp.ClientSession, token: str, group_id: int, photo_bytes: bytes) -> str:
    """Загружает фото на стену группы, возвращает attachment-строку "photo{owner}_{id}"."""
    upload = await _call(session, "photos.getWallUploadServer", token, group_id=group_id)

    form = aiohttp.FormData()
    form.add_field("photo", photo_bytes, filename="photo.jpg", content_type="image/jpeg")

    async with session.post(upload["upload_url"], data=form) as resp:
        resp.raise_for_status()
        uploaded = await resp.json()

    saved = await _call(
        session, "photos.saveWallPhoto", token,
        group_id=group_id,
        photo=uploaded["photo"],
        server=uploaded["server"],
        hash=uploaded["hash"],
    )
    photo = saved[0]
    return f"photo{photo['owner_id']}_{photo['id']}"


async def post_to_wall(
    session: aiohttp.ClientSession,
    token: str,
    group_id: int,
    text: str,
    photos: list[bytes],
) -> None:
    """Публикует пост на стену группы. Молча ничего не делает без фото."""
    if not photos:
        return

    attachments = []
    for photo_bytes in photos:
        attachments.append(await _upload_photo(session, token, group_id, photo_bytes))

    await _call(
        session, "wall.post", token,
        owner_id=-group_id,
        from_group=1,
        message=text or "",
        attachments=",".join(attachments),
    )
