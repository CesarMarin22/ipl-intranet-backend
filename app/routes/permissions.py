import os
from flask import Blueprint, request, session
from app.db import fetch_all, fetch_one, execute_query
from app.helpers import (
    ok_response,
    error_response,
    validate_active_session,
    require_permission,
)

permissions_bp = Blueprint("permissions", __name__)
SCHEMA = os.getenv("HANA_SCHEMA", "QRTEST")


@permissions_bp.route("/menu", methods=["GET"])
def get_menu():
    valid, response = validate_active_session()
    if not valid:
        return response

    user_id = session["user_id"]

    rows = fetch_all(
        f'''
        SELECT DISTINCT
            "MODULOID",
            "CLAVE_MODULO",
            "NOMBRE_MODULO",
            "RUTA"
        FROM "{SCHEMA}"."VW_PERMISOS_EFECTIVOS"
        WHERE "USUARIOID" = ?
          AND UPPER("CLAVE_ACCION") = 'VER'
          AND "PERMITIDO_FINAL" = 1
        ORDER BY "MODULOID"
        ''',
        [user_id],
    )

    return ok_response(rows)


@permissions_bp.route("/me", methods=["GET"])
def get_my_permissions():
    valid, response = validate_active_session()
    if not valid:
        return response

    user_id = session["user_id"]

    rows = fetch_all(
        f'''
        SELECT
            "USUARIOID",
            "USUARIO",
            "NOMBRE_USUARIO",
            "PERFILID",
            "NOMBRE_PERFIL",
            "MODULOID",
            "CLAVE_MODULO",
            "NOMBRE_MODULO",
            "RUTA",
            "ACCIONID",
            "CLAVE_ACCION",
            "NOMBRE_ACCION",
            "PERMITIDO_FINAL",
            "ORIGEN_FINAL"
        FROM "{SCHEMA}"."VW_PERMISOS_EFECTIVOS"
        WHERE "USUARIOID" = ?
        ORDER BY "MODULOID", "ACCIONID"
        ''',
        [user_id],
    )

    return ok_response(rows)


@permissions_bp.route("/effective/<int:usuario_id>", methods=["GET"])
def get_effective_permissions_by_user(usuario_id):
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("PERMISOS", "VER")
    if not allowed:
        return response

    rows = fetch_all(
        f'''
        SELECT
            "USUARIOID",
            "USUARIO",
            "NOMBRE_USUARIO",
            "PERFILID",
            "NOMBRE_PERFIL",
            "MODULOID",
            "CLAVE_MODULO",
            "NOMBRE_MODULO",
            "RUTA",
            "ACCIONID",
            "CLAVE_ACCION",
            "NOMBRE_ACCION",
            "PERMITIDO_FINAL",
            "ORIGEN_FINAL"
        FROM "{SCHEMA}"."VW_PERMISOS_EFECTIVOS"
        WHERE "USUARIOID" = ?
        ORDER BY "MODULOID", "ACCIONID"
        ''',
        [usuario_id],
    )

    return ok_response(rows)


@permissions_bp.route("/profile-permissions/<int:perfil_id>", methods=["GET"])
def get_profile_permissions(perfil_id):
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("PERMISOS", "VER")
    if not allowed:
        return response

    rows = fetch_all(
        f'''
        SELECT
            "PERFILID",
            "MODULOID",
            "ACCIONID",
            "PERMITIDO",
            "ACTIVO"
        FROM "{SCHEMA}"."PERMISOS_PERFIL"
        WHERE "PERFILID" = ?
        ORDER BY "MODULOID", "ACCIONID"
        ''',
        [perfil_id],
    )

    return ok_response(rows)


@permissions_bp.route("/profile-permissions/<int:perfil_id>", methods=["PUT"])
def replace_profile_permissions(perfil_id):
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("PERMISOS", "EDITAR")
    if not allowed:
        return response

    perfil = fetch_one(
        f'''
        SELECT "PERFILID"
        FROM "{SCHEMA}"."PERFILES"
        WHERE "PERFILID" = ?
        ''',
        [perfil_id],
    )

    if not perfil:
        return error_response("Perfil no encontrado", 404)

    data = request.get_json(silent=True) or {}
    permissions = data.get("permissions", [])

    execute_query(
        f'''
        DELETE FROM "{SCHEMA}"."PERMISOS_PERFIL"
        WHERE "PERFILID" = ?
        ''',
        [perfil_id],
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
            ],
        )

    return ok_response(message="Permisos por perfil actualizados")


@permissions_bp.route("/user-permissions/<int:usuario_id>", methods=["GET"])
def get_user_permissions(usuario_id):
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("PERMISOS_USUARIO", "VER")
    if not allowed:
        return response

    rows = fetch_all(
        f'''
        SELECT
            "USUARIOID",
            "MODULOID",
            "ACCIONID",
            "PERMITIDO",
            "ORIGEN",
            "ACTIVO"
        FROM "{SCHEMA}"."PERMISOS_USUARIO"
        WHERE "USUARIOID" = ?
        ORDER BY "MODULOID", "ACCIONID"
        ''',
        [usuario_id],
    )

    return ok_response(rows)


@permissions_bp.route("/user-permissions/<int:usuario_id>", methods=["PUT"])
def replace_user_permissions(usuario_id):
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("PERMISOS_USUARIO", "EDITAR")
    if not allowed:
        return response

    usuario = fetch_one(
        f'''
        SELECT "USUARIOID"
        FROM "{SCHEMA}"."USUARIOS"
        WHERE "USUARIOID" = ?
        ''',
        [usuario_id],
    )

    if not usuario:
        return error_response("Usuario no encontrado", 404)

    data = request.get_json(silent=True) or {}
    permissions = data.get("permissions", [])

    execute_query(
        f'''
        DELETE FROM "{SCHEMA}"."PERMISOS_USUARIO"
        WHERE "USUARIOID" = ?
        ''',
        [usuario_id],
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
            ],
        )

    return ok_response(message="Permisos por usuario actualizados")


@permissions_bp.route(
    "/user-permissions/<int:usuario_id>/<int:modulo_id>/<int:accion_id>",
    methods=["DELETE"],
)
def delete_user_permission_override(usuario_id, modulo_id, accion_id):
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("PERMISOS", "EDITAR")
    if not allowed:
        return response

    existing = fetch_one(
        f'''
        SELECT "USUARIOID"
        FROM "{SCHEMA}"."PERMISOS_USUARIO"
        WHERE "USUARIOID" = ?
          AND "MODULOID" = ?
          AND "ACCIONID" = ?
        ''',
        [usuario_id, modulo_id, accion_id],
    )

    if not existing:
        return error_response("Override de permiso no encontrado", 404)

    execute_query(
        f'''
        DELETE FROM "{SCHEMA}"."PERMISOS_USUARIO"
        WHERE "USUARIOID" = ?
          AND "MODULOID" = ?
          AND "ACCIONID" = ?
        ''',
        [usuario_id, modulo_id, accion_id],
    )

    return ok_response(message="Override eliminado")