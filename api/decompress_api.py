from flask import Blueprint
import os, json
import re  # 1.6高危漏洞修改：用于 task_id/路径参数校验
from flask import request, Response
from flask import jsonify
from pathlib import Path
import time
from datetime import datetime
import tempfile
import zipfile
import rarfile
import shutil
from utils import task_folder_utils
from utils.pdf_cut import extract_dpf
import sys
import subprocess
import uuid
from utils import task_folder_utils
from utils.pdf_cut import extract_dpf        
from utils.status_tracker import StatusTracker, Stage, Status      # 1224修改[总日志追踪]
# 11111
from utils.auth_util import AuthUtils
from utils.task_verify import task_permission_required
from functools import wraps
from flask import request, jsonify
try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass
processed_files_status = {}

decompress_bp = Blueprint("decompress", __name__)
from utils.stream_registry import register_stream, get_stream_info, stream_keys


# 新增：短 ID 注册表 & 全局允许扩展名（用于 register 验证）
_stream_registry: dict = {}
ALLOWED_ENTRY_EXTS_GLOBAL = {
    ".zip", ".rar", ".7z", ".tar", ".gz", ".bz2",
    ".pdf", ".docx", ".doc", ".xlsx", ".xls",
    ".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff",
    ".txt"
}

# 1.6高危漏洞修改：folder-content 允许访问的一级目录白名单（仅限任务目录内）
ALLOWED_FOLDER_TYPES = {
    "downloads",
    "uploads",
    "extracted",
    "parsed",
    "images",
    "pdf_cut",
    "chunked",
    "results",
}
# ===================== 1.6高危漏洞修改：路径安全工具函数（防目录穿越/任意文件读取） =====================
class BadPath(ValueError):
    pass

def _reject_if_unsafe_user_path(user_value: str, *, allow_subdirs: bool) -> Path:
    """
    将用户输入解析成“相对路径”，并做安全约束：
    - 禁止绝对路径（如 /etc/passwd）
    - 禁止 .. 目录穿越
    - 默认只允许单段（文件名/文件夹名）；如需支持子目录，显式 allow_subdirs=True
    """
    if user_value is None:
        raise BadPath("empty path")
    s = str(user_value).strip().replace("\\", "/")
    if not s:
        raise BadPath("empty path")
    p = Path(s)

    # 绝对路径（/etc/passwd、C:\\xxx、\\\\server\\share 等）
    if p.is_absolute() or getattr(p, "drive", ""):
        raise BadPath("absolute path not allowed")

    parts = p.parts
    if any(part in ("..", "") for part in parts):
        raise BadPath("path traversal not allowed")

    if not allow_subdirs and len(parts) != 1:
        raise BadPath("subdirectories not allowed here")

    return p

def safe_join_under(base_dir: Path, user_value: str, *, allow_subdirs: bool) -> Path:
    """
    base_dir / user_value 但确保最终 resolve 后仍在 base_dir 下
    """
    base_resolved = Path(base_dir).resolve()
    rel = _reject_if_unsafe_user_path(user_value, allow_subdirs=allow_subdirs)
    target = (base_resolved / rel).resolve()
    try:
        target.relative_to(base_resolved)
    except ValueError:
        raise BadPath("resolved path escapes base")
    return target

def _validate_task_id(task_id: str) -> str:
    """
    1.6高危漏洞修改：约束 task_id 形态，避免被当作路径片段注入
    """
    if not task_id or not re.fullmatch(r"[A-Za-z0-9_-]+", str(task_id)) or ".." in task_id or "/" in task_id or "\\" in task_id:
        raise BadPath("invalid task_id")
    return task_id
# ===================== 1.6高危漏洞修改：路径安全工具函数结束 =====================

# lz修改：文件名乱码问题
def _decode_zip_filename(raw_name: str) -> str:
    """
    Python 读取 ZIP 时已经用 cp437 解码为 str，此处把它还
    原为 bytes，再尝试用几种常见编码转回中文。
    如果遇到 UTF‑8 标志位，也直接用 utf‑8。
    """
    if not isinstance(raw_name, str):
        return raw_name
    # 如果 header 指示 UTF‑8，则直接返回（通常已经正确）
    # 这个属性在 ZipInfo 对象上可用：info.flag_bits & 0x800
    # 调用方可传入 info 对象以检测，此处仅做简单处理。

    try:
        b = raw_name.encode('cp437')
    except Exception:
        return raw_name

    for enc in ('utf-8', 'gb18030', 'gbk', 'cp936'):
        try:
            s = b.decode(enc)
            # 只要得到任一汉字就认为成功
            if any('\u4e00' <= ch <= '\u9fff' for ch in s):
                return s
        except Exception:
            continue
    # 如果都失败，返回原始字符串
    return raw_name
def perform_pdf_cut_after_classification(task_id: str,selected_files: list = None):
    """
    在文件分类完成后执行PDF裁剪
    """
    try:
        # 获取任务目录
        task_dirs = task_folder_utils.get_task_dirs(task_id)
        extracted_right_dir = task_dirs["extracted"] / "right"
        downloads_dir = task_dirs["downloads"]
        pdf_cut_dir = task_dirs["pdf_cut"]
        temp_dir = pdf_cut_dir / "temp"

        # 确保目录存在
        os.makedirs(pdf_cut_dir, exist_ok=True)
        os.makedirs(temp_dir, exist_ok=True)

        # 获取任务的提取规则
        from pathlib import Path
        import json

        TASKS_FILE = Path("data/tasks/tasks.json")
        extraction_rules = []

        if TASKS_FILE.exists():
            with open(TASKS_FILE, "r", encoding="utf-8") as f:
                tasks = json.load(f)

            task = next((t for t in tasks if t.get("id") == task_id), None)
            if task:
                # 从 extractionRules 数组中提取所有规则
                task_extraction_rules = task.get("extractionRules", [])

                # 将所有规则转换为适合 pdf_cut.py 使用的格式
                for rule in task_extraction_rules:
                    if rule.get("enabled", True):  # 只处理启用的规则
                        extraction_rules.append({
                            "name": rule.get("name", ""),
                            "sections": rule.get("sections", []),
                            "keywords": [],  # 如果需要 keywords，可以从其他地方获取
                            "page_range": ""
                        })

                print(f"提取规则: {extraction_rules}")

        pdf_files = []
        
        # 处理用户选择的文件
        if selected_files:
            for file_name in selected_files:
                # 检查是否是压缩包
                file_ext = Path(file_name).suffix.lower()
                archive_extensions = ['.zip', '.rar', '.7z', '.tar', '.gz', '.bz2']
                
                if file_ext in archive_extensions:
                    # 如果是压缩包，查找同名文件夹
                    folder_name = Path(file_name).stem  # 获取不带扩展名的文件名
                    target_folder = extracted_right_dir / folder_name
                    
                    if target_folder.exists():
                        # 遍历文件夹中的PDF文件
                        for root, dirs, files in os.walk(target_folder):
                            for file in files:
                                if file.lower().endswith('.pdf'):
                                    pdf_files.append(os.path.join(root, file))
                    else:
                        print(f"警告: 未找到压缩包对应的文件夹 {target_folder}")
                else:
                    # 如果不是压缩包，按原逻辑处理
                    for root_dir in [downloads_dir, extracted_right_dir]:
                        if root_dir.exists():
                            for root, dirs, files in os.walk(root_dir):
                                for file in files:
                                    if file.lower().endswith('.pdf') and file == file_name:
                                        pdf_files.append(os.path.join(root, file))
        else:
            # 如果没有指定文件，处理所有PDF文件
            for root_dir in [downloads_dir, extracted_right_dir]:
                if root_dir.exists():
                    for root, dirs, files in os.walk(root_dir):
                        for file in files:
                            if file.lower().endswith('.pdf'):
                                pdf_files.append(os.path.join(root, file))

        if not pdf_files:
            if selected_files:
                print(f"在选定的文件中没有找到PDF文件: {selected_files}")
            else:
                print("没有找到需要裁剪的PDF文件")
            return True, []

        print(f"开始执行PDF裁剪，任务ID: {task_id}，处理文件数: {len(pdf_files)}")

        # 调用PDF裁剪函数
        results = extract_dpf(
                str(downloads_dir),
                str(extracted_right_dir),
                str(pdf_cut_dir),
                str(temp_dir),
                extraction_rules,
                task_id=task_id,
                selected_files=selected_files  # 传递选中的文件列表
            
            )
            
        print("PDF裁剪完成")
        return True, results


    except Exception as e:
        print(f"PDF裁剪过程中出现错误: {e}")
        import traceback
        traceback.print_exc()
        return False, str(e)

# 在 decompress_api.py 的 get_task_files 函数中添加处理状态信息
@decompress_bp.get("/<task_id>/files")
@AuthUtils.token_required
@task_permission_required()
def get_task_files(task_id: str):
    """获取任务特定文件夹下的文件列表"""
    try:
        subfolder = request.args.get("subfolder", "") 
        folders = request.args.get("folders", "downloads,uploads").split(",")
        result = {}
        _validate_task_id(task_id)  # 1.6高危漏洞修改：校验 task_id
        tasks_root = Path("data/tasks").resolve()  # 1.6高危漏洞修改：任务根目录
        task_base_path = safe_join_under(tasks_root, task_id, allow_subdirs=False)  # 1.6高危漏洞修改：防目录穿越
        
        # 获取已处理的文件列表 - 现在只检查downloads目录
        processed_files = set()
        downloads_dir = task_base_path / "downloads"
        
        # 获取downloads目录中所有文件的名称（不含扩展名）


        for folder in folders:
            # 1.6高危漏洞修改：folders 仅允许访问任务目录内的白名单子目录，防 /etc 等任意目录枚举
            ALLOWED_FOLDERS = {"downloads", "uploads", "parsed", "chunked", "images", "extracted", "extraction_results", "discrimination_results", "results", "pdf_cut"}
            folder_key = folder.strip()
            if folder_key not in ALLOWED_FOLDERS:
                return jsonify({"success": False, "error": f"folder not allowed: {folder_key}"}), 400
            folder_path = safe_join_under(task_base_path, folder_key, allow_subdirs=False)  # 1.6高危漏洞修改：安全拼接目录
            # 如果指定了子文件夹，则在该子文件夹中查找
            if subfolder:
                folder_path = safe_join_under(folder_path, subfolder, allow_subdirs=True)  # 1.6高危漏洞修改：安全拼接子目录

            files = []

            if folder_path.exists():
                for file in folder_path.iterdir():
                    # 跳过系统文件
                    if file.name in ["upload_map.json", "download_map.json", "error_logs"]:
                        continue
                    if file.is_file():
                        # 检查文件是否已被处理（通过文件名主干匹配）
                        is_processed = file.stem in processed_files
                        files.append({
                            "name": file.name,
                            "size": file.stat().st_size,
                            "uploadTime": datetime.fromtimestamp(
                                file.stat().st_mtime
                            ).isoformat(),
                            "type": "file",
                            "isProcessed": is_processed,  # 添加已处理状态
                            "path": str(file.relative_to(task_base_path)) if subfolder else f"{folder.strip()}/{file.name}"  # 添加路径信息
                             # 如果指定了子文件夹，则在该子文件夹中查找
                        })
                    # 文件夹
                    elif file.is_dir():
                        files.append({
                            "name": file.name,
                            "size": 0,
                            "uploadTime": datetime.fromtimestamp(
                                file.stat().st_mtime
                            ).isoformat(),
                            "type": "folder",
                            "isProcessed": False,
                            "path": str(file.relative_to(task_base_path)) if subfolder else f"{folder.strip()}/{file.name}"  # 添加路径信息
                        })
            result[folder.strip()] = files

        return jsonify({
            "success": True,
            "data": result,
            "processed_files": list(processed_files), # 返回已处理文件列表
            "current_path": subfolder  # 返回当前路径
        }), 200

    except Exception as e:
        print(f"获取任务文件失败: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500
@decompress_bp.get("/<task_id>/folder-content")
@AuthUtils.token_required
@task_permission_required()
def get_folder_content(task_id: str):
    """获取指定文件夹的内容"""
    try:
        folder_type = request.args.get("folder_type", "downloads")  # 默认是downloads
        folder_path = request.args.get("path", "")

        _validate_task_id(task_id)  # 1.6高危漏洞修改：校验 task_id
        if folder_type not in ALLOWED_FOLDER_TYPES:  # 1.6高危漏洞修改：folder_type 白名单
            return jsonify({
                "success": False,
                "error": f"folder_type not allowed: {folder_type}"
            }), 400

        tasks_root = Path("data/tasks").resolve()  # 1.6高危漏洞修改：任务根目录
        task_base_path = safe_join_under(tasks_root, task_id, allow_subdirs=False)  # 1.6高危漏洞修改：防目录穿越

        # 1.6高危漏洞修改：folder_type 与 folder_path 均做安全拼接，禁止 .. / 绝对路径 跳出任务目录
        base_folder = safe_join_under(task_base_path, folder_type, allow_subdirs=False)
        target_path = base_folder if not folder_path else safe_join_under(base_folder, folder_path, allow_subdirs=True)

        if not target_path.exists() or not target_path.is_dir():
            return jsonify({
                "success": False,
                "error": "文件夹不存在"
            }), 404

        files = []
        for item in target_path.iterdir():
            if item.name in ["upload_map.json", "download_map.json", "error_logs"]:
                continue

            if item.is_file():
                files.append({
                    "name": item.name,
                    "size": item.stat().st_size,
                    "uploadTime": datetime.fromtimestamp(
                        item.stat().st_mtime
                    ).isoformat(),
                    "type": "file",
                    "isProcessed": processed_files_status.get(item.name, False),
                    "path": str(item.relative_to(task_base_path))
                })
            elif item.is_dir():
                files.append({
                    "name": item.name,
                    "size": 0,
                    "uploadTime": datetime.fromtimestamp(
                        item.stat().st_mtime
                    ).isoformat(),
                    "type": "folder",
                    "isProcessed": False,
                    "path": str(item.relative_to(task_base_path))
                })

        return jsonify({
            "success": True,
            "files": files,
            "current_path": str(target_path.relative_to(task_base_path))
        }), 200

    except BadPath as e:  # 1.6高危漏洞修改：统一返回 400
        return jsonify({
            "success": False,
            "error": str(e)
        }), 400
    except Exception as e:
        print(f"获取文件夹内容失败: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500
@decompress_bp.post("/pdf-cut/<task_id>")
@AuthUtils.token_required
@task_permission_required()
def pdf_cut(task_id: str):
    """
    PDF裁剪接口
    body: { "extraction_rules": [...] }
    """
    try:
        body = request.get_json(force=True) or {}
        extraction_rules = body.get("extraction_rules", [])
        selected_files = body.get("selected_files", []) 

        print(f"接收到的裁剪规则: {extraction_rules}")
        print(f"接收到的文件列表: {selected_files}")
        if not extraction_rules:
            from pathlib import Path
            import json
            TASKS_FILE = Path("data/tasks/tasks.json")
            if TASKS_FILE.exists():
                with open(TASKS_FILE, "r", encoding="utf-8") as f:
                    tasks = json.load(f)
                
                task = next((t for t in tasks if t.get("id") == task_id), None)
                if task:
                    # 从 extractionRules 数组中提取所有规则
                    task_extraction_rules = task.get("extractionRules", [])
                    
                    # 将所有规则转换为适合 pdf_cut.py 使用的格式
                    extraction_rules = []
                    for rule in task_extraction_rules:
                        if rule.get("enabled", True):  # 只处理启用的规则
                            extraction_rules.append({
                                "name": rule.get("name", ""),
                                "sections": rule.get("sections", []),
                                "keywords": [],  # 如果需要 keywords，可以从其他地方获取
                                "page_range": ""
                            })
                    
                    print(f"从任务配置获取的规则: {extraction_rules}")
        # 执行PDF裁剪
        success, result = perform_pdf_cut_after_classification(task_id, selected_files=selected_files)
        
        if success:
            return jsonify({
                "success": True,
                "message": "PDF裁剪完成",
                "results": result
            }), 200
        else:
            return jsonify({
                "success": False,
                "message": "PDF裁剪失败",
                "error": result
            }), 500
            
    except Exception as e:
        print(f"PDF裁剪过程中出现错误: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "success": False,
            "message": "PDF裁剪过程中出现错误",
            "error": str(e)
        }), 500
#
@decompress_bp.post("/extract-and-classify-stream/register")
@AuthUtils.token_required
@task_permission_required()
def register_extract_stream():
    """
    注册一个短 stream_id 用于后续 SSE 连接，避免长 URL 导致 414 错误。
    Body JSON: { "task_id": "...", "filenames": ["a.zip","b.pdf",...], "filter_rules": [...] }
    返回: { "stream_id": "..." }
    """

    raw = request.get_data(as_text=True)
    # print("[DEBUG] register raw body:", raw)
    js = request.get_json(silent=True)
    # print("[DEBUG] register parsed json:", js)


    body = request.get_json(silent=True) or {}
    filenames = body.get("filenames")
    task_id = body.get("task_id")
    filter_rules = body.get("filter_rules", [])

    if not task_id or not filenames or not isinstance(filenames, (list, tuple)):
        return jsonify({"success": False, "error": "task_id and filenames(array) required"}), 400
    try:
        _validate_task_id(task_id)
    except BadPath as e:
        return jsonify({"success": False, "error": f"invalid task_id: {e}"}), 400

    validated = []
    for fn in filenames:
        try:
            rel = _reject_if_unsafe_user_path(fn, allow_subdirs=False)
        except BadPath as e:
            return jsonify({"success": False, "error": f"invalid filename: {fn} ({e})"}), 400
        ext = rel.suffix.lower()
        if ext not in ALLOWED_ENTRY_EXTS_GLOBAL:
            return jsonify({"success": False, "error": f"file extension not allowed: {ext}"}), 400
        validated.append(rel.name)

    stream_id = str(uuid.uuid4())
    stream_id = str(uuid.uuid4())
    register_stream(stream_id, {"task_id": task_id, "filenames": validated, "filter_rules": filter_rules, "created": datetime.utcnow()})
    print(f"[DEBUG] registered stream_id={stream_id}, keys={stream_keys()[:20]}")
    return jsonify({"success": True, "stream_id": stream_id}), 200
    
@decompress_bp.get("/extract-and-classify-stream")
@AuthUtils.token_required
@task_permission_required()
def extract_and_classify_stream():
    """
    SSE 接口：批量处理（兼容注册后以 stream_id 连接，避免长 URL）。
    支持两种调用方式：
      - 旧方式：GET ?filenames=...&task_id=...
      - 新方式：GET ?stream_id=... （先 POST /extract-and-classify-stream/register 获取 stream_id）
    """
    print("[DEBUG] incoming GET args:", request.args.to_dict())
    print("[DEBUG] registry keys:", list(_stream_registry.keys())[:20])
    stream_id = request.args.get("stream_id")
    from_registry = False
    info = None

    if stream_id:
        info = get_stream_info(stream_id)
        if not info:
            return jsonify({"status": "error", "message": "stream_id not found"}), 404
        # 从注册信息恢复参数（已在 register 时验证）
        filenames = info["filenames"]
        task_id = info["task_id"]
        filter_rules = info.get("filter_rules", [])
        from_registry = True
    else:
        filenames_raw = request.args.get("filenames", "")
        task_id = request.args.get("task_id")
        filter_rules_json = request.args.get("filter_rules", "[]")

        if not task_id:
            return jsonify({"status": "error", "message": "请先选择任务 (task_id)"}), 400
        if not filenames_raw:
            return jsonify({"status": "error", "message": "缺少 filenames 参数"}), 400

        try:
            filter_rules = json.loads(filter_rules_json) if filter_rules_json else []
        except json.JSONDecodeError:
            return jsonify({"status": "error", "message": "filter_rules 参数格式错误"}), 400

        filenames = [fn.strip() for fn in filenames_raw.split(",") if fn.strip()]

    # 以下验证仅在非 registry 来源时执行（registry 已在注册时验证）
    try:
        _validate_task_id(task_id)
    except BadPath as e:
        return jsonify({"status": "error", "message": f"非法 task_id: {e}"}), 400

    ALLOWED_ENTRY_EXTS = {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2",
                         ".pdf", ".docx", ".doc", ".xlsx", ".xls",
                         ".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".txt"}

    def validate_requested_filename(fn: str) -> str:
        rel = _reject_if_unsafe_user_path(fn, allow_subdirs=False)
        ext = rel.suffix.lower()
        if ext not in ALLOWED_ENTRY_EXTS:
            raise BadPath(f"file extension not allowed: {ext or '(none)'}")
        return rel.name

    if not from_registry:
        try:
            filenames = [validate_requested_filename(fn) for fn in filenames]
        except BadPath as e:
            return jsonify({"status": "error", "message": f"非法 filenames 参数: {e}"}), 400



    task_dirs = task_folder_utils.get_task_dirs(task_id)
    extracted_base = task_dirs["extracted"]
    os.makedirs(extracted_base, exist_ok=True)
    # ===================== 20260303修改：仅保留/展示 Word & PDF =====================
    # 需求：解压缩日志中除 word/pdf 外不显示；解压后其它格式可直接删除。
    # 实现：仅将 .pdf/.doc/.docx 视为“有效文件”；其它格式在解压遍历时直接忽略（不写入 extracted 目录）。
    KEEP_DOC_EXTS = {".pdf", ".doc", ".docx"}
    # ================================================================================

    tracker = StatusTracker(task_id)  # 1224修改[总日志追踪]：引入状态追踪器

    def match_file_rules(fname: str, fsize: int, is_single_file: bool = False) -> bool:
        """
        根据筛选规则判断文件是否符合条件。
        如果是单个文件（非压缩包解压），直接返回True
        所有规则之间是 AND 关系，同一规则内的多个关键词是 OR 关系。
        """
        # 如果是单个文件，直接返回True（不需要筛选）
        if is_single_file:
            return True

        if not filter_rules:
            return True

        for rule in filter_rules:
            rule_type = rule.get('type')
            condition = rule.get('condition')
            value = rule.get('value', '')

            if rule_type == 'filename':
                if condition == 'contains':
                    # 支持中文逗号分隔的多个关键词，OR关系
                    keywords = [k.strip() for k in value.split('，') if k.strip()]
                    if not keywords:
                        continue
                    # 只要文件名包含任一关键词即为匹配
                    if not any(keyword.lower() in fname.lower() for keyword in keywords):
                        return False
                elif condition == 'not_contains':
                    # 支持中文逗号分隔的多个关键词，OR关系
                    keywords = [k.strip() for k in value.split('，') if k.strip()]
                    if not keywords:
                        continue
                    # 如果文件名包含任一关键词，则不匹配
                    if any(keyword.lower() in fname.lower() for keyword in keywords):
                        return False
            elif rule_type == 'filesize':
                try:
                    size_value = int(value) * 1024  # 转换为字节
                    if condition == 'greater_than':
                        if fsize <= size_value:
                            return False
                    elif condition == 'less_than':
                        if fsize >= size_value:
                            return False
                except ValueError:
                    # 如果数值无效，跳过此规则
                    continue

        return True

    def safe_folder_name(name: str) -> str:
        # 把 extension 去掉，安全化文件夹名
        return Path(name).stem.replace(" ", "_")

    def copy_to_dest(src_path: Path, compressed_folder_name: str, is_match: bool, is_single_file: bool = False):
        """
        复制文件到目标目录
        如果是单个文件，直接复制到 right 目录，不创建子文件夹
        """
        if is_single_file:
            # 单个文件直接复制到 right 目录，不创建子文件夹
            subdir = "right"
            dest_parent = extracted_base / subdir
            os.makedirs(dest_parent, exist_ok=True)
            try:
                dest_path = dest_parent / src_path.name
                # 只是复制，不移动原文件
                shutil.copy2(str(src_path), str(dest_path))
                return dest_path
            except Exception as e:
                print(f"复制文件失败: {e}")
                return None
        else:
            # 压缩包解压的文件仍按原逻辑处理
            subdir = "right" if is_match else "error"
            # 创建以原始文件名命名的子文件夹
            original_folder_name = safe_folder_name(compressed_folder_name)
            dest_parent = extracted_base / subdir / original_folder_name
            os.makedirs(dest_parent, exist_ok=True)
            try:
                dest_path = dest_parent / src_path.name
                # 只是复制，不移动原文件
                shutil.copy2(str(src_path), str(dest_path))
                return dest_path
            except Exception as e:
                print(f"复制文件失败: {e}")
                return None

    def process_single_file_entry(file_name: str):
        # 1225：[新增] 计算主键（地块编码）用于日志追踪
        try:
            file_stem = Path(file_name).stem
            # 逻辑：取下划线前的部分作为地块编码，如果没有下划线则用文件名本身
            pk = file_stem.split('_')[0] if '_' in file_stem else file_stem
        except:
            pk = file_name
        """处理单个上传/下载文件：如果是 zip 则解压并遍历，否则直接处理该文件本身"""
        # 优先在任务 uploads/downloads 中查找
        # 1.6高危漏洞修改：防止 file_name 为绝对路径导致越权读取（如 /etc/passwd）
        try:
            upload_candidate = safe_join_under(Path(task_dirs["uploads"]), file_name, allow_subdirs=False)
            download_candidate = safe_join_under(Path(task_dirs["downloads"]), file_name, allow_subdirs=False)
        except BadPath as e:
            yield f"⚠ 非法文件名 {file_name}，跳过: {e}"
            tracker.update_file_stage(file_name, Stage.DECOMPRESS, Status.FAILED, f"非法文件名: {e}", primary_key=pk)
            return
        source_path = None
        source_label = None
        if file_name in ["upload_map.json", "download_map.json"]:
            return  # 直接跳过处理

        if upload_candidate.exists():
            source_path = upload_candidate
            source_label = "TASK_UPLOADS"
        elif download_candidate.exists():
            source_path = download_candidate
            source_label = "TASK_DOWNLOADS"

        # yield f"处理文件: {file_name} 来源: {source_label or 'UNKNOWN'}"

        if not source_path or not source_path.exists():
            yield f"⚠ 文件 {file_name} 未找到，跳过"
            # 1224修改[总日志追踪]：记录失败
            tracker.update_file_stage(file_name, Stage.DECOMPRESS, Status.FAILED, "文件未找到", primary_key=pk)
            return

        # 1228修改：判断是否为压缩包
        file_ext = source_path.suffix.lower()
        is_archive = file_ext in ['.zip', '.rar', '.7z', '.tar', '.gz', '.bz2']
        # 1228修改：如果是压缩包，才标记为处理中；非压缩包保持原状态（可能是SKIPPED）
        if is_archive:
            tracker.update_file_stage(file_name, Stage.DECOMPRESS, Status.PROCESSING, primary_key=pk)    # 1224修改[总日志追踪]：记录处理中

        compressed_folder_name = safe_folder_name(file_name)

        file_ext = source_path.suffix.lower()
        # 如果是 zip，解压到临时目录并遍历
        if file_ext == ".zip":
            target_right_folder = extracted_base / "right" / compressed_folder_name
            if target_right_folder.exists():
                shutil.rmtree(target_right_folder)  # 删除原有文件夹
            os.makedirs(target_right_folder, exist_ok=True)  # 确保新文件夹存在
            # =====20260303修改:清理 error 目录下同名文件夹，避免历史残留=====
            target_error_folder = extracted_base / "error" / compressed_folder_name
            if target_error_folder.exists():
                shutil.rmtree(target_error_folder)
            # =============================================================
            tmpdir = tempfile.mkdtemp()
            tmp_base = Path(tmpdir).resolve()  # 1.6高危漏洞修改：Zip Slip 防护基准目录
            try:
                with zipfile.ZipFile(str(source_path), 'r') as zf:
                    for info in zf.infolist():
                        filename = info.filename
                        if info.flag_bits & 0x800:
                            # Python 3.7+ 已经按 utf‑8 处理，通常无需修改
                            pass
                        else:
                            filename = _decode_zip_filename(filename)

                        target_path = Path(tmpdir) / filename
                        # 1.6高危漏洞修改：Zip Slip 防护（禁止压缩包条目写出 tmpdir）
                        try:
                            target_path.resolve().relative_to(tmp_base)
                        except ValueError:
                            continue

                        # ✅ 关键修复点：目录单独处理
                        if info.is_dir():
                            target_path.mkdir(parents=True, exist_ok=True)
                            continue

                        # 确保父目录存在
                        target_path.parent.mkdir(parents=True, exist_ok=True)

                        # 写文件
                        with zf.open(info) as src, open(target_path, "wb") as dst:
                            shutil.copyfileobj(src, dst)
                # =============20260303修改=============
                kept_count = 0          # 仅统计/展示 Word & PDF
                matched_count = 0       # 满足筛选规则的“有效文件”数量
                matched_files = []
                # ======================================
                # 遍历 tmpdir 下所有文件
                for root, dirs, files in os.walk(tmpdir):
                    for f in files:
                        full = Path(root) / f
                        ext = full.suffix.lower()

                         # ===20260303修改：仅保留 Word/PDF，其余格式不落盘不展示====
                        if ext not in KEEP_DOC_EXTS:
                            continue
                        # =========================================================
                        try:
                            fsize = full.stat().st_size
                        except Exception:
                            fsize = 0
                        # ===20260303修改：====
                        kept_count += 1

                        # ✅ 调用匹配函数判断是否符合条件
                        is_match = match_file_rules(full.name, fsize, is_single_file=False)

                        # 1229修改[新增] 重命名逻辑：确保解压后的文件带有主键前缀
                        target_filename = full.name
                        if pk and not target_filename.startswith(pk):
                            target_filename = f"{pk}_{target_filename}"

                        if is_match:
                            # 符合条件的文件，复制到 right/原始文件夹名
                            dest_path = extracted_base / "right"/ compressed_folder_name / target_filename
                            os.makedirs(dest_path.parent, exist_ok=True)
                            shutil.copy2(str(full), str(dest_path))
                            matched_count += 1
                            matched_files.append(full.name)
                            status_icon = "✅"
                            # yield f"{status_icon} {full.name} ({fsize/1024:.1f}KB) -> 已存储到 right/{compressed_folder_name}/{full.name}"
                        # ========20260303修改========
                        else:
                            #不符合筛选规则的 Word/PDF：不落盘不展示
                            pass

                note_msg = None
                # 若无有效文件或无符合筛选的有效文件：在总日志中标注
                if kept_count == 0 or matched_count == 0:
                    note_msg = "无符合条件的文件（解压后仅保留 .pdf/.doc/.docx；其余格式已忽略）"
                    yield f"⚠ {file_name} 解压完成：{note_msg}"
                else:
                    matched_files_str = ", ".join(matched_files)
                    yield f"✅ {file_name} 解压完成：有效文件 {kept_count} 个，符合筛选 {matched_count} 个: [{matched_files_str}]"
                # ================================
                # 1228修改：循环结束后统一更新状态
                if is_archive:
                    # 20260303修改：新增note_msg
                    final_status = Status.FAILED if note_msg else Status.SUCCESS
                    tracker.update_file_stage(file_name, Stage.DECOMPRESS, final_status, note_msg, primary_key=pk)
            except Exception as e:
                yield f"❌ 解压或处理失败: {str(e)}"
            finally:
                try:
                    shutil.rmtree(tmpdir)
                except Exception:
                    pass

        # 处理RAR文件
        elif file_ext == ".rar":
            target_right_folder = extracted_base / "right" / compressed_folder_name
            if target_right_folder.exists():
                shutil.rmtree(target_right_folder)  # 删除原有文件夹
            os.makedirs(target_right_folder, exist_ok=True)  # 确保新文件夹存在
            #=====20260303修改：清理 error 目录下同名文件夹，避免历史残留=====
            target_error_folder = extracted_base / "error" / compressed_folder_name
            if target_error_folder.exists():
                shutil.rmtree(target_error_folder)
            # ==============================================================
            tmpdir = tempfile.mkdtemp()
            try:
                # 尝试多种方式解压RAR文件
                success = False
                error_msg = ""
                
                # 检查文件大小是否合理
                file_size = source_path.stat().st_size
                if file_size < 1024:  # 小于1KB可能是损坏的文件
                    yield f"❌ {file_name} 文件大小异常 ({file_size} bytes)，可能已损坏"
                    return
                
                # 方式1: 尝试使用 rarfile 库（更安全的方式）
                try:
                    import rarfile
                    with rarfile.RarFile(str(source_path)) as rf:
                        # 获取所有文件并安全解压
                        for member in rf.infolist():
                            if not member.isdir():  # 只处理文件，跳过目录
                                # 安全处理文件路径
                                filename = member.filename.replace('\\', '/').lstrip('/')
                                target_path = Path(tmpdir) / filename
                                
                                # 防止路径遍历攻击
                                try:
                                    # 确保目标路径在临时目录内
                                    target_path.resolve().relative_to(tmpdir.resolve())
                                except ValueError:
                                    # 路径不安全，跳过此文件
                                    continue
                                
                                # 创建必要的目录结构
                                target_path.parent.mkdir(parents=True, exist_ok=True)
                                # 解压文件
                                rf.extract(member, path=tmpdir)
                        success = True
                except rarfile.RarCannotExec:
                    error_msg = "rarfile库不可用，尝试命令行工具"
                except rarfile.BadRarFile:
                    error_msg = "RAR文件损坏或格式不支持"
                except rarfile.PasswordRequired:
                    error_msg = "RAR文件需要密码"
                except rarfile.NotRarFile:
                    error_msg = "文件不是有效的RAR格式"
                except Exception as e:
                    error_msg = f"rarfile解压失败: {str(e)}"
                
                # 如果rarfile方式失败，尝试命令行工具
                if not success:
                    has_unrar = shutil.which("unrar") is not None
                    has_7z = shutil.which("7z") is not None
                    
                    if has_unrar:
                        cmd = ["unrar", "x", "-y", str(source_path), str(tmpdir)]
                    elif has_7z:
                        cmd = ["7z", "x", str(source_path), f"-o{tmpdir}", "-y"]
                    else:
                        yield f"❌ 当前环境不支持 RAR 解压，请安装 unrar 或 7-Zip 工具"
                        return
                    
                    try:
                        proc = subprocess.run(
                            cmd,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            text=True,
                            timeout=300  # 5分钟超时
                        )
                        if proc.returncode == 0:
                            success = True
                        else:
                            error_msg = f"命令行解压失败: {proc.stderr.strip() or proc.stdout.strip()}"
                    except subprocess.TimeoutExpired:
                        error_msg = "RAR解压超时"
                    except Exception as e:
                        error_msg = f"命令行解压异常: {str(e)}"
                
                if not success:
                    yield f"❌ {file_name} RAR解压失败: {error_msg}"
                    return

                # 20260303修改：解压成功，遍历 tmpdir 下所有文件（仅保留 Word/PDF）
                kept_count = 0

                matched_count = 0
                matched_files = []
                for root, dirs, files in os.walk(tmpdir):
                    for f in files:
                        full = Path(root) / f
                        # ====20260303修改====
                        ext = full.suffix.lower()
                        if ext not in KEEP_DOC_EXTS:
                            continue
                        # ====================

                        try:
                            fsize = full.stat().st_size
                        except Exception:
                            fsize = 0

                        target_filename = full.name
                        if pk and not target_filename.startswith(pk):
                            target_filename = f"{pk}_{target_filename}"

                        # ====20260303修改====
                        kept_count += 1

                        is_match = match_file_rules(full.name, fsize, is_single_file=False)
                        if is_match:
                            # 符合条件的文件，复制到 right/原始文件夹名
                            dest_path = extracted_base / "right" / compressed_folder_name / target_filename
                            os.makedirs(dest_path.parent, exist_ok=True)
                            shutil.copy2(str(full), str(dest_path))
                            matched_count += 1
                            matched_files.append(full.name)
                            status_icon = "✅"
                            # yield f"{status_icon} {full.name} ({fsize/1024:.1f}KB) -> 已存储到 right/{compressed_folder_name}/{full.name}"
                        else:
                            # =======20260303修改：不符合筛选规则的 Word/PDF：不落盘不展示=======
                            pass

                note_msg = None
                if kept_count == 0 or matched_count == 0:
                    note_msg = "无符合条件的文件（解压后仅保留 .pdf/.doc/.docx；其余格式已忽略）"
                    yield f"⚠ {file_name} RAR解压完成：{note_msg}"
                else:
                    matched_files_str = ", ".join(matched_files)
                    yield f"✅ {file_name} RAR解压完成：有效文件 {kept_count} 个，符合筛选 {matched_count} 个: [{matched_files_str}]"

                # 统一写入解压阶段状态（带上 note_msg 作为标注信息）
                if is_archive:
                    tracker.update_file_stage(file_name, Stage.DECOMPRESS, Status.SUCCESS, note_msg, primary_key=pk)
                # ===========================================================================
            except Exception as e:
                yield f"❌ {file_name} RAR 解压异常: {str(e)}"
            finally:
                shutil.rmtree(tmpdir, ignore_errors=True)

        else:
            # 非 ZIP/RAR 文件或目录
            if source_path.is_dir():
                # ⭐ 文件夹：递归遍历所有文件
                folder_file_count = 0
                matched_count = 0
                matched_files = []
                for root, dirs, files in os.walk(source_path):
                    for f in files:
                        full = Path(root) / f

                        # 20260303修改：仅保留 Word/PDF
                        ext = full.suffix.lower()
                        if ext not in KEEP_DOC_EXTS:
                            continue

                        fsize = full.stat().st_size if full.exists() else 0
                        folder_file_count += 1
                        # 调用匹配逻辑
                        is_match = match_file_rules(full.name, fsize, is_single_file=False)
                        if is_match:
                            # 符合条件的文件，复制到 right/原始文件夹名
                            dest_path = extracted_base / "right" / compressed_folder_name / full.name
                            os.makedirs(dest_path.parent, exist_ok=True)
                            shutil.copy2(str(full), str(dest_path))
                            matched_count += 1

                            matched_files.append(full.name)  # 记录符合条件的文件名
                            # 1224修改[总日志追踪]：注册有效文件
                            tracker.update_file_stage(full.name, Stage.DECOMPRESS, Status.SUCCESS, primary_key=pk)
                            status_icon = "✅"
                            yield f"{status_icon} {full.name} ({fsize/1024:.1f}KB) -> 已存储到 right/{compressed_folder_name}/{full.name}"
                        else:
                            # =====20260303修改：不符合筛选规则的 Word/PDF：不落盘不展示=====
                            pass

                note_msg = None
                # 仅展示 Word/PDF；若无有效文件/无符合筛选的有效文件：在总日志中标注
                if folder_file_count == 0 or matched_count == 0:
                    note_msg = "无符合条件的文件（仅保留 .pdf/.doc/.docx；其余格式已忽略）"
                    yield f"⚠ {source_path.name} 完成：{note_msg}\n\n"
                else:
                    matched_files_str = ", ".join(matched_files)
                    yield f"✅ {source_path.name} 完成：有效文件 {folder_file_count} 个，符合筛选 {matched_count} 个: [{matched_files_str}]\n\n"

                # 统一写入文件夹的解压阶段状态（带上 note_msg 作为标注信息）
                tracker.update_file_stage(file_name, Stage.DECOMPRESS, Status.SUCCESS, note_msg, primary_key=pk)
            else:
                # 普通文件 - 判断是否为PDF等单个文件
                ext = source_path.suffix.lower()
                try:
                    fsize = source_path.stat().st_size
                except Exception:
                    fsize = 0

                # 如果是PDF、DOCX等单个文件，不做任何处理，保持在原始文件夹（日志仅展示 Word/PDF）
                if ext in ['.pdf', '.docx', '.doc']:
                    status_icon = "✅"
                    yield f"{status_icon}{file_name} ({fsize / 1024:.1f}KB) -> 无需处理，保持在原始文件夹"
                else:
                    # 非 Word/PDF：不展示、不落盘；但需要在总日志中标注“无符合条件文件”
                    note = "无符合条件的文件（仅保留 .pdf/.doc/.docx；其余格式已忽略）"
                    tracker.update_file_stage(file_name, Stage.DECOMPRESS, Status.SUCCESS, note, primary_key=pk)
                    # ==============20260303修改：上述修改完成===================
    def generate():
        try:
            # 显示筛选条件信息
            if not filter_rules:
                yield f"data: 📝 未设置筛选条件，默认所有文件都视为符合条件\n\n"
            else:  
                conditions = []
                for rule in filter_rules:
                    rule_type = rule.get('type')
                    condition = rule.get('condition')
                    value = rule.get('value')
                    
                    if rule_type == 'filename':
                        condition_text = '包含' if condition == 'contains' else '不包含'
                        conditions.append(f"文件名{condition_text}关键词'{value}'")
                    elif rule_type == 'filesize':
                        condition_text = '大于' if condition == 'greater_than' else '小于'
                        conditions.append(f"文件大小{condition_text}{value}KB")
                        
                yield f"data: 📝 筛选条件: {' 且 '.join(conditions)}\n\n"

            for fn in filenames:
                for log in process_single_file_entry(fn):
                    yield f"data: {log}\n\n"
                    time.sleep(0.01)
            # 不再需要清理处理过的文件，因为所有操作都在downloads目录中进行
            yield f"data: ✅ 解压和筛选处理完成"

        except Exception as e:
            yield f"data: ❌ 处理异常：{str(e)}\n\n"
            import traceback
            traceback.print_exc()
        finally:
            yield f"data: END\n\n"


    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return Response(generate(), mimetype="text/event-stream", headers=headers)