from functools import wraps
from flask import g, jsonify
from flask import request

from utils.json_manager import TaskJsonFileManager
from utils import file_utils
from utils.stream_registry import get_stream_info
task_json_manager = TaskJsonFileManager()
def get_task_id_from_request():
    if request.view_args and "task_id" in request.view_args:
        return request.view_args["task_id"]
    if request.is_json:
        data = request.get_json(silent=True) or {}
        if "task_id" in data:
            return data["task_id"]
    return request.args.get("task_id")

def task_permission_required():
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            user = getattr(g, "current_user", None) or getattr(request, "current_user", None)
            task_id = get_task_id_from_request()
            # 回退：如果没有 task_id，则尝试从 stream_id 恢复
            if not task_id:
                stream_id = request.args.get("stream_id")
                if stream_id:
                    info = get_stream_info(stream_id)
                    if info:
                        task_id = info.get("task_id")

            if not task_id:
                return jsonify({"msg": "task_id is required"}), 400

            # 后续保持原有 task 查验逻辑（略）
            task_josn_path = file_utils.get_task_json_path()
            with task_json_manager.file_lock:
                tasks = task_json_manager.load_json_without_lock(task_josn_path)
            select_task = next((t for t in tasks if t["id"] == task_id), None)
            if not select_task:
                return jsonify({"msg": "task not found"}), 404
            if user and select_task["group_name"] != user['group_name']:
                return jsonify({"msg": "permission denied"}), 403
            if user and not select_task.get("share", False) and select_task["createdBy"] != user['user_id']:
                return jsonify({"msg": "permission denied"}), 403

            g.task = select_task
            return func(*args, **kwargs)
        return wrapper
    return decorator


def scene_or_task_permission_required():
    """
    0328修改：支持按 task_id（单任务权限检查）或 scene_id（场景聚合权限检查）。
    如果提供 scene_id，会检查场景中所有任务的权限。
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            user = getattr(g, "current_user", None) or getattr(request, "current_user", None)
            task_id = get_task_id_from_request()
            scene_id = request.args.get("scene_id")
            
            # 如果提供了 scene_id，使用场景权限检查
            if scene_id:
                # 加载所有任务，找到场景中的任务
                task_json_path = file_utils.get_task_json_path()
                with task_json_manager.file_lock:
                    tasks = task_json_manager.load_json_without_lock(task_json_path)
                
                scene_tasks = [t for t in tasks if str(t.get("sceneId") or "").strip() == scene_id]
                if not scene_tasks:
                    return jsonify({"msg": "scene not found or no tasks in scene"}), 404
                
                # 检查用户对场景中所有任务的权限
                for task in scene_tasks:
                    if user and task["group_name"] != user['group_name']:
                        return jsonify({"msg": "permission denied for scene"}), 403
                    if user and not task.get("share", False) and task["createdBy"] != user['user_id']:
                        return jsonify({"msg": "permission denied for scene"}), 403
                
                g.scene_tasks = scene_tasks
                g.scene_id = scene_id
                return func(*args, **kwargs)
            
            # 单任务权限检查（原有逻辑）
            # 回退：如果没有 task_id，则尝试从 stream_id 恢复
            if not task_id:
                stream_id = request.args.get("stream_id")
                if stream_id:
                    info = get_stream_info(stream_id)
                    if info:
                        task_id = info.get("task_id")

            if not task_id:
                return jsonify({"msg": "task_id is required"}), 400

            # 后续保持原有 task 查验逻辑
            task_json_path = file_utils.get_task_json_path()
            with task_json_manager.file_lock:
                tasks = task_json_manager.load_json_without_lock(task_json_path)
            select_task = next((t for t in tasks if t["id"] == task_id), None)
            if not select_task:
                return jsonify({"msg": "task not found"}), 404
            if user and select_task["group_name"] != user['group_name']:
                return jsonify({"msg": "permission denied"}), 403
            if user and not select_task.get("share", False) and select_task["createdBy"] != user['user_id']:
                return jsonify({"msg": "permission denied"}), 403

            g.task = select_task
            return func(*args, **kwargs)
        return wrapper
    return decorator


def _load_all_tasks():
    """0328修改：辅助函数，加载所有任务数据。"""
    task_json_path = file_utils.get_task_json_path()
    with task_json_manager.file_lock:
        tasks = task_json_manager.load_json_without_lock(task_json_path)
    return tasks