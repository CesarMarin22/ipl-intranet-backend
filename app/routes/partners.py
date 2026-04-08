import os
from flask import Blueprint, request
from app.db import fetch_all, execute_query
from app.helpers import ok_response, error_response, require_login, get_next_id

partners_bp = Blueprint("partners", __name__)
SCHEMA = os.getenv("HANA_SCHEMA", "QRTEST")


@partners_bp.route("", methods=["GET"])
def get_partners():
    if not require_login():
        return error_response("No autenticado", 401)

    rows = fetch_all(
        f'''
        SELECT *
        FROM "{SCHEMA}"."SOCIOS"
        ORDER BY "SOCIOID"
        '''
    )
    return ok_response(rows)


@partners_bp.route("", methods=["POST"])
def create_partner():
    if not require_login():
        return error_response("No autenticado", 401)

    data = request.get_json(silent=True) or {}
    new_id = get_next_id("SOCIOS", "SOCIOID")

    execute_query(
        f'''
        INSERT INTO "{SCHEMA}"."SOCIOS"
        ("SOCIOID", "NOMBRE", "RFC", "ACTIVO")
        VALUES (?, ?, ?, ?)
        ''',
        [new_id, data.get("NOMBRE"), data.get("RFC"), data.get("ACTIVO", 1)]
    )

    return ok_response({"SOCIOID": new_id}, "Socio creado", 201)