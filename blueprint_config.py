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
    APPLICATION_ID = "c5a8639164b81b94ffbfb8df94886693"