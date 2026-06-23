import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

class Config:
    SECRET_KEY = "replace-with-a-secure-key"
    JWT_SECRET_KEY = "replace-with-jwt-secret"
    JWT_ACCESS_TOKEN_EXPIRES = 60 * 60 * 24  # 1天
    # SQLALCHEMY_DATABASE_URI = (
    #     f"mysql+pymysql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}?charset=utf8mb4"
    # )
    SQLALCHEMY_DATABASE_URI = (
        f"mysql+pymysql://root:123456@127.0.0.1:3306/text?charset=utf8mb4"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # 文件配置
    TASKS_BASE_DIR = os.path.join(BASE_DIR, "data/tasks")  # 12.2新增任务目录配置
    TASKS_FILE = os.path.join(BASE_DIR,"data/tasks/tasks.json") # 任务信息
    PARSE_TASKS_FILE = os.path.join(BASE_DIR,"data/tasks/parse_tasks.json")
    DOWNLOAD_TASKS_FILE = os.path.join(BASE_DIR, "data/tasks/download_tasks.json")
    EXTRACT_TASKS_FILE = os.path.join(BASE_DIR, "data/tasks/extract_tasks.json")
    JUDGE_TASKS_FILE = os.path.join(BASE_DIR, "data/tasks/judge_tasks.json")
    # 统一鉴权登录
    APPLICATION_ID = "app_852030526452797440"
    OAUTH2_REVOCATION_URL = os.getenv(
        "OAUTH2_REVOCATION_URL",
        "cu",
    )
    OAUTH2_TOKEN_URL = os.getenv(
        "OAUTH2_TOKEN_URL",
        "http://10.100.226.96:8238/oauth2/token",
    )
    OAUTH2_ISSUER = os.getenv(
        "OAUTH2_ISSUER",
        "http://10.100.226.96:8238",
    )
    OAUTH2_JWKS_URL = os.getenv(
        "OAUTH2_JWKS_URL",
        "http://10.100.226.96:8238/.well-known/jwks.json",
    )
    # 本地测试时需替换为本丢服务地址
    OAUTH2_REDIRECT_URI = os.getenv(
        "OAUTH2_REDIRECT_URI",
        "http://localhost:5173/login",
    )
    OAUTH2_CLIENT_ID = os.getenv("OAUTH2_CLIENT_ID", APPLICATION_ID)
    OAUTH2_CLIENT_SECRET = os.getenv(
        "OAUTH2_CLIENT_SECRET",
        "K0JaTzt--SVYWiN7TAkzFN4I7lL0VmNg6J0W6Aygjv0",
    )
    # Compatibility for existing code that used the misspelled setting.
    APPLICATION_SCERET = OAUTH2_CLIENT_SECRET
    OAUTH2_REQUEST_TIMEOUT = float(os.getenv("OAUTH2_REQUEST_TIMEOUT", "5"))
    OAUTH2_REFRESH_COOKIE_NAME = os.getenv(
        "OAUTH2_REFRESH_COOKIE_NAME",
        "oauth_refresh_token",
    )
    OAUTH2_REFRESH_COOKIE_SECURE = (
        os.getenv("OAUTH2_REFRESH_COOKIE_SECURE", "false").lower() == "true"
    )
    OAUTH2_REFRESH_COOKIE_SAMESITE = os.getenv(
        "OAUTH2_REFRESH_COOKIE_SAMESITE",
        "Lax",
    )
    OAUTH2_REFRESH_COOKIE_MAX_AGE = int(
        os.getenv("OAUTH2_REFRESH_COOKIE_MAX_AGE", str(30 * 24 * 60 * 60))
    )
