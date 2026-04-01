from flask import Blueprint
import os, json
import uuid
from flask import request
from flask import jsonify
from pathlib import Path
from datetime import datetime
from utils import task_folder_utils,file_utils
from utils.status_tracker import StatusTracker             # 1224修改[总日志追踪]
import shutil
from utils.auth_util import AuthUtils
from utils.json_manager import DownloadJsonFileManager,ParseJsonFileManager,TaskJsonFileManager
from utils.task_verify import task_permission_required

task_bp = Blueprint("task", __name__)

parse_json_manager = ParseJsonFileManager()
download_json_manager = DownloadJsonFileManager()
task_json_manager = TaskJsonFileManager()

# 0317修改：新增省份白名单，防止 tasks.json 中写入非标准省份名称
PROVINCE_OPTIONS = {
    "北京市", "天津市", "上海市", "重庆市",
    "河北省", "山西省", "辽宁省", "吉林省", "黑龙江省",
    "江苏省", "浙江省", "安徽省", "福建省", "江西省", "山东省",
    "河南省", "湖北省", "湖南省", "广东省", "海南省",
    "四川省", "贵州省", "云南省", "陕西省", "甘肃省", "青海省",
    "台湾省", "内蒙古自治区", "广西壮族自治区", "西藏自治区",
    "宁夏回族自治区", "新疆维吾尔自治区", "香港特别行政区", "澳门特别行政区",
}

# 确保任务目录存在
os.makedirs("data/tasks", exist_ok=True)
TASKS_FILE = Path("data/tasks/tasks.json")
# 初始化任务文件（如果不存在）
if not TASKS_FILE.exists():
    TASKS_FILE.write_text("[]", encoding="utf-8")

# 获取所有任务信息
@task_bp.get("/info")
@AuthUtils.token_required
def get_tasks():
    """获取所有任务"""
    try:
        # 在认证装饰器中有request.current_user = payload，这里直接用
        user_id = request.current_user.get("user_id",0)
        user_departments = request.current_user.get("group_name","unknown")
        print(user_departments)
        print("gettaskinfo",user_id)
        if not TASKS_FILE.exists():
            print("任务文件不存在，创建空文件")
            TASKS_FILE.write_text("[]", encoding="utf-8")
        with task_json_manager.file_lock:
            tasks = task_json_manager.load_json_without_lock(TASKS_FILE)
        # 确保任务数据格式正确
        formatted_tasks = []
        for task in tasks:
            if task.get("group_name","unknown") == user_departments:
                if task.get("share",False) == True or task.get("createdBy",0) == user_id:
                    formatted_task = {
                        "id": task.get("id", str(uuid.uuid4())),
                        "share": task.get("share", False),
                        "group_name": task.get("group_name", "unknown"),
                        "name": task.get("name", "未命名任务"),
                        "province": task.get("province", ""),  # 0317修改：任务列表接口返回省份
                        "description": task.get("description", ""),
                        "extractionRules": task.get("extractionRules", []),
                        "judgmentRules": task.get("judgmentRules", []),
                        "createdAt": task.get("createdAt", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                    }
                    formatted_tasks.append(formatted_task)
        return jsonify({
            "success": True,
            "tasks": formatted_tasks
        })
    except json.JSONDecodeError as e:
        print(f"JSON解析错误: {e}")
        # 如果JSON格式错误，创建新文件
        TASKS_FILE.write_text("[]", encoding="utf-8")
        return jsonify({
            "success": True,
            "tasks": []
        })
    except Exception as e:
        print(f"获取任务失败: {e}")
        import traceback
        print(f"详细错误: {traceback.format_exc()}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

# 根据任务id获取任务信息
@task_bp.get("/get_task/<task_id>")
@AuthUtils.token_required
@task_permission_required()
def get_task(task_id):
    """获取特定任务"""
    try:
        with task_json_manager.file_lock:
            tasks = task_json_manager.load_json_without_lock(TASKS_FILE)
        task = next((t for t in tasks if t.get("id") == task_id), None)
        if task:
            return jsonify({"task": task})
        else:
            return jsonify({"error": "任务不存在"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# 添加任务
@task_bp.post("/add_task")
@AuthUtils.token_required
def create_task():
    """创建新任务"""
    try:
        json_data = request.get_json()

        data = json_data.get("task")
        # 验证必要字段
        if not data.get("name"):
            return jsonify({"error": "任务名称不能为空"}), 400

        province = str(data.get("province", "")).strip()  # 0317修改：创建任务时读取所属省份
        if not province:
            return jsonify({"error": "所属省份不能为空"}), 400
        if province not in PROVINCE_OPTIONS:
            return jsonify({"error": "所属省份不合法"}), 400

        # 读取现有任务
        with task_json_manager.file_lock:
            tasks = task_json_manager.load_json_without_lock(TASKS_FILE)
            # 创建新任务
            new_task = {
                "id": data.get("id", str(uuid.uuid4())),
                "share":data.get("share", True),
                "group_name": data.get("group_name", "unknown"),
                "createdBy": data.get("createdBy", 0),
                "name": data.get("name"),
                "province": province,  # 0317修改：保存省份到 tasks.json
                "description": data.get("description", ""),
                "extractionRules": data.get("extractionRules", []),
                "judgmentRules": data.get("judgmentRules", []),
                "createdAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }

            tasks.append(new_task)
            # 初始化任务目录
            task_folder_utils.init_task_dirs(new_task["id"])
            # 保存任务
            task_json_manager.save_json_without_lock(TASKS_FILE, tasks)
        # ----------------1224修改[总日志追踪]：初始化总日志状态文件,直接写入空的 status_report.json-----------------
        try:
            status_file = Path(f"data/tasks/{new_task['id']}/status_report.json")
            status_file.parent.mkdir(parents=True, exist_ok=True)
            with open(status_file, "w", encoding="utf-8") as f:
                json.dump({}, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"警告: 初始化状态文件失败: {e}")
        # -------------------------------------------------------------------------------

        return jsonify({
            "message": "任务创建成功",
            "task": new_task
        }), 200

    except Exception as e:
        print(f"创建任务失败: {e}")
        return jsonify({"error": str(e)}), 500

# 更改任务信息
@task_bp.put("/update/<task_id>")
@AuthUtils.token_required
@task_permission_required()
def update_task(task_id):
    """更新任务"""
    try:
        data = request.get_json()
        update_task = data['task']

        with task_json_manager.file_lock:
            tasks = task_json_manager.load_json_without_lock(TASKS_FILE)
            # 查找任务
            task_index = next((i for i, t in enumerate(tasks) if t.get("id") == task_id), -1)
            if task_index == -1:
                return jsonify({"error": "任务不存在"}), 404

            province = str(update_task.get("province", tasks[task_index].get("province", ""))).strip()  # 0317修改：更新任务时同步校验省份
            if not province:
                return jsonify({"error": "所属省份不能为空"}), 400
            if province not in PROVINCE_OPTIONS:
                return jsonify({"error": "所属省份不合法"}), 400

            # 更新任务
            tasks[task_index].update({
                "name": update_task.get("name", tasks[task_index].get("name")),
                "share": update_task.get("share",tasks[task_index].get("share")),
                "province": province,  # 0317修改：更新任务时保存省份
                "description": update_task.get("description", tasks[task_index].get("description")),
                "extractionRules": update_task.get("extractionRules", tasks[task_index].get("extractionRules")),
                "judgmentRules": update_task.get("judgmentRules", tasks[task_index].get("judgmentRules")),
            })
            # 保存任务
            task_json_manager.save_json_without_lock(TASKS_FILE, tasks)
        return jsonify({
            "message": "任务更新成功",
            "task": tasks[task_index]
        })

    except Exception as e:
        print(f"更新任务失败: {e}")
        return jsonify({"error": str(e)}), 500

# 删除任务
@task_bp.delete("/delete/<task_id>")
@AuthUtils.token_required
@task_permission_required()
def delete_task(task_id):
    """删除任务"""
    try:
        # 1.删除tasks.json存储的任务
        with task_json_manager.file_lock:
            tasks = task_json_manager.load_json_without_lock(TASKS_FILE)
            # 过滤掉要删除的任务
            filtered_tasks = [t for t in tasks if t.get("id") != task_id]
            if len(filtered_tasks) == len(tasks):
                return jsonify({"error": "任务不存在"}), 404
            task_json_manager.save_json_without_lock(TASKS_FILE, filtered_tasks)
        # 2.删除任务文件夹
        task_id_dir = task_folder_utils.get_task_id_folder(task_id)
        if os.path.exists(task_id_dir):
            shutil.rmtree(task_id_dir)
        # 3.删除download_tasks.json存储的下载信息
        download_json_path = file_utils.get_download_task_json_path()
        with download_json_manager.file_lock:
            download_tasks_dict = download_json_manager.load_json_without_lock(download_json_path)
            download_tasks_dict.pop(task_id, None)  # 不会报错
            download_json_manager.save_json_without_lock(download_json_path, download_tasks_dict)
        # 4.删除parse_tasks.json存储的下载信息
        parse_json_path = file_utils.get_parse_task_json_path()
        with parse_json_manager.file_lock:
            parse_tasks_dict = parse_json_manager.load_json_without_lock(parse_json_path)
            parse_tasks_dict.pop(task_id, None)
            parse_json_manager.save_json_without_lock(parse_json_path, parse_tasks_dict)
        return jsonify({
            "message": "任务删除成功"
        })

    except Exception as e:
        print(f"删除任务失败: {e}")
        return jsonify({"error": str(e)}), 500
