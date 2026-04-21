import json
import os
import threading
from pathlib import Path
from filelock import FileLock
class JsonFileManager:
    _instance = None
    _lock = threading.Lock()  # 用于单例创建时加锁

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(JsonFileManager, cls).__new__(cls)
                cls._instance.file_lock = threading.Lock()   # 每个实例一个文件锁
            return cls._instance

    def load_json(self, path: Path):
        if not path.exists():
            return {}
        with self.file_lock:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)

    def save_json(self, path: Path, data):
        with self.file_lock:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

    def load_json_without_lock(self,path):
        if not path.exists():
            return {}
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def save_json_without_lock(self,path, data):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
class TaskJsonFileManager:
    _instance = None
    _lock = threading.Lock()  # 用于单例创建时加锁

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(TaskJsonFileManager, cls).__new__(cls)
                cls._instance.file_lock = threading.Lock()   # 每个实例一个文件锁
            return cls._instance

    def load_json(self, path: Path):
        if not path.exists():
            return {}
        with self.file_lock:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)

    def save_json(self, path: Path, data):
        with self.file_lock:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

    def load_json_without_lock(self,path):
        if not path.exists():
            return {}
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def save_json_without_lock(self,path, data):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
class DownloadJsonFileManager:
    _instance = None
    _lock = threading.Lock()  # 用于单例创建时加锁

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(DownloadJsonFileManager, cls).__new__(cls)
                cls._instance.file_lock = threading.Lock()   # 每个实例一个文件锁
            return cls._instance

    def load_json(self, path: Path):
        if not path.exists():
            return {}
        with self.file_lock:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)

    def save_json(self, path: Path, data):
        with self.file_lock:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

    def load_json_without_lock(self,path):
        if not path.exists():
            return {}
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def save_json_without_lock(self,path, data):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


cwd = os.getcwd()
print("当前工作目录:", cwd)

parse_json_path = Path(os.getcwd()) / "data/tasks/parse_tasks.json"
print(str(parse_json_path))
class ParseJsonFileManager:
    _instance = None
    _lock = FileLock(str(parse_json_path) + ".lock")  # 用于单例创建时加锁

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(ParseJsonFileManager, cls).__new__(cls)
                cls._instance.file_lock = FileLock(str(parse_json_path) + ".lock")   # 每个实例一个文件锁
            return cls._instance

    def load_json(self, path: Path):
        if not path.exists():
            return {}
        with self.file_lock:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)

    def save_json(self, path: Path, data):
        with self.file_lock:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

    def load_json_without_lock(self,path):
        if not path.exists():
            return {}
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def save_json_without_lock(self,path, data):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

class RecordJsonFileManager:
    _instance = None
    _lock = threading.Lock()  # 用于单例创建时加锁

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(RecordJsonFileManager, cls).__new__(cls)
                cls._instance.file_lock = threading.Lock()   # 每个实例一个文件锁
            return cls._instance

    def load_json(self, path: Path):
        if not path.exists():
            return {}
        with self.file_lock:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)

    def save_json(self, path: Path, data):
        with self.file_lock:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

    def load_json_without_lock(self,path):
        if not path.exists():
            return {}
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def save_json_without_lock(self,path, data):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

class UploadJsonFileManager:
    _instance = None
    _lock = threading.Lock()  # 用于单例创建时加锁

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(UploadJsonFileManager, cls).__new__(cls)
                cls._instance.file_lock = threading.Lock()   # 每个实例一个文件锁
            return cls._instance

    def load_json(self, path: Path):
        if not path.exists():
            return {}
        with self.file_lock:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)

    def save_json(self, path: Path, data):
        with self.file_lock:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

    def load_json_without_lock(self,path):
        if not path.exists():
            return {}
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def save_json_without_lock(self,path, data):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


class SceneStatsJsonFileManager(object):
    _instance = None
    _lock = threading.Lock()  # 用于单例创建时加锁

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(SceneStatsJsonFileManager, cls).__new__(cls)
                cls._instance.file_lock = threading.Lock()   # 每个实例一个文件锁
            return cls._instance

    def load_json(self, path: Path):
        if not path.exists():
            return {}
        with self.file_lock:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)

    def save_json(self, path: Path, data):
        with self.file_lock:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

    def load_json_without_lock(self,path):
        if not path.exists():
            return {}
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def save_json_without_lock(self,path, data):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)





class ResultCollectionJsonFileManager(object):
    _instance = None
    _lock = threading.Lock()  # 用于单例创建时加锁

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(ResultCollectionJsonFileManager, cls).__new__(cls)
                cls._instance.file_lock = threading.Lock()   # 每个实例一个文件锁
            return cls._instance

    def load_json(self, path: Path):
        if not path.exists():
            return {}
        with self.file_lock:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)

    def save_json(self, path: Path, data):
        with self.file_lock:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

    def load_json_without_lock(self,path):
        if not path.exists():
            return {}
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def save_json_without_lock(self,path, data):
        with open(path, "w", encoding="utf-8") as f:

            json.dump(data, f, ensure_ascii=False, indent=2)




class StatsResultJsonFileManager(object):
    _instance = None
    _lock = threading.Lock()  # 用于单例创建时加锁
    _file_locks = {}  # 存储每个文件路径对应的锁
    _file_locks_lock = threading.Lock()  # 保护_file_locks字典的锁

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(StatsResultJsonFileManager, cls).__new__(cls)
            return cls._instance

    def _get_file_lock(self, path: Path):
        """获取指定文件路径的锁"""
        path_str = str(path.resolve())  # 使用绝对路径作为key

        with self._file_locks_lock:
            if path_str not in self._file_locks:
                self._file_locks[path_str] = threading.Lock()
            return self._file_locks[path_str]

    def load_json(self, path: Path):
        if not path.exists():
            return {}
        file_lock = self._get_file_lock(path)
        with file_lock:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)

    def save_json(self, path: Path, data):
        # 确保目录存在
        path.parent.mkdir(parents=True, exist_ok=True)
        file_lock = self._get_file_lock(path)
        with file_lock:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

    def load_json_without_lock(self, path):
        if not path.exists():
            return {}
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def save_json_without_lock(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def clear_locks(self):
        """清理所有锁（可选方法）"""
        with self._file_locks_lock:
            self._file_locks.clear()

            json.dump(data, f, ensure_ascii=False, indent=2)

