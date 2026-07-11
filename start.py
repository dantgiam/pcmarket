import asyncio
import subprocess
import sys


async def run_bot(path):
    return await asyncio.create_subprocess_exec(sys.executable, path)


async def main():
    bot = await run_bot("bot2/main.py")
    await bot.wait()


asyncio.run(main())
