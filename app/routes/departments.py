import os
from flask import Blueprint, request
from app.db import fetch_all, execute_query
from app.helpers import ok_response, error_response, require_login, get_next_id

departments_bp = Blueprint("departments", __name__)
SCHEMA = os.getenv("HANA_SCHEMA", "QRTEST")


@departments_bp.route("", methods=["GET"])
def get_departments():
    if not require_login():
        return error_response("No autenticado", 401)

    rows = fetch_all(
        f'''
        SELECT *
        FROM "{SCHEMA}"."DEPARTAMENTOS"
        ORDER BY "DEPAID"
        '''
    )
    return ok_response(rows)


@departments_bp.route("", methods=["POST"])
def create_department():
    if not require_login():
        return error_response("No autenticado", 401)

    data = request.get_json(silent=True) or {}
    new_id = get_next_id("DEPARTAMENTOS", "DEPAID")

    execute_query(
        f'''
        INSERT INTO "{SCHEMA}"."DEPARTAMENTOS"
        ("DEPAID", "NOMBRE", "ACTIVO")
        VALUES (?, ?, ?)
        ''',
        [new_id, data.get("NOMBRE"), data.get("ACTIVO", 1)]
    )

    return ok_response({"DEPAID": new_id}, "Departamento creado", 201)