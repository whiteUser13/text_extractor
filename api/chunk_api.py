from flask import Blueprint
import os, json
from flask import request, send_from_directory
from flask import jsonify
from pathlib import Path
import tempfile
import zipfile
from utils.json_manager import JsonFileManager
from utils import task_folder_utils, util_chunk, parse as parser
import traceback
from utils.auth_util import AuthUtils
from utils.status_tracker import StatusTracker, Stage, Status            #1224修改[总日志追踪]
from utils.task_verify import task_permission_required
json_manager = JsonFileManager()
RECORD_JSON = Path("data/tasks/record.json")
if not RECORD_JSON.exists():
    RECORD_JSON.write_text("{}", encoding="utf-8")

# 1225[新增] 辅助函数：统一提取主键（地块编码）逻辑
def _get_pk(file_path_or_name):
    """从文件路径或文件名提取主键"""
    if isinstance(file_path_or_name, Path):
        stem = file_path_or_name.stem
    else:
        stem = Path(file_path_or_name).stem
    return stem.split('_')[0] if '_' in stem else stem

def api_ok(data=None):
    return {"code": 0, "msg": "ok", "data": data}
def api_fail(msg: str, code: int = 1):
    return {"code": code, "msg": msg}


def _normalize_rid(code: str) -> str:
    """Normalize report_id to rid (stem, no extension, no temp prefix)."""
    if not code:
        return ""
    name = str(code).strip()
    name = Path(name).name
    if name.startswith("~$"):
        name = name[2:]
    return Path(name).stem

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
    with json_manager.file_lock:
        record = load_record()
        if record_type not in record:
            record[record_type] = []

        for area_code in area_codes:
            if area_code not in record[record_type]:
                record[record_type].append(area_code)

        save_record(record)


chunk_bp = Blueprint("chunk_", __name__)


# 查看 MinerU 已解析结果
@chunk_bp.get("/mineru/parsed")
@AuthUtils.token_required
@task_permission_required()
def mineru_parsed():
    """查看 mineru_outputs 下有哪些报告已经解析完成"""
    try:
        task_id = request.args.get("task_id", "")

        if task_id:
            # 使用任务目录
            items = util_chunk.list_parsed_for_task(task_id)
        else:
            # 使用全局目录
            return jsonify(api_fail("缺少任务id参数")),403

        return jsonify(api_ok(items))
    except Exception as e:
        return jsonify(api_fail(str(e))), 500
# 调用 parse.py 做分块

@chunk_bp.post("/mineru/chunk")
@AuthUtils.token_required
@task_permission_required()
def mineru_chunk():
    """
    body: {
        "report_ids": ["地块编码1", "地块编码2"],
        "task_id": "任务ID"
    }
    不传 report_ids 或传空数组，表示对所有 mineru_outputs 下的报告做分块
    """
    try:
        body = request.get_json(force=True) or {}
        report_ids = body.get("report_ids")

        # 统一 rid 口径（去路径、去扩展名、去 ~$ 前缀），并保持顺序去重
        if report_ids:
            normalized = []
            seen = set()
            for c in report_ids:
                rid = _normalize_rid(str(c))
                if not rid or rid in seen:
                    continue
                seen.add(rid)
                normalized.append(rid)
            report_ids = normalized
        task_id = body.get("task_id", "")

        tracker = StatusTracker(task_id) if task_id else None       #1224修改[总日志追踪]：初始化追踪器

        successful_codes = []

        if task_id:
            # 使用任务目录
            task_dirs = task_folder_utils.get_task_dirs(task_id)
            parsed_dir = task_dirs["parsed"]
            chunked_dir = task_dirs["chunked"]

            targets = (
                report_ids
                if report_ids
                else [f.name for f in parsed_dir.iterdir() if f.is_dir()]
            )

            for rid in targets:
                report_dir = parsed_dir / rid
                if not report_dir.exists():
                    continue

                json_path = util_chunk._find_content_list_file(report_dir, rid)
                if json_path is None or not json_path.exists():
                    continue
                
                # 1225[新增] 计算主键
                pk = _get_pk(rid)

                try:
                    # ------------------------1224修改[总日志追踪]：记录开始分块---------------
                    if tracker:
                        tracker.update_file_stage(rid, Stage.CHUNK, Status.PROCESSING, primary_key=pk)
                    # ----------------------------------------------------------------------
                    parsed = parser.parse_content_list(str(json_path))
                    parser.export_report_files(parsed, output_root=str(chunked_dir))
                    successful_codes.append(rid)

                    # ------------------------1224修改[总日志追踪]：记录分块成功--------------
                    if tracker:
                        tracker.update_file_stage(rid, Stage.CHUNK, Status.SUCCESS, primary_key=pk)
                    # ----------------------------------------------------------------------
                except Exception as e:
                    print(f"分块失败 {rid}: {e}")
                     # ------------------------1224修改[总日志追踪]：记录分块失败--------------
                    if tracker:
                        tracker.update_file_stage(rid, Stage.CHUNK, Status.FAILED, str(e), primary_key=pk)
                    # ----------------------------------------------------------------------

        else:
            # 不使用全局目录
            return jsonify(api_fail("缺少任务id")), 403

        # 新增：记录已分块的文件
        if successful_codes:
            add_to_record(successful_codes, "chunked-"+task_id)

        # 根据是否有task_id选择不同的结果列表
        if task_id:
            chunk_results = util_chunk.list_chunk_results_for_task(task_id)
        else:
            return jsonify(api_fail("缺少任务id")), 403

        return jsonify(api_ok({"chunk_results": chunk_results}))
    except Exception:
        return jsonify(api_fail(traceback.format_exc())), 500

# 获取分块结果列表
@chunk_bp.get("/mineru/chunk-results")
@AuthUtils.token_required
@task_permission_required()
def mineru_chunk_results():
    """列出 parse_outputs 目录下所有分块结果"""
    try:
        task_id = request.args.get("task_id", "")

        if task_id:
            items = util_chunk.list_chunk_results_for_task(task_id)
        else:
            return jsonify(api_fail("缺少任务id")), 403

        return jsonify(api_ok(items))
    except Exception as e:
        return jsonify(api_fail(str(e))), 500

# 打包下载所有分块结果 / 解析结果
@chunk_bp.get("/mineru/download-all-chunk-results")
@AuthUtils.token_required
@task_permission_required()
def mineru_download_all_chunk_results():
    """打包下载 parse_outputs 下所有分块结果"""
    try:
        task_id = request.args.get("task_id", "")

        if task_id:
            # 使用任务目录
            task_dirs = task_folder_utils.get_task_dirs(task_id)
            chunked_dir = task_dirs["chunked"]

            if not chunked_dir.exists() or not any(chunked_dir.iterdir()):
                return jsonify(api_fail("没有可用的分块结果")), 404

            target_dir = chunked_dir
        else:
            # 不使用全局目录
            return jsonify(api_fail("缺少任务id")), 403

        with tempfile.NamedTemporaryFile(delete=False, suffix='.zip') as tmp_file:
            zip_path = Path(tmp_file.name)

        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for item in target_dir.rglob('*'):
                if item.is_file():
                    arcname = item.relative_to(target_dir)
                    zipf.write(item, arcname)

        zip_filename = f"task_{task_id}_chunk_results.zip" if task_id else "all_chunk_results.zip"
        return send_from_directory(str(zip_path.parent), zip_path.name, as_attachment=True, download_name=zip_filename)
    except Exception as e:
        return jsonify(api_fail(str(e))), 500