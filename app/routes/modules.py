import os
from flask import Blueprint, request
from app.db import fetch_all, execute_query
from app.helpers import ok_response, error_response, require_login, get_next_id

modules_bp = Blueprint("modules", __name__)
SCHEMA = os.getenv("HANA_SCHEMA", "QRTEST")


@modules_bp.route("", methods=["GET"])
def get_modules():
    if not require_login():
        return error_response("No autenticado", 401)

    rows = fetch_all(
        f'''
        SELECT *
        FROM "{SCHEMA}"."MODULOS"
        ORDER BY "PADRE_ID", "ORDEN", "MODULOID"
        '''
    )
    return ok_response(rows)


@modules_bp.route("/tree", methods=["GET"])
def get_modules_tree():
    if not require_login():
        return error_response("No autenticado", 401)

    rows = fetch_all(
        f'''
        SELECT *
        FROM "{SCHEMA}"."MODULOS"
        WHERE "ACTIVO" = 1
        ORDER BY "PADRE_ID", "ORDEN", "MODULOID"
        '''
    )
    return ok_response(rows)


@modules_bp.route("", methods=["POST"])
def create_module():
    if not require_login():
        return error_response("No autenticado", 401)

    data = request.get_json(silent=True) or {}
    new_id = get_next_id("MODULOS", "MODULOID")

    execute_query(
        f'''
        INSERT INTO "{SCHEMA}"."MODULOS"
        ("MODULOID", "NOMBRE", "PADRE_ID", "RUTA", "ORDEN", "ACTIVO")
        VALUES (?, ?, ?, ?, ?, ?)
        ''',
        [
            new_id,
            data.get("NOMBRE"),
            data.get("PADRE_ID"),
            data.get("RUTA"),
            data.get("ORDEN"),
            data.get("ACTIVO", 1),
        ]
    )

    return ok_response({"MODULOID": new_id}, "Módulo creado", 201)