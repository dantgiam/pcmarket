# ---------------- Релей MAX -> Telegram + VK ----------------
# Конфиг по магазинам. Ключ словаря — chat_id MAX-чата этого магазина.
#
# max_chat_id и vk_group_id — плейсхолдеры (0, 1, 2 ...), их нужно заменить
# на реальные значения:
#   - max_chat_id узнаётся из логов bot3 (main.py логирует id неизвестных
#     MAX-чатов) после того, как в чат придёт тестовое сообщение;
#   - vk_group_id берётся из настроек/URL созданной VK-группы (id без минуса);
#   - vk_token_env — имя переменной окружения с токеном сообщества VK
#     (Управление группой -> Работа с API -> Ключи доступа, права wall+photos),
#     саму переменную нужно завести в настройках Railway (Variables).

RELAY_SHOPS = {
    0: {
        "name": "Майкоп, ул. Строителей 8Б",
        "tg_chat_id": -1003450185997,
        "vk_group_id": 0,
        "vk_token_env": "VK_TOKEN_MAYKOP_STROITELEY",
    },
    1: {
        "name": "Майкоп, ул. Депутатская 16Б",
        "tg_chat_id": -1003777692701,
        "vk_group_id": 0,
        "vk_token_env": "VK_TOKEN_MAYKOP_DEPUTATSKAYA",
    },
    2: {
        "name": "Тульский",
        "tg_chat_id": -1003974367383,
        "vk_group_id": 0,
        "vk_token_env": "VK_TOKEN_TULSKY",
    },
    3: {
        "name": "Лабинск",
        "tg_chat_id": -1003840431977,
        "vk_group_id": 0,
        "vk_token_env": "VK_TOKEN_LABINSK",
    },
    4: {
        "name": "Усть-Лабинск",
        "tg_chat_id": -1003973787679,
        "vk_group_id": 0,
        "vk_token_env": "VK_TOKEN_UST_LABINSK",
    },
    5: {
        "name": "Белореченск",
        "tg_chat_id": -1003694773601,
        "vk_group_id": 0,
        "vk_token_env": "VK_TOKEN_BELORECHENSK",
    },
    6: {
        "name": "Краснодар, ул. Уральская 156А",
        "tg_chat_id": -1003926086656,
        "vk_group_id": 0,
        "vk_token_env": "VK_TOKEN_KRASNODAR_URALSKAYA",
    },
    7: {
        "name": "Тимашевск",
        "tg_chat_id": -1003992433513,
        "vk_group_id": 0,
        "vk_token_env": "VK_TOKEN_TIMASHEVSK",
    },
    8: {
        "name": "Мостовской",
        "tg_chat_id": -1003929344550,
        "vk_group_id": 0,
        "vk_token_env": "VK_TOKEN_MOSTOVSKOY",
    },
}
