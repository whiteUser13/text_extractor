import os, json
import time
from datetime import datetime
from utils import file_utils
from utils import task_folder_utils
from utils.批量报告下载 import download_file_by_area_code
from utils.json_manager import DownloadJsonFileManager
from pathlib import Path

json_manager = DownloadJsonFileManager()
# 12.8修改下载任务函数，按任务存储
def download_file_task(task):
    """
    map_pending_task_path,下载任务json文件路径
    :param task:
    :param map_pending_task_path:
    :return:
    """

    task_id = task.get("task_id")
    area_code = task.get("area_code")
    map_pending_task_path = Path(f"data/tasks/download_tasks.json")
    #task_id为任务id，area_id为区块编码，根据区块编码下载
    dwonload_folder =  Path(f"data/tasks/{task_id}/downloads")
    try:
        print("开始下载")
        # 1224修改[总日志追踪]：传入 task_id 参数
        result = download_file_by_area_code(area_code, dwonload_folder, task_id=task_id)
    except Exception as e:
        print("线程异常")
        result = {
            "success": False,
            "err_msg": f"exception: {e}"
        }
    print("result",result)
    with json_manager.file_lock:
        tasks = json_manager.load_json_without_lock(map_pending_task_path)
        if task_id not in tasks:
            print("任务id不存在，跳过保存")
            return
        download_tasks = tasks[task_id]

        if result.get("success"):
            download_tasks[area_code]["status"] = "downloaded"
        else:
            download_tasks[area_code]["status"] = "error"
            download_tasks[area_code]["err_msg"] = result.get("err_msg")
        json_manager.save_json_without_lock(map_pending_task_path, tasks)

    if result["success"] and task_id:
        with json_manager.file_lock:
            # download_map_json = task_folder_utils.get_downloads_dir(task_id) / "download_map.json"
            download_map_json = Path(f"data/tasks/{task_id}/downloads/download_map.json")
            if not download_map_json.exists():
                download_map_json.write_text("{}", encoding="utf-8")
            downloads = json_manager.load_json_without_lock(download_map_json)
            downloads[area_code] = {
                "area_code": area_code,
                "file_name": result["filename"],
                "origin_name": result["filename"],
                "folder_name": Path(result["filename"]).stem,
                "size": result["size"],
                "type": Path(result["filename"]).suffix,
                "download_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "status": "downloaded",
                "task_id": task_id  # 添加任务ID
            }
            json_manager.save_json_without_lock(download_map_json, downloads)


def get_download_tasks(task_id):
    download_task_path = file_utils.get_download_task_json_path()
    with json_manager.file_lock:
        all_tasks = json_manager.load_json_without_lock(download_task_path)
        download_tasks = all_tasks.get(task_id)
    if download_tasks:
        return download_tasks
    else:
        return {}
def get_pending_download_tasks():
    """
    获取待下载的任务
    :param task_id:
    :return:
    """
    download_task_path = file_utils.get_download_task_json_path()
    with json_manager.file_lock:
        all_tasks = json_manager.load_json_without_lock(download_task_path)
        pending_area_codes = {} # {task_id:[{area_code:,created_at:,}]}
        for task_id, info in all_tasks.items():
            pending_area_codes[task_id] = []
            for area_code,download_task in info.items():
                # 每个任务id只取五个
                if len(pending_area_codes[task_id]) >= 5:
                    break
                if download_task.get("status") == "pending":
                    pending_area_codes[task_id].append({
                        "area_code": area_code,
                        "task_id": task_id,
                        "type":"download"
                    })
            if len(pending_area_codes[task_id]) == 0:
                del pending_area_codes[task_id]
    return pending_area_codes

def get_sorted_pending_download_task():
    pending_area_codes = get_pending_download_tasks()
    sorted_tasks = []
    # 按顺序提取每个任务的提交的任务
    for i in range(5):
        for task_id, info in pending_area_codes.items():
            if i >= len(info):  # 🔥 关键防御
                continue
            sorted_tasks.append(info[i])

            if len(sorted_tasks) >= 5:
                return sorted_tasks
    return sorted_tasks

def update_download_task_status(task_id, area_code,status):
    """
    status:pending|downloading|downloaded|error
    :param task_id:
    :param status:
    :return:
    """
    valid_status = ["pending", "downloading", "downloaded","error"]
    map_pending_task_path = file_utils.get_download_task_json_path()
    if status not in valid_status:
        print("状态码错误，跳过保存")
        return
    with json_manager.file_lock:
        tasks = json_manager.load_json_without_lock(map_pending_task_path)
        if task_id not in tasks:
            print("任务id不存在，跳过保存")
            return
        download_tasks = tasks[task_id]
        download_tasks[area_code]["status"] = status
        json_manager.save_json_without_lock(map_pending_task_path, tasks)

