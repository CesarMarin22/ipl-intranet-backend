import os
from flask import Blueprint, request
from app.db import fetch_all, fetch_one, execute_query
from app.helpers import (
    ok_response,
    error_response,
    validate_active_session,
    require_permission,
    get_next_id,
)

profiles_bp = Blueprint("profiles", __name__)
SCHEMA = os.getenv("HANA_SCHEMA", "QRTEST")


@profiles_bp.route("", methods=["GET"])
def get_profiles():
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("PERFILES", "VER")
    if not allowed:
        return response

    rows = fetch_all(
        f'''
        SELECT *
        FROM "{SCHEMA}"."PERFILES"
        ORDER BY "PERFILID"
        '''
    )
    return ok_response(rows)


@profiles_bp.route("/<int:perfil_id>", methods=["GET"])
def get_profile(perfil_id):
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("PERFILES", "VER")
    if not allowed:
        return response

    row = fetch_one(
        f'''
        SELECT *
        FROM "{SCHEMA}"."PERFILES"
        WHERE "PERFILID" = ?
        ''',
        [perfil_id]
    )

    if not row:
        return error_response("Perfil no encontrado", 404)

    return ok_response(row)


@profiles_bp.route("", methods=["POST"])
def create_profile():
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("PERFILES", "CREAR")
    if not allowed:
        return response

    data = request.get_json(silent=True) or {}

    nombre = (data.get("NOMBRE") or "").strip()
    descripcion = (data.get("DESCRIPCION") or "").strip()

    if not nombre:
        return error_response("El campo NOMBRE es requerido", 400)

    if "ACTIVO" not in data:
        return error_response("El campo ACTIVO es requerido", 400)

    existing = fetch_one(
        f'''
        SELECT "PERFILID"
        FROM "{SCHEMA}"."PERFILES"
        WHERE UPPER("NOMBRE") = UPPER(?)
        ''',
        [nombre]
    )

    if existing:
        return error_response("Ya existe un perfil con ese nombre", 409)

    new_id = get_next_id("PERFILES", "PERFILID")

    execute_query(
        f'''
        INSERT INTO "{SCHEMA}"."PERFILES"
        ("PERFILID", "NOMBRE", "DESCRIPCION", "ACTIVO")
        VALUES (?, ?, ?, ?)
        ''',
        [
            new_id,
            nombre,
            descripcion,
            data.get("ACTIVO", 1),
        ]
    )

    return ok_response({"PERFILID": new_id}, "Perfil creado", 201)


@profiles_bp.route("/<int:perfil_id>", methods=["PUT"])
def update_profile(perfil_id):
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("PERFILES", "EDITAR")
    if not allowed:
        return response

    data = request.get_json(silent=True) or {}

    existing = fetch_one(
        f'''
        SELECT "PERFILID"
        FROM "{SCHEMA}"."PERFILES"
        WHERE "PERFILID" = ?
        ''',
        [perfil_id]
    )

    if not existing:
        return error_response("Perfil no encontrado", 404)

    nombre = (data.get("NOMBRE") or "").strip()
    descripcion = (data.get("DESCRIPCION") or "").strip()

    if not nombre:
        return error_response("El campo NOMBRE es requerido", 400)

    if "ACTIVO" not in data:
        return error_response("El campo ACTIVO es requerido", 400)

    duplicate = fetch_one(
        f'''
        SELECT "PERFILID"
        FROM "{SCHEMA}"."PERFILES"
        WHERE UPPER("NOMBRE") = UPPER(?)
          AND "PERFILID" <> ?
        ''',
        [nombre, perfil_id]
    )

    if duplicate:
        return error_response("Ya existe otro perfil con ese nombre", 409)

    execute_query(
        f'''
        UPDATE "{SCHEMA}"."PERFILES"
        SET
            "NOMBRE" = ?,
            "DESCRIPCION" = ?,
            "ACTIVO" = ?
        WHERE "PERFILID" = ?
        ''',
        [
            nombre,
            descripcion,
            data.get("ACTIVO", 1),
            perfil_id,
        ]
    )

    return ok_response(message="Perfil actualizado")


@profiles_bp.route("/<int:perfil_id>/status", methods=["PATCH"])
def update_profile_status(perfil_id):
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("PERFILES", "EDITAR")
    if not allowed:
        return response

    data = request.get_json(silent=True) or {}

    if "ACTIVO" not in data:
        return error_response("El campo ACTIVO es requerido", 400)

    existing = fetch_one(
        f'''
        SELECT "PERFILID"
        FROM "{SCHEMA}"."PERFILES"
        WHERE "PERFILID" = ?
        ''',
        [perfil_id]
    )

    if not existing:
        return error_response("Perfil no encontrado", 404)

    execute_query(
        f'''
        UPDATE "{SCHEMA}"."PERFILES"
        SET "ACTIVO" = ?
        WHERE "PERFILID" = ?
        ''',
        [data.get("ACTIVO"), perfil_id]
    )

    return ok_response(message="Estado actualizado")


@profiles_bp.route("/<int:perfil_id>", methods=["DELETE"])
def delete_profile(perfil_id):
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("PERFILES", "ELIMINAR")
    if not allowed:
        return response

    existing = fetch_one(
        f'''
        SELECT "PERFILID"
        FROM "{SCHEMA}"."PERFILES"
        WHERE "PERFILID" = ?
        ''',
        [perfil_id]
    )

    if not existing:
        return error_response("Perfil no encontrado", 404)

    try:
        execute_query(
            f'''
            DELETE FROM "{SCHEMA}"."PERFILES"
            WHERE "PERFILID" = ?
            ''',
            [perfil_id]
        )
        return ok_response(message="Perfil eliminado")
    except Exception as e:
        return error_response(f"No se pudo eliminar el perfil: {str(e)}", 400)