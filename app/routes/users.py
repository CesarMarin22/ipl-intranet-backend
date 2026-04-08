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

    allowed, response = require_permission("USUARIOS_VER")
    if not allowed:
        return response

    rows = fetch_all(
        f'''
        SELECT
            U."USUARIOID",
            U."NOMBRE",
            U."USUARIO",
            U."SUCURSAL",
            U."ACTIVO",
            U."SOCIOID",
            U."PERFILID",
            U."DEPAID",
            U."JEFEID",
            P."NOMBRE" AS "PERFIL_NOMBRE",
            D."NOMBRE" AS "DEPARTAMENTO_NOMBRE",
            S."NOMBRE" AS "SOCIO_NOMBRE"
        FROM "{SCHEMA}"."USUARIOS" U
        LEFT JOIN "{SCHEMA}"."PERFILES" P ON U."PERFILID" = P."PERFILID"
        LEFT JOIN "{SCHEMA}"."DEPARTAMENTOS" D ON U."DEPAID" = D."DEPAID"
        LEFT JOIN "{SCHEMA}"."SOCIOS" S ON U."SOCIOID" = S."SOCIOID"
        ORDER BY U."USUARIOID"
        '''
    )
    return ok_response(rows)


@users_bp.route("/<int:user_id>", methods=["GET"])
def get_user(user_id):
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("USUARIOS_VER")
    if not allowed:
        return response

    row = fetch_one(
        f'''
        SELECT *
        FROM "{SCHEMA}"."USUARIOS"
        WHERE "USUARIOID" = ?
        ''',
        [user_id]
    )

    if not row:
        return error_response("Usuario no encontrado", 404)

    return ok_response(row)


@users_bp.route("", methods=["POST"])
def create_user():
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("USUARIOS_CREAR")
    if not allowed:
        return response

    data = request.get_json(silent=True) or {}

    required_fields = ["NOMBRE", "USUARIO", "PWD", "PERFILID"]
    for field in required_fields:
        if data.get(field) in [None, ""]:
            return error_response(f"El campo {field} es requerido", 400)

    existing_user = fetch_one(
        f'''
        SELECT "USUARIOID"
        FROM "{SCHEMA}"."USUARIOS"
        WHERE UPPER("USUARIO") = UPPER(?)
        ''',
        [data.get("USUARIO")]
    )

    if existing_user:
        return error_response("El usuario ya existe", 409)

    new_id = get_next_id("USUARIOS", "USUARIOID")

    execute_query(
        f'''
        INSERT INTO "{SCHEMA}"."USUARIOS"
        ("USUARIOID", "SOCIOID", "PERFILID", "DEPAID", "JEFEID", "NOMBRE", "USUARIO", "PWD", "SUCURSAL", "ACTIVO")
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        [
            new_id,
            data.get("SOCIOID"),
            data.get("PERFILID"),
            data.get("DEPAID"),
            data.get("JEFEID"),
            data.get("NOMBRE"),
            data.get("USUARIO"),
            data.get("PWD"),
            data.get("SUCURSAL"),
            data.get("ACTIVO", 1),
        ]
    )

    return ok_response({"USUARIOID": new_id}, "Usuario creado", 201)


@users_bp.route("/<int:user_id>", methods=["PUT"])
def update_user(user_id):
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("USUARIOS_EDITAR")
    if not allowed:
        return response

    data = request.get_json(silent=True) or {}

    existing = fetch_one(
        f'''
        SELECT "USUARIOID"
        FROM "{SCHEMA}"."USUARIOS"
        WHERE "USUARIOID" = ?
        ''',
        [user_id]
    )

    if not existing:
        return error_response("Usuario no encontrado", 404)

    if not data.get("NOMBRE"):
        return error_response("El campo NOMBRE es requerido", 400)

    if not data.get("USUARIO"):
        return error_response("El campo USUARIO es requerido", 400)

    if not data.get("PERFILID"):
        return error_response("El campo PERFILID es requerido", 400)

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
            "SUCURSAL" = ?,
            "ACTIVO" = ?
        WHERE "USUARIOID" = ?
        ''',
        [
            data.get("SOCIOID"),
            data.get("PERFILID"),
            data.get("DEPAID"),
            data.get("JEFEID"),
            data.get("NOMBRE"),
            data.get("USUARIO"),
            data.get("PWD"),
            data.get("SUCURSAL"),
            data.get("ACTIVO", 1),
            user_id,
        ]
    )

    return ok_response(message="Usuario actualizado")