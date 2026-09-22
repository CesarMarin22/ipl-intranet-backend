import os
from flask import Blueprint
from app.db import fetch_all
from app.helpers import ok_response, validate_active_session

branches_bp = Blueprint("branches", __name__)
SCHEMA = os.getenv("HANA_SCHEMA", "QRTEST")


@branches_bp.route("", methods=["GET"])
def get_branches():
    valid, response = validate_active_session()
    if not valid:
        return response

    rows = fetch_all(
        f'''
        SELECT
            "SUCURSALID",
            "CLAVE",
            "NOMBRE",
            "ACTIVO"
        FROM "{SCHEMA}"."SUCURSALES"
        WHERE "ACTIVO" = 1
        ORDER BY "CLAVE"
        '''
    )

    return ok_response(rows)