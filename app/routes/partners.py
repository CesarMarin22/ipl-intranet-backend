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

partners_bp = Blueprint("partners", __name__)
SCHEMA = os.getenv("HANA_SCHEMA", "QRTEST")


@partners_bp.route("", methods=["GET"])
def get_partners():
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("SOCIOS", "VER")
    if not allowed:
        return response

    rows = fetch_all(
        f'''
        SELECT *
        FROM "{SCHEMA}"."SOCIOS"
        ORDER BY "SOCIOID"
        '''
    )
    return ok_response(rows)


@partners_bp.route("/<int:socio_id>", methods=["GET"])
def get_partner(socio_id):
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("SOCIOS", "VER")
    if not allowed:
        return response

    row = fetch_one(
        f'''
        SELECT *
        FROM "{SCHEMA}"."SOCIOS"
        WHERE "SOCIOID" = ?
        ''',
        [socio_id]
    )

    if not row:
        return error_response("Socio no encontrado", 404)

    return ok_response(row)


@partners_bp.route("", methods=["POST"])
def create_partner():
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("SOCIOS", "CREAR")
    if not allowed:
        return response

    data = request.get_json(silent=True) or {}

    nombre = (data.get("NOMBRE") or "").strip()
    rfc = (data.get("RFC") or "").strip().upper() or None

    if not nombre:
        return error_response("El campo NOMBRE es requerido", 400)

    if "ACTIVO" not in data:
        return error_response("El campo ACTIVO es requerido", 400)

    existing = fetch_one(
        f'''
        SELECT "SOCIOID"
        FROM "{SCHEMA}"."SOCIOS"
        WHERE UPPER("NOMBRE") = UPPER(?)
        ''',
        [nombre]
    )

    if existing:
        return error_response("Ya existe un socio con ese nombre", 409)

    if rfc:
        existing_rfc = fetch_one(
            f'''
            SELECT "SOCIOID"
            FROM "{SCHEMA}"."SOCIOS"
            WHERE UPPER("RFC") = UPPER(?)
            ''',
            [rfc]
        )

        if existing_rfc:
            return error_response("Ya existe un socio con ese RFC", 409)

    new_id = get_next_id("SOCIOS", "SOCIOID")

    execute_query(
        f'''
        INSERT INTO "{SCHEMA}"."SOCIOS"
        ("SOCIOID", "NOMBRE", "RFC", "ACTIVO")
        VALUES (?, ?, ?, ?)
        ''',
        [
            new_id,
            nombre,
            rfc,
            data.get("ACTIVO", 1),
        ]
    )

    return ok_response({"SOCIOID": new_id}, "Socio creado", 201)


@partners_bp.route("/<int:socio_id>", methods=["PUT"])
def update_partner(socio_id):
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("SOCIOS", "EDITAR")
    if not allowed:
        return response

    data = request.get_json(silent=True) or {}

    existing = fetch_one(
        f'''
        SELECT "SOCIOID"
        FROM "{SCHEMA}"."SOCIOS"
        WHERE "SOCIOID" = ?
        ''',
        [socio_id]
    )

    if not existing:
        return error_response("Socio no encontrado", 404)

    nombre = (data.get("NOMBRE") or "").strip()
    rfc = (data.get("RFC") or "").strip().upper() or None

    if not nombre:
        return error_response("El campo NOMBRE es requerido", 400)

    if "ACTIVO" not in data:
        return error_response("El campo ACTIVO es requerido", 400)

    duplicate = fetch_one(
        f'''
        SELECT "SOCIOID"
        FROM "{SCHEMA}"."SOCIOS"
        WHERE UPPER("NOMBRE") = UPPER(?)
          AND "SOCIOID" <> ?
        ''',
        [nombre, socio_id]
    )

    if duplicate:
        return error_response("Ya existe otro socio con ese nombre", 409)

    if rfc:
        duplicate_rfc = fetch_one(
            f'''
            SELECT "SOCIOID"
            FROM "{SCHEMA}"."SOCIOS"
            WHERE UPPER("RFC") = UPPER(?)
              AND "SOCIOID" <> ?
            ''',
            [rfc, socio_id]
        )

        if duplicate_rfc:
            return error_response("Ya existe otro socio con ese RFC", 409)

    execute_query(
        f'''
        UPDATE "{SCHEMA}"."SOCIOS"
        SET
            "NOMBRE" = ?,
            "RFC" = ?,
            "ACTIVO" = ?
        WHERE "SOCIOID" = ?
        ''',
        [
            nombre,
            rfc,
            data.get("ACTIVO", 1),
            socio_id,
        ]
    )

    return ok_response(message="Socio actualizado")


@partners_bp.route("/<int:socio_id>/status", methods=["PATCH"])
def update_partner_status(socio_id):
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("SOCIOS", "EDITAR")
    if not allowed:
        return response

    data = request.get_json(silent=True) or {}

    if "ACTIVO" not in data:
        return error_response("El campo ACTIVO es requerido", 400)

    existing = fetch_one(
        f'''
        SELECT "SOCIOID"
        FROM "{SCHEMA}"."SOCIOS"
        WHERE "SOCIOID" = ?
        ''',
        [socio_id]
    )

    if not existing:
        return error_response("Socio no encontrado", 404)

    execute_query(
        f'''
        UPDATE "{SCHEMA}"."SOCIOS"
        SET "ACTIVO" = ?
        WHERE "SOCIOID" = ?
        ''',
        [data.get("ACTIVO"), socio_id]
    )

    return ok_response(message="Estado actualizado")


@partners_bp.route("/<int:socio_id>", methods=["DELETE"])
def delete_partner(socio_id):
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("SOCIOS", "ELIMINAR")
    if not allowed:
        return response

    existing = fetch_one(
        f'''
        SELECT "SOCIOID"
        FROM "{SCHEMA}"."SOCIOS"
        WHERE "SOCIOID" = ?
        ''',
        [socio_id]
    )

    if not existing:
        return error_response("Socio no encontrado", 404)

    try:
        execute_query(
            f'''
            DELETE FROM "{SCHEMA}"."SOCIOS"
            WHERE "SOCIOID" = ?
            ''',
            [socio_id]
        )
        return ok_response(message="Socio eliminado")
    except Exception as e:
        return error_response(f"No se pudo eliminar el socio: {str(e)}", 400)