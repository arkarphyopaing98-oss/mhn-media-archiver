"""Google Drive upload + Sheets log, using OAuth user credentials (refresh token).

A service account cannot own files in a personal Drive (no storage quota), so we act as you.
All Google calls are blocking; callers run them in a thread (asyncio.to_thread).
"""
from __future__ import annotations

import logging
from typing import Iterable

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

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
        self.creds = creds
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

    def thumbnail(self, file_id: str) -> bytes | None:
        """Drive's own preview image of a file (works for videos without downloading them)."""
        from google.auth.transport.requests import AuthorizedSession
        link = self.svc.files().get(fileId=file_id, fields="thumbnailLink").execute().get("thumbnailLink")
        if not link:
            return None
        r = AuthorizedSession(self.creds).get(link.replace("=s220", "=s800"), timeout=60)
        return r.content if r.ok and r.content else None

    def move_rename(self, file_id: str, new_name: str, new_parent: str) -> dict:
        cur = self.svc.files().get(fileId=file_id, fields="parents").execute().get("parents", [])
        kw = {}
        if new_parent not in cur:  # already there (e.g. the voice folder) -> rename only
            kw = {"addParents": new_parent, "removeParents": ",".join(cur)}
        return self.svc.files().update(fileId=file_id, body={"name": new_name}, fields="id, webViewLink",
                                       **kw).execute()

    def download(self, file_id: str, local_path: str) -> None:
        req = self.svc.files().get_media(fileId=file_id)
        with open(local_path, "wb") as fh:
            dl = MediaIoBaseDownload(fh, req, chunksize=16 * 1024 * 1024)
            done = False
            while not done:
                _, done = dl.next_chunk()


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

    def rows(self, a1: str) -> list[list[str]]:
        return self.svc.spreadsheets().values().get(
            spreadsheetId=self.sheet_id, range=f"{self.tab}!{a1}").execute().get("values", [])

    def _row_of(self, msg_id: int) -> int | None:
        rows = self.svc.spreadsheets().values().get(
            spreadsheetId=self.sheet_id, range=f"{self.tab}!A:A").execute().get("values", [])
        for i, r in enumerate(rows, start=1):
            if r and str(r[0]) == str(msg_id):
                return i
        return None

    def set_row(self, msg_id: int, row: list) -> None:
        """Replace the row for msg_id (append if missing)."""
        i = self._row_of(msg_id)
        if i is None:
            return self.append(row)
        end = chr(ord("A") + len(row) - 1)
        self.svc.spreadsheets().values().update(
            spreadsheetId=self.sheet_id, range=f"{self.tab}!A{i}:{end}{i}", valueInputOption="RAW",
            body={"values": [row]}).execute()

    def set_cell(self, msg_id: int, col: str, value: str) -> None:
        """Write one cell in the row whose column A equals msg_id."""
        rows = self.svc.spreadsheets().values().get(
            spreadsheetId=self.sheet_id, range=f"{self.tab}!A:A").execute().get("values", [])
        for i, r in enumerate(rows, start=1):
            if r and str(r[0]) == str(msg_id):
                self.svc.spreadsheets().values().update(
                    spreadsheetId=self.sheet_id, range=f"{self.tab}!{col}{i}", valueInputOption="RAW",
                    body={"values": [[value]]}).execute()
                return

    def set_range(self, msg_id: int, start_col: str, values: list) -> None:
        """Write values into the row of msg_id starting at start_col (single letters only)."""
        i = self._row_of(msg_id)
        if i is None:
            return
        end = chr(ord(start_col) + len(values) - 1)
        self.svc.spreadsheets().values().update(
            spreadsheetId=self.sheet_id, range=f"{self.tab}!{start_col}{i}:{end}{i}", valueInputOption="RAW",
            body={"values": [values]}).execute()

    def append(self, row: list) -> None:
        self.svc.spreadsheets().values().append(
            spreadsheetId=self.sheet_id, range=f"{self.tab}!A1",
            valueInputOption="RAW", insertDataOption="INSERT_ROWS", body={"values": [row]},
        ).execute()


def make_clients(cfg) -> tuple[Drive, Log]:
    creds = _creds(cfg.google_client_id, cfg.google_client_secret, cfg.google_refresh_token)
    return Drive(creds, cfg.drive_root_folder_id), Log(creds, cfg.sheet_id, cfg.sheet_tab)
