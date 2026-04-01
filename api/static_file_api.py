from flask import Blueprint, jsonify
from utils import task_folder_utils
from flask import send_from_directory
from pathlib import Path
import re
from utils.auth_util import AuthUtils
from utils.task_verify import task_permission_required
file_bp = Blueprint("file", __name__)

# ===================== 1.6高危漏洞修改：静态文件接口参数校验（防目录穿越） =====================
class BadPath(ValueError):
    pass

def _validate_task_id(task_id: str) -> str:
    if not task_id or not re.fullmatch(r"[A-Za-z0-9_-]+", str(task_id)) or ".." in task_id or "/" in task_id or "\\" in task_id:
        raise BadPath("invalid task_id")
    return task_id

def _reject_if_unsafe_user_path(user_value: str, *, allow_subdirs: bool) -> str:
    s = str(user_value).strip().replace("\\", "/")
    if not s:
        raise BadPath("empty filename")
    p = Path(s)
    if p.is_absolute() or getattr(p, "drive", ""):
        raise BadPath("absolute path not allowed")
    if any(part in ("..", "") for part in p.parts):
        raise BadPath("path traversal not allowed")
    if not allow_subdirs and len(p.parts) != 1:
        raise BadPath("subdirectories not allowed")
    # 归一化为 posix 风格，避免反斜杠
    return "/".join(p.parts)
# ===================== 1.6高危漏洞修改：静态文件接口参数校验结束 =====================





# @file_bp.route('/task-uploads/<task_id>/<path:filename>')
# def task_uploaded_file(task_id, filename):
#     """提供任务上传目录静态文件访问"""
#     try:
#         _validate_task_id(task_id)  # 1.6高危漏洞修改：校验 task_id
#         filename = _reject_if_unsafe_user_path(filename, allow_subdirs=True)  # 1.6高危漏洞修改：校验 filename
#     except BadPath as e:
#         return jsonify({"success": False, "error": str(e)}), 400  # 1.6高危漏洞修改
#     task_dirs = task_folder_utils.get_task_dirs(task_id)
#     return send_from_directory(str(task_dirs["uploads"]), filename)

@file_bp.route('/task-downloads/<task_id>/downloads/<path:filename>')
@AuthUtils.token_required
@task_permission_required()
def task_downloaded_file(task_id, filename):
    """提供任务下载目录静态文件访问"""
    try:
        _validate_task_id(task_id)  # 1.6高危漏洞修改：校验 task_id
        filename = _reject_if_unsafe_user_path(filename, allow_subdirs=True)  # 1.6高危漏洞修改：校验 filename
    except BadPath as e:
        return jsonify({"success": False, "error": str(e)}), 400  # 1.6高危漏洞修改
    task_dirs = task_folder_utils.get_task_dirs(task_id)
    return send_from_directory(str(task_dirs["downloads"]), filename)
@file_bp.route('/get/100000_full_map')
@AuthUtils.token_required
def get_map():
    """提供任务下载目录静态文件访问"""
    task_dir = task_folder_utils.get_task_dir()
    return send_from_directory(str(task_dir), '100000_full.json')

@file_bp.route('/task-cut/<task_id>/pdf_cut/<path:filename>')
@AuthUtils.token_required
@task_permission_required()
def pdf_cut_file(task_id, filename):
    """提供任务下载目录静态文件访问"""
    try:
        _validate_task_id(task_id)  # 1.6高危漏洞修改：校验 task_id
        filename = _reject_if_unsafe_user_path(filename, allow_subdirs=True)  # 1.6高危漏洞修改：校验 filename
    except BadPath as e:
        return jsonify({"success": False, "error": str(e)}), 400  # 1.6高危漏洞修改
    task_dirs = task_folder_utils.get_task_dirs(task_id)
    return send_from_directory(str(task_dirs["pdf_cut"]), filename)
#
# @file_bp.route('/task-extracted/<task_id>/<path:filename>')
# def task_extracted_file(task_id, filename):
#     """提供任务解压目录静态文件访问"""
#     try:
#         _validate_task_id(task_id)  # 1.6高危漏洞修改：校验 task_id
#         filename = _reject_if_unsafe_user_path(filename, allow_subdirs=True)  # 1.6高危漏洞修改：校验 filename
#     except BadPath as e:
#         return jsonify({"success": False, "error": str(e)}), 400  # 1.6高危漏洞修改
#     task_dirs = task_folder_utils.get_task_dirs(task_id)
#     return send_from_directory(str(task_dirs["extracted_right"]), filename)
@file_bp.route('/task-chunked/<task_id>/<path:filename>')
@AuthUtils.token_required
@task_permission_required()
def task_chunked_file(task_id, filename):
    """提供任务分块目录静态文件访问"""
    try:
        _validate_task_id(task_id)  # 1.6高危漏洞修改：校验 task_id
        filename = _reject_if_unsafe_user_path(filename, allow_subdirs=True)  # 1.6高危漏洞修改：校验 filename
    except BadPath as e:
        return jsonify({"success": False, "error": str(e)}), 400  # 1.6高危漏洞修改
    task_dirs = task_folder_utils.get_task_dirs(task_id)
    return send_from_directory(str(task_dirs["chunked"]), filename)
@file_bp.route('/task-parsed/<task_id>/<path:filename>')
@AuthUtils.token_required
@task_permission_required()
def task_parsed_file(task_id, filename):
    """提供任务解析目录静态文件访问"""
    try:
        _validate_task_id(task_id)  # 1.6高危漏洞修改：校验 task_id
        filename = _reject_if_unsafe_user_path(filename, allow_subdirs=True)  # 1.6高危漏洞修改：校验 filename
    except BadPath as e:
        return jsonify({"success": False, "error": str(e)}), 400  # 1.6高危漏洞修改
    task_dirs = task_folder_utils.get_task_dirs(task_id)
    return send_from_directory(str(task_dirs["parsed"]), filename)
@file_bp.route('/task-extraction-results/<task_id>/<path:filename>')
@AuthUtils.token_required
@task_permission_required()
def task_extraction_results_file(task_id, filename):
    """提供任务提取结果目录静态文件访问"""
    try:
        _validate_task_id(task_id)  # 1.6高危漏洞修改：校验 task_id
        filename = _reject_if_unsafe_user_path(filename, allow_subdirs=True)  # 1.6高危漏洞修改：校验 filename
    except BadPath as e:
        return jsonify({"success": False, "error": str(e)}), 400  # 1.6高危漏洞修改
    task_dirs = task_folder_utils.get_task_dirs(task_id)
    return send_from_directory(str(task_dirs["extraction_results"]), filename)

@file_bp.route('/task-discrimination-results/<task_id>/<path:filename>')
@AuthUtils.token_required
@task_permission_required()
def task_discrimination_results_file(task_id, filename):
    """提供任务判别结果目录静态文件访问"""
    try:
        _validate_task_id(task_id)  # 1.6高危漏洞修改：校验 task_id
        filename = _reject_if_unsafe_user_path(filename, allow_subdirs=True)  # 1.6高危漏洞修改：校验 filename
    except BadPath as e:
        return jsonify({"success": False, "error": str(e)}), 400  # 1.6高危漏洞修改
    task_dirs = task_folder_utils.get_task_dirs(task_id)
    return send_from_directory(str(task_dirs["discrimination_results"]), filename)
@file_bp.route('/task-results/<task_id>/<path:filename>')
@AuthUtils.token_required
@task_permission_required()
def task_results_file(task_id, filename):
    """提供任务结果目录静态文件访问"""
    try:
        _validate_task_id(task_id)  # 1.6高危漏洞修改：校验 task_id
        filename = _reject_if_unsafe_user_path(filename, allow_subdirs=True)  # 1.6高危漏洞修改：校验 filename
    except BadPath as e:
        return jsonify({"success": False, "error": str(e)}), 400  # 1.6高危漏洞修改
    task_dirs = task_folder_utils.get_task_dirs(task_id)
    return send_from_directory(str(task_dirs["results"]), filename)
