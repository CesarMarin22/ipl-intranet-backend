import io
import os

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

SCOPES = ["https://www.googleapis.com/auth/drive"]

_drive_service = None


def _parent_folder_id():
    return os.getenv("GOOGLE_DRIVE_FLASH_FOLDER_ID")


def get_drive_service():
    global _drive_service
    if _drive_service is None:
        service_account_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
        if not service_account_file:
            raise RuntimeError("Falta configurar GOOGLE_SERVICE_ACCOUNT_FILE en el .env")
        credenciales = service_account.Credentials.from_service_account_file(
            service_account_file, scopes=SCOPES
        )
        _drive_service = build("drive", "v3", credentials=credenciales, cache_discovery=False)
    return _drive_service


def _buscar_carpeta(nombre, parent_id):
    service = get_drive_service()
    nombre_escapado = nombre.replace("'", "\\'")
    query = (
        f"name = '{nombre_escapado}' "
        "and mimeType = 'application/vnd.google-apps.folder' "
        f"and '{parent_id}' in parents and trashed = false"
    )
    resultado = service.files().list(
        q=query, spaces="drive", fields="files(id, name)",
        supportsAllDrives=True, includeItemsFromAllDrives=True,
    ).execute()
    archivos = resultado.get("files", [])
    return archivos[0]["id"] if archivos else None


def obtener_o_crear_carpeta(nombre_carpeta, parent_id=None):
    parent_id = parent_id or _parent_folder_id()
    if not parent_id:
        raise RuntimeError("Falta configurar GOOGLE_DRIVE_FLASH_FOLDER_ID en el .env")

    carpeta_id = _buscar_carpeta(nombre_carpeta, parent_id)
    if carpeta_id:
        return carpeta_id

    service = get_drive_service()
    metadata = {
        "name": nombre_carpeta,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id],
    }
    carpeta = service.files().create(body=metadata, fields="id", supportsAllDrives=True).execute()
    return carpeta["id"]


def subir_imagen(ref_id, nombre_archivo, contenido_bytes, mimetype):
    carpeta_id = obtener_o_crear_carpeta(ref_id)
    service = get_drive_service()
    media = MediaIoBaseUpload(io.BytesIO(contenido_bytes), mimetype=mimetype, resumable=False)
    metadata = {"name": nombre_archivo, "parents": [carpeta_id]}
    return service.files().create(
        body=metadata, media_body=media, fields="id, name, mimeType", supportsAllDrives=True,
    ).execute()


def listar_imagenes(ref_id):
    parent_id = _parent_folder_id()
    if not ref_id or not parent_id:
        return []
    carpeta_id = _buscar_carpeta(ref_id, parent_id)
    if not carpeta_id:
        return []
    service = get_drive_service()
    resultado = service.files().list(
        q=f"'{carpeta_id}' in parents and trashed = false",
        spaces="drive", fields="files(id, name, mimeType)",
        supportsAllDrives=True, includeItemsFromAllDrives=True,
    ).execute()
    return resultado.get("files", [])


def descargar_imagen(file_id):
    service = get_drive_service()
    metadata = service.files().get(
        fileId=file_id, fields="name, mimeType", supportsAllDrives=True
    ).execute()
    request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    buffer.seek(0)
    return (
        buffer.read(),
        metadata.get("mimeType", "application/octet-stream"),
        metadata.get("name", file_id),
    )
