import os
from decimal import Decimal
from dotenv import load_dotenv
from hdbcli import dbapi

load_dotenv()


def get_connection():
    hana_address = os.getenv("HANA_ADDRESS")
    hana_port = int(os.getenv("HANA_PORT", "30015"))
    hana_user = os.getenv("HANA_USER")
    hana_password = os.getenv("HANA_PASSWORD")

    missing = []
    if not hana_address:
        missing.append("HANA_ADDRESS")
    if not hana_user:
        missing.append("HANA_USER")
    if not hana_password:
        missing.append("HANA_PASSWORD")

    if missing:
        raise ValueError(
            f"Faltan variables de entorno para SAP HANA: {', '.join(missing)}"
        )

    return dbapi.connect(
        address=hana_address,
        port=hana_port,
        user=hana_user,
        password=hana_password,
    )


def _normalize_value(value):
    if isinstance(value, Decimal):
        return float(value)
    return value


def fetch_all(query, params=None):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(query, params or [])
        columns = [col[0] for col in cursor.description]
        rows = cursor.fetchall()

        results = []
        for row in rows:
            row_dict = {}
            for col, val in zip(columns, row):
                row_dict[col] = _normalize_value(val)
            results.append(row_dict)
        return results
    finally:
        cursor.close()
        conn.close()


def fetch_one(query, params=None):
    rows = fetch_all(query, params)
    return rows[0] if rows else None


def execute_query(query, params=None):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(query, params or [])
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def execute_many(operations):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        for query, params in operations:
            cursor.execute(query, params or [])
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()