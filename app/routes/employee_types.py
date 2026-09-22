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

employee_types_bp = Blueprint("employee_types", __name__)
SCHEMA = os.getenv("HANA_SCHEMA", "QRTEST")


@employee_types_bp.route("", methods=["GET"])
def get_employee_types():
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("TIPOS_EMPLEADO", "VER")
    if not allowed:
        return response

    rows = fetch_all(
        f'''
        SELECT *
        FROM "{SCHEMA}"."TIPOS_EMPLEADO"
        ORDER BY "TIPO_EMPLEADO_ID"
        '''
    )

    return ok_response(rows)


@employee_types_bp.route("/<int:tipo_id>", methods=["GET"])
def get_employee_type(tipo_id):
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("TIPOS_EMPLEADO", "VER")
    if not allowed:
        return response

    row = fetch_one(
        f'''
        SELECT *
        FROM "{SCHEMA}"."TIPOS_EMPLEADO"
        WHERE "TIPO_EMPLEADO_ID" = ?
        ''',
        [tipo_id]
    )

    if not row:
        return error_response("Tipo de empleado no encontrado", 404)

    return ok_response(row)


@employee_types_bp.route("", methods=["POST"])
def create_employee_type():
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("TIPOS_EMPLEADO", "CREAR")
    if not allowed:
        return response

    data = request.get_json(silent=True) or {}

    clave = (data.get("CLAVE") or "").strip().upper() or None
    nombre = (data.get("NOMBRE") or "").strip()

    if not clave:
        return error_response("El campo CLAVE es requerido", 400)

    if not nombre:
        return error_response("El campo NOMBRE es requerido", 400)

    if "ACTIVO" not in data:
        return error_response("El campo ACTIVO es requerido", 400)

    existing_name = fetch_one(
        f'''
        SELECT "TIPO_EMPLEADO_ID"
        FROM "{SCHEMA}"."TIPOS_EMPLEADO"
        WHERE UPPER("NOMBRE") = UPPER(?)
        ''',
        [nombre]
    )

    if existing_name:
        return error_response("Ya existe un tipo de empleado con ese nombre", 409)

    existing_key = fetch_one(
        f'''
        SELECT "TIPO_EMPLEADO_ID"
        FROM "{SCHEMA}"."TIPOS_EMPLEADO"
        WHERE UPPER("CLAVE") = UPPER(?)
        ''',
        [clave]
    )

    if existing_key:
        return error_response("Ya existe un tipo de empleado con esa clave", 409)

    new_id = get_next_id("TIPOS_EMPLEADO", "TIPO_EMPLEADO_ID")

    execute_query(
        f'''
        INSERT INTO "{SCHEMA}"."TIPOS_EMPLEADO"
        ("TIPO_EMPLEADO_ID", "CLAVE", "NOMBRE", "ACTIVO")
        VALUES (?, ?, ?, ?)
        ''',
        [
            new_id,
            clave,
            nombre,
            data.get("ACTIVO", 1),
        ]
    )

    return ok_response({"TIPO_EMPLEADO_ID": new_id}, "Tipo de empleado creado", 201)


@employee_types_bp.route("/<int:tipo_id>", methods=["PUT"])
def update_employee_type(tipo_id):
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("TIPOS_EMPLEADO", "EDITAR")
    if not allowed:
        return response

    data = request.get_json(silent=True) or {}

    existing = fetch_one(
        f'''
        SELECT "TIPO_EMPLEADO_ID"
        FROM "{SCHEMA}"."TIPOS_EMPLEADO"
        WHERE "TIPO_EMPLEADO_ID" = ?
        ''',
        [tipo_id]
    )

    if not existing:
        return error_response("Tipo de empleado no encontrado", 404)

    clave = (data.get("CLAVE") or "").strip().upper() or None
    nombre = (data.get("NOMBRE") or "").strip()

    if not clave:
        return error_response("El campo CLAVE es requerido", 400)

    if not nombre:
        return error_response("El campo NOMBRE es requerido", 400)

    if "ACTIVO" not in data:
        return error_response("El campo ACTIVO es requerido", 400)

    duplicate_name = fetch_one(
        f'''
        SELECT "TIPO_EMPLEADO_ID"
        FROM "{SCHEMA}"."TIPOS_EMPLEADO"
        WHERE UPPER("NOMBRE") = UPPER(?)
          AND "TIPO_EMPLEADO_ID" <> ?
        ''',
        [nombre, tipo_id]
    )

    if duplicate_name:
        return error_response("Ya existe otro tipo de empleado con ese nombre", 409)

    duplicate_key = fetch_one(
        f'''
        SELECT "TIPO_EMPLEADO_ID"
        FROM "{SCHEMA}"."TIPOS_EMPLEADO"
        WHERE UPPER("CLAVE") = UPPER(?)
          AND "TIPO_EMPLEADO_ID" <> ?
        ''',
        [clave, tipo_id]
    )

    if duplicate_key:
        return error_response("Ya existe otro tipo de empleado con esa clave", 409)

    execute_query(
        f'''
        UPDATE "{SCHEMA}"."TIPOS_EMPLEADO"
        SET
            "CLAVE" = ?,
            "NOMBRE" = ?,
            "ACTIVO" = ?
        WHERE "TIPO_EMPLEADO_ID" = ?
        ''',
        [
            clave,
            nombre,
            data.get("ACTIVO", 1),
            tipo_id,
        ]
    )

    return ok_response(message="Tipo de empleado actualizado")

@employee_types_bp.route("/<int:tipo_id>/status", methods=["PATCH"])
def update_employee_type_status(tipo_id):
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("TIPOS_EMPLEADO", "EDITAR")
    if not allowed:
        return response

    data = request.get_json(silent=True) or {}

    if "ACTIVO" not in data:
        return error_response("El campo ACTIVO es requerido", 400)

    existing = fetch_one(
        f'''
        SELECT "TIPO_EMPLEADO_ID"
        FROM "{SCHEMA}"."TIPOS_EMPLEADO"
        WHERE "TIPO_EMPLEADO_ID" = ?
        ''',
        [tipo_id]
    )

    if not existing:
        return error_response("Tipo de empleado no encontrado", 404)

    execute_query(
        f'''
        UPDATE "{SCHEMA}"."TIPOS_EMPLEADO"
        SET "ACTIVO" = ?
        WHERE "TIPO_EMPLEADO_ID" = ?
        ''',
        [data.get("ACTIVO"), tipo_id]
    )

    return ok_response(message="Estado actualizado")


@employee_types_bp.route("/<int:tipo_id>", methods=["DELETE"])
def delete_employee_type(tipo_id):
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("TIPOS_EMPLEADO", "ELIMINAR")
    if not allowed:
        return response

    existing = fetch_one(
        f'''
        SELECT "TIPO_EMPLEADO_ID"
        FROM "{SCHEMA}"."TIPOS_EMPLEADO"
        WHERE "TIPO_EMPLEADO_ID" = ?
        ''',
        [tipo_id]
    )

    if not existing:
        return error_response("Tipo de empleado no encontrado", 404)

    try:
        execute_query(
            f'''
            DELETE FROM "{SCHEMA}"."TIPOS_EMPLEADO"
            WHERE "TIPO_EMPLEADO_ID" = ?
            ''',
            [tipo_id]
        )
        return ok_response(message="Tipo de empleado eliminado")
    except Exception as e:
        return error_response(f"No se pudo eliminar el tipo de empleado: {str(e)}", 400)