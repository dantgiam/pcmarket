# ---------------- Магазины ----------------
# Единый источник данных о магазинах.
# Используется в auto_reply.py (автоответы про адрес / график / MAX).

import re

SHOPS = {
    -1003450185997: {
        "address": "📍 Наш адрес: Майкоп, ул. Строителей 8Б (район железного рынка)",
        "work_time": "🕒 Мы работаем: 10:00–19:00 каждый день!",
        "max_link": "📱 Мы есть в MAX, [нажмите сюда](https://max.ru/join/IMHKjeOxfKJFcRQTQVrhlCGvLx-qOzAUiTpxCussSr0) чтобы перейти в группу. \n 🖥 Так же есть сайт с адресами и группами других точек: https://polcenimarket.ru/",
        "tg_link": "https://t.me/PolCenimarketMaykop",
    },
    -1003777692701: {
        "address": "📍 Наш адрес: Майкоп, ул. Депутатская 16Б",
        "work_time": "🕒 Мы работаем: 10:00–20:00 каждый день!",
        "max_link": "📱 Мы есть в MAX, [нажмите сюда](https://max.ru/join/WZ8T-qgVdTK7He20c2UAvDcawKYbedKxKFmKVZbWovo) чтобы перейти в группу. \n 🖥 Так же есть сайт с адресами и группами других точек: https://polcenimarket.ru/",
        "tg_link": "https://t.me/polcenimarketmaikop1",
    },
    -1003974367383: {
        "address": "📍 Наш адрес: Тульский, ул. Октябрьская 24в",
        "work_time": "🕒 Мы работаем: 10:00–19:00 каждый день!",
        "max_link": "📱 Мы есть в MAX, [нажмите сюда](https://max.ru/join/lmh-4y6OUJs3oPywN225Hq2uXW66nPcSX56gRBsFHKo) чтобы перейти в группу. \n 🖥 Так же есть сайт с адресами и группами других точек: https://polcenimarket.ru/",
        "tg_link": "https://t.me/polcenimarkettulskiy",
    },
    -1003840431977: {
        "address": "📍 Наш адрес: Лабинск, ул. Победы 161",
        "work_time": "🕒 Мы работаем: 09:00–18:00 каждый день!",
        "max_link": "📱 Мы есть в MAX, [нажмите сюда](https://max.ru/join/caMNU_JQa9Q1-UlwqS1r6G9AECURkQn0ARdLGtM25wI) чтобы перейти в группу. \n 🖥 Так же есть сайт с адресами и группами других точек: https://polcenimarket.ru/",
        "tg_link": "https://t.me/polcenimarketlabinsk",
    },
    -1003973787679: {
        "address": "📍 Наш адрес: Усть-Лабинск, ул. Октябрьская 105",
        "work_time": "🕒 Мы работаем: 10:00–19:00 каждый день!",
        "max_link": "📱 Мы есть в MAX, [нажмите сюда](https://max.ru/join/5Vo1w5KZaGoAnbRCoZRcShcjQif2Qz99iLQrSux6j0Y) чтобы перейти в группу. \n 🖥 Так же есть сайт с адресами и группами других точек: https://polcenimarket.ru/",
        "tg_link": "https://t.me/polcenimarket1",
    },
    -1003694773601: {
        "address": "📍 Наш адрес: Белореченск, ул. Дундича 1А",
        "work_time": "🕒 Мы работаем: 08:00–19:00 каждый день!",
        "max_link": "📱 Мы есть в MAX, [нажмите сюда](https://max.ru/join/XjYfUfeZb9suqYSMjFki3-xv8qUd2DOYj7AC5PbJBKk) чтобы перейти в группу. \n 🖥 Так же есть сайт с адресами и группами других точек: https://polcenimarket.ru/",
        "tg_link": "https://t.me/polcenimarketfresh",
    },
    -1003926086656: {
        "address": "📍 Наш адрес: Краснодар, ул. Уральская 156А",
        "work_time": "🕒 Мы работаем: 10:00–19:00 каждый день!",
        "max_link": "📱 Мы есть в MAX, [нажмите сюда](https://max.ru/join/NS9LjzGRRQA9YVoqNpKRMGXedgLYCWBR1ZOkGtsW8YY) чтобы перейти в группу. \n 🖥 Так же есть сайт с адресами и группами других точек: https://polcenimarket.ru/",
        "tg_link": "https://t.me/+VXcieic29NxmMjc6",
    },
    -1003992433513: {
        "address": "📍 Наш адрес: Тимашевск, ул. Ленина 65",
        "work_time": "🕒 Мы работаем: 10:00–19:00 каждый день!",
        "max_link": "📱 Мы есть в MAX, [нажмите сюда](https://max.ru/join/0_Ic8E5Idr21QH6DrlJZwbV2bya1ppo0_Sc0Nq5tFAc) чтобы перейти в группу. \n 🖥 Так же есть сайт с адресами и группами других точек: https://polcenimarket.ru/",
        "tg_link": "https://t.me/polcenimarkettimash",
    },
    -1003929344550: {
        "address": "📍 Наш адрес: Мостовской, ул. Аэродромная 2А",
        "work_time": "🕒 Мы работаем: 10:00–19:00 каждый день!",
        "max_link": "📱 Мы есть в MAX, [нажмите сюда](https://max.ru/join/KEDNP1v6ISWU50sxOUfUCvJC5sxNTqLCxAilAPzW_kQ) чтобы перейти в группу. \n 🖥 Так же есть сайт с адресами и группами других точек: https://polcenimarket.ru/",
        "tg_link": "https://t.me/polcenimarketmosti",
    },
    -1003934880016: {
        "address": "📍 Наш адрес: Ханская, ул. Верещагина 2Л",
        "work_time": "🕒 Мы работаем: 10:00–19:00 каждый день!",
        "max_link": "📱 Мы есть в MAX, [нажмите сюда](https://max.ru/join/vixPGFHUH9vru8ZeXx2wiM4_EflQ1cKVmL_bTOF3pUk) чтобы перейти в группу. \n 🖥 Так же есть сайт с адресами и группами других точек: https://polcenimarket.ru/",
        "tg_link": "https://t.me/polcenimarkethanskaya",
    },
    -1004409128265: {
        "address": "📍 Наш адрес: Краснодар, ул. Калинина 327/2",
        "work_time": "🕒 Мы работаем: 10:00–21:00 каждый день!",
        "max_link": "📱 Мы есть в MAX, [нажмите сюда](https://max.ru/join/G2E9OErnlhV7TkNlf02X0qNfZf9-CoCjw_20r2zn6fo) чтобы перейти в группу. \n 🖥 Так же есть сайт с адресами и группами других точек: https://polcenimarket.ru/",
        "tg_link": "https://t.me/polcenimarketkras",
    },
    -1003893903704: {
        "address": "📍 Наш адрес: Великовечное, ул. Восточная 33",
        "work_time": "🕒 Мы работаем: 09:00–18:00 каждый день!",
        "max_link": "📱 Мы есть в MAX, [нажмите сюда](https://max.ru/join/mNnm6zV6jPYFCPRF7MGnF-x0SzoN3vdM9-yQyqXgMDY) чтобы перейти в группу. \n 🖥 Так же есть сайт с адресами и группами других точек: https://polcenimarket.ru/",
        "tg_link": "https://t.me/polcenimarketveliko",
    },
}

# Собственные ссылки сети (чаты TG/MAX любого магазина + сайт). Модерация не
# должна удалять сообщение только за то, что участник поделился ссылкой на
# одну из НАШИХ групп (например, в ответ на просьбу другого участника) —
# в отличие от рекламы сторонних каналов/групп.
_OWN_LINKS = {shop["tg_link"] for shop in SHOPS.values()} | {"https://polcenimarket.ru/"} | {
    match.group(0)
    for shop in SHOPS.values()
    for match in [re.search(r"https://max\.ru/join/\S+?(?=[)\s]|$)", shop["max_link"])]
    if match
}


def contains_own_link(text: str) -> bool:
    """True, если в тексте есть ссылка на один из наших чатов/сайт."""
    if not text:
        return False
    return any(link in text for link in _OWN_LINKS)
