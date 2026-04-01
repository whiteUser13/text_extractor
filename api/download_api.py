from flask import Blueprint
import os, json
from flask import request
from flask import jsonify
from datetime import datetime
from utils import file_utils
from utils.json_manager import DownloadJsonFileManager
from pathlib import Path
from utils.auth_util import AuthUtils
from utils.task_verify import task_permission_required

json_manager = DownloadJsonFileManager()
# 待下载文件映射词典
DOWNLOAD_TASK_JSON = Path("data/tasks/download_tasks.json")
if not DOWNLOAD_TASK_JSON.exists():
    DOWNLOAD_TASK_JSON.write_text("{}", encoding="utf-8")

download_bp = Blueprint("download", __name__)
# 12.8修改添加下载任务
@download_bp.post("/add_download_task")
@AuthUtils.token_required
@task_permission_required()
def add_download_task():
    data = request.get_json()
    area_code_arr = data.get("area_codes")
    task_id = data.get("task_id")
    print(area_code_arr)
    if not area_code_arr:
        return jsonify({"error": "缺少必要参数 area_codes"}), 400
    if not task_id:
        return jsonify({"error": "请先选择任务"}), 401
    # 加载当前任务文件
    if not DOWNLOAD_TASK_JSON.exists():
        DOWNLOAD_TASK_JSON.write_text("{}", encoding="utf-8")

    with json_manager.file_lock:
        tasks = json_manager.load_json_without_lock(DOWNLOAD_TASK_JSON)
        if task_id not in tasks:
            tasks[task_id] = {}
        download_tasks = tasks[task_id]
        return_tasks = []
        for area_code in area_code_arr:
            if area_code in download_tasks:
                return_tasks.append(download_tasks[area_code])
                continue
            # 添加任务,把字典的键改为task_id-areacode的形式
            download_tasks[area_code] = {
                "area_code": area_code,
                "file_name" :"",
                "status": "pending",
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "size": 0,
                "err_msg": "",
                "task_id" :task_id
            }
            return_tasks.append(download_tasks[area_code])
        json_manager.save_json_without_lock(DOWNLOAD_TASK_JSON, tasks)
    return jsonify({"message": "任务添加成功", "task": return_tasks})

# 重试下载错误的文件
@download_bp.get("/retry/error")
@AuthUtils.token_required
@task_permission_required()
def retry_error():
    # 判断文件是否存在
    # 加载当前任务文件
    task_id = request.args.get('task_id')
    if not task_id:
        return jsonify({"error": "请先选择任务"}), 400
    # 加载当前任务文件
    if not DOWNLOAD_TASK_JSON.exists():
        DOWNLOAD_TASK_JSON.write_text("{}", encoding="utf-8")

    result_list = []
    with json_manager.file_lock:
        tasks = json_manager.load_json_without_lock(DOWNLOAD_TASK_JSON)
        if task_id not in tasks:
            return jsonify({"message": "刷新成功", "tasks": result_list})
        download_tasks = tasks.get(task_id)
        for area_code, info in download_tasks.items():
            if(info.get("status") == "error"):
                download_tasks[area_code]["status"] = "pending"
                result_list.append(download_tasks[area_code])
        json_manager.save_json_without_lock(DOWNLOAD_TASK_JSON, tasks)
    return jsonify({"message": "刷新成功", "tasks": result_list})

# 根据任务id获取下载任务信息，即获取download_tasks.json文件的信息
@download_bp.get("/info")
@AuthUtils.token_required
@task_permission_required()
def get_download_task_info():
    """
    获取 JSON 文件中所有项的信息（以数组形式返回）
    """
    # 判断文件是否存在
    # 加载当前任务文件
    if not DOWNLOAD_TASK_JSON.exists():
        return jsonify({"error": "数据文件不存在"}), 404
    task_id = request.args.get('task_id')
    if not task_id:
        return jsonify({"error": "请先选择任务"}), 400
    # 一定要重新加载最新的内容
    try:
        tasks = json_manager.load_json(DOWNLOAD_TASK_JSON)
    except json.JSONDecodeError:
        return jsonify({"error": "JSON 文件格式错误"}), 500

    result_list = []
    if task_id not in tasks:
        return jsonify({
            "count": len(result_list),
            "tasks": result_list
        })
    download_tasks = tasks.get(task_id)
    for key, info in download_tasks.items():
        result_list.append(info)
    # 返回 JSON
    return jsonify({
        "count": len(result_list),
        "tasks": result_list
    })


