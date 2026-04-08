import os
from decimal import Decimal
from flask import Blueprint, request, session
from app.db import fetch_all, fetch_one, execute_query, execute_many
from app.helpers import ok_response, error_response, require_login, get_next_id

comedor_bp = Blueprint("comedor", __name__)
SCHEMA = os.getenv("HANA_SCHEMA", "QRTEST")


def get_config(clave):
    row = fetch_one(
        f'''
        SELECT "VALOR"
        FROM "{SCHEMA}"."CONFIG_COMEDOR"
        WHERE "CLAVE" = ?
          AND "ACTIVO" = 1
        ''',
        [clave]
    )
    return row["VALOR"] if row else None


def ensure_saldo(usuario_id):
    row = fetch_one(
        f'''
        SELECT "USUARIOID"
        FROM "{SCHEMA}"."SALDOS_COMEDOR"
        WHERE "USUARIOID" = ?
        ''',
        [usuario_id]
    )

    if not row:
        execute_query(
            f'''
            INSERT INTO "{SCHEMA}"."SALDOS_COMEDOR"
            ("USUARIOID", "COMIDAS_DISPONIBLES", "SALDO", "ACTIVO")
            VALUES (?, 0, 0, 1)
            ''',
            [usuario_id]
        )


@comedor_bp.route("/config", methods=["GET"])
def comedor_config():
    if not require_login():
        return error_response("No autenticado", 401)

    return ok_response({
        "COSTO_COMIDA": get_config("COSTO_COMIDA"),
        "BLOQUE_COMPRA": get_config("BLOQUE_COMPRA"),
        "MAXIMO_CONSUMO_DIARIO": get_config("MAXIMO_CONSUMO_DIARIO"),
    })


@comedor_bp.route("/saldos/<int:usuario_id>", methods=["GET"])
def get_saldo(usuario_id):
    if not require_login():
        return error_response("No autenticado", 401)

    ensure_saldo(usuario_id)

    row = fetch_one(
        f'''
        SELECT *
        FROM "{SCHEMA}"."SALDOS_COMEDOR"
        WHERE "USUARIOID" = ?
        ''',
        [usuario_id]
    )

    return ok_response(row)


@comedor_bp.route("/recargas", methods=["GET"])
def get_recargas():
    if not require_login():
        return error_response("No autenticado", 401)

    rows = fetch_all(
        f'''
        SELECT *
        FROM "{SCHEMA}"."RECARGAS_COMEDOR"
        ORDER BY "RECARGAID" DESC
        '''
    )
    return ok_response(rows)


@comedor_bp.route("/recargas", methods=["POST"])
def create_recarga():
    if not require_login():
        return error_response("No autenticado", 401)

    data = request.get_json(silent=True) or {}

    usuario_id = int(data.get("USUARIOID"))
    cantidad = int(data.get("CANTIDAD_COMIDAS"))
    observaciones = data.get("OBSERVACIONES", "")

    bloque = int(get_config("BLOQUE_COMPRA") or 5)
    costo_unitario = Decimal(str(get_config("COSTO_COMIDA") or "0"))
    monto_total = costo_unitario * cantidad

    if cantidad <= 0 or cantidad % bloque != 0:
        return error_response(f"Las comidas solo se pueden comprar en bloques de {bloque}", 400)

    ensure_saldo(usuario_id)

    recarga_id = get_next_id("RECARGAS_COMEDOR", "RECARGAID")

    execute_many([
        (
            f'''
            INSERT INTO "{SCHEMA}"."RECARGAS_COMEDOR"
            ("RECARGAID", "USUARIOID", "CANTIDAD_COMIDAS", "COSTO_UNITARIO", "MONTO_TOTAL", "FECHA", "USUARIO_RECARGA", "OBSERVACIONES", "ACTIVO")
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?, 1)
            ''',
            [recarga_id, usuario_id, cantidad, costo_unitario, monto_total, session["user_id"], observaciones]
        ),
        (
            f'''
            UPDATE "{SCHEMA}"."SALDOS_COMEDOR"
            SET
                "COMIDAS_DISPONIBLES" = "COMIDAS_DISPONIBLES" + ?,
                "SALDO" = "SALDO" + ?
            WHERE "USUARIOID" = ?
            ''',
            [cantidad, monto_total, usuario_id]
        )
    ])

    return ok_response({"RECARGAID": recarga_id}, "Recarga realizada", 201)


@comedor_bp.route("/consumos", methods=["GET"])
def get_consumos():
    if not require_login():
        return error_response("No autenticado", 401)

    rows = fetch_all(
        f'''
        SELECT *
        FROM "{SCHEMA}"."CONSUMOS_COMEDOR"
        ORDER BY "CONSUMOID" DESC
        '''
    )
    return ok_response(rows)


@comedor_bp.route("/consumos", methods=["POST"])
def create_consumo():
    if not require_login():
        return error_response("No autenticado", 401)

    data = request.get_json(silent=True) or {}
    usuario_id = int(data.get("USUARIOID"))
    qr_folio = data.get("QR_FOLIO", "")

    ensure_saldo(usuario_id)

    maximo_diario = int(get_config("MAXIMO_CONSUMO_DIARIO") or 1)
    costo_comida = Decimal(str(get_config("COSTO_COMIDA") or "0"))

    consumo_hoy = fetch_one(
        f'''
        SELECT COUNT(*) AS "TOTAL"
        FROM "{SCHEMA}"."CONSUMOS_COMEDOR"
        WHERE "USUARIOID" = ?
          AND "FECHA" = CURRENT_DATE
          AND "ACTIVO" = 1
        ''',
        [usuario_id]
    )

    if int(consumo_hoy["TOTAL"]) >= maximo_diario:
        return error_response("El usuario ya consumió hoy", 400)

    saldo = fetch_one(
        f'''
        SELECT "COMIDAS_DISPONIBLES", "SALDO"
        FROM "{SCHEMA}"."SALDOS_COMEDOR"
        WHERE "USUARIOID" = ?
        ''',
        [usuario_id]
    )

    if int(saldo["COMIDAS_DISPONIBLES"]) <= 0:
        return error_response("El usuario no tiene comidas disponibles", 400)

    consumo_id = get_next_id("CONSUMOS_COMEDOR", "CONSUMOID")

    execute_many([
        (
            f'''
            INSERT INTO "{SCHEMA}"."CONSUMOS_COMEDOR"
            ("CONSUMOID", "USUARIOID", "FECHA", "HORA", "COMIDAS_DESCONTADAS", "COSTO_APLICADO", "SUCURSAL", "QR_FOLIO", "USUARIO_COBRO", "ACTIVO")
            VALUES (?, ?, CURRENT_DATE, CURRENT_TIME, 1, ?, ?, ?, ?, 1)
            ''',
            [consumo_id, usuario_id, costo_comida, session.get("sucursal"), qr_folio, session["user_id"]]
        ),
        (
            f'''
            UPDATE "{SCHEMA}"."SALDOS_COMEDOR"
            SET
                "COMIDAS_DISPONIBLES" = "COMIDAS_DISPONIBLES" - 1,
                "SALDO" = "SALDO" - ?
            WHERE "USUARIOID" = ?
            ''',
            [costo_comida, usuario_id]
        )
    ])

    return ok_response({"CONSUMOID": consumo_id}, "Consumo registrado", 201)


@comedor_bp.route("/reportes/resumen", methods=["GET"])
def comedor_resumen():
    if not require_login():
        return error_response("No autenticado", 401)

    recargas = fetch_one(
        f'''
        SELECT COUNT(*) AS "TOTAL_RECARGAS", COALESCE(SUM("MONTO_TOTAL"), 0) AS "MONTO_RECARGAS"
        FROM "{SCHEMA}"."RECARGAS_COMEDOR"
        WHERE "ACTIVO" = 1
        '''
    )

    consumos = fetch_one(
        f'''
        SELECT COUNT(*) AS "TOTAL_CONSUMOS", COALESCE(SUM("COSTO_APLICADO"), 0) AS "MONTO_CONSUMOS"
        FROM "{SCHEMA}"."CONSUMOS_COMEDOR"
        WHERE "ACTIVO" = 1
        '''
    )

    return ok_response({
        "recargas": recargas,
        "consumos": consumos
    })