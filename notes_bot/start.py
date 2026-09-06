# ---------------- Заглушка для Railway ----------------
# Настоящая точка входа — main.py, и в Procfile указана именно она.
# Но сервис Railway помнит стартовую команду от ботов магазинов
# («python start.py»), и ни Procfile, ни startCommand в railway.json её не
# перебивают. Чтобы деплой не падал в цикле, кладём файл с этим именем
# рядом: он просто запускает бота заметок.
#
# Когда в дашборде Railway очистишь Settings → Deploy → Custom Start Command,
# этот файл станет не нужен — но и мешать не будет.

from main import main

if __name__ == "__main__":
    main()
