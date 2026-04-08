from flask import Flask
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


def create_app():
    load_dotenv()

    app = Flask(__name__)
    app.config["SECRET_KEY"] = "ipl_api_secret"

    CORS(
        app,
        supports_credentials=True,
        resources={r"/api/*": {"origins": "*"}},
    )

    app.register_blueprint(auth_bp, url_prefix="/api/auth")
    app.register_blueprint(users_bp, url_prefix="/api/users")
    app.register_blueprint(profiles_bp, url_prefix="/api/profiles")
    app.register_blueprint(departments_bp, url_prefix="/api/departments")
    app.register_blueprint(partners_bp, url_prefix="/api/partners")
    app.register_blueprint(modules_bp, url_prefix="/api/modules")
    app.register_blueprint(actions_bp, url_prefix="/api/actions")
    app.register_blueprint(permissions_bp, url_prefix="/api")
    app.register_blueprint(sgc_bp, url_prefix="/api/sgc")
    app.register_blueprint(comedor_bp, url_prefix="/api/comedor")

    return app