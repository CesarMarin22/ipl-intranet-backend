import os
from flask import Blueprint, request, session
from app.db import fetch_all, execute_query
from app.helpers import ok_response, error_response, require_login

permissions_bp = Blueprint("permissions", __name__)
SCHEMA = os.getenv("HANA_SCHEMA", "QRTEST")


@permissions_bp.route("/menu", methods=["GET"])
def get_menu():
    if not require_login():
        return error_response("No autenticado", 401)

    user_id = session["user_id"]

    rows = fetch_all(
        f'''
        SELECT DISTINCT
            "MODULOID",
            "NOMBRE_MODULO",
            "RUTA"
        FROM "{SCHEMA}"."VW_PERMISOS_EFECTIVOS"
        WHERE "USUARIOID" = ?
          AND "CLAVE_ACCION" = 'VER'
          AND "PERMITIDO_FINAL" = 1
        ORDER BY "MODULOID"
        ''',
        [user_id]
    )

    return ok_response(rows)


@permissions_bp.route("/permissions", methods=["GET"])
def get_permissions():
    if not require_login():
        return error_response("No autenticado", 401)

    user_id = session["user_id"]

    rows = fetch_all(
        f'''
        SELECT *
        FROM "{SCHEMA}"."VW_PERMISOS_EFECTIVOS"
        WHERE "USUARIOID" = ?
        ORDER BY "MODULOID", "ACCIONID"
        ''',
        [user_id]
    )

    return ok_response(rows)


@permissions_bp.route("/profile-permissions/<int:perfil_id>", methods=["GET"])
def get_profile_permissions(perfil_id):
    if not require_login():
        return error_response("No autenticado", 401)

    rows = fetch_all(
        f'''
        SELECT *
        FROM "{SCHEMA}"."PERMISOS_PERFIL"
        WHERE "PERFILID" = ?
        ORDER BY "MODULOID", "ACCIONID"
        ''',
        [perfil_id]
    )

    return ok_response(rows)


@permissions_bp.route("/profile-permissions/<int:perfil_id>", methods=["PUT"])
def replace_profile_permissions(perfil_id):
    if not require_login():
        return error_response("No autenticado", 401)

    data = request.get_json(silent=True) or {}
    permissions = data.get("permissions", [])

    execute_query(
        f'''
        DELETE FROM "{SCHEMA}"."PERMISOS_PERFIL"
        WHERE "PERFILID" = ?
        ''',
        [perfil_id]
    )

    for item in permissions:
        execute_query(
            f'''
            INSERT INTO "{SCHEMA}"."PERMISOS_PERFIL"
            ("PERFILID", "MODULOID", "ACCIONID", "PERMITIDO", "ACTIVO")
            VALUES (?, ?, ?, ?, ?)
            ''',
            [
                perfil_id,
                item.get("MODULOID"),
                item.get("ACCIONID"),
                item.get("PERMITIDO", 1),
                item.get("ACTIVO", 1),
            ]
        )

    return ok_response(message="Permisos por perfil actualizados")


@permissions_bp.route("/user-permissions/<int:usuario_id>", methods=["GET"])
def get_user_permissions(usuario_id):
    if not require_login():
        return error_response("No autenticado", 401)

    rows = fetch_all(
        f'''
        SELECT *
        FROM "{SCHEMA}"."PERMISOS_USUARIO"
        WHERE "USUARIOID" = ?
        ORDER BY "MODULOID", "ACCIONID"
        ''',
        [usuario_id]
    )

    return ok_response(rows)


@permissions_bp.route("/user-permissions/<int:usuario_id>", methods=["PUT"])
def replace_user_permissions(usuario_id):
    if not require_login():
        return error_response("No autenticado", 401)

    data = request.get_json(silent=True) or {}
    permissions = data.get("permissions", [])

    execute_query(
        f'''
        DELETE FROM "{SCHEMA}"."PERMISOS_USUARIO"
        WHERE "USUARIOID" = ?
        ''',
        [usuario_id]
    )

    for item in permissions:
        execute_query(
            f'''
            INSERT INTO "{SCHEMA}"."PERMISOS_USUARIO"
            ("USUARIOID", "MODULOID", "ACCIONID", "PERMITIDO", "ORIGEN", "ACTIVO")
            VALUES (?, ?, ?, ?, ?, ?)
            ''',
            [
                usuario_id,
                item.get("MODULOID"),
                item.get("ACCIONID"),
                item.get("PERMITIDO", 1),
                item.get("ORIGEN", "USUARIO"),
                item.get("ACTIVO", 1),
            ]
        )

    return ok_response(message="Permisos por usuario actualizados")