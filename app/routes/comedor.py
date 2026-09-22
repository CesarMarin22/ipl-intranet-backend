import os
import uuid
from decimal import Decimal
from datetime import date
from datetime import datetime
from decimal import Decimal
from flask import Blueprint, request, session
from app.db import fetch_all, fetch_one, execute_many, execute_query
from app.helpers import (
    ok_response,
    error_response,
    validate_active_session,
    require_permission,
    get_next_id,
)

comedor_bp = Blueprint("comedor", __name__)
SCHEMA = os.getenv("HANA_SCHEMA", "QRTEST")


def get_config(clave):
    row = fetch_one(
        f"""
        SELECT "VALOR"
        FROM "{SCHEMA}"."CONFIG_COMEDOR"
        WHERE "CLAVE" = ?
          AND "ACTIVO" = 1
        """,
        [clave],
    )
    return row["VALOR"] if row else None


def ensure_saldo(usuario_id):
    row = fetch_one(
        f"""
        SELECT "USUARIOID"
        FROM "{SCHEMA}"."SALDOS_COMEDOR"
        WHERE "USUARIOID" = ?
        """,
        [usuario_id],
    )

    if not row:
        from app.db import execute_query

        execute_query(
            f"""
            INSERT INTO "{SCHEMA}"."SALDOS_COMEDOR"
            ("USUARIOID", "COMIDAS_DISPONIBLES", "SALDO", "ACTIVO")
            VALUES (?, 0, 0, 1)
            """,
            [usuario_id],
        )


def get_user_by_employee_number(numero_empleado):
    return fetch_one(
        f"""
        SELECT
            "USUARIOID",
            "NOMBRE",
            "NUMERO_EMPLEADO",
            "SUCURSAL",
            "TIPO_EMPLEADO",
            "TIPO_EMPLEADO_ID",
            "ACTIVO"
        FROM "{SCHEMA}"."USUARIOS"
        WHERE UPPER("NUMERO_EMPLEADO") = UPPER(?)
          AND "ACTIVO" = 1
        """,
        [numero_empleado],
    )


def get_date_filters(column_sql):
    fecha_inicio = request.args.get("fecha_inicio")
    fecha_fin = request.args.get("fecha_fin")

    conditions = []
    params = []

    if fecha_inicio:
        conditions.append(f"{column_sql} >= TO_DATE(?)")
        params.append(fecha_inicio)

    if fecha_fin:
        conditions.append(f"{column_sql} <= TO_DATE(?)")
        params.append(fecha_fin)

    return conditions, params


@comedor_bp.route("/config", methods=["GET"])
def comedor_config():
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("COMEDOR", "VER")
    if not allowed:
        return response

    return ok_response(
        {
            "COSTO_COMIDA": get_config("COSTO_COMIDA"),
            "BLOQUE_COMPRA": get_config("BLOQUE_COMPRA"),
            "MAXIMO_CONSUMO_DIARIO": get_config("MAXIMO_CONSUMO_DIARIO"),
        }
    )


@comedor_bp.route("/mi-saldo", methods=["GET"])
def get_my_saldo():
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("CONSUMO_COMEDOR", "VER")
    if not allowed:
        return response

    user_id = session.get("user_id")

    if not user_id:
        return error_response("No autenticado", 401)

    user = fetch_one(
        f"""
        SELECT
            "USUARIOID",
            "NOMBRE",
            "NUMERO_EMPLEADO",
            "SUCURSAL"
        FROM "{SCHEMA}"."USUARIOS"
        WHERE "USUARIOID" = ?
          AND "ACTIVO" = 1
        """,
        [user_id],
    )

    if not user:
        return error_response("Usuario no encontrado", 404)

    ensure_saldo(user_id)

    saldo = fetch_one(
        f"""
        SELECT
            "COMIDAS_DISPONIBLES"
        FROM "{SCHEMA}"."SALDOS_COMEDOR"
        WHERE "USUARIOID" = ?
        """,
        [user_id],
    )

    consumos_mes = fetch_one(
        f"""
        SELECT
            COALESCE(
                SUM("COMIDAS_DESCONTADAS"),
                0
            ) AS "TOTAL"
        FROM "{SCHEMA}"."CONSUMOS_COMEDOR"
        WHERE "USUARIOID" = ?
          AND "ACTIVO" = 1
          AND YEAR("FECHA") = YEAR(CURRENT_DATE)
          AND MONTH("FECHA") = MONTH(CURRENT_DATE)
        """,
        [user_id],
    )

    ultimo_consumo = fetch_one(
        f"""
        SELECT TOP 1
            TO_VARCHAR("FECHA", 'YYYY-MM-DD') AS "FECHA",
            TO_VARCHAR("HORA", 'HH24:MI:SS') AS "HORA",
            "COMIDAS_DESCONTADAS",
            "QR_FOLIO"
        FROM "{SCHEMA}"."CONSUMOS_COMEDOR"
        WHERE "USUARIOID" = ?
          AND "ACTIVO" = 1
        ORDER BY
            "FECHA" DESC,
            "HORA" DESC
        """,
        [user_id],
    )

    ultima_recarga = fetch_one(
        f"""
        SELECT TOP 1
            TO_VARCHAR("FECHA", 'YYYY-MM-DD') AS "FECHA",
            TO_VARCHAR("FECHA", 'HH24:MI:SS') AS "HORA",
            "CANTIDAD_COMIDAS",
            "PAQUETE_COMIDAS"
        FROM "{SCHEMA}"."RECARGAS_COMEDOR"
        WHERE "USUARIOID" = ?
          AND "ACTIVO" = 1
        ORDER BY "FECHA" DESC
        """,
        [user_id],
    )

    compartidas_activas = fetch_one(
        f"""
        SELECT
            COALESCE(
                SUM("CANTIDAD_COMIDAS"),
                0
            ) AS "TOTAL"
        FROM "{SCHEMA}"."COMEDOR_QR_COMPARTIDOS"
        WHERE "USUARIO_ORIGEN_ID" = ?
          AND "USADO" = 0
          AND "CANCELADO" = 0
          AND "EXPIRADO" = 0
          AND "DEVUELTO" = 0
          AND "FECHA_EXPIRACION" >= CURRENT_TIMESTAMP
        """,
        [user_id],
    )

    compartidas_mes = fetch_one(
        f"""
        SELECT
            COALESCE(
                SUM("CANTIDAD_COMIDAS"),
                0
            ) AS "TOTAL"
        FROM "{SCHEMA}"."COMEDOR_QR_COMPARTIDOS"
        WHERE "USUARIO_ORIGEN_ID" = ?
          AND YEAR("FECHA_CREACION") = YEAR(CURRENT_DATE)
          AND MONTH("FECHA_CREACION") = MONTH(CURRENT_DATE)
        """,
        [user_id],
    )

    comidas_disponibles = (
        int(saldo.get("COMIDAS_DISPONIBLES") or 0)
        if saldo
        else 0
    )

    consumidas_mes = (
        int(consumos_mes.get("TOTAL") or 0)
        if consumos_mes
        else 0
    )

    compartidas_activas_total = (
        int(compartidas_activas.get("TOTAL") or 0)
        if compartidas_activas
        else 0
    )

    compartidas_mes_total = (
        int(compartidas_mes.get("TOTAL") or 0)
        if compartidas_mes
        else 0
    )

    ultimo_consumo_data = None

    if ultimo_consumo:
        qr_folio = str(
            ultimo_consumo.get("QR_FOLIO") or ""
        )

        ultimo_consumo_data = {
            "FECHA": ultimo_consumo.get("FECHA"),
            "HORA": ultimo_consumo.get("HORA"),
            "COMIDAS": int(
                ultimo_consumo.get(
                    "COMIDAS_DESCONTADAS"
                ) or 0
            ),
            "TIPO": (
                "COMPARTIDA"
                if qr_folio.startswith("IPL|SHARE|")
                else "PERSONAL"
            ),
        }

    ultima_recarga_data = None

    if ultima_recarga:
        cantidad_recargada = int(
            ultima_recarga.get("CANTIDAD_COMIDAS") or 0
        )

        paquete_comidas = int(
            ultima_recarga.get("PAQUETE_COMIDAS") or 0
        )

        ultima_recarga_data = {
            "FECHA": ultima_recarga.get("FECHA"),
            "HORA": ultima_recarga.get("HORA"),
            "COMIDAS_AGREGADAS": cantidad_recargada,
            "PAQUETE_COMIDAS": paquete_comidas,
        }

    return ok_response(
        {
            "USUARIOID": user["USUARIOID"],
            "NOMBRE": user["NOMBRE"],
            "NUMERO_EMPLEADO": user["NUMERO_EMPLEADO"],
            "SUCURSAL": user["SUCURSAL"],
            "COMIDAS_DISPONIBLES": comidas_disponibles,
            "CONSUMIDAS_MES": consumidas_mes,
            "ULTIMO_CONSUMO": ultimo_consumo_data,
            "ULTIMA_RECARGA": ultima_recarga_data,
            "COMPARTIDAS_ACTIVAS": compartidas_activas_total,
            "COMPARTIDAS_MES": compartidas_mes_total,
        }
    )

@comedor_bp.route("/empleado/<string:numero_empleado>", methods=["GET"])
def get_empleado_comedor(numero_empleado):
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("RECARGAS_COMEDOR", "VER")
    if not allowed:
        return response

    user = get_user_by_employee_number(numero_empleado)
    if not user:
        return error_response("Empleado no encontrado", 404)

    if not str(user.get("SUCURSAL") or "").strip():
        return error_response("El empleado no tiene sucursal asignada", 400)

    tipo_empleado_id = user.get("TIPO_EMPLEADO_ID")

    if not tipo_empleado_id:
        return error_response("El empleado no tiene tipo de empleado asignado", 400)

    precio = fetch_one(
        f"""
        SELECT
            T."NOMBRE" AS "TIPO_EMPLEADO_NOMBRE",
            P."PRECIO_NORMAL",
            P."PRECIO_X10",
            P."PRECIO_X20"
        FROM "{SCHEMA}"."TIPOS_EMPLEADO" T
        INNER JOIN "{SCHEMA}"."PRECIOS_COMEDOR" P
            ON P."TIPO_EMPLEADO_ID" = T."TIPO_EMPLEADO_ID"
        WHERE T."TIPO_EMPLEADO_ID" = ?
          AND T."ACTIVO" = 1
          AND P."ACTIVO" = 1
        """,
        [tipo_empleado_id],
    )

    if not precio:
        return error_response("No existe precio activo para el tipo de empleado", 400)

    precio_normal = float(precio.get("PRECIO_NORMAL") or 0)
    precio_x10 = float(precio.get("PRECIO_X10") or 0)
    precio_x20 = float(precio.get("PRECIO_X20") or 0)
    precio_vigente = precio_normal

    ensure_saldo(user["USUARIOID"])

    saldo = fetch_one(
        f"""
        SELECT "USUARIOID", "COMIDAS_DISPONIBLES", "SALDO", "ACTIVO"
        FROM "{SCHEMA}"."SALDOS_COMEDOR"
        WHERE "USUARIOID" = ?
        """,
        [user["USUARIOID"]],
    )

    return ok_response(
        {
            "USUARIOID": user["USUARIOID"],
            "NOMBRE": user["NOMBRE"],
            "NUMERO_EMPLEADO": user["NUMERO_EMPLEADO"],
            "SUCURSAL": user["SUCURSAL"],
            "TIPO_EMPLEADO_ID": tipo_empleado_id,
            "TIPO_EMPLEADO_NOMBRE": precio.get("TIPO_EMPLEADO_NOMBRE"),
            "PRECIO_NORMAL": precio_normal,
            "PRECIO_VIGENTE": precio_vigente,
            "PRECIO_X10": precio_x10,
            "PRECIO_X20": precio_x20,
            "COMIDAS_DISPONIBLES": saldo.get("COMIDAS_DISPONIBLES", 0) if saldo else 0,
            "SALDO": saldo.get("SALDO", 0) if saldo else 0,
        }
    )


@comedor_bp.route("/recargas", methods=["GET"])
def get_recargas():
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("RECARGAS_COMEDOR", "VER")
    if not allowed:
        return response

    rows = fetch_all(f"""
        SELECT 
            R."RECARGAID",
            R."USUARIOID",
            U."NOMBRE",
            U."NUMERO_EMPLEADO",
            R."CANTIDAD_COMIDAS",
            R."PAQUETE_COMIDAS",
            R."COSTO_UNITARIO",
            R."DESCUENTO_APLICADO",
            R."MONTO_TOTAL",
            R."FECHA",
            R."OBSERVACIONES",
            R."USUARIO_RECARGA",
            UR."NOMBRE" AS "NOMBRE_USUARIO_RECARGA"
        FROM "{SCHEMA}"."RECARGAS_COMEDOR" R
        INNER JOIN "{SCHEMA}"."USUARIOS" U
            ON R."USUARIOID" = U."USUARIOID"
        LEFT JOIN "{SCHEMA}"."USUARIOS" UR
            ON R."USUARIO_RECARGA" = UR."USUARIOID"
        WHERE R."ACTIVO" = 1
        ORDER BY R."RECARGAID" DESC
    """)

    return ok_response(rows)


@comedor_bp.route("/recargas", methods=["POST"])
def create_recarga():
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("RECARGAS_COMEDOR", "CREAR")
    if not allowed:
        return response

    data = request.get_json(silent=True) or {}

    try:
        usuario_id = int(data.get("USUARIOID"))
        cantidad = int(data.get("CANTIDAD_COMIDAS"))
    except (TypeError, ValueError):
        return error_response("USUARIOID y CANTIDAD_COMIDAS son obligatorios", 400)

    observaciones = data.get("OBSERVACIONES", "")

    paquete = fetch_one(
        f"""
        SELECT
            "PAQUETEID",
            "CANTIDAD_COMIDAS",
            "NOMBRE",
            "PORCENTAJE_DESCUENTO"
        FROM "{SCHEMA}"."PAQUETES_COMEDOR"
        WHERE "CANTIDAD_COMIDAS" = ?
          AND "ACTIVO" = 1
        """,
        [cantidad],
    )

    if not paquete:
        return error_response("El paquete seleccionado no existe o está inactivo", 400)

    usuario = fetch_one(
        f"""
        SELECT
            U."USUARIOID",
            U."NOMBRE",
            U."TIPO_EMPLEADO_ID",
            T."NOMBRE" AS "TIPO_EMPLEADO_NOMBRE",
            P."PRECIO_NORMAL",
            P."PRECIO_X10",
            P."PRECIO_X20"
        FROM "{SCHEMA}"."USUARIOS" U
        INNER JOIN "{SCHEMA}"."TIPOS_EMPLEADO" T
            ON T."TIPO_EMPLEADO_ID" = U."TIPO_EMPLEADO_ID"
        INNER JOIN "{SCHEMA}"."PRECIOS_COMEDOR" P
            ON P."TIPO_EMPLEADO_ID" = U."TIPO_EMPLEADO_ID"
        WHERE U."USUARIOID" = ?
          AND U."ACTIVO" = 1
          AND T."ACTIVO" = 1
          AND P."ACTIVO" = 1
        """,
        [usuario_id],
    )

    if not usuario:
        return error_response(
            "Usuario no encontrado, inactivo o sin precio configurado", 404
        )

    tipo_empleado_id = usuario.get("TIPO_EMPLEADO_ID")
    cantidad_decimal = Decimal(cantidad)

    if cantidad == 1:
        monto_total = Decimal(str(usuario.get("PRECIO_NORMAL") or "0"))
    elif cantidad == 10:
        monto_total = Decimal(str(usuario.get("PRECIO_X10") or "0"))
    elif cantidad == 20:
        monto_total = Decimal(str(usuario.get("PRECIO_X20") or "0"))
    else:
        return error_response(
            "Paquete no configurado. Solo se permite 1, 10 o 20 comidas.",
            400,
        )

    if monto_total <= 0:
        return error_response(
            "El precio del paquete no está configurado correctamente",
            400,
        )

    costo_unitario = monto_total / cantidad_decimal
    monto_subtotal = monto_total
    descuento_aplicado = Decimal("0")

    ensure_saldo(usuario_id)
    recarga_id = get_next_id("RECARGAS_COMEDOR", "RECARGAID")

    execute_many(
        [
            (
                f"""
                INSERT INTO "{SCHEMA}"."RECARGAS_COMEDOR"
                (
                    "RECARGAID",
                    "USUARIOID",
                    "CANTIDAD_COMIDAS",
                    "COSTO_UNITARIO",
                    "MONTO_TOTAL",
                    "FECHA",
                    "USUARIO_RECARGA",
                    "OBSERVACIONES",
                    "ACTIVO",
                    "PAQUETE_COMIDAS",
                    "DESCUENTO_APLICADO"
                )
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?, 1, ?, ?)
                """,
                [
                    recarga_id,
                    usuario_id,
                    cantidad,
                    costo_unitario,
                    monto_total,
                    session["user_id"],
                    observaciones,
                    cantidad,
                    descuento_aplicado,
                ],
            ),
            (
                f"""
                UPDATE "{SCHEMA}"."SALDOS_COMEDOR"
                SET
                    "COMIDAS_DISPONIBLES" = "COMIDAS_DISPONIBLES" + ?,
                    "SALDO" = "SALDO" + ?
                WHERE "USUARIOID" = ?
                """,
                [cantidad, monto_total, usuario_id],
            ),
        ]
    )

    return ok_response(
        {
            "RECARGAID": recarga_id,
            "USUARIOID": usuario_id,
            "CANTIDAD_COMIDAS": cantidad,
            "TIPO_EMPLEADO_ID": tipo_empleado_id,
            "TIPO_EMPLEADO_NOMBRE": usuario.get("TIPO_EMPLEADO_NOMBRE"),
            "PAQUETE": paquete.get("NOMBRE"),
            "COSTO_UNITARIO": costo_unitario,
            "SUBTOTAL": monto_subtotal,
            "DESCUENTO_APLICADO": descuento_aplicado,
            "MONTO_TOTAL": monto_total,
        },
        "Recarga realizada",
        201,
    )


@comedor_bp.route("/consumos", methods=["GET"])
def get_consumos():
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("CONSUMO_COMEDOR", "VER")
    if not allowed:
        return response

    rows = fetch_all(f"""
        SELECT *
        FROM "{SCHEMA}"."CONSUMOS_COMEDOR"
        ORDER BY "CONSUMOID" DESC
        """)
    return ok_response(rows)


@comedor_bp.route("/consumos/scan", methods=["POST"])
def create_consumo_by_scan():
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("ESCANEO_COMEDOR", "CONSUMIR")
    if not allowed:
        return response

    data = request.get_json(silent=True) or {}

    numero_empleado = (data.get("NUMERO_EMPLEADO") or "").strip()
    qr_folio = (data.get("QR_FOLIO") or numero_empleado).strip()

    if not qr_folio:
        return error_response(
            "QR_FOLIO o NUMERO_EMPLEADO es obligatorio",
            400,
        )

    if qr_folio.startswith("IPL|SHARE|"):
        return consumir_qr_compartido_desde_scan(qr_folio)

    usuario_cobro = fetch_one(
        f"""
        SELECT "SUCURSAL"
        FROM "{SCHEMA}"."USUARIOS"
        WHERE "USUARIOID" = ?
          AND "ACTIVO" = 1
        """,
        [session["user_id"]],
    )

    sucursal_cobro = str(usuario_cobro.get("SUCURSAL") if usuario_cobro else "").strip()

    if not sucursal_cobro:
        return error_response(
            "El usuario que cobra no tiene sucursal asignada",
            400,
        )

    if sucursal_cobro != "90":
        return error_response(
            "Este punto de comedor no está habilitado para consumo",
            400,
        )

    qr_dyn_token = None

    if qr_folio.startswith("IPL|"):
        user, qr_error, qr_dyn_token = get_user_by_dynamic_qr(qr_folio)

        if qr_error:
            return error_response(qr_error, 400)
    else:
        user = get_user_by_employee_number(numero_empleado)

        if not user:
            return error_response(
                "Empleado no encontrado",
                404,
            )

    usuario_id = user["USUARIOID"]

    ensure_saldo(usuario_id)

    consumo_hoy = fetch_one(
        f"""
        SELECT COUNT(*) AS "TOTAL"
        FROM "{SCHEMA}"."CONSUMOS_COMEDOR"
        WHERE "USUARIOID" = ?
          AND "FECHA" = CURRENT_DATE
          AND "ACTIVO" = 1
          AND (
                "QR_FOLIO" IS NULL
                OR "QR_FOLIO" NOT LIKE 'IPL|SHARE|%'
              )
        """,
        [usuario_id],
    )

    if int(consumo_hoy.get("TOTAL") or 0) > 0:
        return error_response(
            f'{user["NOMBRE"]} ya registró su comida del día',
            400,
        )

    saldo = fetch_one(
        f"""
        SELECT
            "COMIDAS_DISPONIBLES",
            "COMIDAS_PROMO",
            "SALDO"
        FROM "{SCHEMA}"."SALDOS_COMEDOR"
        WHERE "USUARIOID" = ?
        """,
        [usuario_id],
    )

    comidas_disponibles = int(saldo.get("COMIDAS_DISPONIBLES") or 0) if saldo else 0

    comidas_promo = int(saldo.get("COMIDAS_PROMO") or 0) if saldo else 0

    if comidas_disponibles <= 0:
        return error_response(
            f'{user["NOMBRE"]} ya no cuenta con comidas disponibles',
            400,
        )

    # El consumo depende únicamente de COMIDAS_DISPONIBLES.
    # SALDO no se valida y tampoco se descuenta.
    descontar_promo = comidas_promo > 0
    costo_aplicado = Decimal("0")

    consumo_id = get_next_id(
        "CONSUMOS_COMEDOR",
        "CONSUMOID",
    )

    operations = [
        (
            f"""
            INSERT INTO "{SCHEMA}"."CONSUMOS_COMEDOR"
            (
                "CONSUMOID",
                "USUARIOID",
                "FECHA",
                "HORA",
                "COMIDAS_DESCONTADAS",
                "COSTO_APLICADO",
                "SUCURSAL",
                "QR_FOLIO",
                "USUARIO_COBRO",
                "ACTIVO",
                "TIPO_EMPLEADO_ID"
            )
            VALUES (
                ?,
                ?,
                CURRENT_DATE,
                CURRENT_TIME,
                1,
                ?,
                ?,
                ?,
                ?,
                1,
                ?
            )
            """,
            [
                consumo_id,
                usuario_id,
                costo_aplicado,
                sucursal_cobro,
                qr_folio,
                session["user_id"],
                user.get("TIPO_EMPLEADO_ID"),
            ],
        )
    ]

    if descontar_promo:
        operations.append(
            (
                f"""
                UPDATE "{SCHEMA}"."SALDOS_COMEDOR"
                SET
                    "COMIDAS_DISPONIBLES" =
                        "COMIDAS_DISPONIBLES" - 1,
                    "COMIDAS_PROMO" =
                        "COMIDAS_PROMO" - 1
                WHERE "USUARIOID" = ?
                  AND "COMIDAS_DISPONIBLES" > 0
                  AND "COMIDAS_PROMO" > 0
                """,
                [usuario_id],
            )
        )
    else:
        operations.append(
            (
                f"""
                UPDATE "{SCHEMA}"."SALDOS_COMEDOR"
                SET
                    "COMIDAS_DISPONIBLES" =
                        "COMIDAS_DISPONIBLES" - 1
                WHERE "USUARIOID" = ?
                  AND "COMIDAS_DISPONIBLES" > 0
                """,
                [usuario_id],
            )
        )

    if qr_dyn_token:
        operations.append(
            (
                f"""
                UPDATE "{SCHEMA}"."QR_TOKENS_DINAMICOS"
                SET "USADO" = 1
                WHERE "TOKEN" = ?
                """,
                [qr_dyn_token],
            )
        )

    execute_many(operations)

    saldo_actualizado = fetch_one(
        f"""
        SELECT
            "COMIDAS_DISPONIBLES",
            "COMIDAS_PROMO",
            "SALDO"
        FROM "{SCHEMA}"."SALDOS_COMEDOR"
        WHERE "USUARIOID" = ?
        """,
        [usuario_id],
    )

    return ok_response(
        {
            "CONSUMOID": consumo_id,
            "NOMBRE": user["NOMBRE"],
            "NUMERO_EMPLEADO": user["NUMERO_EMPLEADO"],
            "SUCURSAL_CONSUMO": sucursal_cobro,
            "SUCURSAL_EMPLEADO": user.get("SUCURSAL"),
            "TIPO_EMPLEADO_ID": user.get("TIPO_EMPLEADO_ID"),
            "COSTO_APLICADO": costo_aplicado,
            "COMIDAS_DISPONIBLES": saldo_actualizado.get("COMIDAS_DISPONIBLES"),
            "COMIDAS_PROMO": saldo_actualizado.get("COMIDAS_PROMO"),
            "SALDO": saldo_actualizado.get("SALDO"),
        },
        "Consumo aprobado",
        201,
    )


def consumir_qr_compartido_desde_scan(qr_folio):
    parts = qr_folio.split("|")

    if len(parts) != 3:
        return error_response("Formato de QR compartido inválido", 400)

    _, tipo, token = parts

    if tipo != "SHARE":
        return error_response("Tipo de QR compartido inválido", 400)

    usuario_cobro = fetch_one(
        f"""
        SELECT "SUCURSAL"
        FROM "{SCHEMA}"."USUARIOS"
        WHERE "USUARIOID" = ?
          AND "ACTIVO" = 1
        """,
        [session["user_id"]],
    )

    sucursal_cobro = str(usuario_cobro.get("SUCURSAL") if usuario_cobro else "").strip()

    if not sucursal_cobro:
        return error_response("El usuario que cobra no tiene sucursal asignada", 400)

    if sucursal_cobro != "90":
        return error_response(
            "Este punto de comedor no está habilitado para consumo", 400
        )

    qr = fetch_one(
        f"""
        SELECT
            Q."QRID",
            Q."USUARIO_ORIGEN_ID",
            Q."TOKEN",
            Q."CANTIDAD_COMIDAS",
            Q."USADO",
            Q."CANCELADO",
            Q."FECHA_EXPIRACION",
            Q."EXPIRADO",
            Q."DEVUELTO",
            Q."COMIDAS_PROMO_DESCONTADAS",
            Q."COMIDAS_NORMALES_DESCONTADAS",
            Q."MONTO_DESCONTADO",
            Q."TIPO_EMPLEADO_ID",
            U."NOMBRE",
            U."NUMERO_EMPLEADO"
        FROM "{SCHEMA}"."COMEDOR_QR_COMPARTIDOS" Q
        INNER JOIN "{SCHEMA}"."USUARIOS" U
            ON U."USUARIOID" = Q."USUARIO_ORIGEN_ID"
        WHERE Q."TOKEN" = ?
        """,
        [token],
    )

    if not qr:
        return error_response("QR compartido no encontrado", 404)

    if int(qr.get("CANCELADO") or 0) == 1:
        return error_response("Este QR compartido fue cancelado", 400)

    if int(qr.get("USADO") or 0) == 1:
        return error_response("Este QR compartido ya fue usado", 400)

    if int(qr.get("EXPIRADO") or 0) == 1:
        return error_response("Este QR compartido ya expiro", 400)

    fecha_expiracion = qr.get("FECHA_EXPIRACION")

    if fecha_expiracion:
        expirado = fetch_one(
            """
            SELECT
                CASE
                    WHEN CURRENT_TIMESTAMP > ? THEN 1 
                    ELSE 0
                END AS "EXPIRADO"
            FROM DUMMY
            """,
            [fecha_expiracion],
        )

        if int(expirado["EXPIRADO"]) == 1:

            if int(qr.get("DEVUELTO") or 0) == 0:
                devolver_comida_qr_expirado(qr)

            return error_response(
                "Este QR compartido ya expiro y la comida fue devuelta al usuario de origen"
            )

    cantidad = int(qr.get("CANTIDAD_COMIDAS") or 1)
    costo_aplicado = Decimal(str(qr.get("MONTO_DESCONTADO") or "0"))

    consumo_id = get_next_id("CONSUMOS_COMEDOR", "CONSUMOID")

    execute_many(
        [
            (
                f"""
            INSERT INTO "{SCHEMA}"."CONSUMOS_COMEDOR"
            ("CONSUMOID", "USUARIOID", "FECHA", "HORA", "COMIDAS_DESCONTADAS",
             "COSTO_APLICADO", "SUCURSAL", "QR_FOLIO", "USUARIO_COBRO", "ACTIVO", "TIPO_EMPLEADO_ID")
            VALUES (?, ?, CURRENT_DATE, CURRENT_TIME, ?, ?, ?, ?, ?, 1, ?)
            """,
                [
                    consumo_id,
                    qr["USUARIO_ORIGEN_ID"],
                    cantidad,
                    costo_aplicado,
                    sucursal_cobro,
                    qr_folio,
                    session["user_id"],
                    qr.get("TIPO_EMPLEADO_ID"),
                ],
            ),
            (
                f"""
            UPDATE "{SCHEMA}"."COMEDOR_QR_COMPARTIDOS"
            SET
                "USADO" = 1,
                "FECHA_USO" = CURRENT_TIMESTAMP,
                "USUARIO_COBRO" = ?,
                "SUCURSAL_USO" = ?
            WHERE "QRID" = ?
                AND "USADO" = 0
                AND "CANCELADO" = 0
            """,
                [session["user_id"], sucursal_cobro, qr["QRID"]],
            ),
        ]
    )

    return ok_response(
        {
            "CONSUMOID": consumo_id,
            "NOMBRE": f'QR compartido por {qr["NOMBRE"]}',
            "NUMERO_EMPLEADO": qr["NUMERO_EMPLEADO"],
            "SUCURSAL_CONSUMO": sucursal_cobro,
            "COMIDAS_DESCONTADAS": cantidad,
            "COMIDAS_DISPONIBLES": 0,
            "SALDO": 0,
        },
        "Consumo por QR compartido aprobado",
        201,
    )


def devolver_comida_qr_expirado(qr):
    cantidad = int(qr.get("CANTIDAD_COMIDAS") or 1)
    promo_regresar = int(qr.get("COMIDAS_PROMO_DESCONTADAS") or 0)

    # Al expirar el QR solamente se regresan las comidas.
    # El saldo monetario no se modifica.
    execute_many(
        [
            (
                f"""
                UPDATE "{SCHEMA}"."SALDOS_COMEDOR"
                SET
                    "COMIDAS_DISPONIBLES" =
                        "COMIDAS_DISPONIBLES" + ?,
                    "COMIDAS_PROMO" =
                        "COMIDAS_PROMO" + ?
                WHERE "USUARIOID" = ?
                """,
                [
                    cantidad,
                    promo_regresar,
                    qr["USUARIO_ORIGEN_ID"],
                ],
            ),
            (
                f"""
                UPDATE "{SCHEMA}"."COMEDOR_QR_COMPARTIDOS"
                SET
                    "EXPIRADO" = 1,
                    "FECHA_EXPIRADO" = CURRENT_TIMESTAMP,
                    "DEVUELTO" = 1
                WHERE "QRID" = ?
                  AND "USADO" = 0
                  AND "CANCELADO" = 0
                  AND "DEVUELTO" = 0
                """,
                [qr["QRID"]],
            ),
        ]
    )


@comedor_bp.route("/reportes/resumen", methods=["GET"])
def comedor_resumen():
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("REPORTES_COMEDOR", "VER")
    if not allowed:
        return response

    rec_conditions, rec_params = get_date_filters('TO_DATE("FECHA")')
    con_conditions, con_params = get_date_filters('"FECHA"')

    rec_where = " AND ".join(['"ACTIVO" = 1'] + rec_conditions)
    con_where = " AND ".join(['"ACTIVO" = 1'] + con_conditions)

    recargas = fetch_one(
        f"""
        SELECT
            COUNT(*) AS "TOTAL_RECARGAS",
            COALESCE(SUM(CASE WHEN "MONTO_TOTAL" > 0 THEN "MONTO_TOTAL" ELSE 0 END), 0) AS "MONTO_RECARGAS",
            COALESCE(SUM(CASE WHEN "MONTO_TOTAL" = 0 THEN "CANTIDAD_COMIDAS" ELSE 0 END), 0) AS "COMIDAS_PROMO_RECARGADAS"
        FROM "{SCHEMA}"."RECARGAS_COMEDOR"
        WHERE {rec_where}
        """,
        rec_params,
    )

    consumos = fetch_one(
        f"""
        SELECT
            COUNT(*) AS "TOTAL_CONSUMOS",
            COALESCE(SUM("COMIDAS_DESCONTADAS"), 0) AS "COMIDAS_CONSUMIDAS",
            COALESCE(SUM("COSTO_APLICADO"), 0) AS "MONTO_CONSUMOS",
            COALESCE(
                ROUND(
                    COALESCE(SUM("COMIDAS_DESCONTADAS"), 0)
                    / NULLIF(COUNT(DISTINCT "FECHA"), 0),
                    2
                ),
                0
            ) AS "PROMEDIO_DIARIO"
        FROM "{SCHEMA}"."CONSUMOS_COMEDOR"
        WHERE {con_where}
        """,
        con_params,
    )

    return ok_response(
        {
            "recargas": recargas,
            "consumos": consumos,
        }
    )


@comedor_bp.route("/reportes/consumos-diarios", methods=["GET"])
def consumos_diarios():
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("REPORTES_COMEDOR", "VER")
    if not allowed:
        return response

    rows = fetch_all(f"""
        SELECT 
            "FECHA",
            COUNT(*) AS "TOTAL_CONSUMOS",
            SUM("COSTO_APLICADO") AS "MONTO"
        FROM "{SCHEMA}"."CONSUMOS_COMEDOR"
        WHERE "ACTIVO" = 1
        GROUP BY "FECHA"
        ORDER BY "FECHA"
        """)

    return ok_response(rows)


@comedor_bp.route("/reportes/movimientos-diarios", methods=["GET"])
def movimientos_diarios():
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("REPORTES_COMEDOR", "VER")
    if not allowed:
        return response

    rec_conditions, rec_params = get_date_filters('TO_DATE(R."FECHA")')
    con_conditions, con_params = get_date_filters('C."FECHA"')

    rec_where = " AND ".join(['R."ACTIVO" = 1'] + rec_conditions)
    con_where = " AND ".join(['C."ACTIVO" = 1'] + con_conditions)

    rows = fetch_all(
        f"""
        SELECT
            X."FECHA",
            SUM(X."RECARGAS") AS "RECARGAS",
            SUM(X."CONSUMOS") AS "CONSUMOS",
            SUM(X."MONTO_RECARGAS") AS "MONTO_RECARGAS",
            SUM(X."MONTO_CONSUMOS") AS "MONTO_CONSUMOS"
        FROM (
            SELECT
                TO_DATE(R."FECHA") AS "FECHA",
                COUNT(*) AS "RECARGAS",
                0 AS "CONSUMOS",
                COALESCE(SUM(R."MONTO_TOTAL"), 0) AS "MONTO_RECARGAS",
                0 AS "MONTO_CONSUMOS"
            FROM "{SCHEMA}"."RECARGAS_COMEDOR" R
            WHERE {rec_where}
            GROUP BY TO_DATE(R."FECHA")

            UNION ALL

            SELECT
                C."FECHA" AS "FECHA",
                0 AS "RECARGAS",
                COUNT(*) AS "CONSUMOS",
                0 AS "MONTO_RECARGAS",
                COALESCE(SUM(C."COSTO_APLICADO"), 0) AS "MONTO_CONSUMOS"
            FROM "{SCHEMA}"."CONSUMOS_COMEDOR" C
            WHERE {con_where}
            GROUP BY C."FECHA"
        ) X
        GROUP BY X."FECHA"
        ORDER BY X."FECHA"
        """,
        rec_params + con_params,
    )

    return ok_response(rows)


@comedor_bp.route("/reportes/paquetes-recargas", methods=["GET"])
def paquetes_recargas():
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("REPORTES_COMEDOR", "VER")
    if not allowed:
        return response

    conditions, params = get_date_filters('TO_DATE(R."FECHA")')
    where_sql = " AND ".join(['R."ACTIVO" = 1'] + conditions)

    rows = fetch_all(
        f"""
        SELECT
            R."CANTIDAD_COMIDAS" AS "PAQUETE",
            COUNT(*) AS "TOTAL",
            COALESCE(SUM(R."MONTO_TOTAL"), 0) AS "MONTO"
        FROM "{SCHEMA}"."RECARGAS_COMEDOR" R
        WHERE {where_sql}
        GROUP BY R."CANTIDAD_COMIDAS"
        ORDER BY R."CANTIDAD_COMIDAS"
        """,
        params,
    )

    return ok_response(rows)


@comedor_bp.route("/reportes/top-consumos", methods=["GET"])
def top_consumos():
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("REPORTES_COMEDOR", "VER")
    if not allowed:
        return response

    conditions, params = get_date_filters('C."FECHA"')
    where_sql = " AND ".join(['C."ACTIVO" = 1'] + conditions)

    rows = fetch_all(
        f"""
        SELECT TOP 10
            U."USUARIOID",
            U."NOMBRE",
            U."NUMERO_EMPLEADO",
            COALESCE(SUM(C."COMIDAS_DESCONTADAS"), 0) AS "TOTAL_CONSUMOS",
            COUNT(*) AS "TOTAL_MOVIMIENTOS"
        FROM "{SCHEMA}"."CONSUMOS_COMEDOR" C
        INNER JOIN "{SCHEMA}"."USUARIOS" U
            ON U."USUARIOID" = C."USUARIOID"
        WHERE {where_sql}
        GROUP BY U."USUARIOID", U."NOMBRE", U."NUMERO_EMPLEADO"
        ORDER BY COALESCE(SUM(C."COMIDAS_DESCONTADAS"), 0) DESC
        """,
        params,
    )

    return ok_response(rows)


@comedor_bp.route("/reportes/saldos-bajos", methods=["GET"])
def saldos_bajos():
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("REPORTES_COMEDOR", "VER")
    if not allowed:
        return response

    rows = fetch_all(f"""
        SELECT
            U."USUARIOID",
            U."NOMBRE",
            U."NUMERO_EMPLEADO",
            S."COMIDAS_DISPONIBLES",
            S."SALDO"
        FROM "{SCHEMA}"."SALDOS_COMEDOR" S
        INNER JOIN "{SCHEMA}"."USUARIOS" U
            ON U."USUARIOID" = S."USUARIOID"
        WHERE S."ACTIVO" = 1
          AND S."COMIDAS_DISPONIBLES" <= 1
        ORDER BY S."COMIDAS_DISPONIBLES" ASC, U."NOMBRE"
        """)

    return ok_response(rows)


@comedor_bp.route("/reportes/empleados-consumos-dia", methods=["GET"])
def empleados_consumos_dia():
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("REPORTES_COMEDOR", "VER")
    if not allowed:
        return response

    conditions, params = get_date_filters('C."FECHA"')

    # Si NO mandan filtros, muestra solo HOY
    if not conditions:
        where_sql = 'C."ACTIVO" = 1 AND C."FECHA" = CURRENT_DATE'
        params = []
    else:
        where_sql = " AND ".join(['C."ACTIVO" = 1'] + conditions)

    rows = fetch_all(
        f"""
        SELECT
            C."FECHA",
            U."USUARIOID",
            U."NOMBRE",
            U."NUMERO_EMPLEADO",
            COALESCE(SUM(C."COMIDAS_DESCONTADAS"), 0) AS "COMIDAS_CONSUMIDAS",
            COUNT(*) AS "TOTAL_MOVIMIENTOS"
        FROM "{SCHEMA}"."CONSUMOS_COMEDOR" C
        INNER JOIN "{SCHEMA}"."USUARIOS" U
            ON U."USUARIOID" = C."USUARIOID"
        WHERE {where_sql}
        GROUP BY
            C."FECHA",
            U."USUARIOID",
            U."NOMBRE",
            U."NUMERO_EMPLEADO"
        ORDER BY C."FECHA" DESC, U."NOMBRE"
        """,
        params,
    )

    return ok_response(rows)


@comedor_bp.route("/recargas/<int:recarga_id>", methods=["DELETE"])
def delete_recarga(recarga_id):
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("RECARGAS_COMEDOR", "ELIMINAR")
    if not allowed:
        return response

    recarga = fetch_one(
        f"""
        SELECT
            "RECARGAID",
            "USUARIOID",
            "CANTIDAD_COMIDAS",
            "MONTO_TOTAL"
        FROM "{SCHEMA}"."RECARGAS_COMEDOR"
        WHERE "RECARGAID" = ?
          AND "ACTIVO" = 1
        """,
        [recarga_id],
    )

    if not recarga:
        return error_response("Recarga no encontrada o ya fue eliminada", 404)

    saldo = fetch_one(
        f"""
        SELECT
            "COMIDAS_DISPONIBLES",
            "SALDO"
        FROM "{SCHEMA}"."SALDOS_COMEDOR"
        WHERE "USUARIOID" = ?
        """,
        [recarga["USUARIOID"]],
    )

    if not saldo:
        return error_response("Saldo del usuario no encontrado", 404)

    comidas_disponibles = int(saldo.get("COMIDAS_DISPONIBLES") or 0)
    saldo_actual = Decimal(str(saldo.get("SALDO") or "0"))

    comidas_recarga = int(recarga.get("CANTIDAD_COMIDAS") or 0)
    monto_recarga = Decimal(str(recarga.get("MONTO_TOTAL") or "0"))

    if comidas_disponibles < comidas_recarga:
        return error_response(
            "No se puede eliminar esta recarga porque el usuario ya consumió parte de esas comidas.",
            400,
        )

    if saldo_actual < monto_recarga:
        return error_response(
            "No se puede eliminar esta recarga porque el saldo actual no cubre el monto de la recarga.",
            400,
        )

    execute_many(
        [
            (
                f"""
            UPDATE "{SCHEMA}"."SALDOS_COMEDOR"
            SET
                "COMIDAS_DISPONIBLES" = "COMIDAS_DISPONIBLES" - ?,
                "SALDO" = "SALDO" - ?
            WHERE "USUARIOID" = ?
            """,
                [comidas_recarga, monto_recarga, recarga["USUARIOID"]],
            ),
            (
                f"""
            UPDATE "{SCHEMA}"."RECARGAS_COMEDOR"
            SET "ACTIVO" = 0
            WHERE "RECARGAID" = ?
            """,
                [recarga_id],
            ),
        ]
    )

    return ok_response(message="Recarga eliminada correctamente")


def get_user_by_dynamic_qr(qr_value):
    parts = qr_value.split("|")

    if len(parts) != 3:
        return None, "Formato de QR inválido", None

    sistema, tipo, token = parts

    if sistema != "IPL":
        return None, "QR no válido", None

    if tipo == "USR":
        return None, "Este QR es de credencial. Para comedor usa QR dinámico.", None

    if tipo != "USR-DYN":
        return None, "Tipo de QR no válido para comedor", None

    qr = fetch_one(
        f"""
        SELECT "USUARIOID", "TOKEN"
        FROM "{SCHEMA}"."QR_TOKENS_DINAMICOS"
        WHERE "TOKEN" = ?
          AND "ACTIVO" = 1
          AND "USADO" = 0
          AND "FECHA_EXPIRACION" >= CURRENT_TIMESTAMP
        """,
        [token],
    )

    if not qr:
        return None, "QR dinámico inválido o expirado", None

    user = fetch_one(
        f"""
        SELECT
            "USUARIOID",
            "NOMBRE",
            "NUMERO_EMPLEADO",
            "SUCURSAL",
            "TIPO_EMPLEADO",
            "TIPO_EMPLEADO_ID",
            "ACTIVO"
        FROM "{SCHEMA}"."USUARIOS"
        WHERE "USUARIOID" = ?
          AND "ACTIVO" = 1
        """,
        [qr["USUARIOID"]],
    )

    if not user:
        return None, "Usuario no encontrado", None

    return user, None, token


def get_precio_comedor(tipo_empleado_id):
    row = fetch_one(
        f"""
        SELECT
            "PRECIOID",
            "TIPO_EMPLEADO_ID",
            "NOMBRE",
            "TIPO_PROMOCION",
            "PORCENTAJE_DESCUENTO",
            "PRECIO_NORMAL",
            "PRECIO_PROMOCION",
            "PROMOCION_ACTIVA",
            "FECHA_INICIO_PROMO",
            "FECHA_FIN_PROMO"
        FROM "{SCHEMA}"."PRECIOS_COMEDOR"
        WHERE "TIPO_EMPLEADO_ID" = ?
          AND "ACTIVO" = 1
        """,
        [tipo_empleado_id],
    )

    if not row:
        return None

    today = date.today()

    precio_normal = Decimal(str(row["PRECIO_NORMAL"]))
    precio_unitario = precio_normal
    descuento_unitario = Decimal("0.00")

    promo_activa = int(row.get("PROMOCION_ACTIVA") or 0) == 1
    precio_promo = row.get("PRECIO_PROMOCION")
    fecha_inicio = row.get("FECHA_INICIO_PROMO")
    fecha_fin = row.get("FECHA_FIN_PROMO")
    tipo_promocion = row.get("TIPO_PROMOCION") or "PRECIO"
    porcentaje_descuento = row.get("PORCENTAJE_DESCUENTO")

    if promo_activa and fecha_inicio and fecha_fin:
        if fecha_inicio <= today <= fecha_fin:

            if tipo_promocion == "PRECIO" and precio_promo:
                precio_unitario = Decimal(str(precio_promo))

            elif tipo_promocion == "PORCENTAJE" and porcentaje_descuento:
                porcentaje = Decimal(str(porcentaje_descuento))
                descuento = precio_normal * (porcentaje / Decimal("100"))
                precio_unitario = precio_normal - descuento

            descuento_unitario = precio_normal - precio_unitario

    return {
        "PRECIOID": row["PRECIOID"],
        "TIPO_EMPLEADO_ID": row["TIPO_EMPLEADO_ID"],
        "NOMBRE": row["NOMBRE"],
        "PRECIO_NORMAL": precio_normal,
        "PRECIO_UNITARIO": precio_unitario,
        "DESCUENTO_UNITARIO": descuento_unitario,
        "TIPO_PROMOCION": tipo_promocion,
        "PORCENTAJE_DESCUENTO": porcentaje_descuento,
    }


@comedor_bp.route("/precios", methods=["GET"])
def get_precios_comedor():
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("PRECIOS_COMEDOR", "VER")
    if not allowed:
        return response

    rows = fetch_all(f"""
        SELECT
            P."PRECIOID",
            T."TIPO_EMPLEADO_ID",
            T."CLAVE",
            T."NOMBRE",
            P."PRECIO_NORMAL",
            P."PRECIO_X10",
            P."PRECIO_X20",
            P."ACTIVO"
        FROM "{SCHEMA}"."PRECIOS_COMEDOR" P
        INNER JOIN "{SCHEMA}"."TIPOS_EMPLEADO" T
            ON T."TIPO_EMPLEADO_ID" = P."TIPO_EMPLEADO_ID"
        WHERE P."ACTIVO" = 1
        ORDER BY T."TIPO_EMPLEADO_ID"
        """)

    return ok_response(rows, "Precios comedor", 200)


@comedor_bp.route("/precios", methods=["POST"])
def create_precio_comedor():
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("PRECIOS_COMEDOR", "CREAR")
    if not allowed:
        return response

    data = request.get_json(silent=True) or {}

    try:
        tipo_empleado_id = int(data.get("TIPO_EMPLEADO_ID"))
        precio_normal = Decimal(str(data.get("PRECIO_NORMAL")))
        precio_x10 = Decimal(str(data.get("PRECIO_X10") or "0"))
        precio_x20 = Decimal(str(data.get("PRECIO_X20") or "0"))
    except Exception:
        return error_response("TIPO_EMPLEADO_ID y PRECIO_NORMAL son obligatorios", 400)

    tipo_empleado = fetch_one(
        f"""
        SELECT "TIPO_EMPLEADO_ID", "NOMBRE"
        FROM "{SCHEMA}"."TIPOS_EMPLEADO"
        WHERE "TIPO_EMPLEADO_ID" = ?
          AND "ACTIVO" = 1
        """,
        [tipo_empleado_id],
    )

    if not tipo_empleado:
        return error_response("Tipo de empleado no encontrado o inactivo", 404)

    exists = fetch_one(
        f"""
        SELECT "PRECIOID"
        FROM "{SCHEMA}"."PRECIOS_COMEDOR"
        WHERE "TIPO_EMPLEADO_ID" = ?
          AND "ACTIVO" = 1
        """,
        [tipo_empleado_id],
    )

    if exists:
        return error_response(
            "Ya existe un precio activo para este tipo de empleado", 400
        )

    precio_id = get_next_id("PRECIOS_COMEDOR", "PRECIOID")

    execute_many(
        [
            (
                f"""
            INSERT INTO "{SCHEMA}"."PRECIOS_COMEDOR"
            (
                "PRECIOID",
                "TIPO_EMPLEADO_ID",
                "PRECIO_NORMAL",
                "PRECIO_X10",
                "PRECIO_X20",
                "ACTIVO"
            )
            VALUES (?, ?, ?,  ?, ?,  1)
            """,
                [
                    precio_id,
                    tipo_empleado_id,
                    precio_normal,
                    precio_x10,
                    precio_x20,
                ],
            )
        ]
    )

    return ok_response({"PRECIOID": precio_id}, "Precio creado correctamente", 201)


@comedor_bp.route("/precios/<int:precio_id>", methods=["PUT"])
def update_precio_comedor(precio_id):
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("PRECIOS_COMEDOR", "EDITAR")
    if not allowed:
        return response

    data = request.get_json(silent=True) or {}

    try:
        precio_normal = Decimal(str(data.get("PRECIO_NORMAL")))
        precio_x10 = Decimal(str(data.get("PRECIO_X10") or "0"))
        precio_x20 = Decimal(str(data.get("PRECIO_X20") or "0"))
        activo = int(data.get("ACTIVO") if data.get("ACTIVO") is not None else 1)
    except Exception:
        return error_response("PRECIO_NORMAL es obligatorio", 400)

    execute_many(
        [
            (
                f"""
            UPDATE "{SCHEMA}"."PRECIOS_COMEDOR"
            SET
                "PRECIO_NORMAL" = ?,
                "PRECIO_X10" = ?,
                "PRECIO_X20" = ?,
                "ACTIVO" = ?
            WHERE "PRECIOID" = ?
            """,
                [
                    precio_normal,
                    precio_x10,
                    precio_x20,
                    activo,
                    precio_id,
                ],
            )
        ]
    )

    return ok_response({"PRECIOID": precio_id}, "Precio actualizado correctamente", 200)


@comedor_bp.route("/precios/<int:precio_id>", methods=["DELETE"])
def delete_precio_comedor(precio_id):
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("PRECIOS_COMEDOR", "ELIMINAR")
    if not allowed:
        return response

    execute_many(
        [
            (
                f"""
            UPDATE "{SCHEMA}"."PRECIOS_COMEDOR"
            SET "ACTIVO" = 0
            WHERE "PRECIOID" = ?
            """,
                [precio_id],
            )
        ]
    )

    return ok_response({"PRECIOID": precio_id}, "Precio eliminado correctamente", 200)


@comedor_bp.route("/paquetes", methods=["GET"])
def get_paquetes_comedor():
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("PAQUETES_COMEDOR", "VER")
    if not allowed:
        return response

    rows = fetch_all(f"""
        SELECT
            "PAQUETEID",
            "CANTIDAD_COMIDAS",
            "NOMBRE",
            "PORCENTAJE_DESCUENTO",
            "ACTIVO"
        FROM "{SCHEMA}"."PAQUETES_COMEDOR"
        WHERE "ACTIVO" = 1
        ORDER BY "CANTIDAD_COMIDAS"
        """)

    return ok_response(rows, "Paquetes comedor", 200)


@comedor_bp.route("/paquetes", methods=["POST"])
def create_paquete_comedor():
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("PAQUETES_COMEDOR", "CREAR")
    if not allowed:
        return response

    data = request.get_json(silent=True) or {}

    try:
        cantidad = int(data.get("CANTIDAD_COMIDAS"))
        nombre = (data.get("NOMBRE") or "").strip()
        descuento = Decimal(str(data.get("PORCENTAJE_DESCUENTO") or 0))
    except Exception:
        return error_response("Datos inválidos", 400)

    if cantidad <= 0:
        return error_response("La cantidad de comidas debe ser mayor a 0", 400)

    if not nombre:
        nombre = f"{cantidad} comida(s)"

    exists = fetch_one(
        f"""
        SELECT "PAQUETEID"
        FROM "{SCHEMA}"."PAQUETES_COMEDOR"
        WHERE "CANTIDAD_COMIDAS" = ?
          AND "ACTIVO" = 1
        """,
        [cantidad],
    )

    if exists:
        return error_response("Ya existe un paquete activo con esa cantidad", 400)

    paquete_id = get_next_id("PAQUETES_COMEDOR", "PAQUETEID")

    execute_many(
        [
            (
                f"""
            INSERT INTO "{SCHEMA}"."PAQUETES_COMEDOR"
            (
                "PAQUETEID",
                "CANTIDAD_COMIDAS",
                "NOMBRE",
                "PORCENTAJE_DESCUENTO",
                "ACTIVO"
            )
            VALUES (?, ?, ?, ?, 1)
            """,
                [paquete_id, cantidad, nombre, descuento],
            )
        ]
    )

    return ok_response({"PAQUETEID": paquete_id}, "Paquete creado correctamente", 201)


@comedor_bp.route("/paquetes/<int:paquete_id>", methods=["PUT"])
def update_paquete_comedor(paquete_id):
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("PAQUETES_COMEDOR", "EDITAR")
    if not allowed:
        return response

    data = request.get_json(silent=True) or {}

    try:
        cantidad = int(data.get("CANTIDAD_COMIDAS"))
        nombre = (data.get("NOMBRE") or "").strip()
        descuento = Decimal(str(data.get("PORCENTAJE_DESCUENTO") or 0))
        activo = int(data.get("ACTIVO") if data.get("ACTIVO") is not None else 1)
    except Exception:
        return error_response("Datos inválidos", 400)

    if cantidad <= 0:
        return error_response("La cantidad de comidas debe ser mayor a 0", 400)

    if descuento < 0 or descuento > 100:
        return error_response(
            "El porcentaje de descuento debe estar entre 0 y 100", 400
        )

    if not nombre:
        nombre = f"{cantidad} comida(s)"

    exists = fetch_one(
        f"""
        SELECT "PAQUETEID"
        FROM "{SCHEMA}"."PAQUETES_COMEDOR"
        WHERE "CANTIDAD_COMIDAS" = ?
          AND "ACTIVO" = 1
          AND "PAQUETEID" <> ?
        """,
        [cantidad, paquete_id],
    )

    if exists:
        return error_response("Ya existe otro paquete activo con esa cantidad", 400)

    execute_many(
        [
            (
                f"""
            UPDATE "{SCHEMA}"."PAQUETES_COMEDOR"
            SET
                "CANTIDAD_COMIDAS" = ?,
                "NOMBRE" = ?,
                "PORCENTAJE_DESCUENTO" = ?,
                "ACTIVO" = ?
            WHERE "PAQUETEID" = ?
            """,
                [cantidad, nombre, descuento, activo, paquete_id],
            )
        ]
    )

    return ok_response(
        {"PAQUETEID": paquete_id}, "Paquete actualizado correctamente", 200
    )


@comedor_bp.route("/paquetes/<int:paquete_id>", methods=["DELETE"])
def delete_paquete_comedor(paquete_id):
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("PAQUETES_COMEDOR", "ELIMINAR")
    if not allowed:
        return response

    execute_many(
        [
            (
                f"""
            UPDATE "{SCHEMA}"."PAQUETES_COMEDOR"
            SET "ACTIVO" = 0
            WHERE "PAQUETEID" = ?
            """,
                [paquete_id],
            )
        ]
    )

    return ok_response(
        {"PAQUETEID": paquete_id}, "Paquete eliminado correctamente", 200
    )


@comedor_bp.route("/reportes/corte-diario", methods=["GET"])
def get_corte_diario():
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("REPORTES_COMEDOR", "VER")
    if not allowed:
        return response

    fecha = request.args.get("fecha")

    if not fecha:
        fecha = datetime.now().strftime("%Y-%m-%d")

    recargas = fetch_one(
        f"""
        SELECT
            COUNT(*) AS "TOTAL_RECARGAS",
            COALESCE(SUM(CASE WHEN "MONTO_TOTAL" > 0 THEN "MONTO_TOTAL" ELSE 0 END), 0) AS "MONTO_RECARGAS",
            COALESCE(SUM("CANTIDAD_COMIDAS"), 0) AS "COMIDAS_RECARGADAS",
            COALESCE(SUM(CASE WHEN "MONTO_TOTAL" = 0 THEN "CANTIDAD_COMIDAS" ELSE 0 END), 0) AS "COMIDAS_PROMO_RECARGADAS",
            COALESCE(SUM(CASE WHEN "MONTO_TOTAL" > 0 THEN "CANTIDAD_COMIDAS" ELSE 0 END), 0) AS "COMIDAS_PAGADAS_RECARGADAS",
            COUNT(DISTINCT "USUARIOID") AS "USUARIOS_RECARGA"
        FROM "{SCHEMA}"."RECARGAS_COMEDOR"
        WHERE TO_DATE("FECHA") = ?
            AND "ACTIVO" = 1
        """,
        [fecha],
    )

    consumos = fetch_one(
        f"""
        SELECT
            COUNT(*) AS "TOTAL_CONSUMOS",
            COALESCE(SUM("COSTO_APLICADO"), 0) AS "MONTO_CONSUMOS",
            COALESCE(SUM("COMIDAS_DESCONTADAS"), 0) AS "COMIDAS_CONSUMIDAS",
            COUNT(DISTINCT "USUARIOID") AS "USUARIOS_CONSUMO"
        FROM "{SCHEMA}"."CONSUMOS_COMEDOR"
        WHERE "FECHA" = ?
          AND "ACTIVO" = 1
        """,
        [fecha],
    )

    paquete_top = fetch_one(
        f"""
        SELECT TOP 1
            "PAQUETE_COMIDAS",
            COUNT(*) AS "TOTAL"
        FROM "{SCHEMA}"."RECARGAS_COMEDOR"
        WHERE TO_DATE("FECHA") = ?
          AND "ACTIVO" = 1
        GROUP BY "PAQUETE_COMIDAS"
        ORDER BY "TOTAL" DESC
        """,
        [fecha],
    )

    monto_recargas = Decimal(str(recargas.get("MONTO_RECARGAS") or 0))
    monto_consumos = Decimal(str(consumos.get("MONTO_CONSUMOS") or 0))

    diferencia = monto_recargas - monto_consumos

    return ok_response(
        {
            "FECHA": fecha,
            "TOTAL_RECARGAS": recargas.get("TOTAL_RECARGAS", 0),
            "MONTO_RECARGAS": monto_recargas,
            "COMIDAS_RECARGADAS": recargas.get("COMIDAS_RECARGADAS", 0),
            "TOTAL_CONSUMOS": consumos.get("TOTAL_CONSUMOS", 0),
            "MONTO_CONSUMOS": monto_consumos,
            "COMIDAS_CONSUMIDAS": consumos.get("COMIDAS_CONSUMIDAS", 0),
            "COMIDAS_PROMO_RECARGADAS": recargas.get("COMIDAS_PROMO_RECARGADAS", 0),
            "COMIDAS_PAGADAS_RECARGADAS": recargas.get("COMIDAS_PAGADAS_RECARGADAS", 0),
            "DIFERENCIA": diferencia,
            "USUARIOS_ATENDIDOS": int(recargas.get("USUARIOS_RECARGA", 0))
            + int(consumos.get("USUARIOS_CONSUMO", 0)),
            "PAQUETE_TOP": paquete_top.get("PAQUETE_COMIDAS") if paquete_top else None,
        }
    )


@comedor_bp.route("/qr-compartidos", methods=["POST"])
def crear_qr_compartido():
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("CONSUMO_COMEDOR", "VER")
    if not allowed:
        return response

    user_id = session.get("user_id")
    data = request.get_json(silent=True) or {}

    try:
        cantidad = int(data.get("CANTIDAD_COMIDAS") or 1)
    except (TypeError, ValueError):
        return error_response("La cantidad de comidas no es válida", 400)

    observaciones = (data.get("OBSERVACIONES") or "").strip()

    if cantidad <= 0:
        return error_response(
            "La cantidad debe ser mayor a 0",
            400,
        )

    ensure_saldo(user_id)

    user = fetch_one(
        f"""
        SELECT
            "USUARIOID",
            "TIPO_EMPLEADO_ID"
        FROM "{SCHEMA}"."USUARIOS"
        WHERE "USUARIOID" = ?
          AND "ACTIVO" = 1
        """,
        [user_id],
    )

    if not user:
        return error_response(
            "Usuario no encontrado",
            404,
        )

    saldo = fetch_one(
        f"""
        SELECT
            "COMIDAS_DISPONIBLES",
            "COMIDAS_PROMO",
            "SALDO"
        FROM "{SCHEMA}"."SALDOS_COMEDOR"
        WHERE "USUARIOID" = ?
        """,
        [user_id],
    )

    comidas_disponibles = int(saldo.get("COMIDAS_DISPONIBLES") or 0) if saldo else 0

    comidas_promo_actual = int(saldo.get("COMIDAS_PROMO") or 0) if saldo else 0

    # Para compartir solamente se validan las comidas disponibles.
    # El saldo monetario no se consulta ni se compara.
    if comidas_disponibles < cantidad:
        return error_response(
            (
                "No tienes comidas suficientes para compartir. "
                f"Disponibles: {comidas_disponibles}"
            ),
            400,
        )

    # Se conserva la separación entre promocionales y normales
    # para no cambiar la estructura actual del sistema.
    comidas_promo_usable = min(
        comidas_promo_actual,
        comidas_disponibles,
    )

    comidas_promo_descontadas = min(
        comidas_promo_usable,
        cantidad,
    )

    comidas_normales_descontadas = max(
        0,
        cantidad - comidas_promo_descontadas,
    )

    # Ya no se descuenta dinero al compartir.
    monto_descontar = Decimal("0")

    qr_id = get_next_id(
        "COMEDOR_QR_COMPARTIDOS",
        "QRID",
    )

    token = str(uuid.uuid4())
    qr_folio = f"IPL|SHARE|{token}"

    execute_many(
        [
            (
                f"""
                INSERT INTO "{SCHEMA}"."COMEDOR_QR_COMPARTIDOS"
                (
                    "QRID",
                    "USUARIO_ORIGEN_ID",
                    "TOKEN",
                    "CANTIDAD_COMIDAS",
                    "COMIDAS_PROMO_DESCONTADAS",
                    "COMIDAS_NORMALES_DESCONTADAS",
                    "MONTO_DESCONTADO",
                    "TIPO_EMPLEADO_ID",
                    "USADO",
                    "CANCELADO",
                    "FECHA_CREACION",
                    "FECHA_EXPIRACION",
                    "EXPIRADO",
                    "DEVUELTO",
                    "OBSERVACIONES"
                )
                VALUES
                (
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    0,
                    0,
                    CURRENT_TIMESTAMP,
                    TO_TIMESTAMP(
                        TO_VARCHAR(
                            CURRENT_DATE,
                            'YYYY-MM-DD'
                        ) || ' 23:59:59'
                    ),
                    0,
                    0,
                    ?
                )
                """,
                [
                    qr_id,
                    user_id,
                    token,
                    cantidad,
                    comidas_promo_descontadas,
                    comidas_normales_descontadas,
                    monto_descontar,
                    user.get("TIPO_EMPLEADO_ID"),
                    observaciones,
                ],
            ),
            (
                f"""
                UPDATE "{SCHEMA}"."SALDOS_COMEDOR"
                SET
                    "COMIDAS_DISPONIBLES" =
                        "COMIDAS_DISPONIBLES" - ?,

                    "COMIDAS_PROMO" =
                        "COMIDAS_PROMO" - ?

                WHERE "USUARIOID" = ?
                  AND "COMIDAS_DISPONIBLES" >= ?
                  AND "COMIDAS_PROMO" >= ?
                """,
                [
                    cantidad,
                    comidas_promo_descontadas,
                    user_id,
                    cantidad,
                    comidas_promo_descontadas,
                ],
            ),
        ]
    )

    saldo_actualizado = fetch_one(
        f"""
        SELECT
            "COMIDAS_DISPONIBLES",
            "COMIDAS_PROMO",
            "SALDO"
        FROM "{SCHEMA}"."SALDOS_COMEDOR"
        WHERE "USUARIOID" = ?
        """,
        [user_id],
    )

    return ok_response(
        {
            "QRID": qr_id,
            "TOKEN": token,
            "QR_FOLIO": qr_folio,
            "CANTIDAD_COMIDAS": cantidad,
            "COMIDAS_PROMO_DESCONTADAS": comidas_promo_descontadas,
            "COMIDAS_NORMALES_DESCONTADAS": comidas_normales_descontadas,
            "MONTO_DESCONTADO": monto_descontar,
            "COMIDAS_DISPONIBLES": saldo_actualizado.get("COMIDAS_DISPONIBLES"),
            "COMIDAS_PROMO": saldo_actualizado.get("COMIDAS_PROMO"),
            "SALDO": saldo_actualizado.get("SALDO"),
            "FECHA_EXPIRACION": "Hoy 23:59:59",
        },
        "QR compartido generado",
        201,
    )


@comedor_bp.route("/qr-compartidos/mis", methods=["GET"])
def mis_qr_compartidos():
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("CONSUMO_COMEDOR", "VER")
    if not allowed:
        return response

    user_id = session.get("user_id")

    rows = fetch_all(
        f"""
        SELECT
            Q."QRID",
            Q."TOKEN",
            Q."CANTIDAD_COMIDAS",
            Q."USADO",
            Q."CANCELADO",
            Q."FECHA_CREACION",
            Q."FECHA_USO",
            Q."SUCURSAL_USO",
            Q."OBSERVACIONES",
            'IPL|SHARE|' || Q."TOKEN" AS "QR_FOLIO"
        FROM "{SCHEMA}"."COMEDOR_QR_COMPARTIDOS" Q
        WHERE Q."USUARIO_ORIGEN_ID" = ?
        ORDER BY Q."QRID" DESC
        """,
        [user_id],
    )

    return ok_response(rows)


@comedor_bp.route("/qr-compartidos/<int:qr_id>/cancelar", methods=["POST"])
def cancelar_qr_compartido(qr_id):
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("CONSUMO_COMEDOR", "VER")
    if not allowed:
        return response

    user_id = session.get("user_id")

    qr = fetch_one(
        f"""
        SELECT
            "QRID",
            "USUARIO_ORIGEN_ID",
            "CANTIDAD_COMIDAS",
            "COMIDAS_PROMO_DESCONTADAS",
            "COMIDAS_NORMALES_DESCONTADAS",
            "MONTO_DESCONTADO",
            "USADO",
            "CANCELADO",
            "EXPIRADO",
            "DEVUELTO"
        FROM "{SCHEMA}"."COMEDOR_QR_COMPARTIDOS"
        WHERE "QRID" = ?
          AND "USUARIO_ORIGEN_ID" = ?
        """,
        [qr_id, user_id],
    )

    if not qr:
        return error_response(
            "QR compartido no encontrado",
            404,
        )

    if int(qr.get("USADO") or 0) == 1:
        return error_response(
            "No se puede cancelar porque el QR ya fue usado",
            400,
        )

    if int(qr.get("CANCELADO") or 0) == 1:
        return error_response(
            "Este QR ya está cancelado",
            400,
        )

    if int(qr.get("EXPIRADO") or 0) == 1:
        return error_response(
            "No se puede cancelar porque el QR ya expiró",
            400,
        )

    if int(qr.get("DEVUELTO") or 0) == 1:
        return error_response(
            "Las comidas de este QR ya fueron devueltas",
            400,
        )

    cantidad = int(qr.get("CANTIDAD_COMIDAS") or 1)

    promo_regresar = int(qr.get("COMIDAS_PROMO_DESCONTADAS") or 0)

    # Al cancelar solamente se devuelven las comidas.
    # El saldo monetario no se modifica.
    execute_many(
        [
            (
                f"""
                UPDATE "{SCHEMA}"."COMEDOR_QR_COMPARTIDOS"
                SET
                    "CANCELADO" = 1,
                    "DEVUELTO" = 1
                WHERE "QRID" = ?
                  AND "USADO" = 0
                  AND "CANCELADO" = 0
                  AND "DEVUELTO" = 0
                """,
                [qr_id],
            ),
            (
                f"""
                UPDATE "{SCHEMA}"."SALDOS_COMEDOR"
                SET
                    "COMIDAS_DISPONIBLES" =
                        "COMIDAS_DISPONIBLES" + ?,
                    "COMIDAS_PROMO" =
                        "COMIDAS_PROMO" + ?
                WHERE "USUARIOID" = ?
                """,
                [
                    cantidad,
                    promo_regresar,
                    user_id,
                ],
            ),
        ]
    )

    saldo_actualizado = fetch_one(
        f"""
        SELECT
            "COMIDAS_DISPONIBLES",
            "COMIDAS_PROMO",
            "SALDO"
        FROM "{SCHEMA}"."SALDOS_COMEDOR"
        WHERE "USUARIOID" = ?
        """,
        [user_id],
    )

    return ok_response(
        {
            "QRID": qr_id,
            "COMIDAS_DEVUELTAS": cantidad,
            "COMIDAS_DISPONIBLES": (
                saldo_actualizado.get("COMIDAS_DISPONIBLES") if saldo_actualizado else 0
            ),
            "COMIDAS_PROMO": (
                saldo_actualizado.get("COMIDAS_PROMO") if saldo_actualizado else 0
            ),
        },
        "QR compartido cancelado y comidas devueltas",
    )


@comedor_bp.route("/qr-compartidos/publico/<token>", methods=["GET"])
def qr_compartido_publico(token):
    qr = fetch_one(
        f"""
        SELECT
            Q."QRID",
            Q."TOKEN",
            Q."CANTIDAD_COMIDAS",
            Q."USADO",
            Q."CANCELADO",
            Q."FECHA_CREACION",
            Q."FECHA_USO",
            U."NOMBRE" AS "NOMBRE_ORIGEN"
        FROM "{SCHEMA}"."COMEDOR_QR_COMPARTIDOS" Q
        INNER JOIN "{SCHEMA}"."USUARIOS" U
            ON U."USUARIOID" = Q."USUARIO_ORIGEN_ID"
        WHERE Q."TOKEN" = ?
        """,
        [token],
    )

    if not qr:
        return error_response("QR compartido no encontrado", 404)

    estado = "PENDIENTE"

    if int(qr.get("USADO") or 0) == 1:
        estado = "USADO"

    if int(qr.get("CANCELADO") or 0) == 1:
        estado = "CANCELADO"

    qr_folio = f'IPL|SHARE|{qr["TOKEN"]}'

    return ok_response(
        {
            "QRID": qr["QRID"],
            "QR_FOLIO": qr_folio,
            "CANTIDAD_COMIDAS": qr["CANTIDAD_COMIDAS"],
            "NOMBRE_ORIGEN": qr["NOMBRE_ORIGEN"],
            "USADO": qr["USADO"],
            "CANCELADO": qr["CANCELADO"],
            "ESTADO": estado,
            "FECHA_CREACION": qr["FECHA_CREACION"],
            "FECHA_USO": qr["FECHA_USO"],
        }
    )
