import os
from flask import Blueprint, request
from app.db import fetch_all, fetch_one, execute_query
from app.helpers import ok_response, error_response, require_login, get_next_id

sgc_bp = Blueprint("sgc", __name__)
SCHEMA = os.getenv("HANA_SCHEMA", "QRTEST")


@sgc_bp.route("/documents", methods=["GET"])
def get_documents():
    if not require_login():
        return error_response("No autenticado", 401)

    rows = fetch_all(
        f'''
        SELECT *
        FROM "{SCHEMA}"."SGC_DOCUMENTOS"
        ORDER BY "SGCID"
        '''
    )
    return ok_response(rows)


@sgc_bp.route("/documents/<int:sgc_id>", methods=["GET"])
def get_document(sgc_id):
    if not require_login():
        return error_response("No autenticado", 401)

    row = fetch_one(
        f'''
        SELECT *
        FROM "{SCHEMA}"."SGC_DOCUMENTOS"
        WHERE "SGCID" = ?
        ''',
        [sgc_id]
    )

    if not row:
        return error_response("Documento no encontrado", 404)

    return ok_response(row)


@sgc_bp.route("/documents", methods=["POST"])
def create_document():
    if not require_login():
        return error_response("No autenticado", 401)

    data = request.get_json(silent=True) or {}
    new_id = get_next_id("SGC_DOCUMENTOS", "SGCID")

    execute_query(
        f'''
        INSERT INTO "{SCHEMA}"."SGC_DOCUMENTOS"
        ("SGCID", "TITULO", "ARCHIVO_URL", "VISIBILIDAD", "ACTIVO")
        VALUES (?, ?, ?, ?, ?)
        ''',
        [
            new_id,
            data.get("TITULO"),
            data.get("ARCHIVO_URL"),
            data.get("VISIBILIDAD"),
            data.get("ACTIVO", 1),
        ]
    )

    return ok_response({"SGCID": new_id}, "Documento creado", 201)