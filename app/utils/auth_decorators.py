from functools import wraps
from flask import session, jsonify
from app.utils.auth_helpers import get_user_permissions, user_is_active


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("logged_in"):
            return jsonify({
                "ok": False,
                "message": "No autenticado"
            }), 401

        user_id = session.get("user_id")
        if not user_id:
            return jsonify({
                "ok": False,
                "message": "Sesión inválida"
            }), 401

        if not user_is_active(user_id):
            session.clear()
            return jsonify({
                "ok": False,
                "message": "Usuario inactivo o sesión no válida"
            }), 401

        return f(*args, **kwargs)
    return decorated_function


def permission_required(permission_code):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not session.get("logged_in"):
                return jsonify({
                    "ok": False,
                    "message": "No autenticado"
                }), 401

            user_id = session.get("user_id")
            if not user_id:
                return jsonify({
                    "ok": False,
                    "message": "Sesión inválida"
                }), 401

            if not user_is_active(user_id):
                session.clear()
                return jsonify({
                    "ok": False,
                    "message": "Usuario inactivo o sesión no válida"
                }), 401

            permissions = get_user_permissions(user_id)

            if permission_code not in permissions:
                return jsonify({
                    "ok": False,
                    "message": "No tienes permisos para acceder a este recurso"
                }), 403

            return f(*args, **kwargs)
        return decorated_function
    return decorator