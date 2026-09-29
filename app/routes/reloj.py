"""
Reloj Checador: checadas con foto, GPS y celular registrado; panel del REV (asistencia,
turnos, incidencias, tiempo extra, revisión de checadas); gratificaciones; configuración.

Las checadas solo se insertan. La hora la pone el servidor y cada registro lleva un hash
encadenado con el anterior del mismo empleado (y el hash de su foto), así que cualquier
cambio posterior en la base o en el archivo se detecta con /integridad.
"""
import hashlib
import os
import uuid
from datetime import date, datetime, timedelta

from flask import Blueprint, request, send_from_directory
from werkzeug.utils import secure_filename

from app.db import fetch_all, fetch_one, execute_query, execute_many
from app.helpers import (
    ok_response,
    error_response,
    validate_active_session,
    require_permission,
    user_has_permission,
    get_next_id,
    current_user_id,
    current_user,
)
from app.reloj_reglas import (
    TIPOS_INCIDENCIA,
    distancia_m,
    evaluar_dia,
    fecha_laboral_salida,
    horario_del_dia,
    validar_dias,
    validar_hora,
)

reloj_bp = Blueprint("reloj", __name__)
SCHEMA = os.getenv("HANA_SCHEMA", "QRTEST")

BASE_UPLOADS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "uploads")
FOTOS_DIR = os.getenv("RELOJ_FOTOS_DIR", os.path.join(BASE_UPLOADS, "reloj", "fotos"))
INCIDENCIAS_DIR = os.getenv("RELOJ_INCIDENCIAS_DIR", os.path.join(BASE_UPLOADS, "reloj", "incidencias"))

MAX_FOTO_BYTES = 4 * 1024 * 1024
MAX_ARCHIVO_BYTES = 10 * 1024 * 1024
EXT_INCIDENCIA = {"pdf", "png", "jpg", "jpeg"}
# Precisión GPS peor que esto se marca para revisión
PRECISION_MAXIMA_M = int(os.getenv("RELOJ_PRECISION_MAXIMA_M", "150"))
# Diferencia entre el reloj del celular y el del servidor que se marca como sospechosa
DESFASE_MAXIMO_MIN = 5
# Evita el doble toque: misma checada dentro de este lapso se rechaza
MIN_ENTRE_CHECADAS = 2
MAX_DIAS_CONSULTA = 93
# Días anteriores al arranque del reloj no generan faltas (AAAA-MM-DD)
FECHA_INICIO = os.getenv("RELOJ_FECHA_INICIO", "")

TIPOS_UBICACION = {"MATRIZ", "SUCURSAL", "POLIZA", "OTRO"}

BANDERAS = {
    "FUERA_ZONA": "Fuera de zona",
    "SIN_GEOCERCA": "Sin geocerca configurada",
    "BAJA_PRECISION": "GPS poco preciso",
    "DISPOSITIVO_NUEVO": "Celular nuevo sin autorizar",
    "DISPOSITIVO_NO_AUTORIZADO": "Celular no autorizado",
    "DISPOSITIVO_AJENO": "Celular registrado a otro empleado",
    "HORA_DESFASADA": "Hora del celular alterada",
}


# --------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------


def _t(tabla):
    return f'"{SCHEMA}"."{tabla}"'


def _s(row):
    if row is None:
        return None
    out = {}
    for k, v in row.items():
        out[k] = v.isoformat() if isinstance(v, (date, datetime)) else v
    return out


def _sl(rows):
    return [_s(r) for r in rows]


def _fecha(valor):
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    return date.fromisoformat(str(valor)[:10])


def _parse_fecha(valor):
    try:
        return date.fromisoformat(str(valor).strip()[:10]) if valor else None
    except ValueError:
        return None


def _rango(args, dias_default=14):
    hasta = _parse_fecha(args.get("hasta")) or date.today()
    desde = _parse_fecha(args.get("desde")) or hasta - timedelta(days=dias_default - 1)
    if desde > hasta:
        return None, None, "La fecha inicial es posterior a la final"
    if (hasta - desde).days + 1 > MAX_DIAS_CONSULTA:
        return None, None, f"El rango máximo es de {MAX_DIAS_CONSULTA} días"
    return desde, hasta, None


def _dias(desde, hasta):
    return [desde + timedelta(days=i) for i in range((hasta - desde).days + 1)]


def _placeholders(valores):
    return ", ".join("?" for _ in valores)


def _fetch_por_ids(query, ids, params_antes=None, params_despues=None, tam=500):
    """Corre query (con {ids} como marcador de la lista IN) en bloques de ids"""
    ids = list(dict.fromkeys(ids))
    filas = []
    for i in range(0, len(ids), tam):
        bloque = ids[i:i + tam]
        filas += fetch_all(
            query.format(ids=_placeholders(bloque)),
            (params_antes or []) + bloque + (params_despues or []),
        )
    return filas


def _sha256_bytes(contenido):
    return hashlib.sha256(contenido).hexdigest()


def _sha256_archivo(ruta):
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(65536), b""):
            h.update(bloque)
    return h.hexdigest()


def _hash_checada(c, hash_anterior):
    campos = [
        hash_anterior or "",
        str(c["CHECADAID"]),
        str(c["USUARIOID"]),
        c["TIPO"],
        c["FECHA_HORA"].strftime("%Y-%m-%d %H:%M:%S"),
        _fecha(c["FECHA_LABORAL"]).isoformat(),
        f'{float(c["LATITUD"]):.7f}',
        f'{float(c["LONGITUD"]):.7f}',
        "" if c.get("PRECISION_M") is None else str(int(c["PRECISION_M"])),
        c["FOTO_SHA256"],
        "" if c.get("DISPOSITIVOID") is None else str(int(c["DISPOSITIVOID"])),
        c.get("BANDERAS") or "",
    ]
    return hashlib.sha256("|".join(campos).encode("utf-8")).hexdigest()


def _requiere_alguno(*permisos):
    valid, response = validate_active_session()
    if not valid:
        return False, response
    if any(user_has_permission(m, a) for m, a in permisos):
        return True, None
    return False, error_response("No autorizado", 403)


# --------------------------------------------------------------------
# Alcance del REV: qué sucursales puede ver
# --------------------------------------------------------------------


def _alcance():
    """None = todas las sucursales; si no, lista de SUCURSALID"""
    if user_has_permission("RELOJ_CONFIG", "VER"):
        return None
    ids = [
        r["SUCURSALID"]
        for r in fetch_all(
            f'SELECT "SUCURSALID" FROM {_t("RC_REV_SUCURSALES")} WHERE "USUARIOID" = ?',
            [current_user_id()],
        )
    ]
    if not ids:
        user = current_user() or {}
        if user.get("SUCURSAL"):
            ids = [str(user["SUCURSAL"])]
    return ids


def _empleados(sucursal=None, usuario=None, q=None, incluir_inactivos=False):
    alcance = _alcance()
    where, params = [], []
    if not incluir_inactivos:
        where.append('U."ACTIVO" = 1')
    if alcance is not None:
        if not alcance:
            return []
        where.append(f'U."SUCURSAL" IN ({_placeholders(alcance)})')
        params += alcance
    if sucursal:
        where.append('U."SUCURSAL" = ?')
        params.append(str(sucursal))
    if usuario:
        where.append('U."USUARIOID" = ?')
        params.append(int(usuario))
    if q:
        where.append('(UPPER(U."NOMBRE") LIKE ? OR UPPER(U."NUMERO_EMPLEADO") LIKE ?)')
        params += [f"%{q.upper()}%", f"%{q.upper()}%"]

    return fetch_all(
        f'''
        SELECT
            U."USUARIOID", U."NOMBRE", U."NUMERO_EMPLEADO", U."SUCURSAL",
            SUC."CLAVE" AS "SUCURSAL_CLAVE", SUC."NOMBRE" AS "SUCURSAL_NOMBRE",
            D."NOMBRE" AS "DEPARTAMENTO", P."NOMBRE" AS "PERFIL",
            E."HORARIOID", H."NOMBRE" AS "HORARIO_NOMBRE",
            E."UBICACIONID", UB."NOMBRE" AS "UBICACION_NOMBRE",
            COALESCE(E."CHECA", 1) AS "CHECA",
            CASE WHEN E."USUARIOID" IS NULL THEN 0 ELSE 1 END AS "CONFIGURADO"
        FROM {_t("USUARIOS")} U
        LEFT JOIN {_t("SUCURSALES")} SUC ON U."SUCURSAL" = SUC."SUCURSALID"
        LEFT JOIN {_t("DEPARTAMENTOS")} D ON U."DEPAID" = D."DEPAID"
        LEFT JOIN {_t("PERFILES")} P ON U."PERFILID" = P."PERFILID"
        LEFT JOIN {_t("RC_EMPLEADOS")} E ON U."USUARIOID" = E."USUARIOID"
        LEFT JOIN {_t("RC_HORARIOS")} H ON E."HORARIOID" = H."HORARIOID"
        LEFT JOIN {_t("RC_UBICACIONES")} UB ON E."UBICACIONID" = UB."UBICACIONID"
        {"WHERE " + " AND ".join(where) if where else ""}
        ORDER BY U."NOMBRE"
        ''',
        params,
    )


def _empleado_visible(usuarioid):
    return bool(_empleados(usuario=usuarioid, incluir_inactivos=True))


# --------------------------------------------------------------------
# Carga de datos para evaluar días
# --------------------------------------------------------------------


def _contexto(uids, desde, hasta):
    """Todo lo necesario para evaluar los días [desde, hasta] de esos empleados"""
    ctx = {
        u: {"config": None, "asignaciones": [], "incidencias": [], "checadas": {}, "extra": {}}
        for u in uids
    }
    if not uids:
        return ctx, {}, {}

    horarios = {h["HORARIOID"]: h for h in fetch_all(f'SELECT * FROM {_t("RC_HORARIOS")}')}

    for r in _fetch_por_ids(
        f'SELECT * FROM {_t("RC_EMPLEADOS")} WHERE "USUARIOID" IN ({{ids}})', uids
    ):
        ctx[r["USUARIOID"]]["config"] = r

    for r in _fetch_por_ids(
        f'''SELECT * FROM {_t("RC_ASIGNACIONES")}
            WHERE "USUARIOID" IN ({{ids}}) AND "ACTIVO" = 1
              AND "FECHA_INICIO" <= ? AND "FECHA_FIN" >= ?''',
        uids, params_despues=[hasta, desde],
    ):
        ctx[r["USUARIOID"]]["asignaciones"].append(r)

    for r in _fetch_por_ids(
        f'''SELECT * FROM {_t("RC_INCIDENCIAS")}
            WHERE "USUARIOID" IN ({{ids}}) AND "ACTIVO" = 1
              AND "FECHA_INICIO" <= ? AND "FECHA_FIN" >= ?''',
        uids, params_despues=[hasta, desde],
    ):
        ctx[r["USUARIOID"]]["incidencias"].append(r)

    for r in _fetch_por_ids(
        f'''SELECT C."CHECADAID", C."USUARIOID", C."TIPO", C."FECHA_HORA", C."FECHA_LABORAL",
                   C."BANDERAS", R."ESTADO" AS "REVISION"
            FROM {_t("RC_CHECADAS")} C
            LEFT JOIN {_t("RC_CHECADAS_REVISION")} R ON C."CHECADAID" = R."CHECADAID"
            WHERE C."USUARIOID" IN ({{ids}}) AND C."FECHA_LABORAL" BETWEEN ? AND ?
            ORDER BY C."FECHA_HORA"''',
        uids, params_despues=[desde, hasta],
    ):
        ctx[r["USUARIOID"]]["checadas"].setdefault(_fecha(r["FECHA_LABORAL"]), []).append(r)

    for r in _fetch_por_ids(
        f'''SELECT * FROM {_t("RC_HORAS_EXTRA")}
            WHERE "USUARIOID" IN ({{ids}}) AND "FECHA" BETWEEN ? AND ?''',
        uids, params_despues=[desde, hasta],
    ):
        ctx[r["USUARIOID"]]["extra"][_fecha(r["FECHA"])] = r

    festivos = {}
    for r in fetch_all(
        f'''SELECT "FECHA", "NOMBRE", "SUCURSALID" FROM {_t("RC_FESTIVOS")}
            WHERE "ACTIVO" = 1 AND "FECHA" BETWEEN ? AND ?''',
        [desde, hasta],
    ):
        festivos.setdefault(_fecha(r["FECHA"]), []).append(r)

    return ctx, horarios, festivos


def _festivo(festivos, dia, sucursal):
    for f in festivos.get(dia, []):
        if not f["SUCURSALID"] or str(f["SUCURSALID"]) == str(sucursal or ""):
            return f["NOMBRE"]
    return None


def _horario_emp(c, horarios, dia):
    config = c["config"] or {}
    return horario_del_dia(dia, horarios.get(config.get("HORARIOID")), c["asignaciones"])


def _evaluar_empleado(emp, c, horarios, festivos, dias, ahora):
    checa = int((c["config"] or {}).get("CHECA", 1)) == 1
    inicio = _parse_fecha(FECHA_INICIO)
    resultado = []
    for dia in dias:
        if inicio and dia < inicio and not c["checadas"].get(dia):
            resultado.append({
                "FECHA": dia.isoformat(), "ESTADO": "SIN_REGISTRO", "DETALLE": "Antes del arranque del reloj",
                "HORARIO": None, "ENTRADA": None, "SALIDA": None, "MINUTOS_RETARDO": 0,
                "MINUTOS_TRABAJADOS": 0, "MINUTOS_EXTRA": 0, "SALIDA_ANTICIPADA": False,
                "INCIDENCIA": None, "ALERTAS": 0, "RECHAZADAS": 0, "EXTRA_ESTADO": None, "EXTRA_AUTORIZADO": 0,
            })
            continue
        horario = _horario_emp(c, horarios, dia)
        todas = c["checadas"].get(dia, [])
        validas = [x for x in todas if x.get("REVISION") != "RECHAZADA"]
        incid = [
            i for i in c["incidencias"]
            if _fecha(i["FECHA_INICIO"]) <= dia <= _fecha(i["FECHA_FIN"])
        ]
        r = evaluar_dia(dia, horario, validas, incid, _festivo(festivos, dia, emp["SUCURSAL"]), ahora, checa)
        if horario is None and not c["config"] and not c["asignaciones"] and r["ESTADO"] == "DESCANSO":
            r["ESTADO"] = "SIN_HORARIO"
        r["ALERTAS"] = sum(1 for x in todas if x.get("BANDERAS") and not x.get("REVISION"))
        r["RECHAZADAS"] = sum(1 for x in todas if x.get("REVISION") == "RECHAZADA")
        extra = c["extra"].get(dia)
        r["EXTRA_ESTADO"] = extra["ESTADO"] if extra else ("PENDIENTE" if r["MINUTOS_EXTRA"] else None)
        r["EXTRA_AUTORIZADO"] = extra["MINUTOS_AUTORIZADOS"] if extra and extra["ESTADO"] == "AUTORIZADA" else 0
        resultado.append(r)
    return resultado


def _totales(dias_eval):
    t = {
        "ASISTENCIAS": 0, "RETARDOS": 0, "FALTAS": 0, "INCAPACIDADES": 0, "VACACIONES": 0,
        "PERMISOS": 0, "FESTIVOS": 0, "MINUTOS_EXTRA_PENDIENTES": 0, "MINUTOS_EXTRA_AUTORIZADOS": 0,
        "ALERTAS": 0,
    }
    for d in dias_eval:
        e = d["ESTADO"]
        if e in ("ASISTENCIA", "EN_CURSO", "COMISION"):
            t["ASISTENCIAS"] += 1
        elif e in ("RETARDO", "EN_CURSO_RETARDO"):
            t["RETARDOS"] += 1
        elif e == "FALTA":
            t["FALTAS"] += 1
        elif e == "INCAPACIDAD":
            t["INCAPACIDADES"] += 1
        elif e == "VACACIONES":
            t["VACACIONES"] += 1
        elif e.startswith("PERMISO") or e == "FALTA_JUSTIFICADA":
            t["PERMISOS"] += 1
        elif e.startswith("FESTIVO"):
            t["FESTIVOS"] += 1
        if d["EXTRA_ESTADO"] == "PENDIENTE":
            t["MINUTOS_EXTRA_PENDIENTES"] += d["MINUTOS_EXTRA"]
        t["MINUTOS_EXTRA_AUTORIZADOS"] += d["EXTRA_AUTORIZADO"]
        t["ALERTAS"] += d["ALERTAS"]
    return t


def _ubicaciones_activas():
    return fetch_all(f'SELECT * FROM {_t("RC_UBICACIONES")} WHERE "ACTIVO" = 1')


def _ubicaciones_permitidas(user, config, horario, ubicaciones):
    ids = set()
    if horario and horario.get("UBICACIONID"):
        ids.add(int(horario["UBICACIONID"]))
    if config and config.get("UBICACIONID"):
        ids.add(int(config["UBICACIONID"]))
    permitidas = [u for u in ubicaciones if u["UBICACIONID"] in ids]
    if user.get("SUCURSAL"):
        permitidas += [
            u for u in ubicaciones
            if str(u.get("SUCURSALID") or "") == str(user["SUCURSAL"]) and u["UBICACIONID"] not in ids
        ]
    return permitidas


# --------------------------------------------------------------------
# Mi checador
# --------------------------------------------------------------------


def _dispositivo_hash(token):
    return hashlib.sha256(f"reloj:{token}".encode("utf-8")).hexdigest()


@reloj_bp.route("/mi-estado", methods=["GET"])
def mi_estado():
    allowed, response = require_permission("RELOJ_CHECAR", "VER")
    if not allowed:
        return response

    uid = current_user_id()
    user = current_user()
    ahora = datetime.now()
    hoy = ahora.date()
    desde = hoy - timedelta(days=6)

    ctx, horarios, festivos = _contexto([uid], desde - timedelta(days=1), hoy)
    c = ctx[uid]
    emp = {"SUCURSAL": user.get("SUCURSAL")}
    semana = _evaluar_empleado(emp, c, horarios, festivos, _dias(desde, hoy), ahora)
    hoy_eval = semana[-1]

    checadas_hoy = fetch_all(
        f'''SELECT C."CHECADAID", C."TIPO", C."FECHA_HORA", C."FECHA_LABORAL", C."BANDERAS",
                   C."DISTANCIA_M", C."FUERA_ZONA", UB."NOMBRE" AS "UBICACION_NOMBRE",
                   R."ESTADO" AS "REVISION"
            FROM {_t("RC_CHECADAS")} C
            LEFT JOIN {_t("RC_UBICACIONES")} UB ON C."UBICACIONID" = UB."UBICACIONID"
            LEFT JOIN {_t("RC_CHECADAS_REVISION")} R ON C."CHECADAID" = R."CHECADAID"
            WHERE C."USUARIOID" = ? AND C."FECHA_LABORAL" >= ?
            ORDER BY C."FECHA_HORA" DESC''',
        [uid, hoy - timedelta(days=1)],
    )

    # Sugerir entrada o salida: si la última checada de su día laboral actual es entrada, toca salida
    por_dia = {
        d: (any(x["TIPO"] == "ENTRADA" for x in c["checadas"].get(d, [])),
            any(x["TIPO"] == "SALIDA" for x in c["checadas"].get(d, [])))
        for d in (hoy - timedelta(days=1), hoy)
    }
    dia_salida = fecha_laboral_salida(ahora, por_dia, lambda d: _horario_emp(c, horarios, d))
    entrada_abierta = por_dia.get(dia_salida, (False, False))
    sugerido = "SALIDA" if entrada_abierta[0] and not entrada_abierta[1] else "ENTRADA"

    token = request.args.get("dispositivo") or ""
    dispositivo = None
    if token:
        dispositivo = fetch_one(
            f'''SELECT "DISPOSITIVOID", "ESTADO" FROM {_t("RC_DISPOSITIVOS")}
                WHERE "USUARIOID" = ? AND "TOKEN_HASH" = ?''',
            [uid, _dispositivo_hash(token)],
        )

    return ok_response({
        "AHORA": ahora.replace(microsecond=0).isoformat(),
        "EMPLEADO": {
            "USUARIOID": uid,
            "NOMBRE": user.get("NOMBRE"),
            "NUMERO_EMPLEADO": user.get("NUMERO_EMPLEADO"),
        },
        "HOY": hoy_eval,
        "SEMANA": semana,
        "CHECADAS_RECIENTES": _sl(checadas_hoy),
        "SUGERIDO": sugerido,
        "DISPOSITIVO_ESTADO": dispositivo["ESTADO"] if dispositivo else "SIN_REGISTRO",
        "BANDERAS": BANDERAS,
    })


@reloj_bp.route("/checar", methods=["POST"])
def checar():
    allowed, response = require_permission("RELOJ_CHECAR", "VER")
    if not allowed:
        return response

    uid = current_user_id()
    user = current_user()
    form = request.form

    tipo = (form.get("tipo") or "").strip().upper()
    if tipo not in ("ENTRADA", "SALIDA"):
        return error_response("Indica si es entrada o salida", 400)

    try:
        lat = round(float(form.get("latitud")), 7)
        lng = round(float(form.get("longitud")), 7)
    except (TypeError, ValueError):
        return error_response("No se recibió tu ubicación. Activa el GPS y da permiso de ubicación.", 400)
    if not (-90 <= lat <= 90 and -180 <= lng <= 180) or (lat == 0 and lng == 0):
        return error_response("La ubicación recibida no es válida", 400)

    try:
        precision = int(round(float(form.get("precision")))) if form.get("precision") else None
    except ValueError:
        precision = None

    token = (form.get("dispositivo") or "").strip()
    if not (16 <= len(token) <= 200):
        return error_response("No se pudo identificar el dispositivo. Recarga la página.", 400)

    foto = request.files.get("foto")
    contenido = foto.read(MAX_FOTO_BYTES + 1) if foto else b""
    if not contenido:
        return error_response("La foto es obligatoria para checar", 400)
    if len(contenido) > MAX_FOTO_BYTES:
        return error_response("La foto es demasiado grande", 400)
    if not contenido.startswith(b"\xff\xd8"):
        return error_response("La foto debe tomarse con la cámara al momento de checar", 400)

    ahora = datetime.now().replace(microsecond=0)
    hoy = ahora.date()

    fecha_dispositivo = None
    try:
        if form.get("fecha_dispositivo"):
            fecha_dispositivo = datetime.fromtimestamp(int(form.get("fecha_dispositivo")) / 1000).replace(microsecond=0)
    except (ValueError, OverflowError, OSError):
        fecha_dispositivo = None

    ultima = fetch_one(
        f'''SELECT TOP 1 "TIPO", "FECHA_HORA", "HASH" FROM {_t("RC_CHECADAS")}
            WHERE "USUARIOID" = ? ORDER BY "CHECADAID" DESC''',
        [uid],
    )
    if ultima and ultima["TIPO"] == tipo and ahora - ultima["FECHA_HORA"] < timedelta(minutes=MIN_ENTRE_CHECADAS):
        return error_response(
            f"Ya registraste tu {tipo.lower()} a las {ultima['FECHA_HORA'].strftime('%H:%M')}", 409
        )

    ctx, horarios, _ = _contexto([uid], hoy - timedelta(days=1), hoy)
    c = ctx[uid]

    if tipo == "ENTRADA":
        fecha_laboral = hoy
    else:
        por_dia = {
            d: (any(x["TIPO"] == "ENTRADA" for x in c["checadas"].get(d, [])),
                any(x["TIPO"] == "SALIDA" for x in c["checadas"].get(d, [])))
            for d in (hoy - timedelta(days=1), hoy)
        }
        fecha_laboral = fecha_laboral_salida(ahora, por_dia, lambda d: _horario_emp(c, horarios, d))

    banderas = []

    # Geocerca
    horario = _horario_emp(c, horarios, fecha_laboral)
    ubicaciones = _ubicaciones_activas()
    permitidas = _ubicaciones_permitidas(user, c["config"], horario, ubicaciones)
    candidatas = permitidas or ubicaciones
    ubicacionid, distancia, fuera_zona = None, None, 0
    if candidatas:
        cercana = min(
            candidatas,
            key=lambda u: distancia_m(lat, lng, float(u["LATITUD"]), float(u["LONGITUD"])),
        )
        ubicacionid = cercana["UBICACIONID"]
        distancia = int(distancia_m(lat, lng, float(cercana["LATITUD"]), float(cercana["LONGITUD"])))
        if not permitidas:
            banderas.append("SIN_GEOCERCA")
        elif not any(
            distancia_m(lat, lng, float(u["LATITUD"]), float(u["LONGITUD"])) <= int(u["RADIO_M"])
            for u in permitidas
        ):
            fuera_zona = 1
            banderas.append("FUERA_ZONA")
    else:
        banderas.append("SIN_GEOCERCA")

    if precision is None or precision > PRECISION_MAXIMA_M:
        banderas.append("BAJA_PRECISION")

    if fecha_dispositivo and abs((fecha_dispositivo - ahora).total_seconds()) > DESFASE_MAXIMO_MIN * 60:
        banderas.append("HORA_DESFASADA")

    # Celular: uno activo por empleado; si el identificador ya es de otro empleado, se marca
    token_hash = _dispositivo_hash(token)
    registrados = fetch_all(
        f'SELECT * FROM {_t("RC_DISPOSITIVOS")} WHERE "TOKEN_HASH" = ?', [token_hash]
    )
    propio = next((d for d in registrados if d["USUARIOID"] == uid), None)
    ajenos = [d for d in registrados if d["USUARIOID"] != uid and d["ESTADO"] == "ACTIVO"]
    if ajenos:
        banderas.append("DISPOSITIVO_AJENO")

    operaciones = []
    if propio:
        dispositivoid = propio["DISPOSITIVOID"]
        if propio["ESTADO"] != "ACTIVO":
            banderas.append("DISPOSITIVO_NO_AUTORIZADO")
        operaciones.append((
            f'UPDATE {_t("RC_DISPOSITIVOS")} SET "ULTIMO_USO" = ? WHERE "DISPOSITIVOID" = ?',
            [ahora, dispositivoid],
        ))
    else:
        tiene_activo = fetch_one(
            f'''SELECT 1 AS X FROM {_t("RC_DISPOSITIVOS")}
                WHERE "USUARIOID" = ? AND "ESTADO" = 'ACTIVO' ''',
            [uid],
        )
        # El primer celular de un empleado queda activo; los siguientes esperan al REV
        estado = "PENDIENTE" if tiene_activo or ajenos else "ACTIVO"
        if estado == "PENDIENTE":
            banderas.append("DISPOSITIVO_NUEVO")
        dispositivoid = get_next_id("RC_DISPOSITIVOS", "DISPOSITIVOID")
        operaciones.append((
            f'''INSERT INTO {_t("RC_DISPOSITIVOS")}
                ("DISPOSITIVOID", "USUARIOID", "TOKEN_HASH", "DESCRIPCION", "ESTADO",
                 "FECHA_REGISTRO", "ULTIMO_USO")
                VALUES (?, ?, ?, ?, ?, ?, ?)''',
            [dispositivoid, uid, token_hash, (request.user_agent.string or "")[:300], estado, ahora, ahora],
        ))

    # Foto
    foto_sha = _sha256_bytes(contenido)
    relativa = f"{ahora:%Y}/{ahora:%m}/{uuid.uuid4().hex}.jpg"
    destino = os.path.join(FOTOS_DIR, *relativa.split("/"))
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    with open(destino, "wb") as f:
        f.write(contenido)

    checadaid = fetch_one(f'SELECT "{SCHEMA}"."RC_CHECADAS_SEQ".NEXTVAL AS "ID" FROM DUMMY')["ID"]
    registro = {
        "CHECADAID": checadaid,
        "USUARIOID": uid,
        "TIPO": tipo,
        "FECHA_HORA": ahora,
        "FECHA_LABORAL": fecha_laboral,
        "LATITUD": lat,
        "LONGITUD": lng,
        "PRECISION_M": precision,
        "FOTO_SHA256": foto_sha,
        "DISPOSITIVOID": dispositivoid,
        "BANDERAS": ",".join(banderas) or None,
    }
    hash_anterior = ultima["HASH"] if ultima else None
    registro_hash = _hash_checada(registro, hash_anterior)

    operaciones.append((
        f'''INSERT INTO {_t("RC_CHECADAS")}
            ("CHECADAID", "USUARIOID", "TIPO", "FECHA_HORA", "FECHA_LABORAL",
             "FECHA_HORA_DISPOSITIVO", "LATITUD", "LONGITUD", "PRECISION_M", "UBICACIONID",
             "DISTANCIA_M", "FUERA_ZONA", "FOTO", "FOTO_SHA256", "DISPOSITIVOID", "BANDERAS",
             "IP", "USER_AGENT", "HASH_ANTERIOR", "HASH")
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        [
            checadaid, uid, tipo, ahora, fecha_laboral, fecha_dispositivo, lat, lng, precision,
            ubicacionid, distancia, fuera_zona, relativa, foto_sha, dispositivoid,
            registro["BANDERAS"],
            (request.headers.get("X-Forwarded-For") or request.remote_addr or "")[:64],
            (request.user_agent.string or "")[:500],
            hash_anterior, registro_hash,
        ],
    ))

    try:
        execute_many(operaciones)
    except Exception:
        try:
            os.remove(destino)
        except OSError:
            pass
        raise

    ubicacion = next((u for u in ubicaciones if u["UBICACIONID"] == ubicacionid), None)
    return ok_response(
        {
            "CHECADAID": checadaid,
            "TIPO": tipo,
            "FECHA_HORA": ahora.isoformat(),
            "FECHA_LABORAL": fecha_laboral.isoformat(),
            "UBICACION_NOMBRE": ubicacion["NOMBRE"] if ubicacion else None,
            "DISTANCIA_M": distancia,
            "BANDERAS": banderas,
            "BANDERAS_TEXTO": [BANDERAS[b] for b in banderas],
        },
        f"{'Entrada' if tipo == 'ENTRADA' else 'Salida'} registrada a las {ahora:%H:%M}",
        201,
    )


@reloj_bp.route("/mis-registros", methods=["GET"])
def mis_registros():
    allowed, response = require_permission("RELOJ_CHECAR", "VER")
    if not allowed:
        return response

    desde, hasta, error = _rango(request.args, 31)
    if error:
        return error_response(error, 400)

    uid = current_user_id()
    user = current_user()
    ctx, horarios, festivos = _contexto([uid], desde, hasta)
    dias = _evaluar_empleado({"SUCURSAL": user.get("SUCURSAL")}, ctx[uid], horarios, festivos, _dias(desde, hasta), datetime.now())
    return ok_response({"DIAS": dias, "TOTALES": _totales(dias)})


# --------------------------------------------------------------------
# Panel del REV
# --------------------------------------------------------------------


@reloj_bp.route("/asistencia", methods=["GET"])
def asistencia():
    allowed, response = require_permission("RELOJ_ASISTENCIA", "VER")
    if not allowed:
        return response

    desde, hasta, error = _rango(request.args, 7)
    if error:
        return error_response(error, 400)

    emps = _empleados(request.args.get("sucursal"), request.args.get("usuario"), request.args.get("q"))
    ctx, horarios, festivos = _contexto([e["USUARIOID"] for e in emps], desde, hasta)
    dias = _dias(desde, hasta)
    ahora = datetime.now()

    filas = []
    for e in emps:
        evaluados = _evaluar_empleado(e, ctx[e["USUARIOID"]], horarios, festivos, dias, ahora)
        filas.append({**_s(e), "DIAS": evaluados, "TOTALES": _totales(evaluados)})

    return ok_response({"DIAS": [d.isoformat() for d in dias], "EMPLEADOS": filas})


@reloj_bp.route("/empleados", methods=["GET"])
def listar_empleados():
    allowed, response = require_permission("RELOJ_ASISTENCIA", "VER")
    if not allowed:
        return response

    emps = _empleados(request.args.get("sucursal"), None, request.args.get("q"))
    if emps:
        dispositivos = {}
        for r in _fetch_por_ids(
            f'''SELECT "USUARIOID", "ESTADO", COUNT(*) AS "N" FROM {_t("RC_DISPOSITIVOS")}
                WHERE "USUARIOID" IN ({{ids}}) GROUP BY "USUARIOID", "ESTADO"''',
            [e["USUARIOID"] for e in emps],
        ):
            dispositivos.setdefault(r["USUARIOID"], {})[r["ESTADO"]] = r["N"]
        for e in emps:
            d = dispositivos.get(e["USUARIOID"], {})
            e["DISPOSITIVOS_ACTIVOS"] = d.get("ACTIVO", 0)
            e["DISPOSITIVOS_PENDIENTES"] = d.get("PENDIENTE", 0)
    return ok_response(_sl(emps))


@reloj_bp.route("/empleados", methods=["PUT"])
def configurar_empleados():
    """Asigna horario, lugar habitual y/o si checa a uno o varios empleados"""
    allowed, response = require_permission("RELOJ_ASISTENCIA", "EDITAR")
    if not allowed:
        return response

    data = request.get_json(silent=True) or {}
    uids = [int(u) for u in data.get("USUARIOIDS") or []]
    if not uids:
        return error_response("Selecciona al menos un empleado", 400)

    visibles = {e["USUARIOID"] for e in _empleados(incluir_inactivos=True)}
    fuera = [u for u in uids if u not in visibles]
    if fuera:
        return error_response("Hay empleados fuera de tus sucursales", 403)

    if "HORARIOID" in data and data["HORARIOID"] and not fetch_one(
        f'SELECT 1 AS X FROM {_t("RC_HORARIOS")} WHERE "HORARIOID" = ?', [data["HORARIOID"]]
    ):
        return error_response("Horario no encontrado", 404)
    if "UBICACIONID" in data and data["UBICACIONID"] and not fetch_one(
        f'SELECT 1 AS X FROM {_t("RC_UBICACIONES")} WHERE "UBICACIONID" = ?', [data["UBICACIONID"]]
    ):
        return error_response("Ubicación no encontrada", 404)

    actuales = {
        r["USUARIOID"]: r
        for r in _fetch_por_ids(f'SELECT * FROM {_t("RC_EMPLEADOS")} WHERE "USUARIOID" IN ({{ids}})', uids)
    }
    ahora = datetime.now()
    operaciones = []
    for u in uids:
        actual = actuales.get(u, {"HORARIOID": None, "UBICACIONID": None, "CHECA": 1})
        horario = data["HORARIOID"] if "HORARIOID" in data else actual["HORARIOID"]
        ubicacion = data["UBICACIONID"] if "UBICACIONID" in data else actual["UBICACIONID"]
        checa = (1 if data["CHECA"] else 0) if "CHECA" in data else actual["CHECA"]
        operaciones.append((
            f'''UPSERT {_t("RC_EMPLEADOS")}
                ("USUARIOID", "HORARIOID", "UBICACIONID", "CHECA", "ACTUALIZO", "FECHA_ACTUALIZACION")
                VALUES (?, ?, ?, ?, ?, ?) WITH PRIMARY KEY''',
            [u, horario or None, ubicacion or None, checa, current_user_id(), ahora],
        ))
    execute_many(operaciones)
    return ok_response(None, f"{len(uids)} empleado(s) actualizado(s)")


@reloj_bp.route("/checadas", methods=["GET"])
def listar_checadas():
    allowed, response = require_permission("RELOJ_ASISTENCIA", "VER")
    if not allowed:
        return response

    desde, hasta, error = _rango(request.args, 7)
    if error:
        return error_response(error, 400)

    emps = {e["USUARIOID"]: e for e in _empleados(request.args.get("sucursal"), request.args.get("usuario"), request.args.get("q"))}
    if not emps:
        return ok_response([])

    filtro = ""
    if request.args.get("alertas") == "1":
        filtro += ' AND C."BANDERAS" IS NOT NULL'
    if request.args.get("sin_revisar") == "1":
        filtro += ' AND R."ESTADO" IS NULL'

    filas = _fetch_por_ids(
        f'''SELECT C."CHECADAID", C."USUARIOID", C."TIPO", C."FECHA_HORA", C."FECHA_LABORAL",
                   C."FECHA_HORA_DISPOSITIVO", C."LATITUD", C."LONGITUD", C."PRECISION_M",
                   C."UBICACIONID", UB."NOMBRE" AS "UBICACION_NOMBRE", UB."RADIO_M",
                   C."DISTANCIA_M", C."FUERA_ZONA", C."BANDERAS", C."DISPOSITIVOID",
                   R."ESTADO" AS "REVISION", R."COMENTARIO" AS "REVISION_COMENTARIO",
                   RU."NOMBRE" AS "REVISO_NOMBRE", R."FECHA" AS "REVISION_FECHA"
            FROM {_t("RC_CHECADAS")} C
            LEFT JOIN {_t("RC_UBICACIONES")} UB ON C."UBICACIONID" = UB."UBICACIONID"
            LEFT JOIN {_t("RC_CHECADAS_REVISION")} R ON C."CHECADAID" = R."CHECADAID"
            LEFT JOIN {_t("USUARIOS")} RU ON R."REVISO" = RU."USUARIOID"
            WHERE C."USUARIOID" IN ({{ids}}) AND C."FECHA_LABORAL" BETWEEN ? AND ? {filtro}
            ORDER BY C."FECHA_HORA" DESC''',
        list(emps), params_despues=[desde, hasta],
    )
    for f in filas:
        e = emps[f["USUARIOID"]]
        f["NOMBRE"] = e["NOMBRE"]
        f["NUMERO_EMPLEADO"] = e["NUMERO_EMPLEADO"]
        f["SUCURSAL_CLAVE"] = e["SUCURSAL_CLAVE"]
        f["BANDERAS_TEXTO"] = [BANDERAS.get(b, b) for b in (f["BANDERAS"] or "").split(",") if b]
    filas.sort(key=lambda f: f["FECHA_HORA"], reverse=True)
    return ok_response(_sl(filas))


def _checada_visible(checadaid):
    return fetch_one(
        f'SELECT "CHECADAID", "USUARIOID", "FOTO" FROM {_t("RC_CHECADAS")} WHERE "CHECADAID" = ?',
        [checadaid],
    )


@reloj_bp.route("/checadas/<int:checadaid>/foto", methods=["GET"])
def foto_checada(checadaid):
    valid, response = validate_active_session()
    if not valid:
        return response

    c = _checada_visible(checadaid)
    if not c:
        return error_response("Checada no encontrada", 404)
    propia = c["USUARIOID"] == current_user_id()
    if not propia and not (
        user_has_permission("RELOJ_ASISTENCIA", "VER") and _empleado_visible(c["USUARIOID"])
    ):
        return error_response("No autorizado", 403)

    carpeta, archivo = os.path.split(os.path.join(FOTOS_DIR, *c["FOTO"].split("/")))
    if not os.path.exists(os.path.join(carpeta, archivo)):
        return error_response("La foto no está disponible", 404)
    return send_from_directory(carpeta, archivo, mimetype="image/jpeg", max_age=3600)


@reloj_bp.route("/checadas/<int:checadaid>/revision", methods=["POST"])
def revisar_checada(checadaid):
    allowed, response = require_permission("RELOJ_ASISTENCIA", "EDITAR")
    if not allowed:
        return response

    c = _checada_visible(checadaid)
    if not c or not _empleado_visible(c["USUARIOID"]):
        return error_response("Checada no encontrada", 404)
    if c["USUARIOID"] == current_user_id():
        return error_response("No puedes revisar tus propias checadas", 403)

    data = request.get_json(silent=True) or {}
    estado = (data.get("ESTADO") or "").upper()
    comentario = (data.get("COMENTARIO") or "").strip()[:500]
    if estado not in ("VALIDADA", "RECHAZADA"):
        return error_response("Estado inválido", 400)
    if estado == "RECHAZADA" and len(comentario) < 10:
        return error_response("Explica por qué se rechaza la checada (mínimo 10 caracteres)", 400)

    execute_query(
        f'''UPSERT {_t("RC_CHECADAS_REVISION")} ("CHECADAID", "ESTADO", "COMENTARIO", "REVISO", "FECHA")
            VALUES (?, ?, ?, ?, ?) WITH PRIMARY KEY''',
        [checadaid, estado, comentario or None, current_user_id(), datetime.now()],
    )
    return ok_response(None, "Checada validada" if estado == "VALIDADA" else "Checada rechazada: ya no cuenta como asistencia")


# ---- Turnos (asignaciones) ----


@reloj_bp.route("/asignaciones", methods=["GET"])
def listar_asignaciones():
    allowed, response = require_permission("RELOJ_ASISTENCIA", "VER")
    if not allowed:
        return response

    desde, hasta, error = _rango(request.args, 31)
    if error:
        return error_response(error, 400)
    emps = {e["USUARIOID"]: e for e in _empleados(request.args.get("sucursal"), request.args.get("usuario"), request.args.get("q"))}
    if not emps:
        return ok_response([])

    filas = _fetch_por_ids(
        f'''SELECT A.*, UB."NOMBRE" AS "UBICACION_NOMBRE", CU."NOMBRE" AS "CREADO_POR_NOMBRE"
            FROM {_t("RC_ASIGNACIONES")} A
            LEFT JOIN {_t("RC_UBICACIONES")} UB ON A."UBICACIONID" = UB."UBICACIONID"
            LEFT JOIN {_t("USUARIOS")} CU ON A."CREADO_POR" = CU."USUARIOID"
            WHERE A."USUARIOID" IN ({{ids}}) AND A."ACTIVO" = 1
              AND A."FECHA_INICIO" <= ? AND A."FECHA_FIN" >= ?''',
        list(emps), params_despues=[hasta, desde],
    )
    for f in filas:
        f["NOMBRE"] = emps[f["USUARIOID"]]["NOMBRE"]
        f["SUCURSAL_CLAVE"] = emps[f["USUARIOID"]]["SUCURSAL_CLAVE"]
    filas.sort(key=lambda f: (f["FECHA_INICIO"], f["NOMBRE"]))
    return ok_response(_sl(filas))


@reloj_bp.route("/asignaciones", methods=["POST"])
def crear_asignacion():
    allowed, response = require_permission("RELOJ_ASISTENCIA", "EDITAR")
    if not allowed:
        return response

    data = request.get_json(silent=True) or {}
    uids = [int(u) for u in data.get("USUARIOIDS") or []]
    inicio = _parse_fecha(data.get("FECHA_INICIO"))
    fin = _parse_fecha(data.get("FECHA_FIN"))
    entrada = (data.get("HORA_ENTRADA") or "").strip()
    salida = (data.get("HORA_SALIDA") or "").strip()
    dias = data.get("DIAS") or "1111111"

    if not uids:
        return error_response("Selecciona al menos un empleado", 400)
    if not inicio or not fin or fin < inicio:
        return error_response("Rango de fechas inválido", 400)
    if (fin - inicio).days > 366:
        return error_response("Un turno no puede durar más de un año", 400)
    if not validar_hora(entrada) or not validar_hora(salida):
        return error_response("Horas inválidas (formato HH:MM)", 400)
    if not validar_dias(dias) or dias == "0000000":
        return error_response("Selecciona los días que se trabajan", 400)
    try:
        tolerancia = int(data.get("TOLERANCIA_MIN", 15))
    except (TypeError, ValueError):
        return error_response("Tolerancia inválida", 400)
    if not 0 <= tolerancia <= 120:
        return error_response("La tolerancia debe estar entre 0 y 120 minutos", 400)

    visibles = {e["USUARIOID"] for e in _empleados()}
    if any(u not in visibles for u in uids):
        return error_response("Hay empleados fuera de tus sucursales", 403)

    siguiente = get_next_id("RC_ASIGNACIONES", "ASIGNACIONID")
    ahora = datetime.now()
    operaciones = []
    for i, u in enumerate(uids):
        operaciones.append((
            f'''INSERT INTO {_t("RC_ASIGNACIONES")}
                ("ASIGNACIONID", "USUARIOID", "FECHA_INICIO", "FECHA_FIN", "HORA_ENTRADA",
                 "HORA_SALIDA", "TOLERANCIA_MIN", "DIAS", "UBICACIONID", "COMENTARIO",
                 "CREADO_POR", "FECHA_CREACION", "ACTIVO")
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)''',
            [siguiente + i, u, inicio, fin, entrada[:5], salida[:5], tolerancia, dias,
             data.get("UBICACIONID") or None, (data.get("COMENTARIO") or "").strip()[:500] or None,
             current_user_id(), ahora],
        ))
    execute_many(operaciones)
    return ok_response(None, f"Turno asignado a {len(uids)} empleado(s)", 201)


@reloj_bp.route("/asignaciones/<int:asignacionid>", methods=["DELETE"])
def eliminar_asignacion(asignacionid):
    allowed, response = require_permission("RELOJ_ASISTENCIA", "EDITAR")
    if not allowed:
        return response

    a = fetch_one(f'SELECT "USUARIOID" FROM {_t("RC_ASIGNACIONES")} WHERE "ASIGNACIONID" = ?', [asignacionid])
    if not a or not _empleado_visible(a["USUARIOID"]):
        return error_response("Turno no encontrado", 404)
    execute_query(f'UPDATE {_t("RC_ASIGNACIONES")} SET "ACTIVO" = 0 WHERE "ASIGNACIONID" = ?', [asignacionid])
    return ok_response(None, "Turno eliminado")


# ---- Incidencias ----


@reloj_bp.route("/incidencias/tipos", methods=["GET"])
def tipos_incidencia():
    valid, response = validate_active_session()
    if not valid:
        return response
    return ok_response([{"CLAVE": k, "NOMBRE": v} for k, v in TIPOS_INCIDENCIA.items()])


@reloj_bp.route("/incidencias", methods=["GET"])
def listar_incidencias():
    allowed, response = require_permission("RELOJ_ASISTENCIA", "VER")
    if not allowed:
        return response

    desde, hasta, error = _rango(request.args, 31)
    if error:
        return error_response(error, 400)
    emps = {e["USUARIOID"]: e for e in _empleados(request.args.get("sucursal"), request.args.get("usuario"), request.args.get("q"))}
    if not emps:
        return ok_response([])

    filas = _fetch_por_ids(
        f'''SELECT I."INCIDENCIAID", I."USUARIOID", I."TIPO", I."FECHA_INICIO", I."FECHA_FIN",
                   I."FOLIO", I."COMENTARIO", I."ARCHIVO_NOMBRE", I."FECHA_CREACION",
                   CU."NOMBRE" AS "CREADO_POR_NOMBRE"
            FROM {_t("RC_INCIDENCIAS")} I
            LEFT JOIN {_t("USUARIOS")} CU ON I."CREADO_POR" = CU."USUARIOID"
            WHERE I."USUARIOID" IN ({{ids}}) AND I."ACTIVO" = 1
              AND I."FECHA_INICIO" <= ? AND I."FECHA_FIN" >= ?''',
        list(emps), params_despues=[hasta, desde],
    )
    for f in filas:
        f["NOMBRE"] = emps[f["USUARIOID"]]["NOMBRE"]
        f["SUCURSAL_CLAVE"] = emps[f["USUARIOID"]]["SUCURSAL_CLAVE"]
        f["TIPO_NOMBRE"] = TIPOS_INCIDENCIA.get(f["TIPO"], f["TIPO"])
        f["TIENE_ARCHIVO"] = bool(f.pop("ARCHIVO_NOMBRE", None))
    filas.sort(key=lambda f: f["FECHA_INICIO"], reverse=True)
    return ok_response(_sl(filas))


@reloj_bp.route("/incidencias", methods=["POST"])
def crear_incidencia():
    allowed, response = require_permission("RELOJ_ASISTENCIA", "EDITAR")
    if not allowed:
        return response

    form = request.form
    try:
        uid = int(form.get("USUARIOID"))
    except (TypeError, ValueError):
        return error_response("Selecciona el empleado", 400)
    tipo = (form.get("TIPO") or "").upper()
    inicio = _parse_fecha(form.get("FECHA_INICIO"))
    fin = _parse_fecha(form.get("FECHA_FIN")) or inicio
    comentario = (form.get("COMENTARIO") or "").strip()[:500]

    if tipo not in TIPOS_INCIDENCIA:
        return error_response("Tipo de incidencia inválido", 400)
    if not inicio or fin < inicio:
        return error_response("Rango de fechas inválido", 400)
    if (fin - inicio).days > 366:
        return error_response("El rango no puede ser mayor a un año", 400)
    if not comentario:
        return error_response("Agrega un comentario", 400)
    if not _empleado_visible(uid):
        return error_response("Empleado fuera de tus sucursales", 403)

    archivo, archivo_nombre = None, None
    adjunto = request.files.get("archivo")
    if adjunto and adjunto.filename:
        ext = adjunto.filename.rsplit(".", 1)[-1].lower() if "." in adjunto.filename else ""
        if ext not in EXT_INCIDENCIA:
            return error_response("El comprobante debe ser PDF o imagen", 400)
        contenido = adjunto.read(MAX_ARCHIVO_BYTES + 1)
        if len(contenido) > MAX_ARCHIVO_BYTES:
            return error_response("El comprobante no puede pesar más de 10 MB", 400)
        os.makedirs(INCIDENCIAS_DIR, exist_ok=True)
        archivo = f"{uuid.uuid4().hex}.{ext}"
        archivo_nombre = secure_filename(adjunto.filename)[:255] or archivo
        with open(os.path.join(INCIDENCIAS_DIR, archivo), "wb") as f:
            f.write(contenido)

    execute_query(
        f'''INSERT INTO {_t("RC_INCIDENCIAS")}
            ("INCIDENCIAID", "USUARIOID", "TIPO", "FECHA_INICIO", "FECHA_FIN", "FOLIO",
             "COMENTARIO", "ARCHIVO", "ARCHIVO_NOMBRE", "CREADO_POR", "FECHA_CREACION", "ACTIVO")
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)''',
        [get_next_id("RC_INCIDENCIAS", "INCIDENCIAID"), uid, tipo, inicio, fin,
         (form.get("FOLIO") or "").strip()[:50] or None, comentario, archivo, archivo_nombre,
         current_user_id(), datetime.now()],
    )
    return ok_response(None, "Incidencia registrada", 201)


@reloj_bp.route("/incidencias/<int:incidenciaid>", methods=["DELETE"])
def cancelar_incidencia(incidenciaid):
    allowed, response = require_permission("RELOJ_ASISTENCIA", "EDITAR")
    if not allowed:
        return response

    i = fetch_one(f'SELECT "USUARIOID" FROM {_t("RC_INCIDENCIAS")} WHERE "INCIDENCIAID" = ? AND "ACTIVO" = 1', [incidenciaid])
    if not i or not _empleado_visible(i["USUARIOID"]):
        return error_response("Incidencia no encontrada", 404)
    execute_query(
        f'''UPDATE {_t("RC_INCIDENCIAS")} SET "ACTIVO" = 0, "CANCELADO_POR" = ?, "FECHA_CANCELACION" = ?
            WHERE "INCIDENCIAID" = ?''',
        [current_user_id(), datetime.now(), incidenciaid],
    )
    return ok_response(None, "Incidencia cancelada")


@reloj_bp.route("/incidencias/<int:incidenciaid>/archivo", methods=["GET"])
def archivo_incidencia(incidenciaid):
    allowed, response = require_permission("RELOJ_ASISTENCIA", "VER")
    if not allowed:
        return response

    i = fetch_one(
        f'SELECT "USUARIOID", "ARCHIVO", "ARCHIVO_NOMBRE" FROM {_t("RC_INCIDENCIAS")} WHERE "INCIDENCIAID" = ?',
        [incidenciaid],
    )
    if not i or not i["ARCHIVO"] or not _empleado_visible(i["USUARIOID"]):
        return error_response("Comprobante no encontrado", 404)
    return send_from_directory(INCIDENCIAS_DIR, i["ARCHIVO"], download_name=i["ARCHIVO_NOMBRE"])


# ---- Tiempo extra ----


@reloj_bp.route("/horas-extra", methods=["GET"])
def listar_horas_extra():
    allowed, response = require_permission("RELOJ_ASISTENCIA", "VER")
    if not allowed:
        return response

    desde, hasta, error = _rango(request.args, 14)
    if error:
        return error_response(error, 400)

    emps = _empleados(request.args.get("sucursal"), request.args.get("usuario"), request.args.get("q"))
    ctx, horarios, festivos = _contexto([e["USUARIOID"] for e in emps], desde, hasta)
    dias = _dias(desde, hasta)
    ahora = datetime.now()

    nombres = {
        r["USUARIOID"]: r["NOMBRE"]
        for r in fetch_all(f'SELECT "USUARIOID", "NOMBRE" FROM {_t("USUARIOS")} WHERE "USUARIOID" IN (SELECT DISTINCT "AUTORIZO" FROM {_t("RC_HORAS_EXTRA")})')
    }

    filas = []
    for e in emps:
        c = ctx[e["USUARIOID"]]
        for d in _evaluar_empleado(e, c, horarios, festivos, dias, ahora):
            registro = c["extra"].get(date.fromisoformat(d["FECHA"]))
            if not d["MINUTOS_EXTRA"] and not registro:
                continue
            filas.append({
                "USUARIOID": e["USUARIOID"],
                "NOMBRE": e["NOMBRE"],
                "NUMERO_EMPLEADO": e["NUMERO_EMPLEADO"],
                "SUCURSAL_CLAVE": e["SUCURSAL_CLAVE"],
                "FECHA": d["FECHA"],
                "ESTADO_DIA": d["ESTADO"],
                "HORARIO": d["HORARIO"],
                "ENTRADA": d["ENTRADA"],
                "SALIDA": d["SALIDA"],
                "MINUTOS_CALCULADOS": d["MINUTOS_EXTRA"],
                "ESTADO": registro["ESTADO"] if registro else "PENDIENTE",
                "MINUTOS_AUTORIZADOS": registro["MINUTOS_AUTORIZADOS"] if registro else None,
                "COMENTARIO": registro["COMENTARIO"] if registro else None,
                "AUTORIZO_NOMBRE": nombres.get(registro["AUTORIZO"]) if registro else None,
                "FECHA_AUTORIZACION": registro["FECHA_AUTORIZACION"].isoformat() if registro else None,
            })
    filas.sort(key=lambda f: (f["ESTADO"] != "PENDIENTE", f["FECHA"], f["NOMBRE"]))
    return ok_response(filas)


@reloj_bp.route("/horas-extra", methods=["POST"])
def decidir_horas_extra():
    allowed, response = require_permission("RELOJ_ASISTENCIA", "APROBAR")
    if not allowed:
        return response

    data = request.get_json(silent=True) or {}
    try:
        uid = int(data.get("USUARIOID"))
    except (TypeError, ValueError):
        return error_response("Empleado inválido", 400)
    dia = _parse_fecha(data.get("FECHA"))
    estado = (data.get("ESTADO") or "").upper()
    comentario = (data.get("COMENTARIO") or "").strip()[:500]

    if not dia:
        return error_response("Fecha inválida", 400)
    if estado not in ("AUTORIZADA", "RECHAZADA"):
        return error_response("Estado inválido", 400)
    if uid == current_user_id():
        return error_response("No puedes autorizar tu propio tiempo extra", 403)

    emps = _empleados(usuario=uid, incluir_inactivos=True)
    if not emps:
        return error_response("Empleado fuera de tus sucursales", 403)

    ctx, horarios, festivos = _contexto([uid], dia, dia)
    calculado = _evaluar_empleado(emps[0], ctx[uid], horarios, festivos, [dia], datetime.now())[0]["MINUTOS_EXTRA"]

    if estado == "AUTORIZADA":
        try:
            minutos = int(data.get("MINUTOS_AUTORIZADOS", calculado))
        except (TypeError, ValueError):
            return error_response("Minutos inválidos", 400)
        if minutos <= 0:
            return error_response("Los minutos autorizados deben ser mayores a cero", 400)
        if minutos > calculado:
            return error_response(f"No se pueden autorizar más de los {calculado} minutos registrados", 400)
    else:
        minutos = 0
        if len(comentario) < 5:
            return error_response("Indica el motivo del rechazo", 400)

    existente = ctx[uid]["extra"].get(dia)
    execute_query(
        f'''UPSERT {_t("RC_HORAS_EXTRA")}
            ("HORAEXTRAID", "USUARIOID", "FECHA", "MINUTOS_CALCULADOS", "MINUTOS_AUTORIZADOS",
             "ESTADO", "COMENTARIO", "AUTORIZO", "FECHA_AUTORIZACION")
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) WITH PRIMARY KEY''',
        [existente["HORAEXTRAID"] if existente else get_next_id("RC_HORAS_EXTRA", "HORAEXTRAID"),
         uid, dia, calculado, minutos, estado, comentario or None, current_user_id(), datetime.now()],
    )
    return ok_response(None, "Tiempo extra autorizado" if estado == "AUTORIZADA" else "Tiempo extra rechazado")


# ---- Dispositivos ----


@reloj_bp.route("/empleados/<int:usuarioid>/dispositivos", methods=["GET"])
def dispositivos_empleado(usuarioid):
    allowed, response = require_permission("RELOJ_ASISTENCIA", "VER")
    if not allowed:
        return response
    if not _empleado_visible(usuarioid):
        return error_response("Empleado fuera de tus sucursales", 403)

    filas = fetch_all(
        f'''SELECT D."DISPOSITIVOID", D."DESCRIPCION", D."ESTADO", D."FECHA_REGISTRO", D."ULTIMO_USO",
                   D."FECHA_AUTORIZACION", AU."NOMBRE" AS "AUTORIZO_NOMBRE",
                   (SELECT STRING_AGG(U2."NOMBRE", ', ') FROM {_t("RC_DISPOSITIVOS")} D2
                      JOIN {_t("USUARIOS")} U2 ON D2."USUARIOID" = U2."USUARIOID"
                     WHERE D2."TOKEN_HASH" = D."TOKEN_HASH" AND D2."USUARIOID" <> D."USUARIOID") AS "COMPARTIDO_CON"
            FROM {_t("RC_DISPOSITIVOS")} D
            LEFT JOIN {_t("USUARIOS")} AU ON D."AUTORIZO" = AU."USUARIOID"
            WHERE D."USUARIOID" = ?
            ORDER BY D."FECHA_REGISTRO" DESC''',
        [usuarioid],
    )
    return ok_response(_sl(filas))


@reloj_bp.route("/dispositivos/<int:dispositivoid>/<accion>", methods=["POST"])
def cambiar_dispositivo(dispositivoid, accion):
    allowed, response = require_permission("RELOJ_ASISTENCIA", "EDITAR")
    if not allowed:
        return response
    if accion not in ("autorizar", "revocar"):
        return error_response("Acción inválida", 400)

    d = fetch_one(f'SELECT * FROM {_t("RC_DISPOSITIVOS")} WHERE "DISPOSITIVOID" = ?', [dispositivoid])
    if not d or not _empleado_visible(d["USUARIOID"]):
        return error_response("Dispositivo no encontrado", 404)

    ahora = datetime.now()
    if accion == "autorizar":
        # Un solo celular activo por empleado: el anterior queda revocado
        execute_many([
            (f'''UPDATE {_t("RC_DISPOSITIVOS")} SET "ESTADO" = 'REVOCADO'
                 WHERE "USUARIOID" = ? AND "ESTADO" = 'ACTIVO' AND "DISPOSITIVOID" <> ?''',
             [d["USUARIOID"], dispositivoid]),
            (f'''UPDATE {_t("RC_DISPOSITIVOS")} SET "ESTADO" = 'ACTIVO', "AUTORIZO" = ?, "FECHA_AUTORIZACION" = ?
                 WHERE "DISPOSITIVOID" = ?''',
             [current_user_id(), ahora, dispositivoid]),
        ])
        return ok_response(None, "Celular autorizado; el anterior quedó revocado")

    execute_query(
        f'''UPDATE {_t("RC_DISPOSITIVOS")} SET "ESTADO" = 'REVOCADO', "AUTORIZO" = ?, "FECHA_AUTORIZACION" = ?
            WHERE "DISPOSITIVOID" = ?''',
        [current_user_id(), ahora, dispositivoid],
    )
    return ok_response(None, "Celular revocado")


# --------------------------------------------------------------------
# Gratificaciones
# --------------------------------------------------------------------


@reloj_bp.route("/gratificaciones", methods=["GET"])
def listar_gratificaciones():
    allowed, response = require_permission("RELOJ_GRATIFICACIONES", "VER")
    if not allowed:
        return response

    desde, hasta, error = _rango(request.args, 31)
    if error:
        return error_response(error, 400)
    emps = {e["USUARIOID"]: e for e in _empleados(request.args.get("sucursal"), request.args.get("usuario"), request.args.get("q"), incluir_inactivos=True)}
    if not emps:
        return ok_response([])

    filas = _fetch_por_ids(
        f'''SELECT G."GRATIFICACIONID", G."USUARIOID", G."FECHA", G."MONTO", G."JUSTIFICACION",
                   G."FECHA_CREACION", CU."NOMBRE" AS "CREADO_POR_NOMBRE"
            FROM {_t("RC_GRATIFICACIONES")} G
            LEFT JOIN {_t("USUARIOS")} CU ON G."CREADO_POR" = CU."USUARIOID"
            WHERE G."USUARIOID" IN ({{ids}}) AND G."ACTIVO" = 1 AND G."FECHA" BETWEEN ? AND ?''',
        list(emps), params_despues=[desde, hasta],
    )
    for f in filas:
        f["NOMBRE"] = emps[f["USUARIOID"]]["NOMBRE"]
        f["NUMERO_EMPLEADO"] = emps[f["USUARIOID"]]["NUMERO_EMPLEADO"]
        f["SUCURSAL_CLAVE"] = emps[f["USUARIOID"]]["SUCURSAL_CLAVE"]
    filas.sort(key=lambda f: f["FECHA"], reverse=True)
    return ok_response(_sl(filas))


@reloj_bp.route("/gratificaciones", methods=["POST"])
def crear_gratificacion():
    allowed, response = require_permission("RELOJ_GRATIFICACIONES", "CREAR")
    if not allowed:
        return response

    data = request.get_json(silent=True) or {}
    try:
        uid = int(data.get("USUARIOID"))
    except (TypeError, ValueError):
        return error_response("Selecciona el empleado", 400)
    dia = _parse_fecha(data.get("FECHA")) or date.today()
    justificacion = (data.get("JUSTIFICACION") or "").strip()
    monto = data.get("MONTO")

    if len(justificacion) < 20:
        return error_response("Justifica la gratificación (mínimo 20 caracteres)", 400)
    if monto not in (None, ""):
        try:
            monto = round(float(monto), 2)
        except (TypeError, ValueError):
            return error_response("Monto inválido", 400)
        if monto <= 0:
            return error_response("El monto debe ser mayor a cero", 400)
    else:
        monto = None
    if uid == current_user_id():
        return error_response("No puedes registrarte una gratificación a ti mismo", 403)
    if not _empleado_visible(uid):
        return error_response("Empleado fuera de tus sucursales", 403)

    execute_query(
        f'''INSERT INTO {_t("RC_GRATIFICACIONES")}
            ("GRATIFICACIONID", "USUARIOID", "FECHA", "MONTO", "JUSTIFICACION", "CREADO_POR",
             "FECHA_CREACION", "ACTIVO")
            VALUES (?, ?, ?, ?, ?, ?, ?, 1)''',
        [get_next_id("RC_GRATIFICACIONES", "GRATIFICACIONID"), uid, dia, monto,
         justificacion[:1000], current_user_id(), datetime.now()],
    )
    return ok_response(None, "Gratificación registrada", 201)


@reloj_bp.route("/gratificaciones/<int:gratificacionid>", methods=["DELETE"])
def cancelar_gratificacion(gratificacionid):
    allowed, response = require_permission("RELOJ_GRATIFICACIONES", "ELIMINAR")
    if not allowed:
        return response

    g = fetch_one(f'SELECT "USUARIOID" FROM {_t("RC_GRATIFICACIONES")} WHERE "GRATIFICACIONID" = ? AND "ACTIVO" = 1', [gratificacionid])
    if not g or not _empleado_visible(g["USUARIOID"]):
        return error_response("Gratificación no encontrada", 404)
    execute_query(
        f'''UPDATE {_t("RC_GRATIFICACIONES")} SET "ACTIVO" = 0, "CANCELADO_POR" = ?, "FECHA_CANCELACION" = ?
            WHERE "GRATIFICACIONID" = ?''',
        [current_user_id(), datetime.now(), gratificacionid],
    )
    return ok_response(None, "Gratificación cancelada")


# --------------------------------------------------------------------
# Configuración: horarios, ubicaciones, festivos, REV por sucursal
# --------------------------------------------------------------------

LECTURA_CATALOGOS = (("RELOJ_ASISTENCIA", "VER"), ("RELOJ_CONFIG", "VER"))


def _datos_horario(data):
    clave = (data.get("CLAVE") or "").strip().upper()[:30]
    nombre = (data.get("NOMBRE") or "").strip()[:100]
    entrada = (data.get("HORA_ENTRADA") or "").strip()[:5]
    salida = (data.get("HORA_SALIDA") or "").strip()[:5]
    dias = data.get("DIAS") or "1111100"
    try:
        tolerancia = int(data.get("TOLERANCIA_MIN", 15))
    except (TypeError, ValueError):
        tolerancia = -1
    if not clave or not nombre:
        return None, "Clave y nombre son obligatorios"
    if not validar_hora(entrada) or not validar_hora(salida):
        return None, "Horas inválidas (formato HH:MM)"
    if not validar_dias(dias) or dias == "0000000":
        return None, "Selecciona los días que se trabajan"
    if not 0 <= tolerancia <= 120:
        return None, "La tolerancia debe estar entre 0 y 120 minutos"
    return [clave, nombre, entrada, tolerancia, salida, dias, 1 if data.get("ACTIVO", 1) else 0], None


@reloj_bp.route("/horarios", methods=["GET"])
def listar_horarios():
    allowed, response = _requiere_alguno(*LECTURA_CATALOGOS)
    if not allowed:
        return response
    return ok_response(fetch_all(f'SELECT * FROM {_t("RC_HORARIOS")} ORDER BY "HORARIOID"'))


@reloj_bp.route("/horarios", methods=["POST"])
@reloj_bp.route("/horarios/<int:horarioid>", methods=["PUT"])
def guardar_horario(horarioid=None):
    allowed, response = require_permission("RELOJ_CONFIG", "EDITAR")
    if not allowed:
        return response

    valores, error = _datos_horario(request.get_json(silent=True) or {})
    if error:
        return error_response(error, 400)

    if horarioid is None:
        execute_query(
            f'''INSERT INTO {_t("RC_HORARIOS")}
                ("HORARIOID", "CLAVE", "NOMBRE", "HORA_ENTRADA", "TOLERANCIA_MIN", "HORA_SALIDA", "DIAS", "ACTIVO")
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
            [get_next_id("RC_HORARIOS", "HORARIOID")] + valores,
        )
        return ok_response(None, "Horario creado", 201)

    execute_query(
        f'''UPDATE {_t("RC_HORARIOS")} SET "CLAVE" = ?, "NOMBRE" = ?, "HORA_ENTRADA" = ?,
                "TOLERANCIA_MIN" = ?, "HORA_SALIDA" = ?, "DIAS" = ?, "ACTIVO" = ?
            WHERE "HORARIOID" = ?''',
        valores + [horarioid],
    )
    return ok_response(None, "Horario actualizado")


@reloj_bp.route("/ubicaciones", methods=["GET"])
def listar_ubicaciones():
    allowed, response = _requiere_alguno(*LECTURA_CATALOGOS)
    if not allowed:
        return response
    return ok_response(fetch_all(
        f'''SELECT UB.*, SUC."CLAVE" AS "SUCURSAL_CLAVE", SUC."NOMBRE" AS "SUCURSAL_NOMBRE"
            FROM {_t("RC_UBICACIONES")} UB
            LEFT JOIN {_t("SUCURSALES")} SUC ON UB."SUCURSALID" = SUC."SUCURSALID"
            ORDER BY UB."TIPO", UB."NOMBRE"'''
    ))


@reloj_bp.route("/ubicaciones", methods=["POST"])
@reloj_bp.route("/ubicaciones/<int:ubicacionid>", methods=["PUT"])
def guardar_ubicacion(ubicacionid=None):
    allowed, response = require_permission("RELOJ_CONFIG", "EDITAR")
    if not allowed:
        return response

    data = request.get_json(silent=True) or {}
    nombre = (data.get("NOMBRE") or "").strip()[:150]
    tipo = (data.get("TIPO") or "").upper()
    try:
        lat = round(float(data.get("LATITUD")), 7)
        lng = round(float(data.get("LONGITUD")), 7)
        radio = int(data.get("RADIO_M", 150))
    except (TypeError, ValueError):
        return error_response("Latitud, longitud y radio deben ser números", 400)

    if not nombre:
        return error_response("El nombre es obligatorio", 400)
    if tipo not in TIPOS_UBICACION:
        return error_response("Tipo de ubicación inválido", 400)
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return error_response("Coordenadas inválidas", 400)
    if not 30 <= radio <= 5000:
        return error_response("El radio debe estar entre 30 y 5000 metros", 400)

    valores = [nombre, tipo, data.get("SUCURSALID") or None, lat, lng, radio,
               (data.get("DIRECCION") or "").strip()[:300] or None, 1 if data.get("ACTIVO", 1) else 0]

    if ubicacionid is None:
        execute_query(
            f'''INSERT INTO {_t("RC_UBICACIONES")}
                ("UBICACIONID", "NOMBRE", "TIPO", "SUCURSALID", "LATITUD", "LONGITUD", "RADIO_M", "DIRECCION", "ACTIVO")
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            [get_next_id("RC_UBICACIONES", "UBICACIONID")] + valores,
        )
        return ok_response(None, "Ubicación creada", 201)

    execute_query(
        f'''UPDATE {_t("RC_UBICACIONES")} SET "NOMBRE" = ?, "TIPO" = ?, "SUCURSALID" = ?, "LATITUD" = ?,
                "LONGITUD" = ?, "RADIO_M" = ?, "DIRECCION" = ?, "ACTIVO" = ?
            WHERE "UBICACIONID" = ?''',
        valores + [ubicacionid],
    )
    return ok_response(None, "Ubicación actualizada")


@reloj_bp.route("/festivos", methods=["GET"])
def listar_festivos():
    allowed, response = _requiere_alguno(*LECTURA_CATALOGOS)
    if not allowed:
        return response
    try:
        anio = int(request.args.get("anio") or date.today().year)
    except ValueError:
        anio = date.today().year
    return ok_response(_sl(fetch_all(
        f'''SELECT F.*, SUC."CLAVE" AS "SUCURSAL_CLAVE"
            FROM {_t("RC_FESTIVOS")} F
            LEFT JOIN {_t("SUCURSALES")} SUC ON F."SUCURSALID" = SUC."SUCURSALID"
            WHERE F."ACTIVO" = 1 AND YEAR(F."FECHA") = ?
            ORDER BY F."FECHA"''',
        [anio],
    )))


@reloj_bp.route("/festivos", methods=["POST"])
def crear_festivo():
    allowed, response = require_permission("RELOJ_CONFIG", "EDITAR")
    if not allowed:
        return response

    data = request.get_json(silent=True) or {}
    dia = _parse_fecha(data.get("FECHA"))
    nombre = (data.get("NOMBRE") or "").strip()[:150]
    if not dia or not nombre:
        return error_response("Fecha y nombre son obligatorios", 400)
    execute_query(
        f'''INSERT INTO {_t("RC_FESTIVOS")} ("FESTIVOID", "FECHA", "NOMBRE", "SUCURSALID", "ACTIVO")
            VALUES (?, ?, ?, ?, 1)''',
        [get_next_id("RC_FESTIVOS", "FESTIVOID"), dia, nombre, data.get("SUCURSALID") or None],
    )
    return ok_response(None, "Día festivo agregado", 201)


@reloj_bp.route("/festivos/<int:festivoid>", methods=["DELETE"])
def eliminar_festivo(festivoid):
    allowed, response = require_permission("RELOJ_CONFIG", "EDITAR")
    if not allowed:
        return response
    execute_query(f'UPDATE {_t("RC_FESTIVOS")} SET "ACTIVO" = 0 WHERE "FESTIVOID" = ?', [festivoid])
    return ok_response(None, "Día festivo eliminado")


@reloj_bp.route("/revs", methods=["GET"])
def listar_revs():
    allowed, response = require_permission("RELOJ_CONFIG", "VER")
    if not allowed:
        return response

    filas = fetch_all(
        f'''SELECT R."USUARIOID", U."NOMBRE", U."NUMERO_EMPLEADO", R."SUCURSALID", SUC."CLAVE" AS "SUCURSAL_CLAVE"
            FROM {_t("RC_REV_SUCURSALES")} R
            JOIN {_t("USUARIOS")} U ON R."USUARIOID" = U."USUARIOID"
            LEFT JOIN {_t("SUCURSALES")} SUC ON R."SUCURSALID" = SUC."SUCURSALID"
            ORDER BY U."NOMBRE", SUC."CLAVE"'''
    )
    revs = {}
    for f in filas:
        r = revs.setdefault(f["USUARIOID"], {
            "USUARIOID": f["USUARIOID"], "NOMBRE": f["NOMBRE"],
            "NUMERO_EMPLEADO": f["NUMERO_EMPLEADO"], "SUCURSALES": [], "SUCURSALES_CLAVE": [],
        })
        r["SUCURSALES"].append(f["SUCURSALID"])
        r["SUCURSALES_CLAVE"].append(f["SUCURSAL_CLAVE"] or f["SUCURSALID"])
    return ok_response(list(revs.values()))


@reloj_bp.route("/revs/<int:usuarioid>", methods=["PUT"])
def guardar_rev(usuarioid):
    allowed, response = require_permission("RELOJ_CONFIG", "EDITAR")
    if not allowed:
        return response

    if not fetch_one(f'SELECT 1 AS X FROM {_t("USUARIOS")} WHERE "USUARIOID" = ?', [usuarioid]):
        return error_response("Usuario no encontrado", 404)

    sucursales = [str(s) for s in (request.get_json(silent=True) or {}).get("SUCURSALES") or []]
    operaciones = [(f'DELETE FROM {_t("RC_REV_SUCURSALES")} WHERE "USUARIOID" = ?', [usuarioid])]
    for s in dict.fromkeys(sucursales):
        operaciones.append((
            f'INSERT INTO {_t("RC_REV_SUCURSALES")} ("USUARIOID", "SUCURSALID") VALUES (?, ?)',
            [usuarioid, s],
        ))
    execute_many(operaciones)
    return ok_response(None, "Sucursales del REV actualizadas" if sucursales else "REV sin sucursales asignadas")


@reloj_bp.route("/usuarios", methods=["GET"])
def buscar_usuarios():
    """Todos los usuarios activos, para elegir a un REV"""
    allowed, response = require_permission("RELOJ_CONFIG", "VER")
    if not allowed:
        return response
    return ok_response(fetch_all(
        f'''SELECT U."USUARIOID", U."NOMBRE", U."NUMERO_EMPLEADO", SUC."CLAVE" AS "SUCURSAL_CLAVE"
            FROM {_t("USUARIOS")} U
            LEFT JOIN {_t("SUCURSALES")} SUC ON U."SUCURSAL" = SUC."SUCURSALID"
            WHERE U."ACTIVO" = 1 ORDER BY U."NOMBRE"'''
    ))


@reloj_bp.route("/integridad", methods=["GET"])
def verificar_integridad():
    """Recalcula la cadena de hashes (y el hash de cada foto) para detectar alteraciones"""
    allowed, response = require_permission("RELOJ_CONFIG", "VER")
    if not allowed:
        return response

    desde, hasta, error = _rango(request.args, 31)
    if error:
        return error_response(error, 400)

    where, params = "", []
    if request.args.get("usuario"):
        where, params = ' AND "USUARIOID" = ?', [int(request.args["usuario"])]

    # Se verifica la cadena completa de cada empleado con checadas en el rango
    uids = [
        r["USUARIOID"] for r in fetch_all(
            f'''SELECT DISTINCT "USUARIOID" FROM {_t("RC_CHECADAS")}
                WHERE "FECHA_LABORAL" BETWEEN ? AND ? {where}''',
            [desde, hasta] + params,
        )
    ]
    revisadas, problemas = 0, []
    for uid in uids:
        anterior = None
        for c in fetch_all(
            f'SELECT * FROM {_t("RC_CHECADAS")} WHERE "USUARIOID" = ? ORDER BY "CHECADAID"', [uid]
        ):
            revisadas += 1
            motivo = []
            if (c["HASH_ANTERIOR"] or None) != anterior:
                motivo.append("falta o se alteró la checada anterior")
            if _hash_checada(c, c["HASH_ANTERIOR"]) != c["HASH"]:
                motivo.append("los datos de la checada fueron modificados")
            ruta = os.path.join(FOTOS_DIR, *c["FOTO"].split("/"))
            if not os.path.exists(ruta):
                motivo.append("la foto no existe")
            elif _sha256_archivo(ruta) != c["FOTO_SHA256"]:
                motivo.append("la foto fue reemplazada")
            if motivo:
                problemas.append({
                    "CHECADAID": c["CHECADAID"], "USUARIOID": uid,
                    "FECHA_HORA": c["FECHA_HORA"].isoformat(), "MOTIVO": "; ".join(motivo),
                })
            anterior = c["HASH"]

    return ok_response({"REVISADAS": revisadas, "EMPLEADOS": len(uids), "PROBLEMAS": problemas})
