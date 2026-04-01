from flask import current_app
from pathlib import Path
class FileUtils:
    def get_pending_download_json_path(self):
        return Path(current_app.config['MAP_PENDING_TASK'])

    def get_task_json_path(self):
        return Path(current_app.config['TASKS_FILE'])

    def get_download_task_json_path(self):
        return Path(current_app.config['DOWNLOAD_TASKS_FILE'])

    def get_parse_task_json_path(self):
        return Path(current_app.config['PARSE_TASKS_FILE'])


# ✨ 对象实例（单例模式）
# 使用current_app，在蓝图 / 路由函数可以直接使用，如果utils函数在路由中执行，可以使用，如果在后台线程执行需要考虑，可以在方法里使用
# 即，在蓝图路由里调用file——utils.get_xxxx
file_utils = FileUtils()

