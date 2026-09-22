import os
import traceback
import secrets
import qrcode
import qrcode.constants
from io import BytesIO
from datetime import datetime, timedelta
from flask import Blueprint, request, send_file, session
from PIL import Image, ImageDraw

from app.db import fetch_one, execute_query
from app.helpers import (
    ok_response,
    error_response,
    validate_active_session,
    get_next_id,
)

qr_bp = Blueprint("qr", __name__)
SCHEMA = os.getenv("HANA_SCHEMA", "QRTEST")


def get_or_create_user_token(usuario_id):
    row = fetch_one(
        f"""
        SELECT "TOKEN"
        FROM "{SCHEMA}"."QR_TOKENS"
        WHERE "TIPO" = 'USR'
          AND "REFERENCIA_ID" = ?
          AND "ACTIVO" = 1
          AND "REVOCADO" = 0
        """,
        [str(usuario_id)],
    )

    if row:
        return row["TOKEN"]

    token = secrets.token_urlsafe(32)
    qrid = get_next_id("QR_TOKENS", "QRID")

    execute_query(
        f"""
        INSERT INTO "{SCHEMA}"."QR_TOKENS"
        ("QRID", "TIPO", "REFERENCIA_ID", "TOKEN", "ACTIVO", "REVOCADO")
        VALUES (?, 'USR', ?, ?, 1, 0)
        """,
        [qrid, str(usuario_id), token],
    )

    return token


@qr_bp.route("/usuario/<int:usuario_id>", methods=["GET"])
def get_user_qr(usuario_id):
    valid, response = validate_active_session()
    if not valid:
        return response

    token = get_or_create_user_token(usuario_id)

    return ok_response(
        {
            "usuario_id": usuario_id,
            "token": token,
            "qr_string": f"IPL|USR|{token}",
        }
    )


@qr_bp.route("/usuario/<int:usuario_id>/regenerar", methods=["POST"])
def regenerate_qr(usuario_id):
    valid, response = validate_active_session()
    if not valid:
        return response

    execute_query(
        f"""
        UPDATE "{SCHEMA}"."QR_TOKENS"
        SET "REVOCADO" = 1
        WHERE "TIPO" = 'USR'
          AND "REFERENCIA_ID" = ?
          AND "ACTIVO" = 1
        """,
        [str(usuario_id)],
    )

    token = secrets.token_urlsafe(32)
    qrid = get_next_id("QR_TOKENS", "QRID")

    execute_query(
        f"""
        INSERT INTO "{SCHEMA}"."QR_TOKENS"
        ("QRID", "TIPO", "REFERENCIA_ID", "TOKEN", "ACTIVO", "REVOCADO")
        VALUES (?, 'USR', ?, ?, 1, 0)
        """,
        [qrid, str(usuario_id), token],
    )

    return ok_response(
        {
            "usuario_id": usuario_id,
            "token": token,
            "qr_string": f"IPL|USR|{token}",
        },
        "QR regenerado",
    )


@qr_bp.route("/usuario/<int:usuario_id>/revocar", methods=["POST"])
def revoke_qr(usuario_id):
    valid, response = validate_active_session()
    if not valid:
        return response

    execute_query(
        f"""
        UPDATE "{SCHEMA}"."QR_TOKENS"
        SET "REVOCADO" = 1
        WHERE "TIPO" = 'USR'
          AND "REFERENCIA_ID" = ?
        """,
        [str(usuario_id)],
    )

    return ok_response(message="QR revocado")


@qr_bp.route("/image", methods=["GET"])
def generate_qr():
    try:
        tipo = (request.args.get("tipo") or "").strip().upper()
        valor = (request.args.get("valor") or "").strip()

        if not tipo or not valor:
            return error_response("tipo y valor requeridos", 400)

        if tipo == "EQP":
            # Equipo: el QR contiene SOLO el número de serie
            contenido = valor

        elif tipo == "USR":
            usuario_id = int(valor)
            token = get_or_create_user_token(usuario_id)
            contenido = f"IPL|USR|{token}"
        
        elif tipo == "USR-DYN":
            contenido = valor
        
        elif tipo == "WEB":
            contenido = valor

        else:
            return error_response("Tipo no válido", 400)

        qr_builder = qrcode.QRCode(
            version=5,  # 🔥 MISMO TAMAÑO PARA TODOS
            error_correction=qrcode.constants.ERROR_CORRECT_H,
            box_size=10,
            border=4,
        )
        qr_builder.add_data(contenido)
        qr_builder.make(fit=True)

        qr = qr_builder.make_image(
            fill_color="black",
            back_color="white",
        ).convert("RGB")

        BASE_DIR = os.path.dirname(os.path.abspath(__file__))
        logo_path = os.path.abspath(
            os.path.join(BASE_DIR, "..", "static", "logo_ipl.jpg")
        )

        if os.path.exists(logo_path):
            logo = Image.open(logo_path).convert("RGBA")

            # Ajusta estos dos valores si quieres el logo más chico/grande
            logo_size = 110
            badge_size = 115

            logo = logo.resize((logo_size, logo_size), Image.LANCZOS)

            badge = Image.new(
                "RGBA",
                (badge_size, badge_size),
                (255, 255, 255, 0),
            )

            draw = ImageDraw.Draw(badge)

            # 🔥 fondo blanco cuadrado
            draw.rectangle(
                (0, 0, badge_size, badge_size),
                fill=(255, 255, 255, 255),
            )

            logo_pos = (
                (badge_size - logo_size) // 2,
                (badge_size - logo_size) // 2,
            )

            badge.paste(logo, logo_pos, logo)

            badge_pos = (
                (qr.size[0] - badge_size) // 2,
                (qr.size[1] - badge_size) // 2,
            )

            qr.paste(badge, badge_pos, badge)

        buffer = BytesIO()
        qr.save(buffer, format="JPEG", quality=100, subsampling=0)
        buffer.seek(0)

        return send_file(buffer, mimetype="image/jpeg")

    except Exception:
        error = traceback.format_exc()
        print(error)
        return error_response(error, 500)
    
@qr_bp.route("/image.jpg", methods=["GET"])
def generate_qr_jpg():
    return generate_qr()


@qr_bp.route("/scan", methods=["POST"])
def scan_qr():
    data = request.get_json(silent=True) or {}
    qr_value = (data.get("QR") or "").strip()

    if not qr_value:
        return error_response("QR vacío", 400)

    # Equipo: si no trae separador, devuelve SOLO el número de serie
    if "|" not in qr_value:
        return ok_response(qr_value)

    parts = qr_value.split("|")

    if len(parts) != 3:
        return error_response("QR inválido", 400)

    sistema, tipo, token = parts

    if sistema != "IPL":
        return error_response("QR inválido", 400)

    # Usuario QR estático
    if tipo == "USR":
        qr = fetch_one(
            f'''
            SELECT "REFERENCIA_ID"
            FROM "{SCHEMA}"."QR_TOKENS"
            WHERE "TOKEN" = ?
              AND "TIPO" = 'USR'
              AND "ACTIVO" = 1
              AND "REVOCADO" = 0
            ''',
            [token],
        )

        if not qr:
            return error_response("QR inválido", 404)

        return ok_response(qr["REFERENCIA_ID"])

    # Usuario QR dinámico
    if tipo == "USR-DYN":
        qr = fetch_one(
            f'''
            SELECT "USUARIOID"
            FROM "{SCHEMA}"."QR_TOKENS_DINAMICOS"
            WHERE "TOKEN" = ?
              AND "ACTIVO" = 1
              AND "USADO" = 0
              AND "FECHA_EXPIRACION" >= CURRENT_TIMESTAMP
            ''',
            [token],
        )

        if not qr:
            return error_response("QR dinámico inválido o expirado", 404)

        return ok_response(qr["USUARIOID"])

    return error_response("Tipo de QR no soportado", 400)


def crear_qr_dinamico_usuario(usuario_id, segundos=60):
    token = secrets.token_urlsafe(32)
    qrdyn_id = get_next_id("QR_TOKENS_DINAMICOS", "QRDYNID")

    fecha_expiracion = datetime.now() + timedelta(seconds=segundos)

    execute_query(
        f"""
        INSERT INTO "{SCHEMA}"."QR_TOKENS_DINAMICOS"
        ("QRDYNID", "USUARIOID", "TOKEN", "FECHA_EXPIRACION", "USADO", "ACTIVO")
        VALUES (?, ?, ?, ?, 0, 1)
        """,
        [qrdyn_id, usuario_id, token, fecha_expiracion],
    )

    return token, fecha_expiracion


@qr_bp.route("/usuario/dinamico", methods=["GET"])
def get_dynamic_user_qr():
    valid, response = validate_active_session()
    if not valid:
        return response

    usuario_id = session.get("user_id")
    if not usuario_id:
        return error_response("No autenticado", 401)

    user = fetch_one(
        f"""
        SELECT
            "USUARIOID",
            "NOMBRE",
            "USUARIO",
            "NUMERO_EMPLEADO",
            "SUCURSAL"
        FROM "{SCHEMA}"."USUARIOS"
        WHERE "USUARIOID" = ?
          AND "ACTIVO" = 1
        """,
        [usuario_id],
    )

    if not user:
        return error_response("Usuario no encontrado", 404)

    token, fecha_expiracion = crear_qr_dinamico_usuario(usuario_id, 60)

    return ok_response(
        {
            "qr_string": f"IPL|USR-DYN|{token}",
            "expira_en_segundos": 60,
            "fecha_expiracion": str(fecha_expiracion),
            "usuario": {
                "USUARIOID": user["USUARIOID"],
                "NOMBRE": user["NOMBRE"],
                "USUARIO": user["USUARIO"],
                "NUMERO_EMPLEADO": user["NUMERO_EMPLEADO"],
                "SUCURSAL": user["SUCURSAL"],
            },
        }
    )
    
@qr_bp.route("/create-qr-code", methods=["GET"])
def create_qr_code_crystal():
    print("CRYSTAL PIDIO QR:", request.url)
    valor = (request.args.get("data") or "").strip()

    if not valor:
        return error_response("data requerido", 400)

    return generar_qr_equipo_para_crystal(valor)


def generar_qr_equipo_para_crystal(valor):
    try:
        contenido = valor.strip()

        qr_builder = qrcode.QRCode(
            version=5,
            error_correction=qrcode.constants.ERROR_CORRECT_H,
            box_size=10,
            border=4,
        )
        qr_builder.add_data(contenido)
        qr_builder.make(fit=True)

        qr = qr_builder.make_image(
            fill_color="black",
            back_color="white",
        ).convert("RGB")

        BASE_DIR = os.path.dirname(os.path.abspath(__file__))
        logo_path = os.path.abspath(
            os.path.join(BASE_DIR, "..", "static", "logo_ipl.jpg")
        )

        if os.path.exists(logo_path):
            logo = Image.open(logo_path).convert("RGBA")

            logo_size = 110
            badge_size = 115

            logo = logo.resize((logo_size, logo_size), Image.LANCZOS)

            badge = Image.new(
                "RGBA",
                (badge_size, badge_size),
                (255, 255, 255, 0),
            )

            draw = ImageDraw.Draw(badge)
            draw.rectangle(
                (0, 0, badge_size, badge_size),
                fill=(255, 255, 255, 255),
            )

            logo_pos = (
                (badge_size - logo_size) // 2,
                (badge_size - logo_size) // 2,
            )

            badge.paste(logo, logo_pos, logo)

            badge_pos = (
                (qr.size[0] - badge_size) // 2,
                (qr.size[1] - badge_size) // 2,
            )

            qr.paste(badge, badge_pos, badge)

        buffer = BytesIO()
        qr.save(buffer, format="JPEG", quality=100, subsampling=0)
        buffer.seek(0)

        response = send_file(buffer, mimetype="image/jpeg")
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        response.headers["Content-Disposition"] = "inline; filename=qr.jpg"
        return response

    except Exception:
        error = traceback.format_exc()
        print(error)
        return error_response(error, 500)
    
    
@qr_bp.route("/create-qr-code-simple", methods=["GET"])
def create_qr_code_simple():
    valor = (request.args.get("data") or "").strip()

    if not valor:
        return "data requerido", 400

    qr = qrcode.make(valor)

    buffer = BytesIO()
    qr.save(buffer, format="PNG")
    buffer.seek(0)

    return send_file(buffer, mimetype="image/png")

@qr_bp.route("/values", methods=["GET"])
def qr_values_cfdi():
    texto = (request.args.get("Txt") or "").strip()

    if not texto:
        return "Txt requerido", 400

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=4,
    )
    qr.add_data(texto)
    qr.make(fit=True)

    img = qr.make_image(
        fill_color="black",
        back_color="white",
    ).convert("RGB")

    buffer = BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)

    return send_file(buffer, mimetype="image/png")