import os
import io
import uuid
from datetime import datetime, date

from flask import Blueprint, request, send_from_directory
from werkzeug.utils import secure_filename
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas

from app.db import fetch_all, fetch_one, execute_query
from app.helpers import (
    ok_response,
    error_response,
    validate_active_session,
    require_permission,
    user_has_permission,
    get_next_id,
    current_user_id,
)
from app.email_utils import (
    send_email,
    build_sgc_reminder_email,
    build_pending_approval_email,
    build_decision_email,
)

sgc_bp = Blueprint("sgc", __name__)
SCHEMA = os.getenv("HANA_SCHEMA", "QRTEST")

UPLOAD_DIR = os.getenv(
    "SGC_UPLOAD_DIR",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "uploads", "sgc"),
)

ALLOWED_EXTENSIONS = {
    "pdf",
    "doc",
    "docx",
    "xls",
    "xlsx",
    "ppt",
    "pptx",
    "png",
    "jpg",
    "jpeg",
    "txt",
    "csv",
}

DIAS_URGENTE = int(os.getenv("SGC_DIAS_URGENTE", "3"))
DIAS_PROXIMO = int(os.getenv("SGC_DIAS_PROXIMO", "7"))
CRON_SECRET = os.getenv("SGC_CRON_SECRET", "")
QUALITY_PROFILE_NAME = os.getenv("SGC_PERFIL_CALIDAD_NOMBRE", "Calidad")
FORMATO_TYPE_NAME = os.getenv("SGC_TIPO_FORMATO_NOMBRE", "Formato")


# --------------------------------------------------------------------
# Utilidades de archivo
# --------------------------------------------------------------------


def _generar_pagina_marca_agua(width, height, texto="DOCUMENTO OBSOLETO"):
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=(width, height))
    c.saveState()
    c.setFont("Helvetica-Bold", 46)
    c.setFillColorRGB(0.85, 0.1, 0.1, alpha=0.35)
    c.translate(width / 2, height / 2)
    c.rotate(45)
    c.drawCentredString(0, 0, texto)
    c.restoreState()
    c.save()
    buffer.seek(0)
    return PdfReader(buffer).pages[0]


def _aplicar_marca_agua_obsoleto(input_path, output_path):
    """
    Imprime 'DOCUMENTO OBSOLETO' en diagonal sobre cada página de un PDF.
    Se usa cuando Calidad da de baja definitivamente un documento (no debe
    volver a usarse), tal como pide el procedimiento CAL-PRO-01.
    """
    reader = PdfReader(input_path)
    writer = PdfWriter()

    for page in reader.pages:
        width = float(page.mediabox.width)
        height = float(page.mediabox.height)
        marca = _generar_pagina_marca_agua(width, height)
        page.merge_page(marca)
        writer.add_page(page)

    with open(output_path, "wb") as f:
        writer.write(f)


def _ensure_upload_dir():
    os.makedirs(UPLOAD_DIR, exist_ok=True)


def _allowed_file(filename):
    if "." not in filename:
        return False
    ext = filename.rsplit(".", 1)[1].lower()
    return ext in ALLOWED_EXTENSIONS


def _save_uploaded_file(file_storage):
    if not file_storage or not file_storage.filename:
        return None, None

    original_name = file_storage.filename
    if not _allowed_file(original_name):
        raise ValueError(
            "Tipo de archivo no permitido. Formatos válidos: "
            + ", ".join(sorted(ALLOWED_EXTENSIONS))
        )

    _ensure_upload_dir()
    safe_name = secure_filename(original_name)
    ext = safe_name.rsplit(".", 1)[1] if "." in safe_name else "bin"
    stored_name = f"{uuid.uuid4().hex}.{ext}"
    file_storage.save(os.path.join(UPLOAD_DIR, stored_name))

    return stored_name, original_name


def _parse_date(value):
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def calcular_estado(fecha_limite):
    if not fecha_limite:
        return "SIN_FECHA", None

    fecha_limite = _parse_date(fecha_limite)
    if not fecha_limite:
        return "SIN_FECHA", None

    dias = (fecha_limite - date.today()).days

    if dias < 0:
        return "VENCIDO", dias
    if dias <= DIAS_URGENTE:
        return "URGENTE", dias
    if dias <= DIAS_PROXIMO:
        return "PROXIMO_A_VENCER", dias
    return "VIGENTE", dias


# --------------------------------------------------------------------
# Usuario en sesión / rol de Calidad / visibilidad
# --------------------------------------------------------------------


def _get_viewer_context():
    user_id = current_user_id()
    if not user_id:
        return None
    return fetch_one(
        f"""
        SELECT U."USUARIOID", U."DEPAID", U."SUCURSAL", U."PERFILID",
               P."NOMBRE" AS "PERFIL_NOMBRE"
        FROM "{SCHEMA}"."USUARIOS" U
        LEFT JOIN "{SCHEMA}"."PERFILES" P ON U."PERFILID" = P."PERFILID"
        WHERE U."USUARIOID" = ?
        """,
        [user_id],
    )


def _is_quality(viewer):
    if not viewer:
        return False
    return (
        viewer.get("PERFIL_NOMBRE") or ""
    ).strip().lower() == QUALITY_PROFILE_NAME.strip().lower()


def _es_tipo_formato(tipo_documento):
    return (tipo_documento or "").strip().lower() == FORMATO_TYPE_NAME.strip().lower()


def _get_quality_users():
    return fetch_all(
        f"""
        SELECT U."USUARIOID", U."NOMBRE", U."EMAIL"
        FROM "{SCHEMA}"."USUARIOS" U
        INNER JOIN "{SCHEMA}"."PERFILES" P ON U."PERFILID" = P."PERFILID"
        WHERE U."ACTIVO" = 1 AND UPPER(P."NOMBRE") = UPPER(?)
        """,
        [QUALITY_PROFILE_NAME],
    )


def _visibilidad_normalizada(value):
    return (value or "").strip().lower().replace("ú", "u")


def _puede_ver_historial(row, viewer, is_quality):
    if is_quality:
        return True
    if (
        viewer
        and row.get("RESPONSABLE_ID")
        and viewer.get("USUARIOID") == row.get("RESPONSABLE_ID")
    ):
        return True
    return user_has_permission("SGC_DOCUMENTOS", "EDITAR")


def _can_view_document(doc, viewer, is_quality):
    if is_quality:
        return True
    if (
        viewer
        and doc.get("RESPONSABLE_ID")
        and viewer.get("USUARIOID") == doc.get("RESPONSABLE_ID")
    ):
        return True

    vis = _visibilidad_normalizada(doc.get("VISIBILIDAD"))

    if vis == "publico":
        return True
    if vis == "interno":
        return (
            bool(viewer)
            and viewer.get("SUCURSAL")
            and viewer.get("SUCURSAL") == doc.get("RESPONSABLE_SUCURSAL")
        )
    if vis == "confidencial":
        return (
            bool(viewer)
            and viewer.get("DEPAID")
            and viewer.get("DEPAID") == doc.get("DEPAID")
        )

    return False


def _can_view_external_document(doc, viewer, is_quality):
    if is_quality:
        return True

    vis = _visibilidad_normalizada(doc.get("VISIBILIDAD"))

    if vis == "publico":
        return True
    if vis == "interno":
        return (
            bool(viewer)
            and viewer.get("SUCURSAL")
            and viewer.get("SUCURSAL") == doc.get("SUCURSAL")
        )
    if vis == "confidencial":
        return (
            bool(viewer)
            and viewer.get("DEPAID")
            and viewer.get("DEPAID") == doc.get("DEPAID")
        )

    return False


# --------------------------------------------------------------------
# Consultas de documentos / versiones
# --------------------------------------------------------------------


def _select_documents_base():
    return f"""
        SELECT
            S."SGCID", S."CODIGO", S."TITULO", S."TIPO_DOCUMENTO", S."DEPAID",
            D."NOMBRE" AS "DEPARTAMENTO_NOMBRE",
            S."VISIBILIDAD", S."RESPONSABLE_ID",
            R."NOMBRE" AS "RESPONSABLE_NOMBRE",
            R."EMAIL" AS "RESPONSABLE_EMAIL",
            R."SUCURSAL" AS "RESPONSABLE_SUCURSAL",
            S."FECHA_CREACION_DOC", S."FECHA_ULTIMA_REVISION", S."FECHA_LIMITE",
            S."ULTIMO_RECORDATORIO_ENVIADO", S."ACTIVO", S."VERSION_ACTIVA_ID",
            S."FORMATO_ORIGEN_ID",
            FO."TITULO" AS "FORMATO_ORIGEN_TITULO",
            S."OBSOLETO", S."FECHA_OBSOLETO",
            OB."NOMBRE" AS "OBSOLETO_POR_NOMBRE",
            VA."NUMERO_VERSION" AS "VERSION_ACTIVA_NUMERO",
            VA."ARCHIVO_NOMBRE_ORIGINAL" AS "VERSION_ACTIVA_ARCHIVO",
            VA."ARCHIVO_PDF_URL" AS "VERSION_ACTIVA_PDF_URL",
            VA."ARCHIVO_PDF_NOMBRE_ORIGINAL" AS "VERSION_ACTIVA_PDF_NOMBRE"
        FROM "{SCHEMA}"."SGC_DOCUMENTOS" S
        LEFT JOIN "{SCHEMA}"."DEPARTAMENTOS" D ON S."DEPAID" = D."DEPAID"
        LEFT JOIN "{SCHEMA}"."USUARIOS" R ON S."RESPONSABLE_ID" = R."USUARIOID"
        LEFT JOIN "{SCHEMA}"."SGC_DOCUMENTOS_VERSIONES" VA ON S."VERSION_ACTIVA_ID" = VA."VERSIONID"
        LEFT JOIN "{SCHEMA}"."SGC_DOCUMENTOS" FO ON S."FORMATO_ORIGEN_ID" = FO."SGCID"
        LEFT JOIN "{SCHEMA}"."USUARIOS" OB ON S."OBSOLETO_POR" = OB."USUARIOID"
    """


def _obtener_ultima_version(sgcid):
    return fetch_one(
        f"""
        SELECT TOP 1
            V."VERSIONID", V."NUMERO_VERSION", V."ESTADO", V."FECHA_SUBIDA",
            V."FECHA_AUTORIZACION", V."COMENTARIOS", V."ARCHIVO_URL",
            V."ARCHIVO_NOMBRE_ORIGINAL",
            V."ARCHIVO_PDF_URL", V."ARCHIVO_PDF_NOMBRE_ORIGINAL",
            V."DESCRIPCION_CAMBIO",
            US."NOMBRE" AS "SUBIDO_POR_NOMBRE"
        FROM "{SCHEMA}"."SGC_DOCUMENTOS_VERSIONES" V
        LEFT JOIN "{SCHEMA}"."USUARIOS" US ON V."SUBIDO_POR" = US."USUARIOID"
        WHERE V."SGCID" = ?
        ORDER BY V."NUMERO_VERSION" DESC
        """,
        [sgcid],
    )


def _obtener_versiones(sgcid):
    versiones = fetch_all(
        f"""
        SELECT
            V."VERSIONID", V."SGCID", V."NUMERO_VERSION", V."ARCHIVO_URL",
            V."ARCHIVO_NOMBRE_ORIGINAL", V."ESTADO", V."FECHA_SUBIDA",
            V."FECHA_AUTORIZACION", V."COMENTARIOS",
            V."ARCHIVO_PDF_URL", V."ARCHIVO_PDF_NOMBRE_ORIGINAL",
            V."DESCRIPCION_CAMBIO",
            US."NOMBRE" AS "SUBIDO_POR_NOMBRE",
            UA."NOMBRE" AS "AUTORIZADO_POR_NOMBRE"
        FROM "{SCHEMA}"."SGC_DOCUMENTOS_VERSIONES" V
        LEFT JOIN "{SCHEMA}"."USUARIOS" US ON V."SUBIDO_POR" = US."USUARIOID"
        LEFT JOIN "{SCHEMA}"."USUARIOS" UA ON V."AUTORIZADO_POR" = UA."USUARIOID"
        WHERE V."SGCID" = ?
        ORDER BY V."NUMERO_VERSION" DESC
        """,
        [sgcid],
    )

    for v in versiones:
        v["ARCHIVO_DISPONIBLE"] = bool(v.get("ARCHIVO_URL"))
        v["ARCHIVO_PDF_DISPONIBLE"] = bool(v.get("ARCHIVO_PDF_URL"))

    return versiones


def _serialize_dates_in_place(data):
    """
    Convierte cualquier date/datetime dentro de un dict (incluyendo listas
    o dicts anidados, como VERSIONES/ULTIMA_VERSION) a texto ISO
    ('YYYY-MM-DD' o 'YYYY-MM-DDTHH:MM:SS'). Sin esto, Flask puede serializar
    las fechas en un formato (tipo 'Mon, 15 Sep 2025 00:00:00 GMT') que el
    <input type="date"> del navegador no reconoce, y el campo se ve vacío
    aunque sí tenga valor guardado.
    """
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, (datetime, date)):
                data[key] = value.isoformat()
            elif isinstance(value, (dict, list)):
                _serialize_dates_in_place(value)
    elif isinstance(data, list):
        for item in data:
            _serialize_dates_in_place(item)
    return data


def _estado_aprobacion(ultima_version):
    if not ultima_version:
        return "SIN_VERSION"

    estado = ultima_version.get("ESTADO")

    if estado == "BORRADOR":
        return "PENDIENTE_AUTORIZACION"
    if estado == "AUTORIZADO":
        return "AUTORIZADO"
    if estado == "RECHAZADO":
        return "RECHAZADO"

    return "SIN_VERSION"


def _notificar_calidad_pendiente(sgcid, version_id, es_nueva_version):
    doc = fetch_one(_select_documents_base() + ' WHERE S."SGCID" = ?', [sgcid])
    version = fetch_one(
        f'SELECT "VERSIONID","NUMERO_VERSION" FROM "{SCHEMA}"."SGC_DOCUMENTOS_VERSIONES" WHERE "VERSIONID" = ?',
        [version_id],
    )

    if not doc or not version:
        return

    asunto, html_body, text_body = build_pending_approval_email(
        doc, version, es_nueva_version
    )

    for user in _get_quality_users():
        if user.get("EMAIL"):
            send_email(user["EMAIL"], asunto, html_body, text_body)


# --------------------------------------------------------------------
# Listado / detalle
# --------------------------------------------------------------------


@sgc_bp.route("/documents", methods=["GET"])
def get_documents():
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("SGC_DOCUMENTOS", "VER")
    if not allowed:
        return response

    viewer = _get_viewer_context()
    is_quality = _is_quality(viewer)

    rows = fetch_all(_select_documents_base() + ' ORDER BY S."SGCID" DESC')

    result = []
    for row in rows:
        if not _can_view_document(row, viewer, is_quality):
            continue

        versiones = _obtener_versiones(row["SGCID"])
        ultima_version = versiones[0] if versiones else None
        estado_venc, dias = calcular_estado(row.get("FECHA_LIMITE"))
        puede_ver_historial = _puede_ver_historial(row, viewer, is_quality)

        row["ESTADO"] = estado_venc
        row["DIAS_RESTANTES"] = dias
        row["ESTADO_APROBACION"] = _estado_aprobacion(ultima_version)
        row["ULTIMA_VERSION"] = ultima_version
        row["VERSIONES"] = versiones if puede_ver_historial else []
        row["PUEDE_VER_HISTORIAL"] = puede_ver_historial
        row["PUEDE_APROBAR"] = (
            is_quality and row["ESTADO_APROBACION"] == "PENDIENTE_AUTORIZACION"
        )
        row["ES_RESPONSABLE"] = bool(viewer) and viewer.get("USUARIOID") == row.get(
            "RESPONSABLE_ID"
        )
        row["ES_CALIDAD"] = is_quality
        row["ES_TIPO_FORMATO"] = _es_tipo_formato(row.get("TIPO_DOCUMENTO"))

        result.append(_serialize_dates_in_place(row))

    return ok_response(result)


@sgc_bp.route("/documents/<int:sgc_id>", methods=["GET"])
def get_document(sgc_id):
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("SGC_DOCUMENTOS", "VER")
    if not allowed:
        return response

    row = fetch_one(_select_documents_base() + ' WHERE S."SGCID" = ?', [sgc_id])
    if not row:
        return error_response("Documento no encontrado", 404)

    viewer = _get_viewer_context()
    is_quality = _is_quality(viewer)

    if not _can_view_document(row, viewer, is_quality):
        return error_response("No tienes acceso a este documento", 403)

    versiones = _obtener_versiones(sgc_id)
    ultima_version = versiones[0] if versiones else None
    estado_venc, dias = calcular_estado(row.get("FECHA_LIMITE"))
    puede_ver_historial = _puede_ver_historial(row, viewer, is_quality)

    row["ESTADO"] = estado_venc
    row["DIAS_RESTANTES"] = dias
    row["ESTADO_APROBACION"] = _estado_aprobacion(ultima_version)
    row["VERSIONES"] = versiones if puede_ver_historial else []
    row["PUEDE_VER_HISTORIAL"] = puede_ver_historial
    row["PUEDE_APROBAR"] = (
        is_quality and row["ESTADO_APROBACION"] == "PENDIENTE_AUTORIZACION"
    )
    row["ES_RESPONSABLE"] = bool(viewer) and viewer.get("USUARIOID") == row.get(
        "RESPONSABLE_ID"
    )
    row["ES_CALIDAD"] = is_quality
    row["ES_TIPO_FORMATO"] = _es_tipo_formato(row.get("TIPO_DOCUMENTO"))

    return ok_response(_serialize_dates_in_place(row))


# --------------------------------------------------------------------
# Descarga de archivos
# --------------------------------------------------------------------


@sgc_bp.route("/documents/<int:sgc_id>/file", methods=["GET"])
def download_document_file(sgc_id):
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("SGC_DOCUMENTOS", "VER")
    if not allowed:
        return response

    row = fetch_one(_select_documents_base() + ' WHERE S."SGCID" = ?', [sgc_id])
    if not row:
        return error_response("Documento no encontrado", 404)

    viewer = _get_viewer_context()
    if not _can_view_document(row, viewer, _is_quality(viewer)):
        return error_response("No tienes acceso a este documento", 403)

    if not row.get("VERSION_ACTIVA_ID"):
        return error_response(
            "Este documento todavía no tiene una versión autorizada", 404
        )

    version = fetch_one(
        f'SELECT "ARCHIVO_URL","ARCHIVO_NOMBRE_ORIGINAL","ARCHIVO_PDF_URL","ARCHIVO_PDF_NOMBRE_ORIGINAL" '
        f'FROM "{SCHEMA}"."SGC_DOCUMENTOS_VERSIONES" WHERE "VERSIONID" = ?',
        [row["VERSION_ACTIVA_ID"]],
    )

    if not version:
        return error_response(
            "El archivo de la versión vigente ya no está disponible", 404
        )

    es_formato = _es_tipo_formato(row.get("TIPO_DOCUMENTO"))

    if es_formato:
        # Los Formato quedan editables para cualquiera con acceso al documento.
        if not version.get("ARCHIVO_URL"):
            return error_response(
                "El archivo de la versión vigente ya no está disponible", 404
            )
        filename = version["ARCHIVO_URL"]
        download_name = version.get("ARCHIVO_NOMBRE_ORIGINAL") or filename
    else:
        # Cualquier otro tipo: solo se sirve el PDF; el editable queda
        # restringido a Calidad (se descarga desde el historial de versiones).
        if not version.get("ARCHIVO_PDF_URL"):
            return error_response(
                "El PDF de este documento todavía no está disponible", 404
            )
        filename = version["ARCHIVO_PDF_URL"]
        download_name = version.get("ARCHIVO_PDF_NOMBRE_ORIGINAL") or filename

    if not os.path.exists(os.path.join(UPLOAD_DIR, filename)):
        return error_response("El archivo ya no existe en el servidor", 404)

    inline = request.args.get("inline") == "1"

    return send_from_directory(
        UPLOAD_DIR, filename, as_attachment=not inline, download_name=download_name
    )


@sgc_bp.route("/documents/<int:sgc_id>/versions/<int:version_id>/file", methods=["GET"])
def download_version_file(sgc_id, version_id):
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("SGC_DOCUMENTOS", "VER")
    if not allowed:
        return response

    row = fetch_one(_select_documents_base() + ' WHERE S."SGCID" = ?', [sgc_id])
    if not row:
        return error_response("Documento no encontrado", 404)

    viewer = _get_viewer_context()
    is_quality = _is_quality(viewer)

    if not _can_view_document(row, viewer, is_quality):
        return error_response("No tienes acceso a este documento", 403)

    if not _puede_ver_historial(row, viewer, is_quality):
        return error_response(
            "No tienes acceso al historial de versiones de este documento", 403
        )

    version = fetch_one(
        f'SELECT "ARCHIVO_URL","ARCHIVO_NOMBRE_ORIGINAL","SGCID","ESTADO" FROM "{SCHEMA}"."SGC_DOCUMENTOS_VERSIONES" WHERE "VERSIONID" = ?',
        [version_id],
    )

    if not version or version.get("SGCID") != sgc_id:
        return error_response("Versión no encontrada", 404)

    if version.get("ESTADO") == "RECHAZADO":
        return error_response(
            "No se puede descargar el editable de una versión rechazada", 403
        )

    if version.get("ESTADO") == "AUTORIZADO" and not _es_tipo_formato(
        row.get("TIPO_DOCUMENTO")
    ):
        return error_response(
            "Esta versión ya fue liberada; descarga el PDF en vez del editable "
            "(usa el botón de descarga de PDF de esa versión).",
            403,
        )

    if not version.get("ARCHIVO_URL"):
        return error_response(
            "El archivo de esta versión ya no está disponible (fue superada)", 404
        )

    filename = version["ARCHIVO_URL"]
    download_name = version.get("ARCHIVO_NOMBRE_ORIGINAL") or filename

    if not os.path.exists(os.path.join(UPLOAD_DIR, filename)):
        return error_response("El archivo ya no existe en el servidor", 404)

    return send_from_directory(
        UPLOAD_DIR, filename, as_attachment=True, download_name=download_name
    )


@sgc_bp.route("/documents/<int:sgc_id>/versions/<int:version_id>/pdf", methods=["GET"])
def download_version_pdf_file(sgc_id, version_id):
    """
    Descarga el PDF de una versión específica (incluyendo versiones ya
    superadas por una más reciente). A diferencia del editable, el PDF
    de cada versión autorizada se conserva siempre, para que Calidad
    pueda consultar el histórico completo de un documento.
    """
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("SGC_DOCUMENTOS", "VER")
    if not allowed:
        return response

    row = fetch_one(_select_documents_base() + ' WHERE S."SGCID" = ?', [sgc_id])
    if not row:
        return error_response("Documento no encontrado", 404)

    viewer = _get_viewer_context()
    is_quality = _is_quality(viewer)

    if not _can_view_document(row, viewer, is_quality):
        return error_response("No tienes acceso a este documento", 403)

    if not is_quality:
        return error_response(
            "Solo Calidad puede descargar el PDF de versiones anteriores ya superadas",
            403,
        )

    version = fetch_one(
        f'SELECT "ARCHIVO_PDF_URL","ARCHIVO_PDF_NOMBRE_ORIGINAL","SGCID" FROM "{SCHEMA}"."SGC_DOCUMENTOS_VERSIONES" WHERE "VERSIONID" = ?',
        [version_id],
    )

    if not version or version.get("SGCID") != sgc_id:
        return error_response("Versión no encontrada", 404)

    if not version.get("ARCHIVO_PDF_URL"):
        return error_response("Esta versión no tiene un PDF disponible", 404)

    filename = version["ARCHIVO_PDF_URL"]
    download_name = version.get("ARCHIVO_PDF_NOMBRE_ORIGINAL") or filename

    if not os.path.exists(os.path.join(UPLOAD_DIR, filename)):
        return error_response("El archivo ya no existe en el servidor", 404)

    return send_from_directory(
        UPLOAD_DIR, filename, as_attachment=True, download_name=download_name
    )


# --------------------------------------------------------------------
# Crear documento (versión 1, en borrador)
# --------------------------------------------------------------------


@sgc_bp.route("/documents", methods=["POST"])
def create_document():
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("SGC_DOCUMENTOS", "CREAR")
    if not allowed:
        return response

    form = request.form
    titulo = (form.get("TITULO") or "").strip()
    fecha_limite = _parse_date(form.get("FECHA_LIMITE"))

    if not titulo:
        return error_response("El campo TITULO es requerido", 400)

    try:
        stored_name, original_name = _save_uploaded_file(request.files.get("file"))
    except ValueError as exc:
        return error_response(str(exc), 400)

    if not stored_name:
        return error_response("Debes adjuntar el archivo del documento", 400)

    responsable_id = form.get("RESPONSABLE_ID") or current_user_id()
    new_id = get_next_id("SGC_DOCUMENTOS", "SGCID")

    execute_query(
        f"""
        INSERT INTO "{SCHEMA}"."SGC_DOCUMENTOS"
        ("SGCID","CODIGO","TITULO","TIPO_DOCUMENTO","DEPAID","VISIBILIDAD",
         "RESPONSABLE_ID","FECHA_CREACION_DOC","FECHA_ULTIMA_REVISION","FECHA_LIMITE","ACTIVO",
         "FORMATO_ORIGEN_ID")
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            new_id,
            (form.get("CODIGO") or "").strip() or None,
            titulo,
            (form.get("TIPO_DOCUMENTO") or "").strip() or None,
            form.get("DEPAID") or None,
            "Confidencial",
            responsable_id,
            _parse_date(form.get("FECHA_CREACION_DOC")),
            _parse_date(form.get("FECHA_ULTIMA_REVISION")),
            fecha_limite,
            form.get("ACTIVO", "1"),
            form.get("FORMATO_ORIGEN_ID") or None,
        ],
    )

    version_id = get_next_id("SGC_DOCUMENTOS_VERSIONES", "VERSIONID")

    execute_query(
        f"""
        INSERT INTO "{SCHEMA}"."SGC_DOCUMENTOS_VERSIONES"
        ("VERSIONID","SGCID","NUMERO_VERSION","ARCHIVO_URL","ARCHIVO_NOMBRE_ORIGINAL","ESTADO","SUBIDO_POR","DESCRIPCION_CAMBIO")
        VALUES (?, ?, 1, ?, ?, 'BORRADOR', ?, ?)
        """,
        [
            version_id,
            new_id,
            stored_name,
            original_name,
            current_user_id(),
            "Creación del documento",
        ],
    )

    _notificar_calidad_pendiente(new_id, version_id, es_nueva_version=False)

    return ok_response(
        {"SGCID": new_id, "VERSIONID": version_id},
        "Documento creado, pendiente de autorización de Calidad",
        201,
    )


# --------------------------------------------------------------------
# Subir nueva versión (borrador) de un documento existente
# --------------------------------------------------------------------


@sgc_bp.route("/documents/<int:sgc_id>/versions", methods=["POST"])
def create_document_version(sgc_id):
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("SGC_DOCUMENTOS", "VER")
    if not allowed:
        return response

    doc = fetch_one(
        f'SELECT "SGCID","RESPONSABLE_ID" FROM "{SCHEMA}"."SGC_DOCUMENTOS" WHERE "SGCID" = ?',
        [sgc_id],
    )
    if not doc:
        return error_response("Documento no encontrado", 404)

    viewer_id = current_user_id()
    is_responsable = viewer_id == doc.get("RESPONSABLE_ID")

    if not is_responsable and not user_has_permission("SGC_DOCUMENTOS", "EDITAR"):
        return error_response(
            "No tienes permiso para subir una nueva versión de este documento", 403
        )

    ultima = _obtener_ultima_version(sgc_id)
    if ultima and ultima.get("ESTADO") == "BORRADOR":
        return error_response(
            "Ya hay una versión pendiente de autorización para este documento. "
            "Espera a que Calidad la autorice o la rechace.",
            409,
        )

    try:
        stored_name, original_name = _save_uploaded_file(request.files.get("file"))
    except ValueError as exc:
        return error_response(str(exc), 400)

    if not stored_name:
        return error_response("Debes adjuntar el archivo de la nueva versión", 400)

    descripcion_cambio = (request.form.get("DESCRIPCION_CAMBIO") or "").strip()
    if not descripcion_cambio:
        return error_response(
            "Debes describir brevemente qué cambió respecto a la versión anterior "
            "(tu Control de Cambios lo exige).",
            400,
        )

    siguiente_numero = (ultima.get("NUMERO_VERSION") + 1) if ultima else 1
    version_id = get_next_id("SGC_DOCUMENTOS_VERSIONES", "VERSIONID")

    execute_query(
        f"""
        INSERT INTO "{SCHEMA}"."SGC_DOCUMENTOS_VERSIONES"
        ("VERSIONID","SGCID","NUMERO_VERSION","ARCHIVO_URL","ARCHIVO_NOMBRE_ORIGINAL","ESTADO","SUBIDO_POR","DESCRIPCION_CAMBIO")
        VALUES (?, ?, ?, ?, ?, 'BORRADOR', ?, ?)
        """,
        [
            version_id,
            sgc_id,
            siguiente_numero,
            stored_name,
            original_name,
            viewer_id,
            descripcion_cambio,
        ],
    )

    _notificar_calidad_pendiente(sgc_id, version_id, es_nueva_version=True)

    return ok_response(
        {"VERSIONID": version_id, "NUMERO_VERSION": siguiente_numero},
        "Nueva versión subida, pendiente de autorización de Calidad",
        201,
    )


# --------------------------------------------------------------------
# Autorizar / rechazar una versión (solo Calidad)
# --------------------------------------------------------------------


@sgc_bp.route(
    "/documents/<int:sgc_id>/versions/<int:version_id>/authorize", methods=["PATCH"]
)
def authorize_document_version(sgc_id, version_id):
    valid, response = validate_active_session()
    if not valid:
        return response

    viewer = _get_viewer_context()
    if not _is_quality(viewer):
        return error_response("Solo Calidad puede autorizar documentos", 403)

    doc = fetch_one(
        f'SELECT "SGCID","VERSION_ACTIVA_ID","FECHA_LIMITE","TIPO_DOCUMENTO" FROM "{SCHEMA}"."SGC_DOCUMENTOS" WHERE "SGCID" = ?',
        [sgc_id],
    )
    if not doc:
        return error_response("Documento no encontrado", 404)

    # Se puede capturar/actualizar la Fecha límite en el mismo paso de
    # autorizar, para no obligar a Calidad a ir primero a "Editar" y
    # regresar después solo para poner una fecha.
    fecha_limite_form = _parse_date(request.form.get("FECHA_LIMITE"))
    if fecha_limite_form:
        execute_query(
            f'UPDATE "{SCHEMA}"."SGC_DOCUMENTOS" SET "FECHA_LIMITE" = ? WHERE "SGCID" = ?',
            [fecha_limite_form, sgc_id],
        )
        doc["FECHA_LIMITE"] = fecha_limite_form

    if not doc.get("FECHA_LIMITE"):
        return error_response(
            "Antes de autorizar debes definir la Fecha límite del documento "
            "(edítalo y captúrala primero).",
            400,
        )

    es_formato = _es_tipo_formato(doc.get("TIPO_DOCUMENTO"))

    version = fetch_one(
        f'SELECT "VERSIONID","SGCID","ESTADO","ARCHIVO_URL","ARCHIVO_PDF_URL" FROM "{SCHEMA}"."SGC_DOCUMENTOS_VERSIONES" WHERE "VERSIONID" = ?',
        [version_id],
    )
    if not version or version.get("SGCID") != sgc_id:
        return error_response("Versión no encontrada", 404)
    if version.get("ESTADO") != "BORRADOR":
        return error_response("Esta versión ya fue procesada", 409)

    uploaded_file = request.files.get("file")
    uploaded_pdf = request.files.get("pdf")

    # Todo tipo de documento que NO sea "Formato" necesita un PDF para
    # poder autorizarse: es lo que van a leer todos los demás, mientras
    # que el editable queda restringido solo a Calidad de aquí en
    # adelante. Los "Formato" no lo necesitan porque su editable se
    # queda abierto para que cualquiera lo descargue y lo llene.
    if not es_formato and not (uploaded_pdf and uploaded_pdf.filename):
        return error_response(
            "Este tipo de documento necesita un PDF para autorizarse "
            "(el editable quedará restringido a Calidad). Adjunta el PDF.",
            400,
        )

    # Si Calidad adjunta un archivo editable corregido al momento de
    # autorizar, se reemplaza el archivo de esta versión antes de
    # liberarla.
    if uploaded_file and uploaded_file.filename:
        try:
            stored_name, original_name = _save_uploaded_file(uploaded_file)
        except ValueError as exc:
            return error_response(str(exc), 400)

        old_draft_file = version.get("ARCHIVO_URL")

        execute_query(
            f"""
            UPDATE "{SCHEMA}"."SGC_DOCUMENTOS_VERSIONES"
            SET "ARCHIVO_URL" = ?, "ARCHIVO_NOMBRE_ORIGINAL" = ?
            WHERE "VERSIONID" = ?
            """,
            [stored_name, original_name, version_id],
        )

        if old_draft_file:
            old_draft_path = os.path.join(UPLOAD_DIR, old_draft_file)
            if os.path.exists(old_draft_path):
                try:
                    os.remove(old_draft_path)
                except OSError:
                    pass

    if uploaded_pdf and uploaded_pdf.filename:
        try:
            stored_pdf_name, original_pdf_name = _save_uploaded_file(uploaded_pdf)
        except ValueError as exc:
            return error_response(str(exc), 400)

        old_pdf_file = version.get("ARCHIVO_PDF_URL")

        execute_query(
            f"""
            UPDATE "{SCHEMA}"."SGC_DOCUMENTOS_VERSIONES"
            SET "ARCHIVO_PDF_URL" = ?, "ARCHIVO_PDF_NOMBRE_ORIGINAL" = ?
            WHERE "VERSIONID" = ?
            """,
            [stored_pdf_name, original_pdf_name, version_id],
        )

        if old_pdf_file:
            old_pdf_path = os.path.join(UPLOAD_DIR, old_pdf_file)
            if os.path.exists(old_pdf_path):
                try:
                    os.remove(old_pdf_path)
                except OSError:
                    pass

    execute_query(
        f"""
        UPDATE "{SCHEMA}"."SGC_DOCUMENTOS_VERSIONES"
        SET "ESTADO" = 'AUTORIZADO', "FECHA_AUTORIZACION" = CURRENT_TIMESTAMP, "AUTORIZADO_POR" = ?
        WHERE "VERSIONID" = ?
        """,
        [viewer["USUARIOID"], version_id],
    )

    execute_query(
        f'UPDATE "{SCHEMA}"."SGC_DOCUMENTOS" SET "VERSION_ACTIVA_ID" = ? WHERE "SGCID" = ?',
        [version_id, sgc_id],
    )

    version_anterior_id = doc.get("VERSION_ACTIVA_ID")
    if version_anterior_id and version_anterior_id != version_id:
        anterior = fetch_one(
            f'SELECT "ARCHIVO_URL" FROM "{SCHEMA}"."SGC_DOCUMENTOS_VERSIONES" WHERE "VERSIONID" = ?',
            [version_anterior_id],
        )
        # El editable de la versión superada ya no se necesita (la
        # siguiente corrección parte de cero). El PDF, en cambio, se
        # conserva para siempre: Calidad debe poder consultar el
        # histórico completo de versiones autorizadas en PDF.
        if anterior and anterior.get("ARCHIVO_URL"):
            old_path = os.path.join(UPLOAD_DIR, anterior["ARCHIVO_URL"])
            if os.path.exists(old_path):
                try:
                    os.remove(old_path)
                except OSError:
                    pass
            execute_query(
                f'UPDATE "{SCHEMA}"."SGC_DOCUMENTOS_VERSIONES" SET "ARCHIVO_URL" = NULL WHERE "VERSIONID" = ?',
                [version_anterior_id],
            )

    doc_full = fetch_one(_select_documents_base() + ' WHERE S."SGCID" = ?', [sgc_id])
    version_full = fetch_one(
        f'SELECT "VERSIONID","NUMERO_VERSION" FROM "{SCHEMA}"."SGC_DOCUMENTOS_VERSIONES" WHERE "VERSIONID" = ?',
        [version_id],
    )

    if doc_full and doc_full.get("RESPONSABLE_EMAIL"):
        asunto, html_body, text_body = build_decision_email(
            doc_full, version_full, aprobado=True
        )
        send_email(doc_full["RESPONSABLE_EMAIL"], asunto, html_body, text_body)

    return ok_response(message="Versión autorizada y liberada")


@sgc_bp.route(
    "/documents/<int:sgc_id>/versions/<int:version_id>/reject", methods=["PATCH"]
)
def reject_document_version(sgc_id, version_id):
    valid, response = validate_active_session()
    if not valid:
        return response

    viewer = _get_viewer_context()
    if not _is_quality(viewer):
        return error_response("Solo Calidad puede rechazar documentos", 403)

    data = request.get_json(silent=True) or {}
    comentarios = (data.get("COMENTARIOS") or "").strip() or None

    if not comentarios:
        return error_response(
            "Debes indicar el motivo del rechazo (tu procedimiento lo exige en Comentarios).",
            400,
        )

    version = fetch_one(
        f'SELECT "VERSIONID","SGCID","ESTADO" FROM "{SCHEMA}"."SGC_DOCUMENTOS_VERSIONES" WHERE "VERSIONID" = ?',
        [version_id],
    )
    if not version or version.get("SGCID") != sgc_id:
        return error_response("Versión no encontrada", 404)
    if version.get("ESTADO") != "BORRADOR":
        return error_response("Esta versión ya fue procesada", 409)

    execute_query(
        f"""
        UPDATE "{SCHEMA}"."SGC_DOCUMENTOS_VERSIONES"
        SET "ESTADO" = 'RECHAZADO', "FECHA_AUTORIZACION" = CURRENT_TIMESTAMP,
            "AUTORIZADO_POR" = ?, "COMENTARIOS" = ?
        WHERE "VERSIONID" = ?
        """,
        [viewer["USUARIOID"], comentarios, version_id],
    )

    doc_full = fetch_one(_select_documents_base() + ' WHERE S."SGCID" = ?', [sgc_id])
    version_full = fetch_one(
        f'SELECT "VERSIONID","NUMERO_VERSION" FROM "{SCHEMA}"."SGC_DOCUMENTOS_VERSIONES" WHERE "VERSIONID" = ?',
        [version_id],
    )

    if doc_full and doc_full.get("RESPONSABLE_EMAIL"):
        asunto, html_body, text_body = build_decision_email(
            doc_full, version_full, aprobado=False, comentarios=comentarios
        )
        send_email(doc_full["RESPONSABLE_EMAIL"], asunto, html_body, text_body)

    return ok_response(message="Versión rechazada")


# --------------------------------------------------------------------
# Editar metadatos del documento (sin archivo)
# --------------------------------------------------------------------


@sgc_bp.route("/documents/<int:sgc_id>", methods=["PUT"])
def update_document(sgc_id):
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("SGC_DOCUMENTOS", "EDITAR")
    if not allowed:
        return response

    existing = fetch_one(
        f'SELECT "SGCID" FROM "{SCHEMA}"."SGC_DOCUMENTOS" WHERE "SGCID" = ?', [sgc_id]
    )
    if not existing:
        return error_response("Documento no encontrado", 404)

    data = request.get_json(silent=True) or {}
    titulo = (data.get("TITULO") or "").strip()
    fecha_limite = _parse_date(data.get("FECHA_LIMITE"))

    if not titulo:
        return error_response("El campo TITULO es requerido", 400)
    if not fecha_limite:
        return error_response("El campo FECHA_LIMITE es requerido", 400)

    execute_query(
        f"""
        UPDATE "{SCHEMA}"."SGC_DOCUMENTOS"
        SET "CODIGO" = ?, "TITULO" = ?, "TIPO_DOCUMENTO" = ?, "DEPAID" = ?,
            "VISIBILIDAD" = ?, "RESPONSABLE_ID" = ?, "FECHA_CREACION_DOC" = ?,
            "FECHA_ULTIMA_REVISION" = ?, "FECHA_LIMITE" = ?, "ACTIVO" = ?,
            "FORMATO_ORIGEN_ID" = ?
        WHERE "SGCID" = ?
        """,
        [
            (data.get("CODIGO") or "").strip() or None,
            titulo,
            (data.get("TIPO_DOCUMENTO") or "").strip() or None,
            data.get("DEPAID") or None,
            (data.get("VISIBILIDAD") or "Confidencial").strip(),
            data.get("RESPONSABLE_ID") or None,
            _parse_date(data.get("FECHA_CREACION_DOC")),
            _parse_date(data.get("FECHA_ULTIMA_REVISION")),
            fecha_limite,
            data.get("ACTIVO", 1),
            data.get("FORMATO_ORIGEN_ID") or None,
            sgc_id,
        ],
    )

    return ok_response(message="Documento actualizado")


@sgc_bp.route("/documents/<int:sgc_id>/obsolete", methods=["PATCH"])
def mark_document_obsolete(sgc_id):
    """
    Da de baja definitivamente un documento (no se debe volver a usar,
    a diferencia de una versión superada que solo queda en el historial).
    Solo Calidad puede hacerlo. Si el documento ya tiene un PDF vigente,
    se le imprime la marca de agua 'DOCUMENTO OBSOLETO' de forma
    permanente (se reemplaza el archivo, no hay deshacer).
    """
    valid, response = validate_active_session()
    if not valid:
        return response

    viewer = _get_viewer_context()
    if not _is_quality(viewer):
        return error_response(
            "Solo Calidad puede marcar un documento como obsoleto", 403
        )

    doc = fetch_one(
        f'SELECT "SGCID","VERSION_ACTIVA_ID","OBSOLETO" FROM "{SCHEMA}"."SGC_DOCUMENTOS" WHERE "SGCID" = ?',
        [sgc_id],
    )
    if not doc:
        return error_response("Documento no encontrado", 404)

    if doc.get("OBSOLETO"):
        return error_response("Este documento ya está marcado como obsoleto", 409)

    marcado_pdf = False

    if doc.get("VERSION_ACTIVA_ID"):
        version = fetch_one(
            f'SELECT "ARCHIVO_PDF_URL","ARCHIVO_PDF_NOMBRE_ORIGINAL" FROM "{SCHEMA}"."SGC_DOCUMENTOS_VERSIONES" WHERE "VERSIONID" = ?',
            [doc["VERSION_ACTIVA_ID"]],
        )

        if version and version.get("ARCHIVO_PDF_URL"):
            old_pdf_name = version["ARCHIVO_PDF_URL"]
            old_pdf_path = os.path.join(UPLOAD_DIR, old_pdf_name)

            if os.path.exists(old_pdf_path):
                new_pdf_name = f"{uuid.uuid4().hex}.pdf"
                new_pdf_path = os.path.join(UPLOAD_DIR, new_pdf_name)

                try:
                    _aplicar_marca_agua_obsoleto(old_pdf_path, new_pdf_path)

                    original_pdf_display_name = (
                        version.get("ARCHIVO_PDF_NOMBRE_ORIGINAL") or old_pdf_name
                    )

                    execute_query(
                        f"""
                        UPDATE "{SCHEMA}"."SGC_DOCUMENTOS_VERSIONES"
                        SET "ARCHIVO_PDF_URL" = ?, "ARCHIVO_PDF_NOMBRE_ORIGINAL" = ?
                        WHERE "VERSIONID" = ?
                        """,
                        [
                            new_pdf_name,
                            f"OBSOLETO_{original_pdf_display_name}",
                            doc["VERSION_ACTIVA_ID"],
                        ],
                    )

                    try:
                        os.remove(old_pdf_path)
                    except OSError:
                        pass

                    marcado_pdf = True
                except Exception:
                    # Si algo falla al generar la marca de agua, igual se
                    # marca el documento como obsoleto (el estado en el
                    # sistema es lo prioritario), solo sin marca en el PDF.
                    pass

    viewer_id = current_user_id()

    execute_query(
        f"""
        UPDATE "{SCHEMA}"."SGC_DOCUMENTOS"
        SET "OBSOLETO" = 1, "FECHA_OBSOLETO" = CURRENT_TIMESTAMP, "OBSOLETO_POR" = ?
        WHERE "SGCID" = ?
        """,
        [viewer_id, sgc_id],
    )

    mensaje = (
        "Documento marcado como obsoleto y PDF sellado con marca de agua"
        if marcado_pdf
        else "Documento marcado como obsoleto"
    )

    return ok_response(message=mensaje)


@sgc_bp.route("/documents/<int:sgc_id>/status", methods=["PATCH"])
def update_document_status(sgc_id):
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("SGC_DOCUMENTOS", "EDITAR")
    if not allowed:
        return response

    data = request.get_json(silent=True) or {}

    if "ACTIVO" not in data:
        return error_response("El campo ACTIVO es requerido", 400)

    existing = fetch_one(
        f'SELECT "SGCID" FROM "{SCHEMA}"."SGC_DOCUMENTOS" WHERE "SGCID" = ?', [sgc_id]
    )
    if not existing:
        return error_response("Documento no encontrado", 404)

    execute_query(
        f'UPDATE "{SCHEMA}"."SGC_DOCUMENTOS" SET "ACTIVO" = ? WHERE "SGCID" = ?',
        [data.get("ACTIVO"), sgc_id],
    )

    return ok_response(message="Estado actualizado")


@sgc_bp.route("/documents/<int:sgc_id>", methods=["DELETE"])
def delete_document(sgc_id):
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("SGC_DOCUMENTOS", "ELIMINAR")
    if not allowed:
        return response

    existing = fetch_one(
        f'SELECT "SGCID" FROM "{SCHEMA}"."SGC_DOCUMENTOS" WHERE "SGCID" = ?', [sgc_id]
    )
    if not existing:
        return error_response("Documento no encontrado", 404)

    versiones = fetch_all(
        f'SELECT "VERSIONID","ARCHIVO_URL" FROM "{SCHEMA}"."SGC_DOCUMENTOS_VERSIONES" WHERE "SGCID" = ?',
        [sgc_id],
    )

    try:
        execute_query(
            f'UPDATE "{SCHEMA}"."SGC_DOCUMENTOS" SET "VERSION_ACTIVA_ID" = NULL WHERE "SGCID" = ?',
            [sgc_id],
        )
        execute_query(
            f'DELETE FROM "{SCHEMA}"."SGC_DOCUMENTOS_VERSIONES" WHERE "SGCID" = ?',
            [sgc_id],
        )
        execute_query(
            f'DELETE FROM "{SCHEMA}"."SGC_DOCUMENTOS" WHERE "SGCID" = ?', [sgc_id]
        )

        for v in versiones:
            archivo = v.get("ARCHIVO_URL")
            if archivo:
                path = os.path.join(UPLOAD_DIR, archivo)
                if os.path.exists(path):
                    try:
                        os.remove(path)
                    except OSError:
                        pass

        return ok_response(message="Documento eliminado")
    except Exception as e:
        return error_response(f"No se pudo eliminar el documento: {str(e)}", 400)


# --------------------------------------------------------------------
# Catálogo de Tipos de documento (Manual, Procedimiento, Formato,
# Registro, etc.). Editable: cualquier departamento puede pedir que se
# agreguen tipos nuevos sin tocar código. El listado lo puede leer
# cualquiera con acceso a SGC (lo necesitan para elegir el tipo al
# subir un documento); crear/editar/activar-desactivar es solo para
# Calidad o quien tenga permiso EDITAR.
# --------------------------------------------------------------------


@sgc_bp.route("/tipos-documento", methods=["GET"])
def get_tipos_documento():
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("SGC_DOCUMENTOS", "VER")
    if not allowed:
        return response

    incluir_inactivos = request.args.get("all") == "1"

    if incluir_inactivos:
        viewer = _get_viewer_context()
        if not _is_quality(viewer) and not user_has_permission(
            "SGC_DOCUMENTOS", "EDITAR"
        ):
            return error_response("No tienes permiso para ver los tipos inactivos", 403)

        rows = fetch_all(f"""
            SELECT "TIPO_DOCUMENTO_ID", "NOMBRE", "ACTIVO"
            FROM "{SCHEMA}"."SGC_TIPOS_DOCUMENTO"
            ORDER BY "ACTIVO" DESC, "NOMBRE"
            """)
    else:
        rows = fetch_all(f"""
            SELECT "TIPO_DOCUMENTO_ID", "NOMBRE", "ACTIVO"
            FROM "{SCHEMA}"."SGC_TIPOS_DOCUMENTO"
            WHERE "ACTIVO" = 1
            ORDER BY "NOMBRE"
            """)

    return ok_response(rows)


@sgc_bp.route("/tipos-documento", methods=["POST"])
def create_tipo_documento():
    valid, response = validate_active_session()
    if not valid:
        return response

    viewer = _get_viewer_context()
    if not _is_quality(viewer) and not user_has_permission("SGC_DOCUMENTOS", "EDITAR"):
        return error_response("No tienes permiso para agregar tipos de documento", 403)

    data = request.get_json(silent=True) or {}
    nombre = (data.get("NOMBRE") or "").strip()

    if not nombre:
        return error_response("El nombre del tipo es requerido", 400)

    existing = fetch_one(
        f'SELECT "TIPO_DOCUMENTO_ID" FROM "{SCHEMA}"."SGC_TIPOS_DOCUMENTO" WHERE UPPER("NOMBRE") = UPPER(?)',
        [nombre],
    )
    if existing:
        return error_response("Ya existe un tipo de documento con ese nombre", 409)

    new_id = get_next_id("SGC_TIPOS_DOCUMENTO", "TIPO_DOCUMENTO_ID")

    execute_query(
        f"""
        INSERT INTO "{SCHEMA}"."SGC_TIPOS_DOCUMENTO" ("TIPO_DOCUMENTO_ID","NOMBRE","ACTIVO")
        VALUES (?, ?, 1)
        """,
        [new_id, nombre],
    )

    return ok_response({"TIPO_DOCUMENTO_ID": new_id}, "Tipo de documento creado", 201)


@sgc_bp.route("/tipos-documento/<int:tipo_id>/status", methods=["PATCH"])
def update_tipo_documento_status(tipo_id):
    valid, response = validate_active_session()
    if not valid:
        return response

    viewer = _get_viewer_context()
    if not _is_quality(viewer) and not user_has_permission("SGC_DOCUMENTOS", "EDITAR"):
        return error_response(
            "No tienes permiso para modificar tipos de documento", 403
        )

    data = request.get_json(silent=True) or {}

    if "ACTIVO" not in data:
        return error_response("El campo ACTIVO es requerido", 400)

    execute_query(
        f'UPDATE "{SCHEMA}"."SGC_TIPOS_DOCUMENTO" SET "ACTIVO" = ? WHERE "TIPO_DOCUMENTO_ID" = ?',
        [data.get("ACTIVO"), tipo_id],
    )

    return ok_response(message="Estado actualizado")


# --------------------------------------------------------------------
# Codificación automática Área-Tipo-Consecutivo (según CAL-PRO-01,
# Tablas 2 y 3). El código de área se asigna una vez por departamento;
# el código de tipo ya viene precargado en SGC_TIPOS_DOCUMENTO. Con
# ambos, se puede sugerir el siguiente consecutivo disponible.
# --------------------------------------------------------------------


@sgc_bp.route("/departamentos-codigo", methods=["GET"])
def get_departamentos_codigo():
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("SGC_DOCUMENTOS", "VER")
    if not allowed:
        return response

    rows = fetch_all(f"""
        SELECT "DEPAID", "NOMBRE", "CODIGO_AREA"
        FROM "{SCHEMA}"."DEPARTAMENTOS"
        WHERE "ACTIVO" = 1
        ORDER BY "NOMBRE"
        """)

    return ok_response(rows)


@sgc_bp.route("/departamentos-codigo/<int:depaid>", methods=["PATCH"])
def update_departamento_codigo(depaid):
    valid, response = validate_active_session()
    if not valid:
        return response

    viewer = _get_viewer_context()
    if not _is_quality(viewer) and not user_has_permission("SGC_DOCUMENTOS", "EDITAR"):
        return error_response("No tienes permiso para asignar códigos de área", 403)

    data = request.get_json(silent=True) or {}
    codigo = (data.get("CODIGO_AREA") or "").strip().upper()

    if not codigo:
        return error_response("El código de área es requerido", 400)

    execute_query(
        f'UPDATE "{SCHEMA}"."DEPARTAMENTOS" SET "CODIGO_AREA" = ? WHERE "DEPAID" = ?',
        [codigo, depaid],
    )

    return ok_response(message="Código de área actualizado")


@sgc_bp.route("/next-code", methods=["GET"])
def get_next_code():
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("SGC_DOCUMENTOS", "VER")
    if not allowed:
        return response

    depaid = request.args.get("depaid")
    tipo_nombre = (request.args.get("tipo") or "").strip()

    if not depaid or not tipo_nombre:
        return ok_response(
            {"CODIGO_SUGERIDO": None, "motivo": "Falta departamento o tipo"}
        )

    depa = fetch_one(
        f'SELECT "CODIGO_AREA" FROM "{SCHEMA}"."DEPARTAMENTOS" WHERE "DEPAID" = ?',
        [depaid],
    )
    tipo = fetch_one(
        f'SELECT "CODIGO" FROM "{SCHEMA}"."SGC_TIPOS_DOCUMENTO" WHERE UPPER("NOMBRE") = UPPER(?)',
        [tipo_nombre],
    )

    codigo_area = (depa.get("CODIGO_AREA") or "").strip() if depa else ""
    codigo_tipo = (tipo.get("CODIGO") or "").strip() if tipo else ""

    if not codigo_area:
        return ok_response(
            {
                "CODIGO_SUGERIDO": None,
                "motivo": "Este departamento no tiene código de área asignado",
            }
        )
    if not codigo_tipo:
        return ok_response(
            {
                "CODIGO_SUGERIDO": None,
                "motivo": "Este tipo de documento no tiene código asignado",
            }
        )

    prefijo = f"{codigo_area}-{codigo_tipo}-"

    rows = fetch_all(
        f'SELECT "CODIGO" FROM "{SCHEMA}"."SGC_DOCUMENTOS" WHERE "CODIGO" LIKE ?',
        [prefijo + "%"],
    )

    max_num = 0
    for row in rows:
        sufijo = (row.get("CODIGO") or "")[len(prefijo) :]
        if sufijo.isdigit():
            max_num = max(max_num, int(sufijo))

    codigo_sugerido = f"{prefijo}{max_num + 1:02d}"

    return ok_response({"CODIGO_SUGERIDO": codigo_sugerido})


# --------------------------------------------------------------------
# Documentos Externos (normas ISO, certificados de proveedores, etc.)
# Según CAL-PRO-01 6.4: registro simple, SIN flujo de autorización.
# Solo Calidad (o quien tenga permiso EDITAR en SGC_DOCUMENTOS) los da
# de alta. Sí respetan Confidencial/Interno/Público, igual que los
# documentos internos (Calidad elige la Visibilidad, el Departamento
# y/o la Sucursal manualmente al registrarlos).
# --------------------------------------------------------------------


@sgc_bp.route("/external-documents", methods=["GET"])
def get_external_documents():
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("SGC_DOCUMENTOS", "VER")
    if not allowed:
        return response

    viewer = _get_viewer_context()
    is_quality = _is_quality(viewer)

    rows = fetch_all(f"""
        SELECT
            E."SGCEXTID", E."TITULO", E."ORIGEN", E."FECHA_RECEPCION",
            E."ARCHIVO_NOMBRE_ORIGINAL", E."ACTIVO", E."FECHA_REGISTRO",
            E."VISIBILIDAD", E."DEPAID", E."SUCURSAL",
            D."NOMBRE" AS "DEPARTAMENTO_NOMBRE",
            U."NOMBRE" AS "REGISTRADO_POR_NOMBRE"
        FROM "{SCHEMA}"."SGC_DOCUMENTOS_EXTERNOS" E
        LEFT JOIN "{SCHEMA}"."USUARIOS" U ON E."REGISTRADO_POR" = U."USUARIOID"
        LEFT JOIN "{SCHEMA}"."DEPARTAMENTOS" D ON E."DEPAID" = D."DEPAID"
        ORDER BY E."FECHA_REGISTRO" DESC
        """)

    result = []
    for row in rows:
        if not _can_view_external_document(row, viewer, is_quality):
            continue
        row["ARCHIVO_DISPONIBLE"] = bool(row.get("ARCHIVO_NOMBRE_ORIGINAL"))
        result.append(row)

    return ok_response(_serialize_dates_in_place(result))


@sgc_bp.route("/external-documents", methods=["POST"])
def create_external_document():
    valid, response = validate_active_session()
    if not valid:
        return response

    viewer = _get_viewer_context()
    if not _is_quality(viewer) and not user_has_permission("SGC_DOCUMENTOS", "EDITAR"):
        return error_response(
            "No tienes permiso para registrar documentos externos", 403
        )

    form = request.form
    titulo = (form.get("TITULO") or "").strip()

    if not titulo:
        return error_response("El campo TITULO es requerido", 400)

    stored_name, original_name = None, None
    uploaded_file = request.files.get("file")
    if uploaded_file and uploaded_file.filename:
        try:
            stored_name, original_name = _save_uploaded_file(uploaded_file)
        except ValueError as exc:
            return error_response(str(exc), 400)

    new_id = get_next_id("SGC_DOCUMENTOS_EXTERNOS", "SGCEXTID")

    execute_query(
        f"""
        INSERT INTO "{SCHEMA}"."SGC_DOCUMENTOS_EXTERNOS"
        ("SGCEXTID","TITULO","ORIGEN","FECHA_RECEPCION","ARCHIVO_URL","ARCHIVO_NOMBRE_ORIGINAL","REGISTRADO_POR","ACTIVO","VISIBILIDAD","DEPAID","SUCURSAL")
        VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
        """,
        [
            new_id,
            titulo,
            (form.get("ORIGEN") or "").strip() or None,
            _parse_date(form.get("FECHA_RECEPCION")),
            stored_name,
            original_name,
            current_user_id(),
            (form.get("VISIBILIDAD") or "Confidencial").strip(),
            form.get("DEPAID") or None,
            (form.get("SUCURSAL") or "").strip() or None,
        ],
    )

    return ok_response({"SGCEXTID": new_id}, "Documento externo registrado", 201)


@sgc_bp.route("/external-documents/<int:ext_id>/status", methods=["PATCH"])
def update_external_document_status(ext_id):
    valid, response = validate_active_session()
    if not valid:
        return response

    viewer = _get_viewer_context()
    if not _is_quality(viewer) and not user_has_permission("SGC_DOCUMENTOS", "EDITAR"):
        return error_response(
            "No tienes permiso para modificar documentos externos", 403
        )

    data = request.get_json(silent=True) or {}

    if "ACTIVO" not in data:
        return error_response("El campo ACTIVO es requerido", 400)

    execute_query(
        f'UPDATE "{SCHEMA}"."SGC_DOCUMENTOS_EXTERNOS" SET "ACTIVO" = ? WHERE "SGCEXTID" = ?',
        [data.get("ACTIVO"), ext_id],
    )

    return ok_response(message="Estado actualizado")


@sgc_bp.route("/external-documents/<int:ext_id>", methods=["DELETE"])
def delete_external_document(ext_id):
    valid, response = validate_active_session()
    if not valid:
        return response

    viewer = _get_viewer_context()
    if not _is_quality(viewer) and not user_has_permission("SGC_DOCUMENTOS", "EDITAR"):
        return error_response(
            "No tienes permiso para eliminar documentos externos", 403
        )

    existing = fetch_one(
        f'SELECT "SGCEXTID","ARCHIVO_URL" FROM "{SCHEMA}"."SGC_DOCUMENTOS_EXTERNOS" WHERE "SGCEXTID" = ?',
        [ext_id],
    )
    if not existing:
        return error_response("Documento externo no encontrado", 404)

    execute_query(
        f'DELETE FROM "{SCHEMA}"."SGC_DOCUMENTOS_EXTERNOS" WHERE "SGCEXTID" = ?',
        [ext_id],
    )

    archivo = existing.get("ARCHIVO_URL")
    if archivo:
        path = os.path.join(UPLOAD_DIR, archivo)
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass

    return ok_response(message="Documento externo eliminado")


@sgc_bp.route("/external-documents/<int:ext_id>/file", methods=["GET"])
def download_external_document_file(ext_id):
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("SGC_DOCUMENTOS", "VER")
    if not allowed:
        return response

    row = fetch_one(
        f'SELECT "ARCHIVO_URL","ARCHIVO_NOMBRE_ORIGINAL","VISIBILIDAD","DEPAID","SUCURSAL" '
        f'FROM "{SCHEMA}"."SGC_DOCUMENTOS_EXTERNOS" WHERE "SGCEXTID" = ?',
        [ext_id],
    )

    if not row:
        return error_response("Documento externo no encontrado", 404)

    viewer = _get_viewer_context()
    is_quality = _is_quality(viewer)

    if not _can_view_external_document(row, viewer, is_quality):
        return error_response("No tienes acceso a este documento", 403)

    if not row.get("ARCHIVO_URL"):
        return error_response("Este documento externo no tiene archivo adjunto", 404)

    filename = row["ARCHIVO_URL"]
    download_name = row.get("ARCHIVO_NOMBRE_ORIGINAL") or filename

    if not os.path.exists(os.path.join(UPLOAD_DIR, filename)):
        return error_response("El archivo ya no existe en el servidor", 404)

    return send_from_directory(
        UPLOAD_DIR, filename, as_attachment=True, download_name=download_name
    )


# --------------------------------------------------------------------
# Recordatorios de vencimiento por correo (Tarea Programada de Windows)
# --------------------------------------------------------------------


@sgc_bp.route("/documents/check-reminders", methods=["POST", "GET"])
def check_reminders():
    provided_key = request.args.get("key") or request.headers.get("X-Cron-Secret")

    if not CRON_SECRET or provided_key != CRON_SECRET:
        return error_response("No autorizado", 401)

    rows = fetch_all(
        _select_documents_base()
        + ' WHERE S."ACTIVO" = 1 AND S."FECHA_LIMITE" IS NOT NULL'
    )

    enviados = []
    omitidos = []
    errores = []

    for row in rows:
        estado, dias = calcular_estado(row.get("FECHA_LIMITE"))

        if estado not in ("VENCIDO", "URGENTE", "PROXIMO_A_VENCER"):
            continue

        email = row.get("RESPONSABLE_EMAIL")
        ultimo_envio = row.get("ULTIMO_RECORDATORIO_ENVIADO")

        ya_enviado_hoy = False
        if ultimo_envio:
            try:
                ultimo_envio_dt = (
                    ultimo_envio
                    if isinstance(ultimo_envio, datetime)
                    else datetime.fromisoformat(str(ultimo_envio))
                )
                ya_enviado_hoy = ultimo_envio_dt.date() == date.today()
            except (ValueError, TypeError):
                ya_enviado_hoy = False

        if ya_enviado_hoy:
            omitidos.append({"SGCID": row["SGCID"], "motivo": "ya notificado hoy"})
            continue

        if not email:
            omitidos.append(
                {"SGCID": row["SGCID"], "motivo": "sin correo de responsable"}
            )
            continue

        row["FECHA_LIMITE"] = str(row.get("FECHA_LIMITE"))
        asunto, html_body, text_body = build_sgc_reminder_email(row, estado, dias)
        ok, err = send_email(email, asunto, html_body, text_body)

        if ok:
            execute_query(
                f'UPDATE "{SCHEMA}"."SGC_DOCUMENTOS" SET "ULTIMO_RECORDATORIO_ENVIADO" = CURRENT_TIMESTAMP WHERE "SGCID" = ?',
                [row["SGCID"]],
            )
            enviados.append({"SGCID": row["SGCID"], "email": email, "estado": estado})
        else:
            errores.append({"SGCID": row["SGCID"], "error": err})

    return ok_response(
        {
            "enviados": enviados,
            "omitidos": omitidos,
            "errores": errores,
            "total_revisados": len(rows),
        },
        "Chequeo de recordatorios completado",
    )
