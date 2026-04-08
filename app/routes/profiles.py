import os
from flask import Blueprint, request
from app.db import fetch_all, fetch_one, execute_query
from app.helpers import ok_response, error_response, require_login, get_next_id

profiles_bp = Blueprint("profiles", __name__)
SCHEMA = os.getenv("HANA_SCHEMA", "QRTEST")


@profiles_bp.route("", methods=["GET"])
def get_profiles():
    if not require_login():
        return error_response("No autenticado", 401)

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
    if not require_login():
        return error_response("No autenticado", 401)

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
    if not require_login():
        return error_response("No autenticado", 401)

    data = request.get_json(silent=True) or {}

    new_id = get_next_id("PERFILES", "PERFILID")

    execute_query(
        f'''
        INSERT INTO "{SCHEMA}"."PERFILES"
        ("PERFILID", "NOMBRE", "DESCRIPCION", "ACTIVO")
        VALUES (?, ?, ?, ?)
        ''',
        [new_id, data.get("NOMBRE"), data.get("DESCRIPCION"), data.get("ACTIVO", 1)]
    )

    return ok_response({"PERFILID": new_id}, "Perfil creado", 201)