"""Google Drive upload + Sheets log, using OAuth user credentials (refresh token).

A service account cannot own files in a personal Drive (no storage quota), so we act as you.
All Google calls are blocking; callers run them in a thread (asyncio.to_thread).
"""
from __future__ import annotations

import logging
from typing import Iterable

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

log = logging.getLogger(__name__)
SCOPES = ["https://www.googleapis.com/auth/drive", "https://www.googleapis.com/auth/spreadsheets"]
FOLDER_MIME = "application/vnd.google-apps.folder"


def _creds(client_id: str, client_secret: str, refresh_token: str) -> Credentials:
    return Credentials(
        token=None, refresh_token=refresh_token, client_id=client_id,
        client_secret=client_secret, token_uri="https://oauth2.googleapis.com/token", scopes=SCOPES,
    )


class Drive:
    def __init__(self, creds: Credentials, root_id: str) -> None:
        self.svc = build("drive", "v3", credentials=creds, cache_discovery=False)
        self.root_id = root_id
        self._cache: dict[tuple[str, str], str] = {}

    def _child_folder(self, parent: str, name: str) -> str:
        key = (parent, name)
        if key in self._cache:
            return self._cache[key]
        q = (f"'{parent}' in parents and name = '{name.replace(chr(39), chr(92) + chr(39))}' "
             f"and mimeType = '{FOLDER_MIME}' and trashed = false")
        found = self.svc.files().list(q=q, fields="files(id)", pageSize=1).execute().get("files", [])
        fid = found[0]["id"] if found else self.svc.files().create(
            body={"name": name, "mimeType": FOLDER_MIME, "parents": [parent]}, fields="id"
        ).execute()["id"]
        self._cache[key] = fid
        return fid

    def folder_for(self, path: Iterable[str]) -> str:
        parent = self.root_id
        for name in path:
            parent = self._child_folder(parent, name)
        return parent

    def upload(self, local_path: str, name: str, mime: str, folder_id: str, description: str) -> dict:
        media = MediaFileUpload(local_path, mimetype=mime, resumable=True, chunksize=8 * 1024 * 1024)
        req = self.svc.files().create(
            body={"name": name, "parents": [folder_id], "description": description[:4000]},
            media_body=media, fields="id, webViewLink",
        )
        resp = None
        while resp is None:
            _, resp = req.next_chunk()
        return resp


class Log:
    def __init__(self, creds: Credentials, sheet_id: str, tab: str) -> None:
        self.svc = build("sheets", "v4", credentials=creds, cache_discovery=False)
        self.sheet_id, self.tab = sheet_id, tab

    def seen_ids(self) -> set[int]:
        rows = self.svc.spreadsheets().values().get(
            spreadsheetId=self.sheet_id, range=f"{self.tab}!A2:A").execute().get("values", [])
        out: set[int] = set()
        for r in rows:
            if r and str(r[0]).lstrip("-").isdigit():
                out.add(int(r[0]))
        return out

    def append(self, row: list) -> None:
        self.svc.spreadsheets().values().append(
            spreadsheetId=self.sheet_id, range=f"{self.tab}!A1",
            valueInputOption="RAW", insertDataOption="INSERT_ROWS", body={"values": [row]},
        ).execute()


def make_clients(cfg) -> tuple[Drive, Log]:
    creds = _creds(cfg.google_client_id, cfg.google_client_secret, cfg.google_refresh_token)
    return Drive(creds, cfg.drive_root_folder_id), Log(creds, cfg.sheet_id, cfg.sheet_tab)
