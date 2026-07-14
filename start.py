import asyncio
import subprocess
import sys


async def run_bot(path):
    return await asyncio.create_subprocess_exec(sys.executable, path)


async def main():
    bots = await asyncio.gather(
        run_bot("bot2/main.py"),
        run_bot("bot3/main.py"),
    )
    await asyncio.gather(*(bot.wait() for bot in bots))


asyncio.run(main())
