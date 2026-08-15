# ---------------- Релей MAX -> Telegram + VK ----------------
# Конфиг по магазинам. Ключ словаря — chat_id MAX-чата этого магазина.
#
# vk_group_id — плейсхолдер (0), пока не заведена VK-группа под магазин:
#   - берётся из настроек/URL созданной VK-группы (id без минуса);
#   - vk_token_env — имя переменной окружения с токеном сообщества VK
#     (Управление группой -> Работа с API -> Ключи доступа, права wall+photos),
#     саму переменную нужно завести в настройках Railway (Variables).

from shops import PENDING_KRASNODAR_KUNIKOVA, PENDING_TBILISSKAYA

# Новые магазины, в чьи группы бота ещё не добавили: chat_id MAX-чата и парного
# TG-чата неизвестны. Строковый ключ-заглушка с настоящим (числовым) chat_id
# никогда не совпадёт, поэтому до подстановки релей для них просто не работает.
# Как узнать id MAX-чата: добавить бота в группу — при первом же сообщении bot3
# напишет в лог «⚠️ Неизвестный MAX-чат: <id>»; id TG-чата показывает команда
# /id в самой группе. tg_chat_id здесь — те же заглушки, что и ключи в
# bot2/shops.py: по нему берутся тексты автоответов, значения обязаны совпадать.
PENDING_MAX_KRASNODAR_KUNIKOVA = "pending-max:krasnodar-kunikova"
PENDING_MAX_TBILISSKAYA = "pending-max:tbilisskaya"

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
    -76202362090160: {
        "name": "Краснодар, ул. Калинина 327/2",
        "tg_chat_id": -1004409128265,
        "vk_group_id": 0,
        "vk_token_env": "VK_TOKEN_KRASNODAR_KALININA",
    },
    -76202369430192: {
        "name": "Великовечное",
        "tg_chat_id": -1003893903704,
        "vk_group_id": 0,
        "vk_token_env": "VK_TOKEN_VELIKOVECHNOE",
    },
    PENDING_MAX_TBILISSKAYA: {
        "name": "Тбилисская",
        "tg_chat_id": PENDING_TBILISSKAYA,
        "vk_group_id": 0,
        "vk_token_env": "VK_TOKEN_TBILISSKAYA",
    },
    PENDING_MAX_KRASNODAR_KUNIKOVA: {
        "name": "Краснодар, ул. Цезаря Куникова 24к1",
        "tg_chat_id": PENDING_KRASNODAR_KUNIKOVA,
        "vk_group_id": 0,
        "vk_token_env": "VK_TOKEN_KRASNODAR_KUNIKOVA",
    },
    # Тестовый чат: модерация/OCR работают как обычно, но никто (в т.ч.
    # админ) не считается админом — см. TEST_CHAT_IDS ниже. tg_chat_id=0 →
    # в Telegram не релеим (пары нет), это чисто песочница для спам-тестов.
    # content_tg_chat_id — откуда брать тексты автоответов (адрес/график),
    # т.к. своего магазина у песочницы нет.
    -77262148743856: {
        "name": "ТЕСТ",
        "tg_chat_id": 0,
        "content_tg_chat_id": -1003450185997,  # Майкоп, ул. Строителей 8Б
        "vk_group_id": 0,
        "vk_token_env": "VK_TOKEN_TEST",
    },
}

# Чаты-песочницы: в них никто не считается админом (чтобы можно было
# тестировать модерацию со своего же админского аккаунта) и текстовый
# спам только удаляется, без бана — чтобы не залочить себя при тестах.
TEST_CHAT_IDS = {-77262148743856}
