"""
Crea las tablas del Reloj Checador y da de alta sus módulos en el menú.

Es idempotente: solo crea lo que falta, así que puede correrse de nuevo sin riesgo
(por ejemplo al pasar de QRTEST a otro esquema).

    venv\\Scripts\\python.exe scripts\\reloj_checador_setup.py

Los permisos de los módulos nuevos se dan solo al perfil ADMINISTRADOR; al resto de los
perfiles se les asignan desde la pantalla de Permisos.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import fetch_all, fetch_one, execute_query  # noqa: E402

SCHEMA = os.getenv("HANA_SCHEMA", "QRTEST")
PERFIL_ADMINISTRADOR = 1

TABLAS = {
    # Horarios fijos (matriz / sucursales). DIAS = 7 caracteres lunes..domingo, 1 = se trabaja
    "RC_HORARIOS": '''
        "HORARIOID" INTEGER NOT NULL PRIMARY KEY,
        "CLAVE" NVARCHAR(30) NOT NULL,
        "NOMBRE" NVARCHAR(100) NOT NULL,
        "HORA_ENTRADA" NVARCHAR(5) NOT NULL,
        "TOLERANCIA_MIN" INTEGER NOT NULL DEFAULT 15,
        "HORA_SALIDA" NVARCHAR(5) NOT NULL,
        "DIAS" NVARCHAR(7) NOT NULL DEFAULT '1111100',
        "ACTIVO" TINYINT NOT NULL DEFAULT 1
    ''',
    # Geocercas: matriz, sucursales y pólizas (clientes)
    "RC_UBICACIONES": '''
        "UBICACIONID" INTEGER NOT NULL PRIMARY KEY,
        "NOMBRE" NVARCHAR(150) NOT NULL,
        "TIPO" NVARCHAR(20) NOT NULL,
        "SUCURSALID" NVARCHAR(50),
        "LATITUD" DECIMAL(10,7) NOT NULL,
        "LONGITUD" DECIMAL(10,7) NOT NULL,
        "RADIO_M" INTEGER NOT NULL DEFAULT 150,
        "DIRECCION" NVARCHAR(300),
        "ACTIVO" TINYINT NOT NULL DEFAULT 1
    ''',
    # Horario y lugar habitual de cada empleado
    "RC_EMPLEADOS": '''
        "USUARIOID" INTEGER NOT NULL PRIMARY KEY,
        "HORARIOID" INTEGER,
        "UBICACIONID" INTEGER,
        "CHECA" TINYINT NOT NULL DEFAULT 1,
        "ACTUALIZO" INTEGER,
        "FECHA_ACTUALIZACION" TIMESTAMP
    ''',
    # Turnos que asigna el REV (técnicos de póliza, rotación mañana/tarde, fines de semana)
    "RC_ASIGNACIONES": '''
        "ASIGNACIONID" INTEGER NOT NULL PRIMARY KEY,
        "USUARIOID" INTEGER NOT NULL,
        "FECHA_INICIO" DATE NOT NULL,
        "FECHA_FIN" DATE NOT NULL,
        "HORA_ENTRADA" NVARCHAR(5) NOT NULL,
        "HORA_SALIDA" NVARCHAR(5) NOT NULL,
        "TOLERANCIA_MIN" INTEGER NOT NULL DEFAULT 15,
        "DIAS" NVARCHAR(7) NOT NULL DEFAULT '1111111',
        "UBICACIONID" INTEGER,
        "COMENTARIO" NVARCHAR(500),
        "CREADO_POR" INTEGER,
        "FECHA_CREACION" TIMESTAMP,
        "ACTIVO" TINYINT NOT NULL DEFAULT 1
    ''',
    # Checadas: solo se insertan, nunca se editan ni se borran. HASH encadena cada
    # registro con el anterior del mismo empleado para detectar alteraciones.
    "RC_CHECADAS": '''
        "CHECADAID" BIGINT NOT NULL PRIMARY KEY,
        "USUARIOID" INTEGER NOT NULL,
        "TIPO" NVARCHAR(10) NOT NULL,
        "FECHA_HORA" TIMESTAMP NOT NULL,
        "FECHA_LABORAL" DATE NOT NULL,
        "FECHA_HORA_DISPOSITIVO" TIMESTAMP,
        "LATITUD" DECIMAL(10,7) NOT NULL,
        "LONGITUD" DECIMAL(10,7) NOT NULL,
        "PRECISION_M" INTEGER,
        "UBICACIONID" INTEGER,
        "DISTANCIA_M" INTEGER,
        "FUERA_ZONA" TINYINT NOT NULL DEFAULT 0,
        "FOTO" NVARCHAR(255) NOT NULL,
        "FOTO_SHA256" NVARCHAR(64) NOT NULL,
        "DISPOSITIVOID" INTEGER,
        "BANDERAS" NVARCHAR(300),
        "IP" NVARCHAR(64),
        "USER_AGENT" NVARCHAR(500),
        "HASH_ANTERIOR" NVARCHAR(64),
        "HASH" NVARCHAR(64) NOT NULL
    ''',
    # Revisión del REV sobre una checada con alertas: una checada RECHAZADA no cuenta
    "RC_CHECADAS_REVISION": '''
        "CHECADAID" BIGINT NOT NULL PRIMARY KEY,
        "ESTADO" NVARCHAR(20) NOT NULL,
        "COMENTARIO" NVARCHAR(500),
        "REVISO" INTEGER NOT NULL,
        "FECHA" TIMESTAMP NOT NULL
    ''',
    # Celulares ligados a cada empleado (se guarda solo el hash del identificador)
    "RC_DISPOSITIVOS": '''
        "DISPOSITIVOID" INTEGER NOT NULL PRIMARY KEY,
        "USUARIOID" INTEGER NOT NULL,
        "TOKEN_HASH" NVARCHAR(64) NOT NULL,
        "DESCRIPCION" NVARCHAR(300),
        "ESTADO" NVARCHAR(20) NOT NULL,
        "FECHA_REGISTRO" TIMESTAMP NOT NULL,
        "ULTIMO_USO" TIMESTAMP,
        "AUTORIZO" INTEGER,
        "FECHA_AUTORIZACION" TIMESTAMP
    ''',
    # Incapacidades, vacaciones, permisos, justificaciones
    "RC_INCIDENCIAS": '''
        "INCIDENCIAID" INTEGER NOT NULL PRIMARY KEY,
        "USUARIOID" INTEGER NOT NULL,
        "TIPO" NVARCHAR(30) NOT NULL,
        "FECHA_INICIO" DATE NOT NULL,
        "FECHA_FIN" DATE NOT NULL,
        "FOLIO" NVARCHAR(50),
        "COMENTARIO" NVARCHAR(500),
        "ARCHIVO" NVARCHAR(255),
        "ARCHIVO_NOMBRE" NVARCHAR(255),
        "CREADO_POR" INTEGER,
        "FECHA_CREACION" TIMESTAMP,
        "ACTIVO" TINYINT NOT NULL DEFAULT 1,
        "CANCELADO_POR" INTEGER,
        "FECHA_CANCELACION" TIMESTAMP
    ''',
    # Días festivos; SUCURSALID vacío = aplica a todas
    "RC_FESTIVOS": '''
        "FESTIVOID" INTEGER NOT NULL PRIMARY KEY,
        "FECHA" DATE NOT NULL,
        "NOMBRE" NVARCHAR(150) NOT NULL,
        "SUCURSALID" NVARCHAR(50),
        "ACTIVO" TINYINT NOT NULL DEFAULT 1
    ''',
    # Tiempo extra revisado por el REV (lo pendiente se calcula de las checadas)
    "RC_HORAS_EXTRA": '''
        "HORAEXTRAID" INTEGER NOT NULL PRIMARY KEY,
        "USUARIOID" INTEGER NOT NULL,
        "FECHA" DATE NOT NULL,
        "MINUTOS_CALCULADOS" INTEGER NOT NULL,
        "MINUTOS_AUTORIZADOS" INTEGER NOT NULL,
        "ESTADO" NVARCHAR(20) NOT NULL,
        "COMENTARIO" NVARCHAR(500),
        "AUTORIZO" INTEGER NOT NULL,
        "FECHA_AUTORIZACION" TIMESTAMP NOT NULL
    ''',
    "RC_GRATIFICACIONES": '''
        "GRATIFICACIONID" INTEGER NOT NULL PRIMARY KEY,
        "USUARIOID" INTEGER NOT NULL,
        "FECHA" DATE NOT NULL,
        "MONTO" DECIMAL(12,2),
        "JUSTIFICACION" NVARCHAR(1000) NOT NULL,
        "CREADO_POR" INTEGER NOT NULL,
        "FECHA_CREACION" TIMESTAMP NOT NULL,
        "ACTIVO" TINYINT NOT NULL DEFAULT 1,
        "CANCELADO_POR" INTEGER,
        "FECHA_CANCELACION" TIMESTAMP
    ''',
    # Sucursales que ve cada REV
    "RC_REV_SUCURSALES": '''
        "USUARIOID" INTEGER NOT NULL,
        "SUCURSALID" NVARCHAR(50) NOT NULL,
        PRIMARY KEY ("USUARIOID", "SUCURSALID")
    ''',
}

HORARIOS = [
    (1, "MATRIZ_0730", "Matriz 7:30 a 17:00", "07:30", 15, "17:00", "1111100"),
    (2, "MATRIZ_0800", "Matriz 8:00 a 17:30", "08:00", 15, "17:30", "1111100"),
    (3, "SUCURSAL_0800", "Sucursal 8:00 a 17:30", "08:00", 15, "17:30", "1111100"),
]

# Descanso obligatorio (art. 74 LFT)
FESTIVOS = [
    ("2026-01-01", "Año Nuevo"),
    ("2026-02-02", "Día de la Constitución"),
    ("2026-03-16", "Natalicio de Benito Juárez"),
    ("2026-05-01", "Día del Trabajo"),
    ("2026-09-16", "Día de la Independencia"),
    ("2026-11-16", "Día de la Revolución"),
    ("2026-12-25", "Navidad"),
    ("2027-01-01", "Año Nuevo"),
    ("2027-02-01", "Día de la Constitución"),
    ("2027-03-15", "Natalicio de Benito Juárez"),
    ("2027-05-01", "Día del Trabajo"),
    ("2027-09-16", "Día de la Independencia"),
    ("2027-11-15", "Día de la Revolución"),
    ("2027-12-25", "Navidad"),
]

# (clave, nombre, padre, ruta, orden, acciones)
MODULOS = [
    ("RELOJ", "RELOJ CHECADOR", None, "/reloj", 600, ["VER"]),
    ("RELOJ_CHECAR", "CHECAR", "RELOJ", "/reloj/checar", 601, ["VER"]),
    ("RELOJ_ASISTENCIA", "ASISTENCIA (REV)", "RELOJ", "/reloj/asistencia", 602,
     ["VER", "EDITAR", "APROBAR", "EXPORTAR"]),
    ("RELOJ_GRATIFICACIONES", "GRATIFICACIONES", "RELOJ", "/reloj/gratificaciones", 603,
     ["VER", "CREAR", "ELIMINAR"]),
    ("RELOJ_CONFIG", "CONFIGURACIÓN RELOJ", "RELOJ", "/reloj/configuracion", 604,
     ["VER", "EDITAR"]),
]


def tabla_existe(nombre):
    return fetch_one(
        "SELECT 1 AS X FROM SYS.TABLES WHERE SCHEMA_NAME = ? AND TABLE_NAME = ?",
        [SCHEMA, nombre],
    ) is not None


def crear_tablas():
    for nombre, columnas in TABLAS.items():
        if tabla_existe(nombre):
            print(f"  = {nombre} ya existe")
            continue
        execute_query(f'CREATE COLUMN TABLE "{SCHEMA}"."{nombre}" ({columnas})')
        print(f"  + {nombre}")

    if not fetch_one(
        "SELECT 1 AS X FROM SYS.SEQUENCES WHERE SCHEMA_NAME = ? AND SEQUENCE_NAME = ?",
        [SCHEMA, "RC_CHECADAS_SEQ"],
    ):
        execute_query(f'CREATE SEQUENCE "{SCHEMA}"."RC_CHECADAS_SEQ" START WITH 1')
        print("  + RC_CHECADAS_SEQ")


def sembrar_catalogos():
    for horario in HORARIOS:
        if fetch_one(
            f'SELECT 1 AS X FROM "{SCHEMA}"."RC_HORARIOS" WHERE "CLAVE" = ?', [horario[1]]
        ):
            continue
        execute_query(
            f'''
            INSERT INTO "{SCHEMA}"."RC_HORARIOS"
                ("HORARIOID", "CLAVE", "NOMBRE", "HORA_ENTRADA", "TOLERANCIA_MIN",
                 "HORA_SALIDA", "DIAS", "ACTIVO")
            VALUES (?, ?, ?, ?, ?, ?, ?, 1)
            ''',
            list(horario),
        )
        print(f"  + horario {horario[1]}")

    siguiente = fetch_one(
        f'SELECT COALESCE(MAX("FESTIVOID"), 0) + 1 AS N FROM "{SCHEMA}"."RC_FESTIVOS"'
    )["N"]
    for fecha, nombre in FESTIVOS:
        if fetch_one(
            f'SELECT 1 AS X FROM "{SCHEMA}"."RC_FESTIVOS" WHERE "FECHA" = ? AND "SUCURSALID" IS NULL',
            [fecha],
        ):
            continue
        execute_query(
            f'''
            INSERT INTO "{SCHEMA}"."RC_FESTIVOS" ("FESTIVOID", "FECHA", "NOMBRE", "SUCURSALID", "ACTIVO")
            VALUES (?, ?, ?, NULL, 1)
            ''',
            [siguiente, fecha, nombre],
        )
        siguiente += 1
        print(f"  + festivo {fecha} {nombre}")


def registrar_modulos():
    acciones = {
        r["CLAVE"].upper(): r["ACCIONID"]
        for r in fetch_all(f'SELECT "ACCIONID", "CLAVE" FROM "{SCHEMA}"."ACCIONES"')
    }
    ids = {}

    for clave, nombre, padre, ruta, orden, acciones_modulo in MODULOS:
        row = fetch_one(
            f'SELECT "MODULOID" FROM "{SCHEMA}"."MODULOS" WHERE UPPER("CLAVE") = ?', [clave]
        )
        if row:
            moduloid = row["MODULOID"]
            print(f"  = módulo {clave} ya existe")
        else:
            moduloid = fetch_one(
                f'SELECT COALESCE(MAX("MODULOID"), 0) + 1 AS N FROM "{SCHEMA}"."MODULOS"'
            )["N"]
            execute_query(
                f'''
                INSERT INTO "{SCHEMA}"."MODULOS"
                    ("MODULOID", "NOMBRE", "PADRE_ID", "RUTA", "ORDEN", "ACTIVO", "CLAVE")
                VALUES (?, ?, ?, ?, ?, 1, ?)
                ''',
                [moduloid, nombre, ids.get(padre), ruta, orden, clave],
            )
            print(f"  + módulo {clave} ({moduloid})")
        ids[clave] = moduloid

        for accion in acciones_modulo:
            accionid = acciones[accion]
            if not fetch_one(
                f'SELECT 1 AS X FROM "{SCHEMA}"."MODULO_ACCIONES" WHERE "MODULOID" = ? AND "ACCIONID" = ?',
                [moduloid, accionid],
            ):
                execute_query(
                    f'INSERT INTO "{SCHEMA}"."MODULO_ACCIONES" ("MODULOID", "ACCIONID", "ACTIVO") VALUES (?, ?, 1)',
                    [moduloid, accionid],
                )
            if not fetch_one(
                f'''SELECT 1 AS X FROM "{SCHEMA}"."PERMISOS_PERFIL"
                    WHERE "PERFILID" = ? AND "MODULOID" = ? AND "ACCIONID" = ?''',
                [PERFIL_ADMINISTRADOR, moduloid, accionid],
            ):
                execute_query(
                    f'''INSERT INTO "{SCHEMA}"."PERMISOS_PERFIL"
                        ("PERFILID", "MODULOID", "ACCIONID", "PERMITIDO", "ACTIVO")
                        VALUES (?, ?, ?, 1, 1)''',
                    [PERFIL_ADMINISTRADOR, moduloid, accionid],
                )


if __name__ == "__main__":
    print(f"Esquema {SCHEMA}")
    print("Tablas:")
    crear_tablas()
    print("Catálogos:")
    sembrar_catalogos()
    print("Módulos:")
    registrar_modulos()
    print("Listo.")
