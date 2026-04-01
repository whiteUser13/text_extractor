from flask_sqlalchemy import SQLAlchemy
from flask_jwt_extended import JWTManager
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

db = SQLAlchemy()
jwt = JWTManager()
limiter = Limiter(
    key_func=get_remote_address,
    # default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://",  # 使用内存存储
    strategy="fixed-window"  # 或 "moving-window"
)