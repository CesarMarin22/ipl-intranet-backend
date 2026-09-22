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

modules_bp = Blueprint("modules", __name__)
SCHEMA = os.getenv("HANA_SCHEMA", "QRTEST")


@modules_bp.route("", methods=["GET"])
def get_modules():
    valid, response = validate_active_session()
    if not valid:
        return response

    rows = fetch_all(
        f'''
        SELECT
            M."MODULOID",
            M."NOMBRE",
            M."CLAVE",
            M."RUTA",
            M."PADRE_ID",
            M."ORDEN",
            M."ACTIVO"
        FROM "{SCHEMA}"."MODULOS" M
        WHERE M."ACTIVO" = 1
        ORDER BY
            COALESCE(M."ORDEN", 999999) ASC,
            M."MODULOID" ASC
        '''
    )

    return ok_response(rows)


@modules_bp.route("/tree", methods=["GET"])
def get_modules_tree():
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("MODULOS", "VER")
    if not allowed:
        return response

    rows = fetch_all(
        f'''
        SELECT *
        FROM "{SCHEMA}"."MODULOS"
        WHERE "ACTIVO" = 1
        ORDER BY "MODULOID" ASC
        '''
    )
    return ok_response(rows)


@modules_bp.route("/<int:modulo_id>", methods=["GET"])
def get_module(modulo_id):
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("MODULOS", "VER")
    if not allowed:
        return response

    row = fetch_one(
        f'''
        SELECT *
        FROM "{SCHEMA}"."MODULOS"
        WHERE "MODULOID" = ?
        ''',
        [modulo_id]
    )

    if not row:
        return error_response("Módulo no encontrado", 404)

    return ok_response(row)


def normalize_module_key(value):
    return (value or "").strip().upper().replace(" ", "_")


def normalize_module_route(value):
    value = (value or "").strip()
    return value or None


def validate_module_payload(data, modulo_id=None):
    nombre = (data.get("NOMBRE") or "").strip()
    clave = normalize_module_key(data.get("CLAVE"))
    ruta = normalize_module_route(data.get("RUTA"))
    padre_id = data.get("PADRE_ID")
    orden = data.get("ORDEN")

    if not nombre:
        return None, error_response("El campo NOMBRE es requerido", 400)

    if not clave:
        return None, error_response("El campo CLAVE es requerido", 400)

    if not clave.replace("_", "").isalnum():
        return None, error_response(
            "La CLAVE solo puede contener letras, números y guion bajo",
            400,
        )

    if ruta and not ruta.startswith("/"):
        return None, error_response("La RUTA debe iniciar con /", 400)

    if "ACTIVO" not in data:
        return None, error_response("El campo ACTIVO es requerido", 400)

    if orden not in [None, ""]:
        try:
            orden = int(orden)
            if orden < 0:
                return None, error_response(
                    "El ORDEN debe ser mayor o igual a 0",
                    400,
                )
        except Exception:
            return None, error_response("El ORDEN debe ser numérico", 400)
    else:
        orden = None

    if padre_id in ["", 0, "0"]:
        padre_id = None

    if padre_id is not None:
        try:
            padre_id = int(padre_id)
        except Exception:
            return None, error_response("El PADRE_ID debe ser numérico", 400)

        if modulo_id and padre_id == int(modulo_id):
            return None, error_response(
                "Un módulo no puede ser padre de sí mismo",
                400,
            )

        parent = fetch_one(
            f'''
            SELECT "MODULOID"
            FROM "{SCHEMA}"."MODULOS"
            WHERE "MODULOID" = ?
            ''',
            [padre_id],
        )

        if not parent:
            return None, error_response("El PADRE_ID no existe", 400)

    return {
        "NOMBRE": nombre,
        "CLAVE": clave,
        "PADRE_ID": padre_id,
        "RUTA": ruta,
        "ORDEN": orden,
        "ACTIVO": int(data.get("ACTIVO", 1)),
    }, None


@modules_bp.route("", methods=["POST"])
def create_module():
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("MODULOS", "CREAR")
    if not allowed:
        return response

    data = request.get_json(silent=True) or {}

    payload, error = validate_module_payload(data)
    if error:
        return error

    existing_key = fetch_one(
        f'''
        SELECT "MODULOID"
        FROM "{SCHEMA}"."MODULOS"
        WHERE UPPER("CLAVE") = UPPER(?)
        ''',
        [payload["CLAVE"]],
    )

    if existing_key:
        return error_response("Ya existe un módulo con esa clave", 409)

    existing_name = fetch_one(
        f'''
        SELECT "MODULOID"
        FROM "{SCHEMA}"."MODULOS"
        WHERE UPPER("NOMBRE") = UPPER(?)
        ''',
        [payload["NOMBRE"]],
    )

    if existing_name:
        return error_response("Ya existe un módulo con ese nombre", 409)

    if payload["RUTA"]:
        existing_route = fetch_one(
            f'''
            SELECT "MODULOID"
            FROM "{SCHEMA}"."MODULOS"
            WHERE "RUTA" = ?
            ''',
            [payload["RUTA"]],
        )

        if existing_route:
            return error_response("Ya existe un módulo con esa ruta", 409)

    new_id = get_next_id("MODULOS", "MODULOID")

    execute_query(
        f'''
        INSERT INTO "{SCHEMA}"."MODULOS"
        ("MODULOID", "NOMBRE", "PADRE_ID", "CLAVE", "RUTA", "ORDEN", "ACTIVO")
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ''',
        [
            new_id,
            payload["NOMBRE"],
            payload["PADRE_ID"],
            payload["CLAVE"],
            payload["RUTA"],
            payload["ORDEN"],
            payload["ACTIVO"],
        ],
    )

    return ok_response({"MODULOID": new_id}, "Módulo creado", 201)


@modules_bp.route("/<int:modulo_id>", methods=["PUT"])
def update_module(modulo_id):
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("MODULOS", "EDITAR")
    if not allowed:
        return response

    data = request.get_json(silent=True) or {}

    existing = fetch_one(
        f'''
        SELECT "MODULOID"
        FROM "{SCHEMA}"."MODULOS"
        WHERE "MODULOID" = ?
        ''',
        [modulo_id],
    )

    if not existing:
        return error_response("Módulo no encontrado", 404)

    payload, error = validate_module_payload(data, modulo_id)
    if error:
        return error

    duplicate_key = fetch_one(
        f'''
        SELECT "MODULOID"
        FROM "{SCHEMA}"."MODULOS"
        WHERE UPPER("CLAVE") = UPPER(?)
          AND "MODULOID" <> ?
        ''',
        [payload["CLAVE"], modulo_id],
    )

    if duplicate_key:
        return error_response("Ya existe otro módulo con esa clave", 409)

    duplicate_name = fetch_one(
        f'''
        SELECT "MODULOID"
        FROM "{SCHEMA}"."MODULOS"
        WHERE UPPER("NOMBRE") = UPPER(?)
          AND "MODULOID" <> ?
        ''',
        [payload["NOMBRE"], modulo_id],
    )

    if duplicate_name:
        return error_response("Ya existe otro módulo con ese nombre", 409)

    if payload["RUTA"]:
        duplicate_route = fetch_one(
            f'''
            SELECT "MODULOID"
            FROM "{SCHEMA}"."MODULOS"
            WHERE "RUTA" = ?
              AND "MODULOID" <> ?
            ''',
            [payload["RUTA"], modulo_id],
        )

        if duplicate_route:
            return error_response("Ya existe otro módulo con esa ruta", 409)

    execute_query(
        f'''
        UPDATE "{SCHEMA}"."MODULOS"
        SET
            "NOMBRE" = ?,
            "PADRE_ID" = ?,
            "CLAVE" = ?,
            "RUTA" = ?,
            "ORDEN" = ?,
            "ACTIVO" = ?
        WHERE "MODULOID" = ?
        ''',
        [
            payload["NOMBRE"],
            payload["PADRE_ID"],
            payload["CLAVE"],
            payload["RUTA"],
            payload["ORDEN"],
            payload["ACTIVO"],
            modulo_id,
        ],
    )

    return ok_response(message="Módulo actualizado")


@modules_bp.route("/<int:modulo_id>/status", methods=["PATCH"])
def update_module_status(modulo_id):
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("MODULOS", "EDITAR")
    if not allowed:
        return response

    data = request.get_json(silent=True) or {}

    if "ACTIVO" not in data:
        return error_response("El campo ACTIVO es requerido", 400)

    existing = fetch_one(
        f'''
        SELECT "MODULOID"
        FROM "{SCHEMA}"."MODULOS"
        WHERE "MODULOID" = ?
        ''',
        [modulo_id]
    )

    if not existing:
        return error_response("Módulo no encontrado", 404)

    execute_query(
        f'''
        UPDATE "{SCHEMA}"."MODULOS"
        SET "ACTIVO" = ?
        WHERE "MODULOID" = ?
        ''',
        [data.get("ACTIVO"), modulo_id]
    )

    return ok_response(message="Estado actualizado")


@modules_bp.route("/<int:modulo_id>", methods=["DELETE"])
def delete_module(modulo_id):
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("MODULOS", "ELIMINAR")
    if not allowed:
        return response

    existing = fetch_one(
        f'''
        SELECT "MODULOID"
        FROM "{SCHEMA}"."MODULOS"
        WHERE "MODULOID" = ?
        ''',
        [modulo_id]
    )

    if not existing:
        return error_response("Módulo no encontrado", 404)

    child = fetch_one(
        f'''
        SELECT "MODULOID"
        FROM "{SCHEMA}"."MODULOS"
        WHERE "PADRE_ID" = ?
        ''',
        [modulo_id]
    )

    if child:
        return error_response("No se puede eliminar el módulo porque tiene hijos", 400)

    try:
        execute_query(
            f'''
            DELETE FROM "{SCHEMA}"."MODULOS"
            WHERE "MODULOID" = ?
            ''',
            [modulo_id]
        )
        return ok_response(message="Módulo eliminado")
    except Exception as e:
        return error_response(f"No se pudo eliminar el módulo: {str(e)}", 400)
    
@modules_bp.route("/actions-map", methods=["GET"])
def get_modules_actions_map():
    valid, response = validate_active_session()
    if not valid:
        return response

    rows = fetch_all(
        f'''
        SELECT
            MA."MODULOID",
            MA."ACCIONID",
            A."CLAVE",
            A."NOMBRE",
            A."ACTIVO",
            MA."ACTIVO" AS "ASIGNADA"
        FROM "{SCHEMA}"."MODULO_ACCIONES" MA
        INNER JOIN "{SCHEMA}"."ACCIONES" A
            ON A."ACCIONID" = MA."ACCIONID"
        INNER JOIN "{SCHEMA}"."MODULOS" M
            ON M."MODULOID" = MA."MODULOID"
        WHERE A."ACTIVO" = 1
          AND M."ACTIVO" = 1
          AND MA."ACTIVO" = 1
        ORDER BY
            MA."MODULOID" ASC,
            A."ACCIONID" ASC
        '''
    )

    return ok_response(rows)
    
@modules_bp.route("/<int:modulo_id>/actions", methods=["GET"])
def get_module_actions(modulo_id):
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("MODULOS", "VER")
    if not allowed:
        return response

    rows = fetch_all(
        f'''
        SELECT
            A."ACCIONID",
            A."CLAVE",
            A."NOMBRE",
            A."ACTIVO",
            CASE 
                WHEN MA."ACCIONID" IS NULL THEN 0
                ELSE MA."ACTIVO"
            END AS "ASIGNADA"
        FROM "{SCHEMA}"."ACCIONES" A
        LEFT JOIN "{SCHEMA}"."MODULO_ACCIONES" MA
            ON MA."ACCIONID" = A."ACCIONID"
            AND MA."MODULOID" = ?
        WHERE A."ACTIVO" = 1
        ORDER BY A."ACCIONID"
        ''',
        [modulo_id],
    )

    return ok_response(rows)


@modules_bp.route("/<int:modulo_id>/actions", methods=["PUT"])
def update_module_actions(modulo_id):
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("MODULOS", "EDITAR")
    if not allowed:
        return response

    data = request.get_json(silent=True) or {}
    actions = data.get("actions", [])

    existing = fetch_one(
        f'''
        SELECT "MODULOID"
        FROM "{SCHEMA}"."MODULOS"
        WHERE "MODULOID" = ?
        ''',
        [modulo_id],
    )

    if not existing:
        return error_response("Módulo no encontrado", 404)

    execute_query(
        f'''
        DELETE FROM "{SCHEMA}"."MODULO_ACCIONES"
        WHERE "MODULOID" = ?
        ''',
        [modulo_id],
    )

    for action in actions:
        if int(action.get("ASIGNADA", 0)) == 1:
            execute_query(
                f'''
                INSERT INTO "{SCHEMA}"."MODULO_ACCIONES"
                ("MODULOID", "ACCIONID", "ACTIVO")
                VALUES (?, ?, 1)
                ''',
                [modulo_id, action.get("ACCIONID")],
            )

    return ok_response(message="Acciones del módulo actualizadas")