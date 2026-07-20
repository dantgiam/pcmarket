# ---------------- Релей MAX -> Telegram + VK ----------------
# Конфиг по магазинам. Ключ словаря — chat_id MAX-чата этого магазина.
#
# vk_group_id — плейсхолдер (0), пока не заведена VK-группа под магазин:
#   - берётся из настроек/URL созданной VK-группы (id без минуса);
#   - vk_token_env — имя переменной окружения с токеном сообщества VK
#     (Управление группой -> Работа с API -> Ключи доступа, права wall+photos),
#     саму переменную нужно завести в настройках Railway (Variables).

RELAY_SHOPS = {
    -70930948336178: {
        "name": "Майкоп, ул. Строителей 8Б",
        "tg_chat_id": -1003450185997,
        "vk_group_id": 0,
        "vk_token_env": "VK_TOKEN_MAYKOP_STROITELEY",
    },
    -72381488468530: {
        "name": "Майкоп, ул. Депутатская 16Б",
        "tg_chat_id": -1003777692701,
        "vk_group_id": 0,
        "vk_token_env": "VK_TOKEN_MAYKOP_DEPUTATSKAYA",
    },
    -72141008728275: {
        "name": "Тульский",
        "tg_chat_id": -1003974367383,
        "vk_group_id": 0,
        "vk_token_env": "VK_TOKEN_TULSKY",
    },
    -72381154038322: {
        "name": "Лабинск",
        "tg_chat_id": -1003840431977,
        "vk_group_id": 0,
        "vk_token_env": "VK_TOKEN_LABINSK",
    },
    -74407775081010: {
        "name": "Усть-Лабинск",
        "tg_chat_id": -1003973787679,
        "vk_group_id": 0,
        "vk_token_env": "VK_TOKEN_UST_LABINSK",
    },
    -74889062879794: {
        "name": "Белореченск",
        "tg_chat_id": -1003694773601,
        "vk_group_id": 0,
        "vk_token_env": "VK_TOKEN_BELORECHENSK",
    },
    -75072191828530: {
        "name": "Краснодар, ул. Уральская 156А",
        "tg_chat_id": -1003926086656,
        "vk_group_id": 0,
        "vk_token_env": "VK_TOKEN_KRASNODAR_URALSKAYA",
    },
    -75248258552370: {
        "name": "Тимашевск",
        "tg_chat_id": -1003992433513,
        "vk_group_id": 0,
        "vk_token_env": "VK_TOKEN_TIMASHEVSK",
    },
    -75248220148274: {
        "name": "Мостовской",
        "tg_chat_id": -1003929344550,
        "vk_group_id": 0,
        "vk_token_env": "VK_TOKEN_MOSTOVSKOY",
    },
    # VK: переиспользуем группу/токен, ранее заведённые под Строителей 8Б.
    -76576015528624: {
        "name": "Ханская",
        "tg_chat_id": -1003934880016,
        "vk_group_id": 240271459,
        "vk_token_env": "VK_TOKEN_MAYKOP_STROITELEY",
    },
}
