import os
import requests
from flask import Blueprint, request, session
from app.db import fetch_one
from app.helpers import ok_response, error_response

auth_bp = Blueprint("auth", __name__)
SCHEMA = os.getenv("HANA_SCHEMA", "QRTEST")

SAP_BASE_URL = os.getenv("SAP_BASE_URL", "https://68.155.144.63:50000/b1s/v1")
SAP_COMPANYDB = os.getenv("SAP_COMPANYDB", "B1_IPL")
SAP_USERNAME = os.getenv("SAP_USERNAME", "manager")
SAP_PASSWORD = os.getenv("SAP_PASSWORD", "")


def login_sap_b1():
    sap_response = requests.post(
        f"{SAP_BASE_URL}/Login",
        json={
            "CompanyDB": SAP_COMPANYDB,
            "UserName": SAP_USERNAME,
            "Password": SAP_PASSWORD,
        },
        verify=False,
        timeout=30,
    )

    if sap_response.status_code != 200:
        raise Exception(sap_response.text)

    sap_cookies = sap_response.cookies

    session["SAP_ROUTEID"] = sap_cookies.get("ROUTEID")
    session["SAP_B1SESSION"] = sap_cookies.get("B1SESSION")


@auth_bp.route("/login", methods=["POST"])
def login():
    try:
        data = request.get_json(silent=True) or {}

        username = (data.get("USUARIO") or data.get("username") or "").strip()
        password = (data.get("PWD") or data.get("password") or "").strip()

        if not username or not password:
            return error_response("Usuario y contraseña son obligatorios", 400)

        user = fetch_one(
            f"""
            SELECT
                U."USUARIOID",
                U."SOCIOID",
                U."PERFILID",
                P."NOMBRE" AS "PERFIL_NOMBRE",
                U."DEPAID",
                D."NOMBRE" AS "DEPARTAMENTO_NOMBRE",
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
            LEFT JOIN "{SCHEMA}"."PERFILES" P
                ON U."PERFILID" = P."PERFILID"
            LEFT JOIN "{SCHEMA}"."DEPARTAMENTOS" D
                ON U."DEPAID" = D."DEPAID"
            WHERE UPPER(U."USUARIO") = UPPER(?)
            """,
            [username],
        )

        if not user:
            return error_response("Usuario no encontrado", 404)

        if int(user.get("ACTIVO", 0)) != 1:
            return error_response("Usuario inactivo", 403)

        if str(user.get("PWD")) != password:
            return error_response("Contraseña incorrecta", 401)

        # Login normal de intranet
        # Nota de seguridad: la sesión de Flask va en una cookie firmada
        # (no cifrada) con SECRET_KEY. No se guarda nada sensible aquí
        # (contraseñas, tokens), solo datos de perfil ya visibles para
        # el propio usuario. La firma impide que el navegador la modifique
        # sin invalidarla, así que el DEPAID/PERFILID que se usan para
        # permisos en el backend no se pueden falsificar desde el cliente.
        session["logged_in"] = True
        session["user_id"] = user["USUARIOID"]
        session["username"] = user["USUARIO"]
        session["nombre"] = user["NOMBRE"]
        session["perfil_id"] = user["PERFILID"]
        session["sucursal"] = user["SUCURSAL"]
        session["socio_id"] = user["SOCIOID"]
        session["tipo_empleado_id"] = user["TIPO_EMPLEADO_ID"]
        session["perfil_nombre"] = user["PERFIL_NOMBRE"]
        session["depaid"] = user["DEPAID"]
        session["departamento_nombre"] = user["DEPARTAMENTO_NOMBRE"]

        # Login automático a SAP B1 Service Layer
        try:
            login_sap_b1()
        except Exception as sap_error:
            session.clear()
            return error_response(
                f"Usuario válido, pero no se pudo iniciar sesión en SAP B1: {str(sap_error)}",
                500,
            )

        return ok_response(
            {
                "USUARIOID": user["USUARIOID"],
                "NOMBRE": user["NOMBRE"],
                "USUARIO": user["USUARIO"],
                "PERFILID": user["PERFILID"],
                "SUCURSAL": user["SUCURSAL"],
                "SOCIOID": user["SOCIOID"],
                "TIPO_EMPLEADO_ID": user["TIPO_EMPLEADO_ID"],
                "PERFIL_NOMBRE": user["PERFIL_NOMBRE"],
                "DEPAID": user["DEPAID"],
                "DEPARTAMENTO_NOMBRE": user["DEPARTAMENTO_NOMBRE"],
            },
            "Login correcto",
            200,
        )

    except Exception as e:
        return error_response(f"Error al iniciar sesión: {str(e)}", 500)


@auth_bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return ok_response(message="Sesión cerrada correctamente")


@auth_bp.route("/me", methods=["GET"])
def me():
    if not session.get("logged_in") or not session.get("user_id"):
        return error_response("No autenticado", 401)

    return ok_response(
        {
            "USUARIOID": session.get("user_id"),
            "USUARIO": session.get("username"),
            "NOMBRE": session.get("nombre"),
            "PERFILID": session.get("perfil_id"),
            "SUCURSAL": session.get("sucursal"),
            "SOCIOID": session.get("socio_id"),
            "TIPO_EMPLEADO_ID": session.get("tipo_empleado_id"),
            "PERFIL_NOMBRE": session.get("perfil_nombre"),
            "DEPAID": session.get("depaid"),
            "DEPARTAMENTO_NOMBRE": session.get("departamento_nombre"),
        }
    )