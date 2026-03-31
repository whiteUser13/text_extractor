import uuid
from flask import Blueprint
import os, json
from flask import Flask, request, jsonify, send_from_directory, render_template_string, abort, Response
from flask import jsonify
from pathlib import Path
import time
from datetime import datetime
import tempfile
import zipfile
import shutil
from utils.json_manager import ParseJsonFileManager,RecordJsonFileManager,UploadJsonFileManager
from concurrent.futures import ThreadPoolExecutor, as_completed
from utils import task_folder_utils, util_parse, util_chunk,file_utils,safe_name
import traceback
from utils.auth_util import AuthUtils
from utils.task_verify import task_permission_required

parse_json_manager = ParseJsonFileManager()
record_json_manager = RecordJsonFileManager()
upload_json_manager = UploadJsonFileManager()
RECORD_JSON = Path("data/tasks/record.json")
if not RECORD_JSON.exists():
    RECORD_JSON.write_text("{}", encoding="utf-8")
PARSE_TASK_JSON = Path("data/tasks/parse_tasks.json")
if not PARSE_TASK_JSON.exists():
    PARSE_TASK_JSON.write_text("{}", encoding="utf-8")
executor = ThreadPoolExecutor(max_workers=3)
def api_ok(data=None):
    return {"code": 0, "msg": "ok", "data": data}
def api_fail(msg: str, code: int = 1):
    return {"code": code, "msg": msg}
def list_source_pdfs_for_task(task_id):
    """
    列出指定任务 extracted/right 目录下的所有 PDF 和 Word 文件
    """
    task_dirs = task_folder_utils.get_task_dirs(task_id)
    pdf_cut_dir = task_dirs["pdf_cut"]
    downloads_dir = task_dirs["downloads"]
    extracted_right_dir = task_dirs["extracted_right"]

    if pdf_cut_dir.exists() and any(pdf_cut_dir.iterdir()):
        # pdf_cut 目录不为空，从 pdf_cut 获取文件
        return _list_files_from_directory(pdf_cut_dir, "pdf_cut", task_id)
    else:
        # pdf_cut 目录为空，从 downloads 和 extracted/right 获取文件
        items = []
        items.extend(_list_files_from_directory(downloads_dir, "downloads", task_id))
        items.extend(_list_files_from_directory(extracted_right_dir, "extracted/right", task_id))
        
        # 去重：如果同一文件名在多个目录中出现，优先级为 pdf_cut > downloads > extracted/right
        # 但这里我们只考虑 downloads 和 extracted/right，所以合并去重
        unique_items = {}
        for item in items:
            area_code = item["area_code"]
            if area_code not in unique_items:
                unique_items[area_code] = item
            else:
                # 如果已存在，保留优先级更高的（这里假设downloads优先级更高）
                existing = unique_items[area_code]
                current_source = item["source_dir"]
                existing_source = existing["source_dir"]
                
                # 优先级：downloads > extracted/right
                if (current_source == "downloads" and existing_source == "extracted/right"):
                    unique_items[area_code] = item
        
        return list(unique_items.values())
def _list_files_from_directory(search_dir, source_label, task_id):
    """
    从指定目录列出 PDF 和 Word文件
    """
    if not search_dir.exists():
        return []

    items_map = {}

    # 搜索 PDF 和 Word
    for p in search_dir.rglob("*"):
        if not p.is_file():
            continue

        ext = p.suffix.lower()
        if ext not in {".pdf", ".docx", ".doc"}:
            continue

        area_code = p.stem  # ✅ 统一 rid：文件名（不含扩展名）作为地块编号
        rel_path = p.relative_to(search_dir)

        # 文件类型优先级：PDF=1, Word=2
        priority = 1 if ext == ".pdf" else 2
        file_type = "word" if ext in {".docx", ".doc"} else "pdf"

        existing = items_map.get(area_code)
        # 如果之前没有记录，或者当前文件的优先级更高（数字更小）
        if (existing is None) or (priority < existing["priority"]):
            items_map[area_code] = {
                "area_code": area_code,
                "folder_name": str(rel_path),
                "download_time": p.stat().st_mtime,
                "status": "ready",
                "file_type": file_type,
                "priority": priority,
                "source_dir": source_label,  # 添加源目录信息
            }

    items = []
    for info in sorted(items_map.values(), key=lambda x: x["folder_name"]):
        info.pop("priority", None)  # 移除内部使用的优先级字段
        items.append(info)

    # 过滤掉已经解析过的文件
    return get_filtered_items_for_task(items, "parsed", task_id)
    
def get_filtered_items_for_task(items, record_type="parsed", task_id=""):
    """过滤掉已经记录的项目 - 支持任务目录"""
    if not task_id:
        # 如果没有任务ID，使用全局记录
        record = load_record()
        recorded_codes = set(record.get(record_type, []))
        return [item for item in items if item.get("area_code") not in recorded_codes]
    else:
        # 如果有任务ID，使用任务特定的记录
        # 暂时使用全局记录
        record = load_record()
        recorded_codes = set(record.get(record_type+"-"+task_id, []))
        # ✅ 过滤掉“解析目录空/不完整”的记录，保证这类文件仍会出现在待解析列表里，便于重试
        if record_type.startswith("parsed"):
            try:
                task_dirs = task_folder_utils.get_task_dirs(task_id)
                invalid = set()
                for rid in recorded_codes:
                    rid2 = str(rid).rsplit(".", 1)[0]
                    parsed_folder = task_dirs["parsed"] / rid2
                    if (not parsed_folder.exists()) or (not any(parsed_folder.iterdir())):
                        invalid.add(rid)
                if invalid:
                    recorded_codes -= invalid
            except Exception:
                pass

        return [item for item in items if item.get("area_code") not in recorded_codes]
def load_record():
    """加载记录文件"""
    try:
        with open(RECORD_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"parsed": [], "chunked": []}
def save_record(data):
    """保存记录文件"""
    with open(RECORD_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
def add_to_record(area_codes, record_type="parsed"):
    """添加记录到文件"""
    with record_json_manager.file_lock:
        record = load_record()
        if record_type not in record:
            record[record_type] = []

        for area_code in area_codes:
            if area_code not in record[record_type]:
                record[record_type].append(area_code)

        save_record(record)

parse_bp = Blueprint("parse", __name__)

# 12.9改
@parse_bp.get("/mineru/file-sizes")
@AuthUtils.token_required
@task_permission_required()
def get_mineru_file_sizes():
    """
    获取任务目录中所有 PDF 和 Word 文件的实际大小
    如果 pdf_cut 目录存在且不为空，从 pdf_cut 获取；否则从 downloads 和 extracted/right 获取
    """
    try:
        task_id = request.args.get("task_id", "")

        if task_id:
            # 使用任务目录的
            task_dirs = task_folder_utils.get_task_dirs(task_id)
            pdf_cut_dir = task_dirs["pdf_cut"]
            downloads_dir = task_dirs["downloads"]
            extracted_right_dir = task_dirs["extracted_right"]

            # 检查 pdf_cut 目录是否为空
            if pdf_cut_dir.exists() and any(pdf_cut_dir.iterdir()):
                # pdf_cut 目录不为空，从 pdf_cut 获取文件
                search_dirs = [(pdf_cut_dir, "pdf_cut")]
            else:
                # pdf_cut 目录为空，从 downloads 和 extracted/right 获取文件
                search_dirs = [(downloads_dir, "downloads"), (extracted_right_dir, "extracted/right")]

            file_sizes = {}

            for search_dir, source_label in search_dirs:
                if not search_dir.exists():
                    continue

                # 遍历目录中的所有 PDF 和 Word 文件
                for p in search_dir.rglob("*"):
                    if not p.is_file():
                        continue

                    ext = p.suffix.lower()
                    if ext not in {".pdf", ".docx", ".doc"}:
                        continue

                    area_code = p.stem  # 文件名（不含扩展名）作为地块编号
                    file_size_bytes = p.stat().st_size
                    file_size_kb = round(file_size_bytes / 1024, 2)

                    # 文件类型优先级：PDF=1, Word=2
                    file_priority = 1 if ext == ".pdf" else 2
                    file_type = "word" if ext in {".docx", ".doc"} else "pdf"

                    # 如果地块编码已存在，检查是否需要替换（按优先级）
                    existing = file_sizes.get(area_code)
                    if existing is None or \
                            file_priority < existing.get("priority", 2) or \
                            (file_priority == existing.get("priority", 2) and file_size_bytes > existing.get("size_bytes", 0)):
                        file_sizes[area_code] = {
                            "area_code": area_code,
                            "file_path": str(p),
                            "size_bytes": file_size_bytes,
                            "size_kb": file_size_kb,
                            "size_mb": round(file_size_kb / 1024, 2),
                            "file_type": file_type,
                            "source_dir": source_label,
                            "priority": file_priority,  # 内部使用，用于排序
                        }

            # 移除内部使用的优先级字段
            for area_code in file_sizes:
                file_sizes[area_code].pop("priority", None)

            return jsonify({
                "code": 0,
                "msg": "success",
                "data": file_sizes
            })

        else:
            return jsonify({
                "code": 0,
                "msg": "success",
                "data": {}
            })

    except Exception as e:
        return jsonify({
            "code": 1,
            "msg": f"获取文件大小失败: {str(e)}",
            "data": {}
        })

# 新增：获取记录文件内容
@parse_bp.get("/mineru/record")
@AuthUtils.token_required
@task_permission_required()
def get_record():
    """获取记录文件内容"""
    return jsonify(api_ok(load_record()))
@parse_bp.get("/mineru/decompressed")
@AuthUtils.token_required
@task_permission_required()
def mineru_decompressed():
    """列出所有 PDF（地块列表）"""
    try:
        task_id = request.args.get("task_id", "")

        if task_id:
            # 使用任务目录
            items = list_source_pdfs_for_task(task_id)
        else:
            return jsonify(api_fail("缺少任务id")), 401

        return jsonify(api_ok(items))
    except Exception as e:
        return jsonify(api_fail(str(e))), 500

# 下载解析结果
@parse_bp.get("/mineru/download-all-parse-results")
@AuthUtils.token_required
@task_permission_required()
def mineru_download_all_parse_results():
    """打包下载 mineru_outputs 下所有 MinerU 解析结果"""
    try:
        task_id = request.args.get("task_id", "")

        if task_id:
            # 使用任务目录
            task_dirs = task_folder_utils.get_task_dirs(task_id)
            parsed_dir = task_dirs["parsed"]

            if not parsed_dir.exists() or not any(parsed_dir.iterdir()):
                return jsonify(api_fail("没有可用的解析结果")), 404

            target_dir = parsed_dir
        else:
            return jsonify(api_fail("缺少任务id")), 403

        with tempfile.NamedTemporaryFile(delete=False, suffix='.zip') as tmp_file:
            zip_path = Path(tmp_file.name)

        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for item in target_dir.rglob('*'):
                if item.is_file():
                    arcname = item.relative_to(target_dir)
                    zipf.write(item, arcname)

        zip_filename = f"task_{task_id}_parse_results.zip" if task_id else "all_parse_results.zip"
        return send_from_directory(str(zip_path.parent), zip_path.name, as_attachment=True, download_name=zip_filename)
    except Exception as e:
        return jsonify(api_fail(str(e))), 500

# 执行 MinerU 解析（支持批量地块并行）

def _parsed_outputs_ok(task_dirs, rid: str) -> bool:
    """
    判断某个 rid 的解析输出是否有效（非空）。
    ⚠️ 注意：不再把“只有 rid.pdf”视为解析成功（常见于 DOCX->PDF 或强制中断时的中间产物）。
    """
    parsed_dir = task_dirs["parsed"] / rid
    if not parsed_dir.exists() or not parsed_dir.is_dir():
        return False

    def _nonempty(p: Path) -> bool:
        try:
            return p.exists() and p.is_file() and p.stat().st_size > 0
        except Exception:
            return False

    images_dir = parsed_dir / "images"
    images_has_files = images_dir.exists() and any(images_dir.iterdir())

    md_ok = _nonempty(parsed_dir / f"{rid}.md")

    # content_list 文件名可能是 rid_content_list.json / content_list.json / 其它
    json_ok = False
    for cand in [parsed_dir / f"{rid}_content_list.json", parsed_dir / "content_list.json", parsed_dir / f"{rid}.json"]:
        if _nonempty(cand):
            json_ok = True
            break
    if not json_ok:
        json_ok = any(fp.suffix.lower()==".json" and fp.is_file() and fp.stat().st_size>0 for fp in parsed_dir.iterdir())

    # ✅ 不把 pdf 作为“成功”判据（避免只有转换 pdf 也算成功）
    return md_ok or json_ok or images_has_files

    # 允许 images 目录存在但必须至少有一个文件
    images_dir = parsed_dir / "images"
    images_has_files = images_dir.exists() and any(images_dir.iterdir())

    md_ok = (parsed_dir / f"{rid}.md").exists()
    pdf_ok = (parsed_dir / f"{rid}.pdf").exists()

    # content_list 文件名可能是 rid_content_list.json 或 content_list.json 或其它
    json_ok = False
    for cand in [parsed_dir / f"{rid}_content_list.json", parsed_dir / "content_list.json", parsed_dir / f"{rid}.json"]:
        if cand.exists():
            json_ok = True
            break
    if not json_ok:
        # 再兜底：目录里任何 .json 都算（避免命名差异）
        json_ok = any(fp.suffix.lower()==".json" for fp in parsed_dir.iterdir() if fp.is_file())

    return md_ok or pdf_ok or json_ok or images_has_files


@parse_bp.post("/mineru/parse")
@AuthUtils.token_required
@task_permission_required()
def mineru_parse():
    """
    body: { "area_codes": ["地块编码1", "地块编码2", ...], "task_id": "任务ID" }
    """
    try:
        body = request.get_json(force=True) or {}
        area_codes = body.get("area_codes") or []#去除后缀的文件名
        task_id = body.get("task_id", "")
        print("地块编码",area_codes)

        if not area_codes:
            return jsonify(api_fail("area_codes 不能为空")), 401
        if not task_id:
            return jsonify(api_fail("task_id 不能为空")), 402

        # 说明：MinerU 服务端/Word COM 转换通常不适合并发。
        # 这里默认使用单线程“批量顺序解析”，保证一次请求里解析多个文件都能成功。
        # 如你确认服务端与 DOCX 转换链路支持并发，可设置环境变量 PARSE_MAX_WORKERS>1。
        env_workers = int(os.getenv("PARSE_MAX_WORKERS", "1") or "1")
        max_workers = max(1, min(env_workers, 4))
        errors = []

        # 根据是否有task_id选择不同的解析函数
        parse_func = util_parse._run_single_parse_for_task
        print("route entered")
        print("area_codes:", area_codes)
        print("task_id:", task_id)

        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            task_dirs = task_folder_utils.get_task_dirs(task_id)

            # ✅ 统一 rid 口径：始终使用 stem（去掉扩展名）
            normalized = []
            for rid in area_codes:
                if not rid:
                    continue
                normalized.append(rid.rsplit(".", 1)[0])
            # rid为去除后缀的文件名
            futures = {ex.submit(parse_func, rid, task_id, task_dirs): rid for rid in normalized}
            for fut in as_completed(futures):

                rid = futures[fut]
                try:
                    fut.result()
                except Exception as e:
                    errors.append(f"{rid}: {e}")

        # 新增：记录已解析的文件
        print(errors)
        # ✅ 仅当解析输出有效时才计入成功（避免“空目录也算成功”）
        error_rids = {e.split(":")[0].strip() for e in errors}
        successful_codes = []
        for rid in normalized:
            if rid in error_rids:
                continue
            if not _parsed_outputs_ok(task_dirs, rid):
                errors.append(f"{rid}: 解析输出为空/不完整，可重试")
                continue
            successful_codes.append(rid)

        if successful_codes:
            add_to_record(successful_codes, "parsed-"+task_id)
        print(successful_codes)
        # 根据是否有task_id选择不同的列表函数
        data = {"parsed": util_chunk.list_parsed_for_task(task_id)}


        if errors:
            data["errors"] = errors
        return jsonify(api_ok(data))
    except Exception:
        return jsonify(api_fail(traceback.format_exc())), 500

# ==================== 新增：更新文件状态 (11.27)====================
@parse_bp.post('/update-file-status')
@AuthUtils.token_required
@task_permission_required()
def update_file_status():
    data = request.json
    task_id = data.get("task_id", None)
    file_name = data.get("file_name")
    status = data.get("status")  # 状态，如 "处理中"、"处理完成"
    stage = data.get("stage")  # 阶段，如 "解析中" 或 "完成"
    parsing_status = data.get("parsingStatus")  # 解析状态，如 "解析成功"
    chunking_status = data.get("chunkingStatus")  # 分块状态，如 "分块成功"
    if task_id:
        upload_json = task_folder_utils.get_downloads_dir(task_id) / "upload_map.json"
    else:
        return jsonify({"error": "缺少任务id字段"}), 403

    # 获取文件映射
    with upload_json_manager.file_lock:
        file_map = upload_json_manager.load_json_without_lock(upload_json)
        # origin_names = [item["origin_name"] for item in file_map]
        file_is_exist = False
        for item in file_map.values():
            if file_name == item["origin_name"]:
                file_map[item["id"]]["status"] = status
                file_map[item["id"]]["stage"] = stage
                file_map[item["id"]]["parsingStatus"] = parsing_status
                file_map[item["id"]]["chunkingStatus"] = chunking_status
                file_is_exist = True
                break
        if file_is_exist:
            upload_json_manager.save_json_without_lock(upload_json, file_map)
            return jsonify({"message": "File status updated"}), 200
        else:
            return jsonify({"error": "File not found"}), 404

# 1225新增
@parse_bp.post("/add/parse/task")
@AuthUtils.token_required
@task_permission_required()
def add_mineru_parse_task():
    """
    body: { "file_names": ["去除后缀的文件名", "去除后缀的文件名2", ...], "task_id": "任务ID" }
    """
    try:
        body = request.get_json(force=True) or {}
        file_names = body.get("file_names") or []#去除后缀的文件名
        task_id = body.get("task_id", "")

        if not file_names:
            return jsonify(api_fail("file_names不能为空")), 401
        if not task_id:
            return jsonify(api_fail("task_id 不能为空")), 402
        # 将任务添加到parse_task.json文件
        parse_json_path = file_utils.get_parse_task_json_path()
        if not parse_json_path.exists():
            parse_json_path.write_text("{}", encoding="utf-8")
        with parse_json_manager.file_lock:
            parse_tasks_dict = parse_json_manager.load_json_without_lock(parse_json_path)
            if task_id not in parse_tasks_dict:
                parse_tasks_dict[task_id] = {}
            parse_tasks = parse_tasks_dict[task_id]
            return_tasks = []
            for filename in file_names:
                parse_id = safe_name.base64_key(filename)
                if parse_id in parse_tasks:
                    # 0122修改：允许重复添加同一文件的解析任务（手动重解析）。
                    # 0122修改：重置任务状态，并清理旧解析产物，确保后续解析会覆盖旧结果。
                    try:
                        task_dirs = task_folder_utils.get_task_dirs(task_id)
                        parsed_dir = task_dirs.get('parsed')
                        if parsed_dir:
                            out_dir = Path(parsed_dir) / filename
                            if out_dir.exists():
                                shutil.rmtree(out_dir, ignore_errors=True)
                    except Exception:
                        pass
                    parse_tasks[parse_id]['status'] = 'pending'
                    parse_tasks[parse_id]['created_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    parse_tasks[parse_id]['msg'] = ''
                    parse_tasks[parse_id]['select_type'] = 'mineru'
                    parse_tasks[parse_id]['used'] = ''
                    parse_tasks[parse_id]['reparse_count'] = int(parse_tasks[parse_id].get('reparse_count', 0) or 0) + 1
                    return_tasks.append(parse_tasks[parse_id])
                    continue
                # 添加任务,把字典的键改为task_id-areacode的形式
                parse_tasks[parse_id] = {
                    "parse_id": parse_id,
                    "file_name": filename,
                    "status": "pending",
                    "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "size": 0,
                    "msg": "",
                    "task_id": task_id,
                    "select_type":"mineru",
                    "used":""
                }
                return_tasks.append(parse_tasks[parse_id])
            parse_json_manager.save_json_without_lock(parse_json_path, parse_tasks_dict)
        # 返回给前端parse_id,方便前端查看进度
        return jsonify({"message": "解析任务添加成功", "tasks": return_tasks})
    except Exception:
        return jsonify(api_fail(traceback.format_exc())), 500
@parse_bp.get("/info")
@AuthUtils.token_required
@task_permission_required()
def get_parse_task_info():
    """
    获取 JSON 文件中所有项的信息（以数组形式返回）
    """
    # 判断文件是否存在
    parse_json_path = file_utils.get_parse_task_json_path()
    if not parse_json_path.exists():
        return jsonify({"error": "数据文件不存在"}), 404
    task_id = request.args.get('task_id')
    if not task_id:
        return jsonify({"error": "请先选择任务"}), 400
    # 加载最新的内容
    try:
        tasks = parse_json_manager.load_json(parse_json_path)
    except json.JSONDecodeError:
        return jsonify({"error": "JSON 文件格式错误"}), 500

    result_list = []
    if task_id not in tasks:
        return jsonify({
            "count": len(result_list),
            "tasks": result_list
        })
    parse_tasks = tasks.get(task_id)
    for key, info in parse_tasks.items():
        result_list.append(info)
    # 返回 JSON
    return jsonify({
        "count": len(result_list),
        "tasks": result_list
    })
# 重试解析错误的文件
@parse_bp.get("/retry/error")
@AuthUtils.token_required
@task_permission_required()
def retry_error():
    # 判断文件是否存在
    # 加载当前任务文件
    task_id = request.args.get('task_id')
    if not task_id:
        return jsonify({"error": "请先选择任务"}), 400
    parse_json_path = file_utils.get_parse_task_json_path()
    # 加载当前任务文件
    if not parse_json_path.exists():
        parse_json_path.write_text("{}", encoding="utf-8")

    result_list = []
    with parse_json_manager.file_lock:
        tasks = parse_json_manager.load_json_without_lock(parse_json_path)
        if task_id not in tasks:
            return jsonify({"message": "刷新成功", "tasks": result_list})
        parse_tasks = tasks.get(task_id)
        for parse_id, task in parse_tasks.items():
            if(task.get("status") == "error"):
                parse_tasks[parse_id]["status"] = "pending"
                result_list.append(parse_tasks[parse_id])
        parse_json_manager.save_json_without_lock(parse_json_path, tasks)
    return jsonify({"message": "刷新成功", "tasks": result_list})

#1226新增
@parse_bp.post("/add/pure_txt/task")
@AuthUtils.token_required
@task_permission_required()
def add_text_parse_task():
    """
    body: { "file_names": ["去除后缀的文件名", "去除后缀的文件名2", ...], "task_id": "任务ID" }
    """
    try:
        body = request.get_json(force=True) or {}
        file_names = body.get("file_names") or []#去除后缀的文件名
        task_id = body.get("task_id", "")

        if not file_names:
            return jsonify(api_fail("file_names不能为空")), 401
        if not task_id:
            return jsonify(api_fail("task_id 不能为空")), 402
        # 将任务添加到parse_task.json文件
        parse_json_path = file_utils.get_parse_task_json_path()
        if not parse_json_path.exists():
            parse_json_path.write_text("{}", encoding="utf-8")
        with parse_json_manager.file_lock:
            parse_tasks_dict = parse_json_manager.load_json_without_lock(parse_json_path)
            if task_id not in parse_tasks_dict:
                parse_tasks_dict[task_id] = {}
            parse_tasks = parse_tasks_dict[task_id]
            return_tasks = []
            for filename in file_names:
                parse_id = safe_name.base64_key(filename)
                if parse_id in parse_tasks:
                    # 0122修改：允许重复添加同一文件的解析任务（手动重解析）。
                    # 0122修改：重置任务状态，并清理旧解析产物，确保后续解析会覆盖旧结果。
                    try:
                        task_dirs = task_folder_utils.get_task_dirs(task_id)
                        parsed_dir = task_dirs.get('parsed')
                        if parsed_dir:
                            out_dir = Path(parsed_dir) / filename
                            if out_dir.exists():
                                shutil.rmtree(out_dir, ignore_errors=True)
                    except Exception:
                        pass
                    parse_tasks[parse_id]['status'] = 'pending'
                    parse_tasks[parse_id]['created_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    parse_tasks[parse_id]['msg'] = ''
                    parse_tasks[parse_id]['select_type'] = 'pure_txt'
                    parse_tasks[parse_id]['used'] = ''
                    parse_tasks[parse_id]['reparse_count'] = int(parse_tasks[parse_id].get('reparse_count', 0) or 0) + 1
                    return_tasks.append(parse_tasks[parse_id])
                    continue
                # 添加任务,把字典的键改为task_id-areacode的形式
                parse_tasks[parse_id] = {
                    "parse_id": parse_id,
                    "file_name": filename,
                    "status": "pending",
                    "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "size": 0,
                    "msg": "",
                    "task_id": task_id,
                    "select_type":"pure_txt",
                    "used":""
                }
                return_tasks.append(parse_tasks[parse_id])
            parse_json_manager.save_json_without_lock(parse_json_path, parse_tasks_dict)
        # 返回给前端parse_id,方便前端查看进度
        return jsonify({"message": "文本解析任务添加成功", "tasks": return_tasks})
    except Exception:
        return jsonify(api_fail(traceback.format_exc())), 500
