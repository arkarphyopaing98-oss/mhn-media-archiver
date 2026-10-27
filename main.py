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

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("mhn-archiver")


async def start_health_server(port: int, archiver_ref: dict) -> None:
    async def health(_):
        return web.Response(text="ok")

    async def status(_):
        arcs = archiver_ref.get("all") or []
        return web.json_response({a.profile.key: {"stats": a.stats, "archived_ids": len(a.seen)} for a in arcs})

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

    # Tenants: the main group from TG_GROUP/DRIVE_ROOT_FOLDER_ID/SHEET_ID (profile "mhn" unless PROFILE is set),
    # plus optional EXTRA_TENANTS='[{"profile":"rm","group":-100..,"drive_root":"..","sheet_id":".."}]'.
    import json, os
    from app.google_io import Drive, Log, _creds
    from app.taxonomy import PROFILES
    tenants = [{"profile": os.environ.get("PROFILE", "mhn"), "group": cfg.tg_group,
                "drive_root": cfg.drive_root_folder_id, "sheet_id": cfg.sheet_id}]
    tenants += json.loads(os.environ.get("EXTRA_TENANTS", "[]") or "[]")
    creds = _creds(cfg.google_client_id, cfg.google_client_secret, cfg.google_refresh_token)
    lock = asyncio.Lock()
    archivers = []
    for t in tenants:
        g = str(t["group"])
        chat = await client.get_entity(int(g) if g.lstrip("-").isdigit() else g)
        prof = PROFILES[t["profile"]]
        a = Archiver(cfg, client, chat, Drive(creds, t["drive_root"]), Log(creds, t["sheet_id"], t.get("sheet_tab", "Log")),
                     Classifier(cfg.classifier_key, cfg.classifier_model, cfg.classifier_provider, prof), prof, lock)
        await a.load_seen()
        archivers.append(a)
        log.info("[%s] watching group: %s", prof.key, getattr(chat, "title", g))

        def _handler(arch):
            async def h(event):  # noqa: ANN001
                asyncio.create_task(arch.on_new_message(event.message))
            return h
        client.add_event_handler(_handler(a), events.NewMessage(chats=chat))
    ref["a"] = archivers[0]
    ref["all"] = archivers

    async def catch_up_all():
        for a in archivers:
            await a.catch_up(cfg.backfill, cfg.backfill_limit)
    asyncio.create_task(catch_up_all())
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
