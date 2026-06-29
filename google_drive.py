import io
import json
import os

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload


_SCOPES = ["https://www.googleapis.com/auth/drive"]


def is_configured() -> bool:
    return bool(os.getenv("GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON") and os.getenv("GOOGLE_DRIVE_FOLDER_ID"))


def _load_credentials_info() -> dict:
    value = os.getenv("GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON", "").strip()
    if not value:
        raise RuntimeError("GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON nao configurado")
    if value.startswith("{"):
        return json.loads(value)
    with open(value, "r", encoding="utf-8") as fp:
        return json.load(fp)


def _service():
    credentials = service_account.Credentials.from_service_account_info(
        _load_credentials_info(),
        scopes=_SCOPES,
    )
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def _quote_query_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def ensure_folder_path(folder_path=None) -> str:
    parent_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "").strip()
    if not parent_id:
        raise RuntimeError("GOOGLE_DRIVE_FOLDER_ID nao configurado")
    if not folder_path:
        return parent_id

    service = _service()
    for raw_name in folder_path:
        name = str(raw_name or "").strip()
        if not name:
            continue
        query = (
            "mimeType = 'application/vnd.google-apps.folder' "
            f"and name = '{_quote_query_value(name)}' "
            f"and '{parent_id}' in parents and trashed = false"
        )
        result = service.files().list(q=query, fields="files(id,name)", pageSize=1).execute()
        files = result.get("files") or []
        if files:
            parent_id = files[0]["id"]
            continue

        folder = service.files().create(
            body={
                "name": name,
                "mimeType": "application/vnd.google-apps.folder",
                "parents": [parent_id],
            },
            fields="id",
        ).execute()
        parent_id = folder["id"]
    return parent_id


def upload_bytes(data: bytes, filename: str, mimetype: str, folder_path=None) -> tuple[str, str]:
    service = _service()
    parent_id = ensure_folder_path(folder_path)
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mimetype, resumable=False)
    file_obj = service.files().create(
        body={"name": filename, "parents": [parent_id]},
        media_body=media,
        fields="id,webViewLink",
    ).execute()
    try:
        service.permissions().create(
            fileId=file_obj["id"],
            body={"type": "anyone", "role": "reader"},
            fields="id",
        ).execute()
    except Exception:
        pass
    return file_obj["id"], file_obj.get("webViewLink", "")
