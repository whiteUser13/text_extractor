from flask import Blueprint
import os, json
import uuid
from flask import request
from flask import jsonify
from pathlib import Path
from datetime import datetime
from threading import Lock
from utils.json_manager import JsonFileManager,TaskJsonFileManager
from pathlib import Path
from utils.status_tracker import StatusTracker, Stage, Status              #1224修改[总日志追踪]
from utils.safe_name import is_safe_filename
from utils.auth_util import AuthUtils
from utils.task_verify import task_permission_required
from utils import file_utils

task_json_manager = TaskJsonFileManager()
json_manager = JsonFileManager()

#文件检查
ALLOWED_EXTENSIONS = {'.pdf', '.docx', '.doc', '.zip'}#配置允许上传的文件格式、大小
MAX_CONTENT_LENGTH = 100 * 1024 * 1024  # 100 MB

def allowed_file(filename):
    """检查文件扩展名是否合法"""
    ext = os.path.splitext(filename)[1].lower()
    return ext in ALLOWED_EXTENSIONS

def safe_filename(filename):
    """生成唯一文件名（UUID + 原扩展名）"""
    ext = os.path.splitext(filename)[1].lower()
    return f"{uuid.uuid4().hex}{ext}"

def get_file_type(filename: str) -> str:
    """根据扩展名或 MIME 判断文件类型"""
    ext = Path(filename).suffix.lower()
    if ext in [".doc", ".docx"]:
        return "word"
    elif ext in [".jpg", ".jpeg", ".png", ".gif", ".bmp"]:
        return "image"
    elif ext in [".pdf"]:
        return "pdf"
    elif ext in [".zip"]:
        return "zip"
    else:
        return "other"

upload_bp = Blueprint("upload", __name__)

# 单文件上传
# 12.8修改upload_file函数，支持按任务存储
@upload_bp.post('/upload')
@AuthUtils.token_required
def upload_file():
    """单文件上传 - 按任务存储"""
    if 'file' not in request.files:
        return jsonify({"error": "未选择文件"}), 400

    file = request.files['file']
    data_blob = request.files.get('data')
    
    # 在接口中使用
    if not is_safe_filename(file.filename):
        return jsonify({"error": "文件名包含非法字符"}), 400

    # 解析任务信息
    task_id = ""
    if data_blob:
        try:
            data_json = json.loads(data_blob.read().decode('utf-8'))
            task_id = data_json.get("task_id", "")
        except:
            pass

    if not task_id:
        return jsonify({"error": "请先选择任务"}), 400

    user_id = request.current_user.get("user_id", 0)
    user_departments = request.current_user.get("group_name", "unknown")
    task_josn_path = file_utils.get_task_json_path()
    
    with task_json_manager.file_lock:
        tasks = task_json_manager.load_json_without_lock(task_josn_path)
    
    is_exist_task = False
    select_task = {}
    for task in tasks:
        if task["id"] == task_id:
            is_exist_task = True
            select_task = task
    
    if not is_exist_task:
        return jsonify({"msg": "task not found"}), 404
    elif select_task["group_name"] != user_departments:
        return jsonify({"msg": "permission denied"}), 403
    elif not select_task.get("share", False) and select_task["createdBy"] != user_id:
        return jsonify({"msg": "permission denied"}), 403

    # 初始化状态追踪器
    tracker = StatusTracker(task_id)

    upload_map_json = Path(f"data/tasks/{task_id}/downloads/upload_map.json")
    if not upload_map_json.exists():
        upload_map_json.parent.mkdir(parents=True, exist_ok=True)
        upload_map_json.write_text("{}", encoding="utf-8")
    
    if file.filename == '':
        return jsonify({"error": "未选择文件"}), 400

    if not allowed_file(file.filename):
        return jsonify({"error": f"只允许上传 {', '.join(ALLOWED_EXTENSIONS)}"}), 401

    # 生成安全文件名并保存到任务目录
    original_name = file.filename
    safe_name = safe_filename(file.filename)
    save_path = Path(f"data/tasks/{task_id}/downloads") / original_name
    
    # 确保目录存在
    save_path.parent.mkdir(parents=True, exist_ok=True)
    
    # 计算主键用于日志追踪
    try:
        file_stem = os.path.splitext(original_name)[0]
        # 逻辑：取下划线前的部分作为地块编码，如果没有下划线则用文件名本身
        pk = file_stem.split('_')[0] if '_' in file_stem else file_stem
    except:
        pk = original_name

    try:
        # 检查文件是否已存在（是否是重新上传）
        file_exists = save_path.exists()
        
        # 如果文件已存在，先删除旧文件
        if file_exists:
            try:
                os.remove(save_path)
                # 可选：记录重新上传日志
                print(f"文件已存在，正在重新上传: {original_name}")
            except Exception as e:
                print(f"删除旧文件失败: {e}")
        
        # 开始上传处理
        tracker.update_file_stage(original_name, Stage.UPLOAD, Status.PROCESSING, primary_key=pk)

        # 保存文件
        file.save(save_path)
        
        # 获取文件大小
        size_bytes = os.path.getsize(save_path)
        file_size_kb = round(size_bytes / 1024, 2)
        file_type = get_file_type(safe_name)

        # 上传成功
        tracker.update_file_stage(original_name, Stage.UPLOAD, Status.SUCCESS, primary_key=pk)
        
        # 标记下载阶段为跳过
        tracker.update_file_stage(original_name, Stage.DOWNLOAD, Status.SKIPPED, primary_key=pk)
        
        # 针对非压缩包和PDF文件的状态预处理
        ext = os.path.splitext(original_name)[1].lower()
        
        # 如果不是压缩包，跳过解压阶段
        if ext not in ['.zip', '.rar', '.7z', '.tar', '.gz', '.bz2']:
            tracker.update_file_stage(original_name, Stage.DECOMPRESS, Status.SKIPPED, primary_key=pk)
        
        # 如果是PDF，跳过转换阶段
        if ext == '.pdf':
            tracker.update_file_stage(original_name, Stage.CONVERT, Status.SKIPPED, primary_key=pk)
            
    except Exception as e:
        print(f"文件保存失败: {e}")
        tracker.update_file_stage(original_name, Stage.UPLOAD, Status.FAILED, str(e), primary_key=pk)
        return jsonify({"error": f"文件保存失败: {str(e)}"}), 402

    # 更新映射文件
    with json_manager.file_lock:
        try:
            # 读取现有映射文件
            file_map = json_manager.load_json_without_lock(upload_map_json)
            
            # 如果文件已存在映射中，先删除旧记录（可选）
            # 查找并删除原文件名对应的旧记录
            for key, value in list(file_map.items()):
                if value.get("origin_name") == original_name:
                    del file_map[key]
                    break
            
            # 添加新记录
            file_map[safe_name] = {
                "id": safe_name,
                "origin_name": original_name,
                "size": file_size_kb,
                "type": file_type,
                "upload_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "status": "completed",
                "task_id": task_id,
                "user_id": user_id,
                "path": str(save_path)
            }
            
            # 保存映射文件
            json_manager.save_json_without_lock(upload_map_json, file_map)
            
        except Exception as e:
            print(f"更新映射文件失败: {e}")
            # 即使映射文件更新失败，文件已上传成功，不应返回错误
            # 记录错误但继续返回成功

    # 构建文件URL
    file_url = f"/data/tasks/{task_id}/downloads/{safe_name}"

    return jsonify({
        "success": True,
        "data": {
            "id": safe_name,
            "origin_name": original_name,
            "size": file_size_kb,
            "type": file_type,
            "upload_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "status": "completed",
            "task_id": task_id,
            "file_url": file_url,
            "user_id": user_id
        }
    })

# 根据任务id获取上传文件信息，即获取upload_file_map.json文件的信息
@upload_bp.get("/get_info/by_id")
@AuthUtils.token_required
@task_permission_required()
def get_upload_file_info():
    """
    获取 JSON 文件中所有项的信息（以数组形式返回）
    """
    # 判断文件是否存在
    task_id = request.args.get('task_id')
    if task_id is None:
        return jsonify({"error": "缺少任务id"}), 501

    upload_map_json = Path(f"data/tasks/{task_id}/downloads/upload_map.json")
    if not upload_map_json.exists():
        return jsonify({
            "success": True,
            "data": [],
            "count": 0
        })

    try:
        # 读取 JSON 文件
        files = json_manager.load_json(upload_map_json)
    except json.JSONDecodeError:
        return jsonify({"success": False, "error": "JSON 文件格式错误"}), 500

    # 转换为数组形式
    result_list = []
    # 使用字典来去重（基于原始文件名）
    seen_origin_names = set()
    
    for key, info in files.items():
        origin_name = info.get("origin_name", "")
        
        # 如果已存在相同原始文件名的记录，比较上传时间，保留最新的
        if origin_name in seen_origin_names:
            # 查找已存在的同名记录
            for i, existing in enumerate(result_list):
                if existing["origin_name"] == origin_name:
                    # 比较上传时间，保留最新的
                    existing_time = datetime.strptime(existing["upload_time"], "%Y-%m-%d %H:%M:%S")
                    new_time = datetime.strptime(info.get("upload_time", "1970-01-01 00:00:00"), "%Y-%m-%d %H:%M:%S")
                    
                    if new_time > existing_time:
                        # 替换为更新的记录
                        result_list[i] = {
                            "id": info.get("id", key),
                            "origin_name": origin_name,
                            "size": info.get("size", 0),
                            "type": info.get("type", ""),
                            "upload_time": info.get("upload_time", ""),
                            "status": info.get("status", ""),
                            "task_id": info.get("task_id", ""),
                            "user_id": info.get("user_id", "")
                        }
                    break
        else:
            # 新记录，直接添加
            result_list.append({
                "id": info.get("id", key),
                "origin_name": origin_name,
                "size": info.get("size", 0),
                "type": info.get("type", ""),
                "upload_time": info.get("upload_time", ""),
                "status": info.get("status", ""),
                "task_id": info.get("task_id", ""),
                "user_id": info.get("user_id", "")
            })
            seen_origin_names.add(origin_name)
    
    # 按上传时间倒序排序
    result_list.sort(key=lambda x: x["upload_time"], reverse=True)

    # 返回 JSON
    return jsonify({
        "success": True,
        "data": result_list,
        "count": len(result_list)
    })

# 可选：添加删除上传文件的接口
@upload_bp.delete("/delete")
@AuthUtils.token_required
@task_permission_required()
def delete_upload_file():
    """删除上传的文件"""
    task_id = request.args.get('task_id')
    file_id = request.args.get('file_id')
    
    if not task_id or not file_id:
        return jsonify({"success": False, "error": "缺少参数"}), 400
    
    upload_map_json = Path(f"data/tasks/{task_id}/downloads/upload_map.json")
    if not upload_map_json.exists():
        return jsonify({"success": False, "error": "文件不存在"}), 404
    
    try:
        with json_manager.file_lock:
            files = json_manager.load_json_without_lock(upload_map_json)
            
            if file_id in files:
                # 获取文件路径
                file_path = Path(files[file_id].get("path", ""))
                
                # 删除物理文件
                if file_path.exists():
                    file_path.unlink()
                
                # 删除映射记录
                del files[file_id]
                
                # 保存更新后的映射
                json_manager.save_json_without_lock(upload_map_json, files)
                
                return jsonify({
                    "success": True,
                    "message": "文件删除成功"
                })
            else:
                return jsonify({"success": False, "error": "文件记录不存在"}), 404
                
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500