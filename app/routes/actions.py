import os
from flask import Blueprint, request
from app.db import fetch_all, execute_query
from app.helpers import ok_response, error_response, require_login, get_next_id

actions_bp = Blueprint("actions", __name__)
SCHEMA = os.getenv("HANA_SCHEMA", "QRTEST")


@actions_bp.route("", methods=["GET"])
def get_actions():
    if not require_login():
        return error_response("No autenticado", 401)

    rows = fetch_all(
        f'''
        SELECT *
        FROM "{SCHEMA}"."ACCIONES"
        ORDER BY "ACCIONID"
        '''
    )
    return ok_response(rows)


@actions_bp.route("", methods=["POST"])
def create_action():
    if not require_login():
        return error_response("No autenticado", 401)

    data = request.get_json(silent=True) or {}
    new_id = get_next_id("ACCIONES", "ACCIONID")

    execute_query(
        f'''
        INSERT INTO "{SCHEMA}"."ACCIONES"
        ("ACCIONID", "CLAVE", "NOMBRE", "ACTIVO")
        VALUES (?, ?, ?, ?)
        ''',
        [new_id, data.get("CLAVE"), data.get("NOMBRE"), data.get("ACTIVO", 1)]
    )

    return ok_response({"ACCIONID": new_id}, "Acción creada", 201)