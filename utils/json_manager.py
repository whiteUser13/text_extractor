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