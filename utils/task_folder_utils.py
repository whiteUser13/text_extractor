from flask import current_app
import os
from pathlib import Path

class TaskFolderUtils:
    def get_task_dir(self):
        return current_app.config['TASKS_BASE_DIR']# data/tasks
    def get_task_id_folder(selfs,task_id):
        return os.path.join(current_app.config['TASKS_BASE_DIR'],task_id)


    # 获取某一个任务的相关目录
    def get_task_dirs(self, task_id):
        """获取任务相关目录路径"""
        task_base = Path(self.get_task_dir()) / task_id
        return {
            "base": task_base,
            "uploads": task_base / "uploads",
            "downloads": task_base / "downloads",
            "extracted": task_base / "extracted",
            "extracted_right": task_base / "extracted" / "right",
            "parsed": task_base / "parsed",
            "chunked": task_base / "chunked",  # 12.9新增：分块结果目录
            "extraction_results": task_base / "extraction_results",  # 12.10新增：内容提取结果
            "discrimination_results": task_base / "discrimination_results",  # 12.10新增：判别结果
            "results": task_base / "results",
            "images": task_base / "images",
            "pdf_cut": task_base / "pdf_cut"
        }

    # 初始化任务目
    def init_task_dirs(self,task_id):
        """初始化任务目录结构"""
        dirs = self.get_task_dirs(task_id)
        for dir_path in dirs.values():
            os.makedirs(dir_path, exist_ok=True)
        return dirs

    def get_downloads_dir(self,task_id):
        task_base = Path(self.get_task_dir()) / task_id
        return task_base / "downloads"

    def get_decompress_base_dir(self, task_id):
        task_base = Path(self.get_task_dir()) / task_id
        return task_base / "extracted"

    def get_decompressed_dir(self, task_id):
        task_base = Path(self.get_task_dir()) / task_id
        return task_base / "extracted" / "right"

    def get_parsed_dir(self, task_id):
        task_base = Path(self.get_task_dir()) / task_id
        return task_base / "parsed"

    def get_chunked_dir(self, task_id):
        task_base = Path(self.get_task_dir()) / task_id
        return task_base / "chunked"

    def get_extracted_dir(self, task_id):
        task_base = Path(self.get_task_dir()) / task_id
        return task_base / "extraction_results"

    def get_judged_dir(self, task_id):
        task_base = Path(self.get_task_dir()) / task_id
        return task_base / "discrimination_results"

    def get_excel_result_dir(self, task_id):
        task_base = Path(self.get_task_dir()) / task_id
        return task_base / "results"

# ✨ 对象实例（单例模式）
task_folder_utils = TaskFolderUtils()

