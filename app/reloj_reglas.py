"""
Reglas del Reloj Checador: qué horario aplica a un empleado en un día y en qué estado
queda ese día (asistencia, retardo, falta, incapacidad, ...). Funciones puras, sin base
de datos, para poder probarlas con casos concretos.
"""
import math
import os
from datetime import date, datetime, time, timedelta

# Tiempo extra por debajo de esto no se propone al REV
MIN_TIEMPO_EXTRA = int(os.getenv("RELOJ_MIN_TIEMPO_EXTRA", "30"))
# Horas después de la salida programada en las que todavía se espera la checada de salida
HORAS_GRACIA_SALIDA = int(os.getenv("RELOJ_HORAS_GRACIA_SALIDA", "6"))

TIPOS_INCIDENCIA = {
    "INCAPACIDAD": "Incapacidad",
    "VACACIONES": "Vacaciones",
    "PERMISO_CON_GOCE": "Permiso con goce",
    "PERMISO_SIN_GOCE": "Permiso sin goce",
    "COMISION": "Comisión / trabajo fuera",
    "FALTA_JUSTIFICADA": "Falta justificada",
    "RETARDO_JUSTIFICADO": "Retardo justificado",
}
# Estas cubren el día entero; RETARDO_JUSTIFICADO solo perdona el retardo
INCIDENCIAS_DIA_COMPLETO = set(TIPOS_INCIDENCIA) - {"RETARDO_JUSTIFICADO"}


def parse_hora(valor):
    """'07:30' -> time(7, 30)"""
    horas, minutos = str(valor).strip().split(":")[:2]
    return time(int(horas), int(minutos))


def validar_hora(valor):
    try:
        parse_hora(valor)
        return True
    except (ValueError, TypeError):
        return False


def validar_dias(valor):
    return isinstance(valor, str) and len(valor) == 7 and set(valor) <= {"0", "1"}


def trabaja_ese_dia(dias, dia):
    return (dias or "0000000")[dia.weekday()] == "1"


def distancia_m(lat1, lon1, lat2, lon2):
    """Haversine, en metros"""
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def horario_del_dia(dia, horario_base, asignaciones):
    """
    Horario que aplica ese día o None si es descanso.

    Un turno asignado por el REV que cubre la fecha manda sobre el horario fijo, aunque ese
    día de la semana no se trabaje en el turno (así se da descanso entre semana a quien
    trabaja el domingo). Si hay varios, gana el más reciente.
    """
    vigentes = [
        a for a in asignaciones
        if _fecha(a["FECHA_INICIO"]) <= dia <= _fecha(a["FECHA_FIN"])
    ]
    if vigentes:
        a = max(vigentes, key=lambda x: x["ASIGNACIONID"])
        if not trabaja_ese_dia(a["DIAS"], dia):
            return None
        return {
            "ORIGEN": "TURNO",
            "ASIGNACIONID": a["ASIGNACIONID"],
            "HORA_ENTRADA": a["HORA_ENTRADA"],
            "HORA_SALIDA": a["HORA_SALIDA"],
            "TOLERANCIA_MIN": int(a["TOLERANCIA_MIN"]),
            "UBICACIONID": a.get("UBICACIONID"),
            "NOMBRE": a.get("COMENTARIO") or "Turno asignado",
        }

    if horario_base and trabaja_ese_dia(horario_base["DIAS"], dia):
        return {
            "ORIGEN": "HORARIO",
            "HORARIOID": horario_base["HORARIOID"],
            "HORA_ENTRADA": horario_base["HORA_ENTRADA"],
            "HORA_SALIDA": horario_base["HORA_SALIDA"],
            "TOLERANCIA_MIN": int(horario_base["TOLERANCIA_MIN"]),
            "UBICACIONID": None,
            "NOMBRE": horario_base["NOMBRE"],
        }
    return None


def limites_turno(dia, horario):
    """datetime de entrada y de salida programadas; un turno nocturno sale al día siguiente"""
    entrada = datetime.combine(dia, parse_hora(horario["HORA_ENTRADA"]))
    salida = datetime.combine(dia, parse_hora(horario["HORA_SALIDA"]))
    if salida <= entrada:
        salida += timedelta(days=1)
    return entrada, salida


def es_retardo(entrada_real, entrada_programada, tolerancia_min):
    """
    Se compara al minuto: con entrada 7:30 y 15 de tolerancia, 7:45:59 está a tiempo y
    7:46:00 ya es retardo.
    """
    real = entrada_real.replace(second=0, microsecond=0)
    return real > entrada_programada + timedelta(minutes=tolerancia_min)


def _fecha(valor):
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    return date.fromisoformat(str(valor)[:10])


def _minutos(delta):
    return max(0, int(delta.total_seconds() // 60))


def evaluar_dia(dia, horario, checadas, incidencias, festivo, ahora, checa=True):
    """
    Estado de un día para un empleado.

    checadas: las del día laboral, ya sin las que el REV rechazó, con TIPO y FECHA_HORA.
    incidencias: las que cubren el día. festivo: nombre del festivo o None.

    Regla de olvidos: si falta la entrada o la salida el día cuenta como RETARDO; sin
    ninguna checada (ni justificante) es FALTA.
    """
    entradas = sorted(c["FECHA_HORA"] for c in checadas if c["TIPO"] == "ENTRADA")
    salidas = sorted(c["FECHA_HORA"] for c in checadas if c["TIPO"] == "SALIDA")
    entrada = entradas[0] if entradas else None
    salida = salidas[-1] if salidas else None

    resultado = {
        "FECHA": dia.isoformat(),
        "ESTADO": None,
        "DETALLE": None,
        "HORARIO": horario,
        "ENTRADA": entrada.isoformat() if entrada else None,
        "SALIDA": salida.isoformat() if salida else None,
        "MINUTOS_RETARDO": 0,
        "MINUTOS_TRABAJADOS": _minutos(salida - entrada) if entrada and salida and salida > entrada else 0,
        "MINUTOS_EXTRA": 0,
        "SALIDA_ANTICIPADA": False,
        "INCIDENCIA": None,
    }

    completa = next((i for i in incidencias if i["TIPO"] in INCIDENCIAS_DIA_COMPLETO), None)
    retardo_justificado = next(
        (i for i in incidencias if i["TIPO"] == "RETARDO_JUSTIFICADO"), None
    )

    if completa:
        resultado["ESTADO"] = completa["TIPO"]
        resultado["DETALLE"] = completa.get("COMENTARIO")
        resultado["INCIDENCIA"] = completa["INCIDENCIAID"]
        return resultado

    # Días que no se trabajan: festivo o descanso. Si checó, todo lo trabajado es extra.
    if festivo or horario is None or not checa:
        if not checa:
            resultado["ESTADO"] = "NO_CHECA"
        elif festivo:
            resultado["ESTADO"] = "FESTIVO"
            resultado["DETALLE"] = festivo
        else:
            resultado["ESTADO"] = "DESCANSO"
        if checa and resultado["MINUTOS_TRABAJADOS"] >= MIN_TIEMPO_EXTRA:
            resultado["ESTADO"] = f"{resultado['ESTADO']}_TRABAJADO"
            resultado["MINUTOS_EXTRA"] = resultado["MINUTOS_TRABAJADOS"]
        return resultado

    entrada_prog, salida_prog = limites_turno(dia, horario)
    cierre = salida_prog + timedelta(hours=HORAS_GRACIA_SALIDA)

    if entrada_prog > ahora:
        resultado["ESTADO"] = "PROGRAMADO"
        return resultado

    if entrada:
        resultado["MINUTOS_RETARDO"] = _minutos(entrada.replace(second=0, microsecond=0) - entrada_prog)
        tarde = es_retardo(entrada, entrada_prog, horario["TOLERANCIA_MIN"])
    else:
        tarde = False

    if salida:
        if salida < salida_prog:
            resultado["SALIDA_ANTICIPADA"] = True
        extra = _minutos(salida - salida_prog)
        if extra >= MIN_TIEMPO_EXTRA:
            resultado["MINUTOS_EXTRA"] = extra

    if not entrada and not salida:
        if ahora < cierre:
            resultado["ESTADO"] = "PENDIENTE"
        else:
            resultado["ESTADO"] = "FALTA"
        return resultado

    if entrada and not salida and ahora < cierre:
        resultado["ESTADO"] = "EN_CURSO_RETARDO" if tarde else "EN_CURSO"
        return resultado

    if not entrada or not salida:
        estado, detalle = "RETARDO", (
            "Sin checada de entrada" if not entrada else "Sin checada de salida"
        )
    elif tarde:
        estado, detalle = "RETARDO", f"Llegó {resultado['MINUTOS_RETARDO']} min tarde"
    else:
        estado, detalle = "ASISTENCIA", None

    if estado == "RETARDO" and retardo_justificado:
        estado = "ASISTENCIA"
        detalle = f"Retardo justificado: {retardo_justificado.get('COMENTARIO') or ''}".strip()
        resultado["INCIDENCIA"] = retardo_justificado["INCIDENCIAID"]

    resultado["ESTADO"] = estado
    resultado["DETALLE"] = detalle
    return resultado


def fecha_laboral_salida(ahora, entradas_por_dia, horario_de):
    """
    Día laboral al que pertenece una checada de salida: hoy, salvo que ayer se entró a un
    turno nocturno que todavía no tiene salida.

    entradas_por_dia: {date: (tiene_entrada, tiene_salida)}; horario_de: date -> horario o None
    """
    hoy = ahora.date()
    ayer = hoy - timedelta(days=1)
    tiene_entrada_hoy = entradas_por_dia.get(hoy, (False, False))[0]
    entrada_ayer, salida_ayer = entradas_por_dia.get(ayer, (False, False))
    if not tiene_entrada_hoy and entrada_ayer and not salida_ayer:
        horario = horario_de(ayer)
        if horario:
            _, fin = limites_turno(ayer, horario)
            if fin.date() > ayer and ahora <= fin + timedelta(hours=HORAS_GRACIA_SALIDA):
                return ayer
    return hoy
