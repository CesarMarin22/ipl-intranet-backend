import os
from flask import Blueprint, request, session
from app.db import fetch_one
from app.helpers import ok_response, error_response

auth_bp = Blueprint("auth", __name__)
SCHEMA = os.getenv("HANA_SCHEMA", "QRTEST")


@auth_bp.route("/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}

    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()

    if not username:
        return error_response("El username es requerido", 400)

    if not password:
        return error_response("El password es requerido", 400)

    user = fetch_one(
        f'''
        SELECT
            "USUARIOID",
            "NOMBRE",
            "USUARIO",
            "PWD",
            "PERFILID",
            "ACTIVO",
            "SUCURSAL"
        FROM "{SCHEMA}"."USUARIOS"
        WHERE "USUARIO" = ?
        ''',
        [username]
    )

    if not user:
        return error_response("Usuario o contraseña incorrectos", 401)

    if int(user["ACTIVO"]) != 1:
        return error_response("Usuario inactivo", 403)

    if user["PWD"] != password:
        return error_response("Usuario o contraseña incorrectos", 401)

    session.clear()
    session["logged_in"] = True
    session["user_id"] = user["USUARIOID"]
    session["username"] = user["USUARIO"]
    session["nombre"] = user["NOMBRE"]
    session["perfil_id"] = user["PERFILID"]
    session["sucursal"] = user["SUCURSAL"]

    return ok_response({
        "USUARIOID": user["USUARIOID"],
        "USUARIO": user["USUARIO"],
        "NOMBRE": user["NOMBRE"],
        "PERFILID": user["PERFILID"],
        "SUCURSAL": user["SUCURSAL"]
    }, "Login correcto", 200)


@auth_bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return ok_response(message="Sesión cerrada correctamente")


@auth_bp.route("/me", methods=["GET"])
def me():
    if not session.get("logged_in"):
        return error_response("No autenticado", 401)

    return ok_response({
        "user_id": session.get("user_id"),
        "username": session.get("username"),
        "nombre": session.get("nombre"),
        "perfil_id": session.get("perfil_id"),
        "sucursal": session.get("sucursal"),
    })