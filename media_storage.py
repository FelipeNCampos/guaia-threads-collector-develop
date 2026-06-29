import os

import google_drive
from config import IS_DEV, MEDIA_LOCAL_PATH


def save_media(
    data: bytes,
    filename: str,
    mimetype: str,
    folder_path: list[str],
    *,
    logger=None,
) -> tuple[str, str]:
    """
    Salva bytes de midia no destino configurado.

    Em desenvolvimento (APP_ENV=development), salva em disco local e retorna
    (caminho_absoluto, "file://<caminho_absoluto>"). Em outros ambientes,
    envia para o Google Drive e retorna (file_id, webViewLink).

    Raises RuntimeError se o armazenamento falhar.
    """
    try:
        if IS_DEV:
            target_dir = os.path.abspath(os.path.join(MEDIA_LOCAL_PATH, *folder_path))
            os.makedirs(target_dir, exist_ok=True)
            target_path = os.path.abspath(os.path.join(target_dir, filename))
            with open(target_path, "wb") as fp:
                fp.write(data)
            if logger:
                logger.info("Midia salva localmente em %s", target_path)
            return target_path, f"file://{target_path}"

        file_id, web_view_link = google_drive.upload_bytes(
            data,
            filename,
            mimetype,
            folder_path=folder_path,
        )
        if logger:
            logger.info("Midia salva no Google Drive em %s", web_view_link or file_id)
        return file_id, web_view_link
    except Exception as exc:
        raise RuntimeError(f"Falha ao salvar midia: {exc}") from exc
