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

departments_bp = Blueprint("departments", __name__)
SCHEMA = os.getenv("HANA_SCHEMA", "QRTEST")


@departments_bp.route("", methods=["GET"])
def get_departments():
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("DEPARTAMENTOS", "VER")
    if not allowed:
        return response

    rows = fetch_all(
        f'''
        SELECT *
        FROM "{SCHEMA}"."DEPARTAMENTOS"
        ORDER BY "DEPAID"
        '''
    )
    return ok_response(rows)


@departments_bp.route("/<int:depa_id>", methods=["GET"])
def get_department(depa_id):
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("DEPARTAMENTOS", "VER")
    if not allowed:
        return response

    row = fetch_one(
        f'''
        SELECT *
        FROM "{SCHEMA}"."DEPARTAMENTOS"
        WHERE "DEPAID" = ?
        ''',
        [depa_id]
    )

    if not row:
        return error_response("Departamento no encontrado", 404)

    return ok_response(row)


@departments_bp.route("", methods=["POST"])
def create_department():
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("DEPARTAMENTOS", "CREAR")
    if not allowed:
        return response

    data = request.get_json(silent=True) or {}

    nombre = (data.get("NOMBRE") or "").strip()

    if not nombre:
        return error_response("El campo NOMBRE es requerido", 400)

    if "ACTIVO" not in data:
        return error_response("El campo ACTIVO es requerido", 400)

    existing = fetch_one(
        f'''
        SELECT "DEPAID"
        FROM "{SCHEMA}"."DEPARTAMENTOS"
        WHERE UPPER("NOMBRE") = UPPER(?)
        ''',
        [nombre]
    )

    if existing:
        return error_response("Ya existe un departamento con ese nombre", 409)

    new_id = get_next_id("DEPARTAMENTOS", "DEPAID")

    execute_query(
        f'''
        INSERT INTO "{SCHEMA}"."DEPARTAMENTOS"
        ("DEPAID", "NOMBRE", "ACTIVO")
        VALUES (?, ?, ?)
        ''',
        [
            new_id,
            nombre,
            data.get("ACTIVO", 1),
        ]
    )

    return ok_response({"DEPAID": new_id}, "Departamento creado", 201)

@departments_bp.route("/<int:depa_id>", methods=["PUT"])
def update_department(depa_id):
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("DEPARTAMENTOS", "EDITAR")
    if not allowed:
        return response

    data = request.get_json(silent=True) or {}

    existing = fetch_one(
        f'''
        SELECT "DEPAID"
        FROM "{SCHEMA}"."DEPARTAMENTOS"
        WHERE "DEPAID" = ?
        ''',
        [depa_id]
    )

    if not existing:
        return error_response("Departamento no encontrado", 404)

    nombre = (data.get("NOMBRE") or "").strip()

    if not nombre:
        return error_response("El campo NOMBRE es requerido", 400)

    if "ACTIVO" not in data:
        return error_response("El campo ACTIVO es requerido", 400)

    duplicate = fetch_one(
        f'''
        SELECT "DEPAID"
        FROM "{SCHEMA}"."DEPARTAMENTOS"
        WHERE UPPER("NOMBRE") = UPPER(?)
          AND "DEPAID" <> ?
        ''',
        [nombre, depa_id]
    )

    if duplicate:
        return error_response("Ya existe otro departamento con ese nombre", 409)

    execute_query(
        f'''
        UPDATE "{SCHEMA}"."DEPARTAMENTOS"
        SET
            "NOMBRE" = ?,
            "ACTIVO" = ?
        WHERE "DEPAID" = ?
        ''',
        [
            nombre,
            data.get("ACTIVO", 1),
            depa_id,
        ]
    )

    return ok_response(message="Departamento actualizado")


@departments_bp.route("/<int:depa_id>/status", methods=["PATCH"])
def update_department_status(depa_id):
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("DEPARTAMENTOS", "EDITAR")
    if not allowed:
        return response

    data = request.get_json(silent=True) or {}

    if "ACTIVO" not in data:
        return error_response("El campo ACTIVO es requerido", 400)

    existing = fetch_one(
        f'''
        SELECT "DEPAID"
        FROM "{SCHEMA}"."DEPARTAMENTOS"
        WHERE "DEPAID" = ?
        ''',
        [depa_id]
    )

    if not existing:
        return error_response("Departamento no encontrado", 404)

    execute_query(
        f'''
        UPDATE "{SCHEMA}"."DEPARTAMENTOS"
        SET "ACTIVO" = ?
        WHERE "DEPAID" = ?
        ''',
        [data.get("ACTIVO"), depa_id]
    )

    return ok_response(message="Estado actualizado")


@departments_bp.route("/<int:depa_id>", methods=["DELETE"])
def delete_department(depa_id):
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("DEPARTAMENTOS", "ELIMINAR")
    if not allowed:
        return response

    existing = fetch_one(
        f'''
        SELECT "DEPAID"
        FROM "{SCHEMA}"."DEPARTAMENTOS"
        WHERE "DEPAID" = ?
        ''',
        [depa_id]
    )

    if not existing:
        return error_response("Departamento no encontrado", 404)

    try:
        execute_query(
            f'''
            DELETE FROM "{SCHEMA}"."DEPARTAMENTOS"
            WHERE "DEPAID" = ?
            ''',
            [depa_id]
        )
        return ok_response(message="Departamento eliminado")
    except Exception as e:
        return error_response(f"No se pudo eliminar el departamento: {str(e)}", 400)