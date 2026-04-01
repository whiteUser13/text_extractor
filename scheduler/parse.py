import os, json
import time
from datetime import datetime
from utils import file_utils
from utils import task_folder_utils
from utils.json_manager import ParseJsonFileManager
from pathlib import Path

parse_json_manager = ParseJsonFileManager()
def get_pending_parse_tasks():
    """
    获取待下载的任务
    :param task_id:
    :return:
    """
    parse_task_path = file_utils.get_parse_task_json_path()
    with parse_json_manager.file_lock:
        all_tasks = parse_json_manager.load_json_without_lock(parse_task_path)
        pending_parse_tasks = {} # {task_id:[{parse_id:,taask_id:,type}]}
        for task_id, info in all_tasks.items():
            pending_parse_tasks[task_id] = []
            for parse_id,parse_task in info.items():
                if parse_task.get("status") == "pending":
                    pending_parse_tasks[task_id].append({
                        "parse_id": parse_id,
                        "task_id": task_id,
                        "file_name": parse_task.get("file_name"),
                        "type":"parse",
                        "select_type":parse_task.get("select_type")
                    })
                # 每个任务id只取五个
                if len(pending_parse_tasks[task_id]) >= 5:
                    break
            if len(pending_parse_tasks[task_id]) == 0:
                del pending_parse_tasks[task_id]
    return pending_parse_tasks

def get_sorted_pending_parse_task():
    pending_area_codes = get_pending_parse_tasks()
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

def update_parse_task_status(task_id, parse_id,status):
    """
    status:pending|downloading|downloaded|error
    :param task_id:
    :param status:
    :return:
    """
    valid_status = ["pending", "parsing", "parsed","error"]
    parse_json_path = file_utils.get_parse_task_json_path()
    if status not in valid_status:
        print("状态码错误，跳过保存")
        return
    with parse_json_manager.file_lock:
        tasks = parse_json_manager.load_json_without_lock(parse_json_path)
        if task_id not in tasks:
            print("任务id不存在，跳过保存")
            return
        parse_tasks = tasks[task_id]
        parse_tasks[parse_id]["status"] = status
        parse_json_manager.save_json_without_lock(parse_json_path, tasks)
        print("修改状态成功")

def update_parse_task_msg(parse_json_path,task_id, parse_id,status,msg,used):
    """
    status:pending|downloading|downloaded|error
    :param task_id:
    :param status:
    :return:
    """
    try:
        valid_status = ["parsed", "error"]
        if status not in valid_status:
            print("状态码错误，跳过保存")
            return
        with parse_json_manager.file_lock:
            tasks = parse_json_manager.load_json_without_lock(parse_json_path)
            if task_id not in tasks:
                print("任务id不存在，跳过保存")
                return
            parse_tasks = tasks[task_id]
            parse_tasks[parse_id]["status"] = status
            parse_tasks[parse_id]["msg"] = msg
            if used:
                parse_tasks[parse_id]["used"] = used
            parse_json_manager.save_json_without_lock(parse_json_path, tasks)
            print("修msg成功")
    except Exception as e:
        print(f"修改msg出错{str(e)}")
