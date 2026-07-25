import asyncio
import sys


async def run_bot(path):
    return await asyncio.create_subprocess_exec(sys.executable, path)


async def main():
    bots = await asyncio.gather(
        run_bot("bot2/main.py"),
        run_bot("bot3/main.py"),
    )

    # Ждём, пока завершится ЛЮБОЙ из ботов. Нельзя оставлять контейнер
    # «наполовину живым» (например, релей работает, а модерация упала) —
    # Railway такой контейнер не перезапустит, т.к. главный процесс жив.
    # Поэтому при падении любого бота гасим второй и выходим с ошибкой,
    # чтобы Railway перезапустил оба процесса с нуля.
    tasks = [asyncio.create_task(bot.wait()) for bot in bots]
    await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

    for bot in bots:
        if bot.returncode is None:
            bot.terminate()
    await asyncio.gather(*(bot.wait() for bot in bots), return_exceptions=True)

    sys.exit(1)


asyncio.run(main())
