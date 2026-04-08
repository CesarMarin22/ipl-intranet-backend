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
            "USUARIOID",
            "NOMBRE",
            "USUARIO",
            "PERFILID",
            "ACTIVO",
            "SUCURSAL"
        FROM "{SCHEMA}"."USUARIOS"
        WHERE "USUARIOID" = ?
        ''',
        [user_id]
    )


def validate_active_session():
    """
    Devuelve (True, None) si la sesión es válida
    o (False, response) si debe cortar la ejecución.
    """
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


def user_has_permission(permission_code):
    user_id = current_user_id()
    if not user_id:
        return False

    # Permisos por perfil
    profile_perm = fetch_one(
        f'''
        SELECT 1
        FROM "{SCHEMA}"."USUARIOS" U
        INNER JOIN "{SCHEMA}"."PERFILES_PERMISOS" PP
            ON U."PERFILID" = PP."PERFILID"
        INNER JOIN "{SCHEMA}"."PERMISOS" P
            ON PP."PERMISOID" = P."PERMISOID"
        WHERE U."USUARIOID" = ?
          AND P."CODIGO" = ?
          AND P."ACTIVO" = 1
        ''',
        [user_id, permission_code]
    )

    if profile_perm:
        return True

    # Permisos directos por usuario
    user_perm = fetch_one(
        f'''
        SELECT 1
        FROM "{SCHEMA}"."USUARIOS_PERMISOS" UP
        INNER JOIN "{SCHEMA}"."PERMISOS" P
            ON UP."PERMISOID" = P."PERMISOID"
        WHERE UP."USUARIOID" = ?
          AND P."CODIGO" = ?
          AND P."ACTIVO" = 1
        ''',
        [user_id, permission_code]
    )

    return user_perm is not None


def require_permission(permission_code):
    valid, response = validate_active_session()
    if not valid:
        return False, response

    if not user_has_permission(permission_code):
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