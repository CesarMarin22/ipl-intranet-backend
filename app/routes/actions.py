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

actions_bp = Blueprint("actions", __name__)
SCHEMA = os.getenv("HANA_SCHEMA", "QRTEST")


@actions_bp.route("", methods=["GET"])
def get_actions():
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("ACCIONES", "VER")
    if not allowed:
        return response

    rows = fetch_all(
        f'''
        SELECT *
        FROM "{SCHEMA}"."ACCIONES"
        ORDER BY "ACCIONID"
        '''
    )
    return ok_response(rows)


@actions_bp.route("/<int:accion_id>", methods=["GET"])
def get_action(accion_id):
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("ACCIONES", "VER")
    if not allowed:
        return response

    row = fetch_one(
        f'''
        SELECT *
        FROM "{SCHEMA}"."ACCIONES"
        WHERE "ACCIONID" = ?
        ''',
        [accion_id]
    )

    if not row:
        return error_response("Acción no encontrada", 404)

    return ok_response(row)


def normalize_action_key(value):
    return (value or "").strip().upper().replace(" ", "_")


@actions_bp.route("", methods=["POST"])
def create_action():
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("ACCIONES", "CREAR")
    if not allowed:
        return response

    data = request.get_json(silent=True) or {}

    clave = normalize_action_key(data.get("CLAVE"))
    nombre = (data.get("NOMBRE") or "").strip()

    if not clave:
        return error_response("El campo CLAVE es requerido", 400)

    if not clave.replace("_", "").isalnum():
        return error_response(
            "La CLAVE solo puede contener letras, números y guion bajo",
            400,
        )

    if not nombre:
        return error_response("El campo NOMBRE es requerido", 400)

    if "ACTIVO" not in data:
        return error_response("El campo ACTIVO es requerido", 400)

    existing_key = fetch_one(
        f'''
        SELECT "ACCIONID"
        FROM "{SCHEMA}"."ACCIONES"
        WHERE UPPER("CLAVE") = UPPER(?)
        ''',
        [clave],
    )

    if existing_key:
        return error_response("Ya existe una acción con esa clave", 409)

    existing_name = fetch_one(
        f'''
        SELECT "ACCIONID"
        FROM "{SCHEMA}"."ACCIONES"
        WHERE UPPER("NOMBRE") = UPPER(?)
        ''',
        [nombre],
    )

    if existing_name:
        return error_response("Ya existe una acción con ese nombre", 409)

    new_id = get_next_id("ACCIONES", "ACCIONID")

    execute_query(
        f'''
        INSERT INTO "{SCHEMA}"."ACCIONES"
        ("ACCIONID", "CLAVE", "NOMBRE", "ACTIVO")
        VALUES (?, ?, ?, ?)
        ''',
        [
            new_id,
            clave,
            nombre,
            data.get("ACTIVO", 1),
        ],
    )

    return ok_response({"ACCIONID": new_id}, "Acción creada", 201)

@actions_bp.route("/<int:accion_id>", methods=["PUT"])
def update_action(accion_id):
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("ACCIONES", "EDITAR")
    if not allowed:
        return response

    data = request.get_json(silent=True) or {}

    existing = fetch_one(
        f'''
        SELECT "ACCIONID"
        FROM "{SCHEMA}"."ACCIONES"
        WHERE "ACCIONID" = ?
        ''',
        [accion_id],
    )

    if not existing:
        return error_response("Acción no encontrada", 404)

    clave = normalize_action_key(data.get("CLAVE"))
    nombre = (data.get("NOMBRE") or "").strip()

    if not clave:
        return error_response("El campo CLAVE es requerido", 400)

    if not clave.replace("_", "").isalnum():
        return error_response(
            "La CLAVE solo puede contener letras, números y guion bajo",
            400,
        )

    if not nombre:
        return error_response("El campo NOMBRE es requerido", 400)

    if "ACTIVO" not in data:
        return error_response("El campo ACTIVO es requerido", 400)

    duplicate_key = fetch_one(
        f'''
        SELECT "ACCIONID"
        FROM "{SCHEMA}"."ACCIONES"
        WHERE UPPER("CLAVE") = UPPER(?)
          AND "ACCIONID" <> ?
        ''',
        [clave, accion_id],
    )

    if duplicate_key:
        return error_response("Ya existe otra acción con esa clave", 409)

    duplicate_name = fetch_one(
        f'''
        SELECT "ACCIONID"
        FROM "{SCHEMA}"."ACCIONES"
        WHERE UPPER("NOMBRE") = UPPER(?)
          AND "ACCIONID" <> ?
        ''',
        [nombre, accion_id],
    )

    if duplicate_name:
        return error_response("Ya existe otra acción con ese nombre", 409)

    execute_query(
        f'''
        UPDATE "{SCHEMA}"."ACCIONES"
        SET
            "CLAVE" = ?,
            "NOMBRE" = ?,
            "ACTIVO" = ?
        WHERE "ACCIONID" = ?
        ''',
        [
            clave,
            nombre,
            data.get("ACTIVO", 1),
            accion_id,
        ],
    )

    return ok_response(message="Acción actualizada")


@actions_bp.route("/<int:accion_id>/status", methods=["PATCH"])
def update_action_status(accion_id):
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("ACCIONES", "EDITAR")
    if not allowed:
        return response

    data = request.get_json(silent=True) or {}

    if "ACTIVO" not in data:
        return error_response("El campo ACTIVO es requerido", 400)

    existing = fetch_one(
        f'''
        SELECT "ACCIONID"
        FROM "{SCHEMA}"."ACCIONES"
        WHERE "ACCIONID" = ?
        ''',
        [accion_id]
    )

    if not existing:
        return error_response("Acción no encontrada", 404)

    execute_query(
        f'''
        UPDATE "{SCHEMA}"."ACCIONES"
        SET "ACTIVO" = ?
        WHERE "ACCIONID" = ?
        ''',
        [data.get("ACTIVO"), accion_id]
    )

    return ok_response(message="Estado actualizado")


@actions_bp.route("/<int:accion_id>", methods=["DELETE"])
def delete_action(accion_id):
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("ACCIONES", "ELIMINAR")
    if not allowed:
        return response

    existing = fetch_one(
        f'''
        SELECT "ACCIONID"
        FROM "{SCHEMA}"."ACCIONES"
        WHERE "ACCIONID" = ?
        ''',
        [accion_id]
    )

    if not existing:
        return error_response("Acción no encontrada", 404)

    try:
        execute_query(
            f'''
            DELETE FROM "{SCHEMA}"."ACCIONES"
            WHERE "ACCIONID" = ?
            ''',
            [accion_id]
        )
        return ok_response(message="Acción eliminada")
    except Exception as e:
        return error_response(f"No se pudo eliminar la acción: {str(e)}", 400)