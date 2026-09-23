# RECOMPILE_FORCE: 2026-09-23T23:55:00 - Fixed severidades endpoint to return correct labels
import os
import csv
import sqlite3
import unicodedata
from datetime import datetime
from urllib.parse import quote

import requests
from flask import Blueprint, request, session

from app.helpers import ok_response, error_response, validate_active_session, require_permission

ordenes_trabajo_bp = Blueprint("ordenes_trabajo", __name__)

SAP_BASE_URL = os.getenv("SAP_BASE_URL", "https://68.155.144.63:50000/b1s/v1")
SAP_COMPANYDB = os.getenv("SAP_COMPANYDB", "B1_IPL")
SAP_USERNAME = os.getenv("SAP_USERNAME", "manager")
SAP_PASSWORD = os.getenv("SAP_PASSWORD", "")
CSV_OUTPUT_DIR = os.getenv("CSV_OUTPUT_DIR", r"C:\Publish\addonServicioweb")

# SEVERIDAD_INFO from OTA - Severidades por CallType
SEVERIDAD_INFO = {
    "Menor": ("Menor", "#00b050", "#ffffff"),
    "Moderada": ("Moderada", "#ffff00", "#111111"),
    "Critica": ("Crítica", "#ffc000", "#111111"),
    "Fatal": ("Fatal", "#ff0000", "#ffffff"),
    "CriticaO": ("Crítica (Operación)", "#ff0000", "#ffffff"),
    "Alto": ("Alto (Operación)", "#ffc000", "#111111"),
    "ModeradaO": ("Moderada (Operación)", "#ffff00", "#111111"),
    "Bajo": ("Bajo (Operación)", "#00b050", "#ffffff"),
    "CriticaV": ("Crítica (Vehículos)", "#ff0000", "#ffffff"),
    "ModeradaV": ("Moderada (Vehículos)", "#ffc000", "#111111"),
    "MenorV": ("Menor (Vehículos)", "#00b050", "#ffffff"),
}

# Perfil -> (CallType, nombre de problema) - REPLICATED FROM OTA
PERFIL_FILTROS_FLASH = {
    4: {"call_type_id": 24, "nombre_problema": "Seguridad"},  # Perfil SEGURIDAD
    6: {"call_type_id": 28, "nombre_problema": "Operación"},  # Perfil CALIDAD
    7: {"call_type_id": 27, "nombre_problema": "Vehículos"},  # Perfil LEGAL
}


def limpiar_texto(texto: str) -> str:
    if texto is None:
        return ""

    texto = str(texto)

    texto = texto.replace("\r\n", ". ")
    texto = texto.replace("\n", ". ")
    texto = texto.replace("\r", ". ")

    texto = unicodedata.normalize("NFD", texto)
    texto = texto.encode("ascii", "ignore").decode("utf-8")

    texto = texto.replace("ñ", "n").replace("Ñ", "N")
    texto = texto.replace(",", ".")
    texto = texto.replace(";", ".")
    texto = " ".join(texto.split())

    return texto.upper()


def sap_login():
    response = requests.post(
        f"{SAP_BASE_URL}/Login",
        json={
            "CompanyDB": SAP_COMPANYDB,
            "UserName": SAP_USERNAME,
            "Password": SAP_PASSWORD,
        },
        verify=False,
        timeout=30,
    )

    if response.status_code != 200:
        raise Exception(f"Error al iniciar sesión SAP: {response.text}")

    cookies = response.cookies

    session["SAP_ROUTEID"] = cookies.get("ROUTEID")
    session["SAP_B1SESSION"] = cookies.get("B1SESSION")

    return session["SAP_B1SESSION"], session["SAP_ROUTEID"]


def get_sap_headers():
    b1session = session.get("SAP_B1SESSION")
    route_id = session.get("SAP_ROUTEID")

    if not b1session or not route_id:
        b1session, route_id = sap_login()

    return {
        "Cookie": f"B1SESSION={b1session}; ROUTEID={route_id}",
        "Content-Type": "application/json",
    }


def sap_get(path: str):
    headers = get_sap_headers()

    response = requests.get(
        f"{SAP_BASE_URL}/{path}",
        headers=headers,
        verify=False,
        timeout=60,
    )

    if response.status_code in [401, 403]:
        sap_login()
        headers = get_sap_headers()

        response = requests.get(
            f"{SAP_BASE_URL}/{path}",
            headers=headers,
            verify=False,
            timeout=60,
        )

    if response.status_code != 200:
        raise Exception(response.text)

    return response.json()


@ordenes_trabajo_bp.route("/clientes", methods=["GET"])
def buscar_clientes():
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("OT_NORMAL", "VER")
    if not allowed:
        return response

    query = (request.args.get("query") or "").strip().upper()

    if not query:
        return error_response("El parámetro query es obligatorio", 400)

    try:
        data = sap_get(
            "BusinessPartners?"
            f"$filter=(contains(CardCode, '{query}') "
            f"or contains(CardName, '{query}') "
            f"or contains(CardForeignName, '{query}')) "
            f"and CardType eq 'cCustomer'"
        )
        return ok_response(data)
    except Exception as e:
        return error_response(f"Error al consultar clientes SAP: {str(e)}", 500)


@ordenes_trabajo_bp.route("/equipos-cliente", methods=["GET"])
def equipos_cliente():
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("OT_NORMAL", "VER")
    if not allowed:
        return response

    customer_code = (request.args.get("customer_code") or "").strip().upper()
    search_value = (request.args.get("search_value") or "").strip().upper()

    if not customer_code:
        return error_response("customer_code es obligatorio", 400)

    try:
        data = sap_get(
            "$crossjoin(Items, CustomerEquipmentCards, Manufacturers)"
            "?$expand="
            "Items($select=ItemCode,U_Modelo),"
            "CustomerEquipmentCards($select=U_NoEconomico,ItemCode,ManufacturerSerialNum),"
            "Manufacturers($select=ManufacturerName)"
            f"&$filter="
            f"Items/ItemCode eq CustomerEquipmentCards/ItemCode "
            f"and Items/Manufacturer eq Manufacturers/Code "
            f"and CustomerEquipmentCards/CustomerCode eq '{customer_code}' "
            f"and CustomerEquipmentCards/StatusOfSerialNumber eq 'A' "
            f"and ("
            f"contains(CustomerEquipmentCards/ManufacturerSerialNum,'{search_value}') "
            f"or contains(CustomerEquipmentCards/U_NoEconomico,'{search_value}') "
            f"or contains(Items/U_Modelo,'{search_value}')"
            f")"
        )

        return ok_response(data)

    except Exception as e:
        return error_response(
            f"Error al consultar equipos SAP: {str(e)}",
            500,
        )


@ordenes_trabajo_bp.route("/items", methods=["GET"])
def buscar_items():
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("OT_NORMAL", "VER")
    if not allowed:
        return response

    query = (request.args.get("query") or "").strip().upper()
    group_code = (request.args.get("groupCode") or "538").strip()

    if not query:
        return error_response("query es obligatorio", 400)

    try:
        data = sap_get(
            f"Items?$select=ItemCode,ItemName"
            f"&$filter=startswith(ItemCode, '{query}') and ItemsGroupCode eq {group_code}"
        )
        return ok_response(data)
    except Exception as e:
        return error_response(f"Error al consultar items SAP: {str(e)}", 500)


@ordenes_trabajo_bp.route("/empleados", methods=["GET"])
def buscar_empleados():
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("OT_NORMAL", "VER")
    if not allowed:
        return response

    query = (request.args.get("query") or "").strip().upper()
    todos = str(request.args.get("todos") or "0") == "1"

    if not query:
        return error_response("query es obligatorio", 400)

    filtro_rol = "" if todos else "and (JobTitle eq 'TECNICO' )"

    try:
        data = sap_get(
            "EmployeesInfo?"
            "$select=FirstName,LastName,MiddleName,EmployeeID,Active,EmployeeRolesInfoLines"
            f"&$filter=(Active eq 'tYES') {filtro_rol} "
            f"and (contains(LastName, '{query}') "
            f"or contains(FirstName, '{query}') "
            f"or contains(MiddleName, '{query}'))"
        )

        empleados = data.get("value", [])

        for empleado in empleados:
            empleado["FullName"] = " ".join(
                part
                for part in [
                    empleado.get("LastName"),
                    empleado.get("FirstName"),
                    empleado.get("MiddleName"),
                ]
                if part
            )
            roles = empleado.get("EmployeeRolesInfoLines", [])
            empleado["RoleID"] = roles[0]["RoleID"] if roles else None

        return ok_response({"value": empleados})
    except Exception as e:
        return error_response(f"Error al consultar empleados SAP: {str(e)}", 500)


@ordenes_trabajo_bp.route("/cssrs", methods=["GET"])
def buscar_cssrs():
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("OT_NORMAL", "VER")
    if not allowed:
        return response

    query = (request.args.get("query") or "").strip().upper()

    if not query:
        return error_response("query es obligatorio", 400)

    try:
        data = sap_get(
            "EmployeesInfo?"
            "$select=FirstName,LastName,MiddleName,EmployeeID"
            f"&$filter=Active eq 'tYES' and JobTitle eq 'CSSR' "
            f"and (contains(FirstName, '{query}') "
            f"or contains(LastName, '{query}') "
            f"or contains(MiddleName, '{query}'))"
        )

        cssrs = data.get("value", [])

        for cssr in cssrs:
            cssr["FullName"] = " ".join(
                part
                for part in [
                    cssr.get("LastName"),
                    cssr.get("FirstName"),
                    cssr.get("MiddleName"),
                ]
                if part
            )

        return ok_response({"value": cssrs})
    except Exception as e:
        return error_response(f"Error al consultar CSSR SAP: {str(e)}", 500)


@ordenes_trabajo_bp.route("/flash-reports", methods=["GET"])
def listar_flash_reports():
    """Lista todas las OT de seguridad (Flash Reports) del usuario"""
    # WRITE DEBUG LOG
    with open(r"C:\websites\IPL_API\logs\flash_reports_debug.log", "a", encoding="utf-8") as f:
        f.write(f"\n=== {datetime.now().isoformat()} ===\n")

    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("OT_SEGURIDAD", "VER")
    if not allowed:
        return response

    username = session.get("username", "")
    perfil = session.get("perfil_id")  # FIXED: Changed from "perfil" to "perfil_id"
    page = int(request.args.get("page", 1))
    per_page = 999

    # DEBUG: escribir en archivo de log
    with open(r"C:\websites\IPL_API\logs\flash_reports_debug.log", "a", encoding="utf-8") as f:
        f.write(f"INICIO: perfil_id={perfil}, username={username}, page={page}, per_page={per_page}\n")

    try:
        skip = (page - 1) * per_page
        top = per_page

        # Flash Reports se identifican por U_Severidad ne null
        # REPLICATING OTA LOGIC:
        # - Admin (1): Ve TODOS los Flash Reports
        # - Seguridad (4): Ve CallType=24
        # - Calidad (6): Ve CallType=28
        # - Legal (7): Ve CallType=27
        if perfil == 1:  # Admin - ve todos
            filtro = "U_Severidad ne null"
        elif perfil in PERFIL_FILTROS_FLASH:
            call_type_id = PERFIL_FILTROS_FLASH[perfil]['call_type_id']
            filtro = f"U_Severidad ne null and CallType eq {call_type_id}"
        else:
            # Si no tiene permiso de Flash Report, retornar vacío
            return ok_response({
                "llamadas": [],
                "page": page,
                "per_page": per_page,
                "total_registros": 0,
                "total_paginas": 1,
            })

        # Construir URL sin incluir $filter si está vacío
        url = "ServiceCalls?"
        if filtro:
            filtro_codificado = quote(filtro)
            url += f"$filter={filtro_codificado}&"

        url += (
            f"$orderby=AssignedDate desc"
            f"&$skip={skip}&$top={top}"
            f"&$select=DocNum,CustomerRefNo,CustomerName,ManufacturerSerialNum,AssignedDate,Series,U_Severidad,U_CreateUser"
            f"&$inlinecount=allpages"
        )

        with open(r"C:\websites\IPL_API\logs\flash_reports_debug.log", "a", encoding="utf-8") as f:
            f.write(f"SAP URL: {url}\n")

        data = sap_get(url)

        with open(r"C:\websites\IPL_API\logs\flash_reports_debug.log", "a", encoding="utf-8") as f:
            total = data.get('odata.count', 0) if data else "NO DATA"
            items = len(data.get('value', [])) if data and 'value' in data else "NO VALUE"
            f.write(f"SAP RESPONSE: total={total}, items={items}, data_keys={list(data.keys()) if data else 'None'}\n")

        total_registros = int(data.get("odata.count", 0))
        llamadas = data.get("value", [])

        # Formatear fechas
        for llamada in llamadas:
            fecha_iso = llamada.get("AssignedDate", "")
            try:
                fecha_obj = datetime.strptime(fecha_iso, "%Y-%m-%dT%H:%M:%SZ")
                llamada["FechaFormateada"] = fecha_obj.strftime("%d/%m/%Y")
            except Exception:
                llamada["FechaFormateada"] = fecha_iso

        total_paginas = ((total_registros + per_page - 1) // per_page) if total_registros else 1

        with open(r"C:\websites\IPL_API\logs\flash_reports_debug.log", "a", encoding="utf-8") as f:
            f.write(f"RETURNING: total_registros={total_registros}, llamadas_count={len(llamadas)}, total_paginas={total_paginas}\n")

        return ok_response(
            {
                "llamadas": llamadas,
                "page": page,
                "per_page": per_page,
                "total_registros": total_registros,
                "total_paginas": total_paginas,
                "CODIGO_ACTUALIZADO": "ARCHIVO CAMBIO CORRECTAMENTE - " + str(datetime.now()),
            }
        )

    except Exception as e:
        with open(r"C:\websites\IPL_API\logs\flash_reports_debug.log", "a", encoding="utf-8") as f:
            f.write(f"ERROR: {type(e).__name__}: {str(e)}\n")
            import traceback
            f.write(traceback.format_exc() + "\n")

        return ok_response({
            "llamadas": [],
            "page": page,
            "per_page": per_page,
            "total_registros": 0,
            "total_paginas": 1,
            "_debug_error": str(e),
            "_debug_error_type": type(e).__name__,
        })


@ordenes_trabajo_bp.route("/audi", methods=["GET"])
def listar_ot_audi():
    """Lista OT de Audi (Series = 374)"""
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("OT_AUDI", "VER")
    if not allowed:
        return response

    username = session.get("username", "")
    perfil = session.get("perfil_id")  # FIXED: Changed from "perfil" to "perfil_id"
    page = int(request.args.get("page", 1))
    per_page = 10

    try:
        skip = (page - 1) * per_page
        top = per_page

        # Admin (perfil_id 1): Ve TODAS las OT Audi
        # Otros: Solo las que creó
        if perfil == 1:
            filtro = "Series eq 374"
        else:
            filtro = f"Series eq 374 and U_CreateUser eq '{username}'"

        filtro_codificado = quote(filtro)

        data = sap_get(
            f"ServiceCalls?"
            f"$filter={filtro_codificado}"
            f"&$orderby=AssignedDate desc"
            f"&$skip={skip}&$top={top}"
            f"&$inlinecount=allpages"
        )

        total_registros = int(data.get("odata.count", 0))
        ordenes = data.get("value", [])

        # Formatear fechas
        for orden in ordenes:
            fecha_iso = orden.get("AssignedDate", "")
            try:
                fecha_obj = datetime.strptime(fecha_iso, "%Y-%m-%dT%H:%M:%SZ")
                orden["FechaFormateada"] = fecha_obj.strftime("%d/%m/%Y")
            except Exception:
                orden["FechaFormateada"] = fecha_iso

        total_paginas = ((total_registros + per_page - 1) // per_page) if total_registros else 1

        return ok_response(
            {
                "ordenes": ordenes,
                "page": page,
                "per_page": per_page,
                "total_registros": total_registros,
                "total_paginas": total_paginas,
            }
        )

    except Exception as e:
        return error_response(f"Error al listar OT Audi: {str(e)}", 500)


@ordenes_trabajo_bp.route("/normal", methods=["GET"])
def listar_ot_normal():
    """Lista OT Normal del usuario actual (o todas si es Admin)"""
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("OT_NORMAL", "VER")
    if not allowed:
        return response

    username = session.get("username", "")
    perfil = session.get("perfil_id")  # FIXED: Changed from "perfil" to "perfil_id"
    page = int(request.args.get("page", 1))
    per_page = 10

    try:
        skip = (page - 1) * per_page
        top = per_page

        # Lógica de OTA: OT Normal = Series ne 374 (excluir Audi) y U_Severidad eq null (excluir Flash Reports)
        # Admin ve todas sus OT + Flash Reports (en endpoint separado)
        # Otros ven solo sus OT creadas
        if perfil == 1:
            filtro = "Series ne 374 and U_Severidad eq null"
        else:
            filtro = f"Series ne 374 and U_Severidad eq null and U_CreateUser eq '{username}'"

        filtro_codificado = quote(filtro)

        data = sap_get(
            f"ServiceCalls?"
            f"$filter={filtro_codificado}"
            f"&$orderby=AssignedDate desc"
            f"&$skip={skip}&$top={top}"
            f"&$select=DocNum,CustomerRefNo,CustomerName,ManufacturerSerialNum,AssignedDate,Series,U_Severidad,U_CreateUser"
            f"&$inlinecount=allpages"
        )

        total_registros = int(data.get("odata.count", 0))
        ordenes = data.get("value", [])

        # Formatear fechas
        for orden in ordenes:
            fecha_iso = orden.get("AssignedDate", "")
            try:
                fecha_obj = datetime.strptime(fecha_iso, "%Y-%m-%dT%H:%M:%SZ")
                orden["FechaFormateada"] = fecha_obj.strftime("%d/%m/%Y")
            except Exception:
                orden["FechaFormateada"] = fecha_iso

        total_paginas = ((total_registros + per_page - 1) // per_page) if total_registros else 1

        return ok_response(
            {
                "ordenes": ordenes,
                "page": page,
                "per_page": per_page,
                "total_registros": total_registros,
                "total_paginas": total_paginas,
            }
        )

    except Exception as e:
        return error_response(f"Error al listar OT Normal: {str(e)}", 500)


@ordenes_trabajo_bp.route("/tipos-problema", methods=["GET"])
def tipos_problema():
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("OT_NORMAL", "VER")
    if not allowed:
        return response

    # HARDCODED FROM OTA - REPLICATED EXACTLY AS IN OTA
    # These match the Names shown in OTA's frontend for "Relación del Suceso"
    tipos_fijos = [
        {"ProblemTypeID": 30, "Name": "SEGURIDAD"},
        {"ProblemTypeID": 202, "Name": "OPERACIÓN"},
        {"ProblemTypeID": 203, "Name": "VEHÍCULOS"},
    ]

    return ok_response({"value": tipos_fijos})

@ordenes_trabajo_bp.route("/severidades", methods=["GET"])
def obtener_severidades():
    """Retorna las opciones de severidad según el CallType"""
    valid, response = validate_active_session()
    if not valid:
        return response

    call_type = request.args.get("call_type", "24")  # Default: Seguridad

    # REPLICATED EXACTLY FROM OTA script.js - cargarSeveridades()
    opciones = []

    if call_type == "24":  # Seguridad
        opciones = [
            {"value": "Fatal", "text": "Fatal (fatalidad o incapacidad permanente o total)", "color": "#ff0000", "textColor": "white"},
            {"value": "Critica", "text": "Crítica (daños materiales graves e incapacidad de más de 3 días)", "color": "#ffc000", "textColor": "black"},
            {"value": "Moderada", "text": "Moderada (daños materiales leves e incapacidad)", "color": "#ffff00", "textColor": "black"},
            {"value": "Menor", "text": "Menor (sin daños materiales ni incapacidad)", "color": "#00b050", "textColor": "white"},
        ]
    elif call_type == "28":  # Operación (Calidad)
        opciones = [
            {"value": "CriticaO", "text": "Alto (Detiene la operación o provoca pérdidas económicas importantes, pérdida de clientes o incumplimientos críticos.)", "color": "#ff0000", "textColor": "white"},
            {"value": "Alto", "text": "Medio (Afecta significativamente la operación, genera retrasos importantes o incumplimiento de requisitos del cliente.)", "color": "#ffc000", "textColor": "black"},
            {"value": "ModeradaO", "text": "Bajo (Genera retrasos o afectaciones parciales en la operación, sin detener el proceso.)", "color": "#ffff00", "textColor": "black"},
        ]
    elif call_type == "27":  # Vehículos (Legal)
        opciones = [
            {"value": "CriticaV", "text": "Crítica (Pérdida Total)", "color": "#ff0000", "textColor": "white"},
            {"value": "ModeradaV", "text": "Moderada (Necesita aseguradora)", "color": "#ffc000", "textColor": "black"},
        ]

    return ok_response({"opciones": opciones})

@ordenes_trabajo_bp.route("/guardar-csv", methods=["POST"])
def guardar_csv():
    valid, response = validate_active_session()
    if not valid:
        return response

    datos = request.get_json(silent=True) or {}

    tipo = datos.get("data-tipo", "") or datos.get("tipo", "")

    if tipo == "seguridad":
        module_name = "OT_SEGURIDAD"
        # Flash Report: solo perfil_id 1 (Admin) o 4 (Seguridad) pueden crear
        perfil = session.get("perfil_id")  # FIXED: Changed from "perfil" to "perfil_id"
        if perfil not in [1, 4]:
            return error_response("Solo Seguridad (perfil 4) puede crear Flash Reports", 403)
    elif tipo == "audi":
        module_name = "OT_AUDI"
    else:
        module_name = "OT_NORMAL"

    allowed, response = require_permission(module_name, "CREAR")
    if not allowed:
        return response

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    tipo = datos.get("data-tipo", "") or datos.get("tipo", "")

    if tipo == "seguridad":
        prefix = "flash_report"
    elif tipo == "audi":
        prefix = "audi_ot"
    else:
        prefix = "ordenes_trabajo"

    os.makedirs(CSV_OUTPUT_DIR, exist_ok=True)
    csv_path = os.path.join(CSV_OUTPUT_DIR, f"{prefix}_{timestamp}.csv")

    encabezados_1 = [
        "ServiceCallID", "subject", "CustomerCode", "callType", "ProblemType", "AssigneeCode",
        "CreationDate", "CreationTime", "TechnicianCode", "resolution", "Series", "StartDate",
        "StartTime", "EndDueDate", "EndTime", "CustomerRefNo", "ItemCode", "ManufacturerSerialNum",
        "U_Horometro", "U_HoraInicio", "U_HoraFin", "ProblemSubType", "U_PersonWhoReports",
        "U_Tecnico2", "U_Tecnico3", "U_Tecnico4", "U_CreateUser", "U_EquipoFunciona",
        "U_TipoRefacciones", "U_Qty1", "U_Code1", "U_Qty2", "U_Code2", "U_Qty3", "U_Code3",
        "U_Qty4", "U_Code4", "U_Qty5", "U_Code5", "U_Qty6", "U_Code6", "U_Qty7", "U_Code7",
        "U_Qty8", "U_Code8", "U_Qty9", "U_Code9", "U_Qty10", "U_Code10", "U_Qty11", "U_Code11",
        "U_Qty12", "U_Code12", "U_Qty13", "U_Code13", "U_Qty14", "U_Code14", "U_Qty15", "U_Code15",
        "U_Qty16", "U_Code16", "U_Qty17", "U_Code17", "U_Qty18", "U_Code18", "U_Qty19", "U_Code19",
        "U_Qty20", "U_Code20", "U_Version", "U_CSSR", "U_Severidad", "U_AreaTrabajo", "U_AccionesR",
        "U_Plan", "U_Leccion", "U_Costo", "U_Supervisor", "U_A_Orden", "U_A_NumTec", "U_A_Horas",
        "U_NoOT", "U_A_TipoOT", "U_A_Causa", "U_A_TipoDano"
    ]

    encabezados_2 = [
        "Call ID", "Subject", "Business Partner Code", "Call Type", "Problem Type", "Handled By",
        "Creation Date", "Creation Time", "Technician", "Resolution", "Series", "Start Date",
        "Start Time", "End Date", "End Time", "Business Partner Ref. No.", "itemCode", "manufSN",
        "U_Horometro", "U_HoraInicio", "U_HoraFin", "ProSubType", "U_PersonWhoReports", "U_Tecnico2",
        "U_Tecnico3", "U_Tecnico4", "U_CreateUser", "U_EquipoFunciona", "U_TipoRefacciones",
        "U_Qty1", "U_Code1", "U_Qty2", "U_Code2", "U_Qty3", "U_Code3", "U_Qty4", "U_Code4",
        "U_Qty5", "U_Code5", "U_Qty6", "U_Code6", "U_Qty7", "U_Code7", "U_Qty8", "U_Code8",
        "U_Qty9", "U_Code9", "U_Qty10", "U_Code10", "U_Qty11", "U_Code11", "U_Qty12", "U_Code12",
        "U_Qty13", "U_Code13", "U_Qty14", "U_Code14", "U_Qty15", "U_Code15",
        "U_Qty16", "U_Code16", "U_Qty17", "U_Code17", "U_Qty18", "U_Code18", "U_Qty19", "U_Code19",
        "U_Qty20", "U_Code20", "U_Version", "U_CSSR", "U_Severidad", "U_AreaTrabajo", "U_AccionesR",
        "U_Plan", "U_Leccion", "U_Costo", "U_Supervisor", "U_A_Orden", "U_A_NumTec", "U_A_Horas",
        "U_NoOT", "U_A_TipoOT", "U_A_Causa", "U_A_TipoDano"
    ]

    try:
        refacciones = datos.pop("refacciones", [])
        tipo_refacciones = str(datos.get("tipoRefacciones", "1"))

        refacciones_planas = []

        for i in range(20):
            if i < len(refacciones):
                ref = refacciones[i]
                refacciones_planas.append(ref.get("cantidad", ""))
                refacciones_planas.append(ref.get("numeroParte", ""))
            else:
                refacciones_planas.append("")
                refacciones_planas.append("")

        if tipo_refacciones == "0":
            refacciones_instaladas = [""] * 20
            refacciones_requeridas = refacciones_planas[:20]
        elif tipo_refacciones == "1":
            refacciones_instaladas = refacciones_planas[:20]
            refacciones_requeridas = [""] * 20
        else:
            refacciones_instaladas = refacciones_planas[:20]
            refacciones_requeridas = refacciones_planas[20:]

        fila_datos = [
            1,
            limpiar_texto(datos.get("descripcionFalla", "")),
            datos.get("codigoCliente", ""),
            datos.get("tipoOrden", "") or datos.get("callType", ""),
            limpiar_texto(datos.get("tipoProblema", "")),
            "1",
            datos.get("fechaInicio", "").replace("/", ""),
            datos.get("horaInicioTrabajo", "").replace(":", ""),
            datos.get("realizoTrabajoEmployeeID", ""),
            limpiar_texto(datos.get("trabajoRealizado", "")),
            datos.get("serie", ""),
            datos.get("fechaInicio", "").replace("/", ""),
            datos.get("horaInicioTrabajo", "").replace(":", ""),
            datos.get("fechaTermino", "").replace("/", ""),
            datos.get("horaSalida", "").replace(":", ""),
            datos.get("folio", ""),
            datos.get("itemCode", ""),
            datos.get("noSerie", ""),
            datos.get("horometro", ""),
            datos.get("horaInicioTrabajo", "").replace(":", ""),
            datos.get("horaSalida", "").replace(":", ""),
            datos.get("ProSubType", ""),
            limpiar_texto(datos.get("personaReporta", "")),
            datos.get("revisoTrabajo", ""),
            datos.get("tecnico3", ""),
            datos.get("tecnico4", ""),
            session.get("username", ""),
            datos.get("equipoFuncionamiento", ""),
            tipo_refacciones,
        ] + refacciones_instaladas + refacciones_requeridas + [
            "1",
            datos.get("nombreCssr", ""),
            datos.get("U_Severidad", ""),
            limpiar_texto(datos.get("areaTrabajo", "")),
            limpiar_texto(datos.get("accionesSituacion", "")),
            limpiar_texto(datos.get("planAccion", "")),
            limpiar_texto(datos.get("leccionesAprendidas", "")),
            datos.get("costoAproximado", ""),
            datos.get("vistoBuenoCliente", ""),
            datos.get("U_A_Orden", ""),
            datos.get("NumPersonas", ""),
            datos.get("horasTrabajadas", ""),
            datos.get("otBase", ""),
            datos.get("ordenBase", " "),
            datos.get("causa", " "),
            datos.get("tipoDanio", " "),
        ]

        with open(csv_path, mode="w", newline="", encoding="utf-8") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(encabezados_1)
            writer.writerow(encabezados_2)
            writer.writerow(fila_datos)

        return ok_response(
            {
                "archivo": os.path.basename(csv_path),
                "ruta": csv_path,
                "tipo": tipo or "ot",
            },
            "CSV generado correctamente",
            201,
        )

    except Exception as e:
        return error_response(f"Error al generar CSV: {str(e)}", 500)
    
SUCURSALES_SAP = {
    87: "AGS",
    82: "CLY",
    88: "GDL",
    89: "IRA",
    83: "MEX",
    85: "MTY",
    86: "QRO",
    90: "SLP",
    84: "TOL",
    374: "PUE",
}

# Severidad para Flash Reports (OT Seguridad)
SEVERIDAD_INFO = {
    "Menor": ("#00b050", "#ffffff"),
    "Moderada": ("#ffff00", "#111111"),
    "Critica": ("#ffc000", "#111111"),
    "Fatal": ("#ff0000", "#ffffff"),
    "CriticaO": ("#ff0000", "#ffffff"),
    "Alto": ("#ffc000", "#111111"),
    "ModeradaO": ("#ffff00", "#111111"),
    "Bajo": ("#00b050", "#ffffff"),
    "CriticaV": ("#ff0000", "#ffffff"),
    "ModeradaV": ("#ffc000", "#111111"),
    "MenorV": ("#00b050", "#ffffff"),
}

# Base de datos de seguimiento para Flash Reports
SEGUIMIENTO_DB_PATH = os.getenv(
    "SEGUIMIENTO_DB_PATH", r"C:\Publish\addonServicioweb\seguimiento_flash.db"
)


def obtener_nombre_sucursal(series_id):
    if not series_id:
        return "N/A"

    try:
        return SUCURSALES_SAP.get(int(series_id), f"Serie {series_id}")
    except Exception:
        return f"Serie {series_id}"


def obtener_nombre_tipo_problema(problem_type_id):
    """Obtiene el nombre del tipo de problema desde SAP"""
    if not problem_type_id:
        return "N/A"

    try:
        data = sap_get(
            f"ServiceCallProblemTypes?$filter=ProblemTypeID eq {problem_type_id}"
        )
        rows = data.get("value", [])
        if rows:
            return rows[0].get("Name")
    except Exception:
        pass

    return f"Problema {problem_type_id}"


def perfil_puede_dar_seguimiento(perfil, call_type_id):
    """Valida si el perfil del usuario puede dar seguimiento a Flash Reports"""
    PERFIL_FILTROS_FLASH = {
        4: {"call_type_id": 24},  # Seguridad
        6: {"call_type_id": 28},  # Calidad
        7: {"call_type_id": 27},  # Legal
    }
    if perfil == 1:  # Admin puede todo
        return True
    info = PERFIL_FILTROS_FLASH.get(perfil)
    return bool(info) and info.get("call_type_id") == call_type_id


def obtener_conexion_seguimiento():
    """Obtiene conexión a la base de datos de seguimiento de Flash Reports"""
    os.makedirs(os.path.dirname(SEGUIMIENTO_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(SEGUIMIENTO_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS seguimiento (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            docnum INTEGER NOT NULL,
            estatus TEXT NOT NULL,
            responsable TEXT,
            fecha_compromiso TEXT,
            comentario TEXT,
            creado_por TEXT,
            creado_en TEXT NOT NULL
        )
    """)
    return conn


def obtener_nombre_tipo_orden(call_type_id):
    if call_type_id is None:
        return "N/A"

    data = sap_get("ServiceCallTypes")
    tipos = data.get("value", [])

    for tipo in tipos:
        if tipo.get("CallTypeID") == call_type_id:
            return tipo.get("Name")

    return f"Tipo {call_type_id}"

def safe_text(value):
    return str(value or "").strip()


def obtener_nombre_empleado(employee_id):
    if not employee_id:
        return ""

    try:
        data = sap_get(
            f"EmployeesInfo?$filter=EmployeeID eq {employee_id}"
        )

        empleados = data.get("value", [])

        if not empleados:
            return f"ID {employee_id}"

        emp = empleados[0]

        nombre = " ".join(
            part
            for part in [
                safe_text(emp.get("FirstName")),
                safe_text(emp.get("LastName")),
                safe_text(emp.get("MiddleName")),
            ]
            if part
        )

        return nombre or f"ID {employee_id}"

    except Exception:
        return f"ID {employee_id}"

def obtener_item_info_sheet(item_code):
    if not item_code:
        return {
            "ItemName": "",
            "ManufacturerName": "",
            "U_Modelo": "",
        }

    try:
        item = sap_get(
            f"Items('{item_code}')?$select=ItemCode,ItemName,Manufacturer,U_Modelo"
        )

        manufacturer_name = ""
        manufacturer_code = item.get("Manufacturer")

        if manufacturer_code not in [None, ""]:
            try:
                manufacturer = sap_get(
                    f"Manufacturers({manufacturer_code})?$select=Code,ManufacturerName"
                )
                manufacturer_name = manufacturer.get("ManufacturerName", "")
            except Exception:
                manufacturer_name = ""

        return {
            "ItemName": item.get("ItemName", ""),
            "ManufacturerName": manufacturer_name,
            "U_Modelo": item.get("U_Modelo", ""),
        }

    except Exception:
        return {
            "ItemName": "",
            "ManufacturerName": "",
            "U_Modelo": "",
        }


def formatear_fecha_sheet(fecha):
    if not fecha:
        return ""

    fecha = str(fecha).strip()

    if len(fecha) == 8 and fecha.isdigit():
        return f"{fecha[6:8]}/{fecha[4:6]}/{fecha[0:4]}"

    return fecha


@ordenes_trabajo_bp.route("/sheet/generar-filas", methods=["POST"])
def generar_filas_sheet():
    api_key = request.headers.get("X-API-KEY")

    if api_key != os.getenv("SHEET_API_KEY", "ipl_sheet_2026"):
        return error_response("No autorizado", 401)

    row = request.get_json(silent=True) or {}

    try:
        series = str(row.get("Series", "")).strip()

        if series not in ["82", "86", "89"]:
            return ok_response({
                "insertar": False,
                "rows": []
            })

        # ============================================
        # SOLO REFACCIONES REQUERIDAS
        # U_TipoRefacciones:
        # 0 = requeridas
        # 1 = instaladas
        # ============================================

        technician_code = row.get("TechnicianCode", "")
        solicitante = obtener_nombre_empleado(technician_code)

        revs = row.get("U_CSSR", "")
        customer_code = row.get("CustomerCode", "")
        cliente = row.get("CustomerName", "")

        if not cliente and customer_code:
            try:
                bp = sap_get(
                    f"BusinessPartners('{customer_code}')?$select=CardName"
                )
                cliente = bp.get("CardName", "")
            except Exception:
                cliente = customer_code

        fecha_req = formatear_fecha_sheet(row.get("StartDate", ""))
        folio_ot = row.get("CustomerRefNo", "")
        serie_equipo = row.get("ManufacturerSerialNum", "")
        horometro = row.get("U_Horometro", "")
        accion = row.get("resolution", "") or row.get("Resolution", "")

        item_code = row.get("ItemCode", "")
        equipo_info = obtener_item_info_sheet(item_code)

        marca_equipo = equipo_info.get("ManufacturerName", "")
        modelo = row.get("U_Modelo", "") or equipo_info.get("U_Modelo", "")

        base = {
            "Marca temporal": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
            "Solicitante": solicitante,
            "REVS": revs,
            "Cliente": cliente,
            "Área": "",
            "Fecha del req": fecha_req,
            "Folio de orden de trabajo": folio_ot,
            "Marca de equipo": marca_equipo,
            "Modelo": modelo,
            "Serie del equipo": serie_equipo,
            "Horómetro": horometro,
            "Acción a realizar": accion,
        }

        filas = []

        for i in range(11, 21):
            code = str(row.get(f"U_Code{i}", "") or "").strip()
            qty = str(row.get(f"U_Qty{i}", "") or "").strip()

            if not code:
                continue

            refaccion_info = obtener_item_info_sheet(code)

            filas.append({
                **base,
                "Requerimiento (Descripción)": code,
                "Cantidad": qty,
                "Número de parte": refaccion_info.get("ItemName", ""),
                "Estatus": "",
                "Comentarios": "",
                "Documento respuesta": "",
                "Folio de documento respuesta": "",
                "Seguimiento": False,
            })

        return ok_response({
            "version": "SOLO_U_CODE_11_A_20",
            "debug_codes": {
                "U_Code1": row.get("U_Code1", ""),
                "U_Code2": row.get("U_Code2", ""),
                "U_Code3": row.get("U_Code3", ""),
                "U_Code11": row.get("U_Code11", ""),
                "U_Code12": row.get("U_Code12", ""),
                "U_Code13": row.get("U_Code13", ""),
            },
            "insertar": True,
            "rows": filas
        })

    except Exception as e:
        import traceback
        error_detalle = traceback.format_exc()
        print(error_detalle)

        return {
            "ok": False,
            "message": str(e),
            "traceback": error_detalle
        }, 500

@ordenes_trabajo_bp.route("/<int:docnum>", methods=["GET"])
def ver_orden_trabajo(docnum):
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("VER_OT", "VER")
    if not allowed:
        return response

    try:
        data = sap_get(
            f"ServiceCalls?$filter=DocNum eq {docnum}"
        )

        rows = data.get("value", [])

        if not rows:
            return error_response("No se encontró información de esta OT", 404)

        ot = rows[0]

        call_type_id = ot.get("CallType")
        series_id = ot.get("Series")
        severidad_codigo = ot.get("U_Severidad")

        ot["CallTypeName"] = obtener_nombre_tipo_orden(call_type_id)
        ot["SucursalName"] = obtener_nombre_sucursal(series_id)
        ot["ProblemTypeName"] = obtener_nombre_tipo_problema(ot.get("ProblemType"))

        ot["RealizoTrabajoNombre"] = obtener_nombre_empleado(
            ot.get("TechnicianCode")
        )
        ot["Tecnico3Nombre"] = obtener_nombre_empleado(
            ot.get("U_Tecnico3")
        )
        ot["Tecnico4Nombre"] = obtener_nombre_empleado(
            ot.get("U_Tecnico4")
        )

        # Información de severidad (para Flash Reports)
        if severidad_codigo:
            color_fondo, color_texto = SEVERIDAD_INFO.get(
                severidad_codigo, ("#dddddd", "#111111")
            )
            ot["SeveridadEtiqueta"] = severidad_codigo
            ot["SeveridadColorFondo"] = color_fondo
            ot["SeveridadColorTexto"] = color_texto

        # Determinar tipo de OT
        tipo_vista = "normal"

        if ot.get("U_Severidad"):
            tipo_vista = "seguridad"
        elif ot.get("CustomerName") == "AUDI MEXICO" and int(series_id or 0) == 374:
            tipo_vista = "audi"

        # Validar si puede dar seguimiento (solo para Flash Reports)
        puede_seguimiento = (
            tipo_vista == "seguridad"
            and perfil_puede_dar_seguimiento(session.get("perfil_id"), call_type_id)  # FIXED
        )

        return ok_response(
            {
                "tipo_vista": tipo_vista,
                "ot": ot,
                "puede_seguimiento": puede_seguimiento,
            }
        )

    except Exception as e:
        return error_response(f"Error al consultar OT SAP: {str(e)}", 500)


@ordenes_trabajo_bp.route("/<int:docnum>/seguimiento", methods=["GET"])
def obtener_seguimiento(docnum):
    """Obtiene el historial de seguimiento de un Flash Report"""
    valid, response = validate_active_session()
    if not valid:
        return response

    try:
        conn = obtener_conexion_seguimiento()
        historial = conn.execute(
            "SELECT * FROM seguimiento WHERE docnum = ? ORDER BY creado_en DESC",
            (docnum,)
        ).fetchall()
        conn.close()

        ya_cerrado = any(row["estatus"] == "Cerrado" for row in historial)

        return ok_response(
            {
                "docnum": docnum,
                "historial": [dict(row) for row in historial],
                "ya_cerrado": ya_cerrado,
            }
        )

    except Exception as e:
        return error_response(f"Error al obtener seguimiento: {str(e)}", 500)


@ordenes_trabajo_bp.route("/<int:docnum>/seguimiento", methods=["POST"])
def guardar_seguimiento(docnum):
    """Guarda un nuevo registro de seguimiento para un Flash Report"""
    valid, response = validate_active_session()
    if not valid:
        return response

    allowed, response = require_permission("OT_SEGURIDAD", "CREAR")
    if not allowed:
        return response

    datos = request.get_json(silent=True) or {}
    estatus = datos.get("estatus", "").strip()
    responsable = datos.get("responsable", "").strip()
    fecha_compromiso = datos.get("fecha_compromiso", "").strip()
    comentario = datos.get("comentario", "").strip()

    if not estatus:
        return error_response("El estatus es obligatorio", 400)

    try:
        # Obtener la OT desde SAP para validaciones
        ot_data = sap_get(f"ServiceCalls?$filter=DocNum eq {docnum}")
        ot = ot_data.get("value", [{}])[0]

        if not ot:
            return error_response("No se encontró la OT en SAP", 404)

        # Validar permisos según el CallType
        call_type_id = ot.get("CallType")
        if not perfil_puede_dar_seguimiento(session.get("perfil_id"), call_type_id):  # FIXED
            return error_response("No tiene permiso para dar seguimiento a esta OT", 403)

        # Guardar en base de datos
        creado_por = session.get("username", "Sistema")
        creado_en = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        conn = obtener_conexion_seguimiento()
        conn.execute(
            """INSERT INTO seguimiento
               (docnum, estatus, responsable, fecha_compromiso, comentario, creado_por, creado_en)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (docnum, estatus, responsable, fecha_compromiso, comentario, creado_por, creado_en),
        )
        conn.commit()
        conn.close()

        return ok_response(
            {
                "docnum": docnum,
                "estatus": estatus,
                "creado_en": creado_en,
            },
            "Seguimiento guardado correctamente",
            201,
        )

    except Exception as e:
        return error_response(f"Error al guardar seguimiento: {str(e)}", 500)
    
