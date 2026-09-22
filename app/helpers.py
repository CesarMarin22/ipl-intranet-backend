import os
from flask import jsonify, session
from app.db import fetch_one

SCHEMA = os.getenv("HANA_SCHEMA", "QRTEST")


def ok_response(data=None, message="OK", status=200):
    return jsonify({
        "ok": True,
        "message": message,
        "data": data
    }), status


def error_response(message="Error", status=400):
    return jsonify({
        "ok": False,
        "message": message,
        "data": None
    }), status


def require_login():
    return session.get("logged_in") is True and session.get("user_id") is not None


def current_user_id():
    return session.get("user_id")


def current_user():
    user_id = current_user_id()
    if not user_id:
        return None

    return fetch_one(
        f'''
        SELECT
            U."USUARIOID",
            U."SOCIOID",
            U."PERFILID",
            U."DEPAID",
            U."JEFEID",
            U."NOMBRE",
            U."USUARIO",
            U."PWD",
            U."SUCURSAL",
            U."ACTIVO",
            U."NUMERO_EMPLEADO",
            U."TIPO_EMPLEADO",
            U."TIPO_EMPLEADO_ID"
        FROM "{SCHEMA}"."USUARIOS" U
        WHERE U."USUARIOID" = ?
        ''',
        [user_id]
    )


def validate_active_session():
    if not require_login():
        return False, error_response("No autenticado", 401)

    user = current_user()
    if not user:
        session.clear()
        return False, error_response("Sesión inválida", 401)

    if int(user.get("ACTIVO", 0)) != 1:
        session.clear()
        return False, error_response("Usuario inactivo", 403)

    return True, None


def user_has_permission(module_code: str, action_code: str):
    user_id = current_user_id()
    if not user_id:
        return False

    # 🔥 1. PRIORIDAD: PERMISO POR USUARIO
    user_perm = fetch_one(
        f'''
        SELECT PU."PERMITIDO"
        FROM "{SCHEMA}"."PERMISOS_USUARIO" PU
        INNER JOIN "{SCHEMA}"."MODULOS" M ON PU."MODULOID" = M."MODULOID"
        INNER JOIN "{SCHEMA}"."ACCIONES" A ON PU."ACCIONID" = A."ACCIONID"
        WHERE PU."USUARIOID" = ?
          AND PU."ACTIVO" = 1
          AND UPPER(M."CLAVE") = UPPER(?)
          AND UPPER(A."CLAVE") = UPPER(?)
        ''',
        [user_id, module_code, action_code]
    )

    if user_perm is not None:
        return int(user_perm.get("PERMITIDO", 0)) == 1

    # 🔹 2. SI NO EXISTE → USAR PERFIL
    profile_perm = fetch_one(
        f'''
        SELECT PP."PERMITIDO"
        FROM "{SCHEMA}"."USUARIOS" U
        INNER JOIN "{SCHEMA}"."PERMISOS_PERFIL" PP ON U."PERFILID" = PP."PERFILID"
        INNER JOIN "{SCHEMA}"."MODULOS" M ON PP."MODULOID" = M."MODULOID"
        INNER JOIN "{SCHEMA}"."ACCIONES" A ON PP."ACCIONID" = A."ACCIONID"
        WHERE U."USUARIOID" = ?
          AND PP."ACTIVO" = 1
          AND UPPER(M."CLAVE") = UPPER(?)
          AND UPPER(A."CLAVE") = UPPER(?)
        ''',
        [user_id, module_code, action_code]
    )

    if profile_perm is not None:
        return int(profile_perm.get("PERMITIDO", 0)) == 1

    return False


def require_permission(module_code, action_code):
    valid, response = validate_active_session()
    if not valid:
        return False, response

    if not user_has_permission(module_code, action_code):
        return False, error_response("No autorizado", 403)

    return True, None


def get_next_id(table_name, id_column):
    row = fetch_one(
        f'''
        SELECT COALESCE(MAX("{id_column}"), 0) + 1 AS "NEXT_ID"
        FROM "{SCHEMA}"."{table_name}"
        '''
    )
    return row["NEXT_ID"]