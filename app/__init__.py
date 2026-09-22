import os
import logging
from logging.handlers import RotatingFileHandler

from flask import Flask, jsonify, request
from flask_cors import CORS
from dotenv import load_dotenv

from app.routes.auth import auth_bp
from app.routes.users import users_bp
from app.routes.profiles import profiles_bp
from app.routes.departments import departments_bp
from app.routes.partners import partners_bp
from app.routes.modules import modules_bp
from app.routes.actions import actions_bp
from app.routes.permissions import permissions_bp
from app.routes.sgc import sgc_bp
from app.routes.comedor import comedor_bp
from app.routes.employee_types import employee_types_bp
from app.routes.branches import branches_bp
from app.routes.qr import qr_bp
from app.routes.ordenes_trabajo import ordenes_trabajo_bp
from app.routes.catalog import catalog_bp


def _configure_logging(app):
    log_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "app_errors.log")

    handler = RotatingFileHandler(
        log_path, maxBytes=2_000_000, backupCount=5, encoding="utf-8"
    )
    handler.setLevel(logging.ERROR)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

    app.logger.addHandler(handler)
    app.logger.setLevel(logging.ERROR)


def create_app():
    load_dotenv()

    app = Flask(__name__)
    app.config["SECRET_KEY"] = "ipl_api_secret"

    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = False

    _configure_logging(app)

    CORS(
        app,
        supports_credentials=True,
        resources={
            r"/api/*": {
                "origins": [
                    "http://68.155.144.63:8084",
                    "http://localhost:8084",
                    "http://127.0.0.1:8084",
                    "http://localhost:5173",
                    "http://127.0.0.1:5173",
                ]
            }
        },
    )

    app.register_blueprint(auth_bp, url_prefix="/api/auth")
    app.register_blueprint(users_bp, url_prefix="/api/users")
    app.register_blueprint(profiles_bp, url_prefix="/api/profiles")
    app.register_blueprint(departments_bp, url_prefix="/api/departments")
    app.register_blueprint(partners_bp, url_prefix="/api/partners")
    app.register_blueprint(modules_bp, url_prefix="/api/modules")
    app.register_blueprint(actions_bp, url_prefix="/api/actions")
    app.register_blueprint(permissions_bp, url_prefix="/api/permissions")
    app.register_blueprint(sgc_bp, url_prefix="/api/sgc")
    app.register_blueprint(comedor_bp, url_prefix="/api/comedor")
    app.register_blueprint(employee_types_bp, url_prefix="/api/employee-types")
    app.register_blueprint(branches_bp, url_prefix="/api/branches")
    app.register_blueprint(qr_bp, url_prefix="/api/qr")
    app.register_blueprint(ordenes_trabajo_bp, url_prefix="/api/ordenes-trabajo")
    app.register_blueprint(catalog_bp, url_prefix="/api/catalog")

    @app.errorhandler(Exception)
    def handle_unhandled_exception(exc):
        app.logger.exception(
            "Excepción no controlada en %s %s", request.method, request.path
        )

        payload = {
            "ok": False,
            "message": "Ocurrió un error interno en el servidor",
            "data": None,
        }

        if os.getenv("APP_DEBUG", "0") == "1":
            import traceback

            payload["message"] = f"{type(exc).__name__}: {exc}"
            payload["debug_traceback"] = traceback.format_exc()

        return jsonify(payload), 500

    return app
