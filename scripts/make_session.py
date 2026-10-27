"""Run ONCE on your own computer to log in and print TG_SESSION + your group's id.

    pip install telethon==1.36.0
    python scripts/make_session.py

You need TG_API_ID / TG_API_HASH from https://my.telegram.org -> API development tools.
Telegram sends a login code to your Telegram app. Keep the printed session string secret:
it is a full login to your account.
"""
import asyncio

from telethon import TelegramClient
from telethon.sessions import StringSession


async def main() -> None:
    api_id = int(input("TG_API_ID: ").strip())
    api_hash = input("TG_API_HASH: ").strip()
    async with TelegramClient(StringSession(), api_id, api_hash) as client:
        print("\nTG_SESSION=" + client.session.save())
        print("\nYour groups (copy the id of the Product History group into TG_GROUP):")
        async for d in client.iter_dialogs():
            if d.is_group:
                print(f"  {d.id}\t{d.name}")


if __name__ == "__main__":
    asyncio.run(main())
