import os
from flask import Flask
from api import register_blueprints
from blueprint_config import Config
from flask_cors import CORS
from scheduler.scheduler import start_scheduler
from extensions import db, jwt,limiter

def create_app():
    app = Flask(__name__)
    # CORS(app, supports_credentials=True,origins=["http://10.10.163.145:8080"])
    CORS(app, supports_credentials=True)
    app.config.from_object(Config)
    db.init_app(app)
    jwt.init_app(app)
    limiter.init_app(app)
    register_blueprints(app)
    with app.app_context():
        db.create_all()
    return app
app = create_app()


if __name__ == "__main__":
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        start_scheduler(app)
    app.run(debug=True, host='0.0.0.0', port=8000)
