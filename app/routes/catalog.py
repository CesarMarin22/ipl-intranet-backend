import os
from flask import Blueprint
from app.db import fetch_all
from app.helpers import ok_response, validate_active_session

catalog_bp = Blueprint("catalog", __name__)
SCHEMA = os.getenv("HANA_SCHEMA", "QRTEST")

# --------------------------------------------------------------------
# Catálogo general de la intranet: nombre + departamento de personas,
# y nombre de departamentos. Pensado para que CUALQUIER módulo futuro
# (RH, Órdenes de Trabajo, SGC, etc.) pueda alimentar selectores tipo
# "elige un departamento" / "asígnale esto a alguien" sin tener que
# reinventar permisos cada vez.
#
# Solo requiere sesión activa (no un permiso de módulo específico),
# porque lo único que expone es nombre/id — el mismo tipo de dato que
# ya es visible en cualquier pantalla (el "Bienvenido, Fulano" del
# encabezado, quién es el jefe de quién, etc.). Nunca se debe agregar
# aquí un dato sensible (correo, teléfono, sueldo, etc.) — para eso
# cada módulo debe seguir haciendo su propio endpoint con su propio
# permiso, como se hizo para SGC_DOCUMENTOS.
# --------------------------------------------------------------------


@catalog_bp.route("/departments", methods=["GET"])
def catalog_departments():
    valid, response = validate_active_session()
    if not valid:
        return response

    rows = fetch_all(f"""
        SELECT "DEPAID", "NOMBRE"
        FROM "{SCHEMA}"."DEPARTAMENTOS"
        WHERE "ACTIVO" = 1
        ORDER BY "NOMBRE"
        """)

    return ok_response(rows)


@catalog_bp.route("/users", methods=["GET"])
def catalog_users():
    valid, response = validate_active_session()
    if not valid:
        return response

    rows = fetch_all(f"""
        SELECT
            "USUARIOID",
            "NOMBRE",
            "DEPAID",
            CASE WHEN "EMAIL" IS NOT NULL AND "EMAIL" <> '' THEN 1 ELSE 0 END AS "HAS_EMAIL"
        FROM "{SCHEMA}"."USUARIOS"
        WHERE "ACTIVO" = 1
        ORDER BY "NOMBRE"
        """)

    return ok_response(rows)


@catalog_bp.route("/profiles", methods=["GET"])
def catalog_profiles():
    """
    Solo id + nombre del perfil (ej. "Calidad", "Administrador"). Lo que
    SÍ es sensible (qué permisos tiene cada perfil) sigue viviendo
    exclusivamente en /api/permissions, protegido con el permiso PERMISOS.
    """
    valid, response = validate_active_session()
    if not valid:
        return response

    rows = fetch_all(f"""
        SELECT "PERFILID", "NOMBRE"
        FROM "{SCHEMA}"."PERFILES"
        WHERE "ACTIVO" = 1
        ORDER BY "NOMBRE"
        """)

    return ok_response(rows)


@catalog_bp.route("/partners", methods=["GET"])
def catalog_partners():
    valid, response = validate_active_session()
    if not valid:
        return response

    rows = fetch_all(f"""
        SELECT "SOCIOID", "NOMBRE"
        FROM "{SCHEMA}"."SOCIOS"
        WHERE "ACTIVO" = 1
        ORDER BY "NOMBRE"
        """)

    return ok_response(rows)


@catalog_bp.route("/employee-types", methods=["GET"])
def catalog_employee_types():
    valid, response = validate_active_session()
    if not valid:
        return response

    rows = fetch_all(f"""
        SELECT "TIPO_EMPLEADO_ID", "CLAVE", "NOMBRE"
        FROM "{SCHEMA}"."TIPOS_EMPLEADO"
        WHERE "ACTIVO" = 1
        ORDER BY "NOMBRE"
        """)

    return ok_response(rows)
