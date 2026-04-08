from app.db import fetch_all, fetch_one


def get_user_by_username(username):
    query = """
        SELECT ID, USUARIO, PWD, PERFIL_ID, ACTIVO
        FROM USUARIOS
        WHERE USUARIO = ?
    """
    return fetch_one(query, [username])


def user_is_active(user_id):
    query = """
        SELECT ID, ACTIVO
        FROM USUARIOS
        WHERE ID = ?
    """
    user = fetch_one(query, [user_id])
    return bool(user and user["ACTIVO"] == 1)


def get_user_permissions(user_id):
    """
    Aquí puedes ajustar la consulta según tu modelo real.
    Supongamos:
    - permisos directos por usuario
    - permisos heredados por perfil
    """
    permissions = set()

    # Permisos por perfil
    profile_permissions_query = """
        SELECT P.CODIGO
        FROM USUARIOS U
        INNER JOIN PERFILES PE ON PE.ID = U.PERFIL_ID
        INNER JOIN PERFIL_PERMISOS PP ON PP.PERFIL_ID = PE.ID
        INNER JOIN PERMISOS P ON P.ID = PP.PERMISO_ID
        WHERE U.ID = ?
          AND U.ACTIVO = 1
          AND P.ACTIVO = 1
    """
    rows = fetch_all(profile_permissions_query, [user_id])
    for row in rows:
        permissions.add(row["CODIGO"])

    # Permisos directos por usuario
    user_permissions_query = """
        SELECT P.CODIGO
        FROM USUARIO_PERMISOS UP
        INNER JOIN PERMISOS P ON P.ID = UP.PERMISO_ID
        WHERE UP.USUARIO_ID = ?
          AND P.ACTIVO = 1
    """
    rows = fetch_all(user_permissions_query, [user_id])
    for row in rows:
        permissions.add(row["CODIGO"])

    return list(permissions)