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

users_bp = Blueprint("users", __name__)
SCHEMA = os.getenv("HANA_SCHEMA", "QRTEST")


@users_bp.route("", methods=["GET"])
def get_users():
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("USUARIOS", "VER")
    if not allowed:
        return response

    rows = fetch_all(
        f'''
        SELECT
            U."USUARIOID",
            U."SOCIOID",
            U."PERFILID",
            U."DEPAID",
            U."JEFEID",
            U."NOMBRE",
            U."USUARIO",
            U."PWD",
            U."EMAIL",
            U."SUCURSAL",
            SUC."CLAVE" AS "SUCURSAL_NOMBRE",
            SUC."NOMBRE" AS "SUCURSAL_DESCRIPCION",
            U."ACTIVO",
            U."NUMERO_EMPLEADO",
            U."TIPO_EMPLEADO",
            U."TIPO_EMPLEADO_ID",
            P."NOMBRE" AS "PERFIL_NOMBRE",
            D."NOMBRE" AS "DEPARTAMENTO_NOMBRE",
            S."NOMBRE" AS "SOCIO_NOMBRE",
            TE."NOMBRE" AS "TIPO_EMPLEADO_NOMBRE"
        FROM "{SCHEMA}"."USUARIOS" U
        LEFT JOIN "{SCHEMA}"."SUCURSALES" SUC
            ON U."SUCURSAL" = SUC."SUCURSALID"
        LEFT JOIN "{SCHEMA}"."PERFILES" P
            ON U."PERFILID" = P."PERFILID"
        LEFT JOIN "{SCHEMA}"."DEPARTAMENTOS" D
            ON U."DEPAID" = D."DEPAID"
        LEFT JOIN "{SCHEMA}"."SOCIOS" S
            ON U."SOCIOID" = S."SOCIOID"
        LEFT JOIN "{SCHEMA}"."TIPOS_EMPLEADO" TE
            ON U."TIPO_EMPLEADO_ID" = TE."TIPO_EMPLEADO_ID"
        ORDER BY U."USUARIOID"
        '''
    )

    return ok_response(rows)


@users_bp.route("/<int:user_id>", methods=["GET"])
def get_user(user_id):
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("USUARIOS", "VER")
    if not allowed:
        return response

    row = fetch_one(
        f'''
        SELECT
            U.*,
            SUC."CLAVE" AS "SUCURSAL_NOMBRE",
            SUC."NOMBRE" AS "SUCURSAL_DESCRIPCION"
        FROM "{SCHEMA}"."USUARIOS" U
        LEFT JOIN "{SCHEMA}"."SUCURSALES" SUC
            ON U."SUCURSAL" = SUC."SUCURSALID"
        WHERE U."USUARIOID" = ?
        ''',
        [user_id],
    )

    if not row:
        return error_response("Usuario no encontrado", 404)

    return ok_response(row)


@users_bp.route("", methods=["POST"])
def create_user():
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("USUARIOS", "CREAR")
    if not allowed:
        return response

    data = request.get_json(silent=True) or {}

    nombre = (data.get("NOMBRE") or "").strip()
    usuario = (data.get("USUARIO") or "").strip()
    pwd = (data.get("PWD") or "").strip()
    numero_empleado = (data.get("NUMERO_EMPLEADO") or "").strip() or None
    email = (data.get("EMAIL") or "").strip() or None

    if not nombre:
        return error_response("El campo NOMBRE es requerido", 400)

    if not usuario:
        return error_response("El campo USUARIO es requerido", 400)

    if not pwd:
        return error_response("El campo PWD es requerido", 400)

    if email and "@" not in email:
        return error_response("El campo EMAIL no tiene un formato válido", 400)

    if not data.get("PERFILID"):
        return error_response("El campo PERFILID es requerido", 400)

    if not data.get("DEPAID"):
        return error_response("El campo DEPAID es requerido", 400)

    if not data.get("SOCIOID"):
        return error_response("El campo SOCIOID es requerido", 400)

    if not data.get("SUCURSAL"):
        return error_response("El campo SUCURSAL es requerido", 400)

    if not numero_empleado:
        return error_response("El campo NUMERO_EMPLEADO es requerido", 400)

    if not data.get("TIPO_EMPLEADO_ID"):
        return error_response("El campo TIPO_EMPLEADO_ID es requerido", 400)

    existing_user = fetch_one(
        f'''
        SELECT "USUARIOID"
        FROM "{SCHEMA}"."USUARIOS"
        WHERE UPPER("USUARIO") = UPPER(?)
        ''',
        [usuario],
    )

    if existing_user:
        return error_response("El usuario ya existe", 409)

    existing_employee = fetch_one(
        f'''
        SELECT "USUARIOID"
        FROM "{SCHEMA}"."USUARIOS"
        WHERE "NUMERO_EMPLEADO" = ?
        ''',
        [numero_empleado],
    )

    if existing_employee:
        return error_response("Ya existe un usuario con ese número de empleado", 409)

    new_id = get_next_id("USUARIOS", "USUARIOID")

    execute_query(
        f'''
        INSERT INTO "{SCHEMA}"."USUARIOS"
        (
            "USUARIOID", "SOCIOID", "PERFILID", "DEPAID", "JEFEID",
            "NOMBRE", "USUARIO", "PWD", "EMAIL", "SUCURSAL", "ACTIVO",
            "NUMERO_EMPLEADO", "TIPO_EMPLEADO", "TIPO_EMPLEADO_ID"
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        [
            new_id,
            data.get("SOCIOID"),
            data.get("PERFILID"),
            data.get("DEPAID"),
            data.get("JEFEID"),
            nombre,
            usuario,
            pwd,
            email,
            data.get("SUCURSAL"),
            data.get("ACTIVO", 1),
            numero_empleado,
            data.get("TIPO_EMPLEADO"),
            data.get("TIPO_EMPLEADO_ID"),
        ]
    )

    return ok_response({"USUARIOID": new_id}, "Usuario creado", 201)


@users_bp.route("/<int:user_id>", methods=["PUT"])
def update_user(user_id):
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("USUARIOS", "EDITAR")
    if not allowed:
        return response

    data = request.get_json(silent=True) or {}

    existing = fetch_one(
        f'''
        SELECT "USUARIOID"
        FROM "{SCHEMA}"."USUARIOS"
        WHERE "USUARIOID" = ?
        ''',
        [user_id],
    )

    if not existing:
        return error_response("Usuario no encontrado", 404)

    nombre = (data.get("NOMBRE") or "").strip()
    usuario = (data.get("USUARIO") or "").strip()
    pwd = (data.get("PWD") or "").strip()
    numero_empleado = (data.get("NUMERO_EMPLEADO") or "").strip() or None
    email = (data.get("EMAIL") or "").strip() or None

    if not nombre:
        return error_response("El campo NOMBRE es requerido", 400)

    if not usuario:
        return error_response("El campo USUARIO es requerido", 400)

    if not pwd:
        return error_response("El campo PWD es requerido", 400)

    if email and "@" not in email:
        return error_response("El campo EMAIL no tiene un formato válido", 400)

    if not data.get("PERFILID"):
        return error_response("El campo PERFILID es requerido", 400)

    if not data.get("DEPAID"):
        return error_response("El campo DEPAID es requerido", 400)

    if not data.get("SOCIOID"):
        return error_response("El campo SOCIOID es requerido", 400)

    if not data.get("SUCURSAL"):
        return error_response("El campo SUCURSAL es requerido", 400)

    if not numero_empleado:
        return error_response("El campo NUMERO_EMPLEADO es requerido", 400)

    if not data.get("TIPO_EMPLEADO_ID"):
        return error_response("El campo TIPO_EMPLEADO_ID es requerido", 400)

    duplicate_user = fetch_one(
        f'''
        SELECT "USUARIOID"
        FROM "{SCHEMA}"."USUARIOS"
        WHERE UPPER("USUARIO") = UPPER(?)
          AND "USUARIOID" <> ?
        ''',
        [usuario, user_id],
    )

    if duplicate_user:
        return error_response(
            "Ya existe otro usuario con ese nombre de usuario",
            409,
        )

    duplicate_employee = fetch_one(
        f'''
        SELECT "USUARIOID"
        FROM "{SCHEMA}"."USUARIOS"
        WHERE "NUMERO_EMPLEADO" = ?
          AND "USUARIOID" <> ?
        ''',
        [numero_empleado, user_id],
    )

    if duplicate_employee:
        return error_response(
            "Ya existe otro usuario con ese número de empleado",
            409,
        )

    execute_query(
        f'''
        UPDATE "{SCHEMA}"."USUARIOS"
        SET
            "SOCIOID" = ?,
            "PERFILID" = ?,
            "DEPAID" = ?,
            "JEFEID" = ?,
            "NOMBRE" = ?,
            "USUARIO" = ?,
            "PWD" = ?,
            "EMAIL" = ?,
            "SUCURSAL" = ?,
            "ACTIVO" = ?,
            "NUMERO_EMPLEADO" = ?,
            "TIPO_EMPLEADO" = ?,
            "TIPO_EMPLEADO_ID" = ?
        WHERE "USUARIOID" = ?
        ''',
        [
            data.get("SOCIOID"),
            data.get("PERFILID"),
            data.get("DEPAID"),
            data.get("JEFEID"),
            nombre,
            usuario,
            pwd,
            email,
            data.get("SUCURSAL"),
            data.get("ACTIVO", 1),
            numero_empleado,
            data.get("TIPO_EMPLEADO"),
            data.get("TIPO_EMPLEADO_ID"),
            user_id,
        ],
    )

    return ok_response(message="Usuario actualizado")

@users_bp.route("/<int:user_id>/status", methods=["PATCH"])
def update_user_status(user_id):
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("USUARIOS", "EDITAR")
    if not allowed:
        return response

    data = request.get_json(silent=True) or {}

    if "ACTIVO" not in data:
        return error_response("El campo ACTIVO es requerido", 400)

    existing = fetch_one(
        f'''
        SELECT "USUARIOID"
        FROM "{SCHEMA}"."USUARIOS"
        WHERE "USUARIOID" = ?
        ''',
        [user_id],
    )

    if not existing:
        return error_response("Usuario no encontrado", 404)

    execute_query(
        f'''
        UPDATE "{SCHEMA}"."USUARIOS"
        SET "ACTIVO" = ?
        WHERE "USUARIOID" = ?
        ''',
        [data.get("ACTIVO"), user_id],
    )

    return ok_response(message="Estado actualizado")


@users_bp.route("/<int:user_id>", methods=["DELETE"])
def delete_user(user_id):
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("USUARIOS", "ELIMINAR")
    if not allowed:
        return response

    existing = fetch_one(
        f'''
        SELECT "USUARIOID"
        FROM "{SCHEMA}"."USUARIOS"
        WHERE "USUARIOID" = ?
        ''',
        [user_id],
    )

    if not existing:
        return error_response("Usuario no encontrado", 404)

    try:
        execute_query(
            f'''
            DELETE FROM "{SCHEMA}"."USUARIOS"
            WHERE "USUARIOID" = ?
            ''',
            [user_id],
        )
        return ok_response(message="Usuario eliminado")
    except Exception as e:
        return error_response(f"No se pudo eliminar el usuario: {str(e)}", 400)