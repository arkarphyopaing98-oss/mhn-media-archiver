"""MHN Media Archiver: Telegram "Product History" group -> classified Drive folders + log sheet.

Run: python main.py   (all settings come from environment variables, see .env.example)
"""
from __future__ import annotations

import asyncio
import logging

from aiohttp import web
from telethon import TelegramClient, events
from telethon.sessions import StringSession

from app.archiver import Archiver
from app.classifier import Classifier
from app.config import Config
from app.google_io import make_clients

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("mhn-archiver")


async def start_health_server(port: int, archiver_ref: dict) -> None:
    async def health(_):
        return web.Response(text="ok")

    async def status(_):
        a = archiver_ref.get("a")
        return web.json_response({"stats": a.stats if a else {}, "archived_ids": len(a.seen) if a else 0})

    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/status", status)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    log.info("Health server on :%s", port)


async def main() -> None:
    import os
    if not os.environ.get("TG_SESSION"):
        from app.tg_setup import run_setup_server
        await run_setup_server(int(os.environ.get("PORT", "10000")))
        await asyncio.Event().wait()  # stay up for the browser login
    cfg = Config.from_env()
    ref: dict = {}
    await start_health_server(cfg.port, ref)  # Render needs the port open quickly

    client = TelegramClient(StringSession(cfg.tg_session), cfg.tg_api_id, cfg.tg_api_hash)
    await client.start()
    group = int(cfg.tg_group) if cfg.tg_group.lstrip("-").isdigit() else cfg.tg_group
    chat = await client.get_entity(group)
    log.info("Watching group: %s", getattr(chat, "title", group))

    drive, sheet = make_clients(cfg)
    archiver = Archiver(cfg, client, chat, drive, sheet, Classifier(cfg.classifier_key, cfg.classifier_model, cfg.classifier_provider))
    ref["a"] = archiver
    await archiver.load_seen()

    @client.on(events.NewMessage(chats=chat))
    async def _(event):  # noqa: ANN001
        asyncio.create_task(archiver.on_new_message(event.message))

    asyncio.create_task(archiver.catch_up(cfg.backfill, cfg.backfill_limit))
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
