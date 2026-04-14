import asyncio
import subprocess
import sys

async def run_bot(path):
    return await asyncio.create_subprocess_exec(
        sys.executable, path
    )

async def main():
    bot1 = await run_bot("bot1/main.py")
    bot2 = await run_bot("bot2/main.py")

    await asyncio.gather(
        bot1.wait(),
        bot2.wait()
    )

asyncio.run(main())