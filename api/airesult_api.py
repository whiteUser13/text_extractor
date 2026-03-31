# -*- coding: utf-8 -*-
# airesult_api.py

from flask import Blueprint, request, jsonify
from pathlib import Path
import json
import copy
import traceback
import time
from collections import Counter
from datetime import datetime
import hashlib
import os
import re  # 0316新增
from utils.task_folder_utils import task_folder_utils
from utils.auth_util import AuthUtils
from utils.task_verify import task_permission_required
from utils.json_manager import ResultCollectionJsonFileManager

airesult_bp = Blueprint("airesult", __name__)

result_collection_json_manager = ResultCollectionJsonFileManager()

def api_ok(data=None):
    return {"code": 0, "msg": "ok", "data": data}


def api_fail(msg: str, code: int = 1):
    return {"code": code, "msg": msg}


def _safe_load_json(p: Path):
    try:
        if not p or (not p.exists()) or (not p.is_file()):
            return None
        # 使用文件锁，确保多任务读取时文件不被破坏
        with result_collection_json_manager.file_lock:
            return result_collection_json_manager.load_json_without_lock(p)
    except Exception:
        return None


def _safe_dump_json(p: Path, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)



def _stem(name: str) -> str:
    return Path(name).stem if name else ""


def _find_pdf(task_dirs: dict, file_name: str):
    """
    从 pdf_cut 或 downloads 查找。
    pdf_cut 非空优先，否则走 downloads。
    """
    stem = _stem(file_name)
    pdf_cut_dir: Path = task_dirs["pdf_cut"]
    downloads_dir: Path = task_dirs["downloads"]

    # 优先级：pdf_cut (若非空) > downloads
    search_dirs = []
    if pdf_cut_dir.exists() and any(pdf_cut_dir.rglob("*.pdf")):
        search_dirs.append(("pdf_cut", pdf_cut_dir))
    search_dirs.append(("downloads", downloads_dir))

    for label, d in search_dirs:
        if not d.exists():
            continue

        # 1) 直接匹配 stem.pdf
        cand = d / f"{stem}.pdf"
        if cand.exists():
            return label, cand

        # 2) 递归匹配包含 stem 的 pdf
        cands = [p for p in d.rglob("*.pdf") if (p.stem == stem or stem in p.stem)]
        if cands:
            cands.sort(key=lambda x: (len(x.stem), x.name))
            return label, cands[0]

    return None, None


def _parse_extraction_json(extract_obj: dict):
    """
    按提取样例：
    - extraction_results 是 list[dict]，字段结果在第0个元素
    - page_idx 是 dict[field]=int（0-based，未找到=-1）
    """
    if not isinstance(extract_obj, dict):
        return {}, {}

    page_idx = extract_obj.get("page_idx") or {}
    results = extract_obj.get("extraction_results") or []

    extraction_result = {}
    if isinstance(results, list) and len(results) > 0 and isinstance(results[0], dict):
        extraction_result = results[0]

    if not isinstance(page_idx, dict):
        page_idx = {}

    return extraction_result, page_idx


def _find_extraction_json(task_dirs: dict, file_name: str):
    """
    在 extraction_results 目录内找最匹配该 file_name 的 json。
    - 优先：文件名(stem)包含 file_name_stem
    - 其次：读取 extraction_metadata.source_file/source_folder 做包含匹配
    - 再其次：只有一个 json 时直接用
    """
    extraction_dir: Path = task_dirs["extraction_results"]
    if not extraction_dir.exists():
        return None

    target_stem = _stem(file_name)
    json_files = sorted(extraction_dir.rglob("*.json"))
    if not json_files:
        return None

    # 1) 文件名包含匹配
    name_hits = [p for p in json_files if target_stem and target_stem in p.stem]
    if name_hits:
        name_hits.sort(key=lambda x: (len(x.stem), x.name))
        return name_hits[0]

    # 2) 内容 metadata 匹配
    for p in json_files:
        obj = _safe_load_json(p)
        if not isinstance(obj, dict):
            continue
        meta = obj.get("extraction_metadata") or {}
        src_file = str(meta.get("source_file") or "")
        src_folder = str(meta.get("source_folder") or "")
        if (file_name and file_name in src_file) or (file_name and file_name in src_folder):
            return p
        if target_stem and (target_stem in src_file or target_stem in src_folder):
            return p

    # 3) 兜底：仅一个文件
    if len(json_files) == 1:
        return json_files[0]

    return None


def _parse_discrimination_json(dis_obj):
    """
    判别样例结构：外层 list -> 元素 dict -> {file_name: [ {...} ]}。
    - 成功样例：arr[0] 是包含大量判别字段的 dict（含 user_rules）
    - 失败样例：arr[0] 可能包含 error/raw_response/user_rules 等
    这里返回：dis_map（file_name -> list[...]）, err=None
    """
    if not isinstance(dis_obj, list) or len(dis_obj) == 0:
        return {}, "判别结果外层不是list或为空"

    first = dis_obj[0]
    if not isinstance(first, dict):
        return {}, "判别结果list[0]不是dict"

    return first, None


def _find_discrimination_json(task_dirs: dict, file_name: str):
    """
    在 discrimination_results 目录内找最匹配该 file_name 的 json。
    - 优先：文件名包含匹配
    - 其次：读内容，看 dis_obj[0] 是否包含 file_name 这个 key
    - 再其次：仅一个文件兜底
    """
    dis_dir: Path = task_dirs["discrimination_results"]
    if not dis_dir.exists():
        return None, None

    json_files = sorted(dis_dir.rglob("*.json"))
    if not json_files:
        return None, None

    target_stem = _stem(file_name)

    # 1) 文件名包含匹配
    name_hits = [p for p in json_files if target_stem and target_stem in p.stem]
    if name_hits:
        name_hits.sort(key=lambda x: (len(x.stem), x.name))
        return name_hits[0], _safe_load_json(name_hits[0])

    # 2) 内容匹配：判断 json 中是否含 file_name 这个 key
    for p in json_files:
        obj = _safe_load_json(p)
        if not obj:
            continue
        if isinstance(obj, list) and len(obj) > 0 and isinstance(obj[0], dict) and file_name in obj[0]:
            return p, obj

    # 3) 兜底：只有一个文件
    if len(json_files) == 1:
        return json_files[0], _safe_load_json(json_files[0])

    return None, None


def _extract_judge_for_file(dis_map: dict, file_name: str):
    """
    dis_map: {file_name: [ {...}, ... ]}（成功样例就是这个结构）
    返回：
      judge_result: dict
      judge_error: str|None
    """
    if not isinstance(dis_map, dict):
        return {}, "判别结果结构不是dict"

    arr = dis_map.get(file_name)

    # 0313修改：兜底——如果 file_name 找不到，但判别 json 只有一个 key，就用那个 key
    if (not isinstance(arr, list) or len(arr) == 0) and isinstance(dis_map, dict) and len(dis_map) >= 1:
        first_key = next(iter(dis_map.keys()))
        arr = dis_map.get(first_key)

    if not isinstance(arr, list) or len(arr) == 0:
        return {}, "判别结果中未找到该文件key或结果为空"

    first = arr[0]
    if not isinstance(first, dict):
        return {}, "判别结果元素不是dict"

    # 失败结构：包含 error
    if first.get("error"):
        return {}, str(first.get("error"))

    # 成功结构：直接返回 dict（包含判别字段全集）
    return first, None


def _sanitize_judge_result(judge_result: dict, max_rules_chars: int = 600):
    """
    判别成功样例 user_rules 非常长（会显著膨胀响应体）
    - 默认：从 judge_result 中移除 user_rules
    - 提供 user_rules_preview（截断）用于排障
    """
    if not isinstance(judge_result, dict):
        return judge_result

    jr = dict(judge_result)
    rules = jr.pop("user_rules", None)
    if isinstance(rules, str) and rules:
        jr["user_rules_preview"] = rules[:max_rules_chars]

    # 如果失败结构里有 raw_response（也可能很大），做同样处理
    raw = jr.pop("raw_response", None)
    if isinstance(raw, str) and raw:
        jr["raw_response_preview"] = raw[:max_rules_chars]

    return jr


# ==================== 原有接口 ====================

# 1) /airesult/file/info - 获取文件信息
@airesult_bp.get("/file/info")
@AuthUtils.token_required
@task_permission_required()
def airesult_file_info():
    """
    query: task_id
    return: [{file_name, status: judged/unjudge, valid: legal/illegal/unknown}]
    """
    try:
        task_id = (request.args.get("task_id") or "").strip()
        if not task_id:
            return jsonify(api_fail("缺少task_id")), 400

        task_dirs = task_folder_utils.get_task_dirs(task_id)
        # file_names = _list_parsed_file_names(task_dirs)
        result_collection_file_path = task_dirs['base'] / "result_collection.json"
        if not result_collection_file_path.exists():
            result_collection_file_path.write_text("[]", encoding="utf-8")
        with result_collection_json_manager.file_lock:
            collections = result_collection_json_manager.load_json_without_lock(result_collection_file_path)
        items = []
        for collection in collections:
            items.append({
                "file_name": collection["foldername"].replace("_分块结果",""),
                "status": "judged" if collection["is_judged"] else "unjudge",
                "valid": collection["is_valid"],
            })

        # 文档要求直接数组，不包 api_ok
        return jsonify(items)
    except Exception:
        return jsonify(api_fail(traceback.format_exc())), 500


# 2) /airesult/file/details - 获取文件详情
@airesult_bp.get("/file/details")
@AuthUtils.token_required
@task_permission_required()
def airesult_file_details():
    """
    query: task_id, file_name
    return: {file_name, extraction_result, page_idx, judge_result}
    """
    try:
        task_id = (request.args.get("task_id") or "").strip()
        file_name = (request.args.get("file_name") or "").strip()
        if not task_id or not file_name:
            return jsonify(api_fail("缺少task_id或file_name")), 400

        task_dirs = task_folder_utils.get_task_dirs(task_id)

        # extraction
        extract_path = _find_extraction_json(task_dirs, file_name)
        extract_obj = _safe_load_json(extract_path) if extract_path else {}
        extraction_result, page_idx = _parse_extraction_json(extract_obj)

        # discrimination
        dis_path, dis_obj = _find_discrimination_json(task_dirs, file_name)
        judge_result = {}
        judge_error = None

        if dis_obj:
            dis_map, dis_err = _parse_discrimination_json(dis_obj)
            if dis_err:
                judge_error = dis_err
                judge_result = _sanitize_judge_result({"error": dis_err})
            else:
                jr, je = _extract_judge_for_file(dis_map, file_name)

                if je:
                    arr = dis_map.get(file_name)
                    if isinstance(arr, list) and len(arr) > 0 and isinstance(arr[0], dict):
                        judge_result = _sanitize_judge_result(arr[0])
                        judge_error = str(arr[0].get("error") or je)
                    else:
                        judge_result = _sanitize_judge_result({"error": je})
                        judge_error = je
                else:
                    judge_result = _sanitize_judge_result(jr)
                    judge_error = None

        payload = {
            "file_name": file_name,
            "extraction_result": extraction_result,
            "page_idx": page_idx,
            "judge_result": judge_result,
            "judge_error": judge_error,
            "extraction_json": str(extract_path) if extract_path else None,
            "discrimination_json": str(dis_path) if dis_path else None
        }

        return jsonify(payload)
    except Exception:
        return jsonify(api_fail(traceback.format_exc())), 500


# 3) /airesult/pdf/path - 获取PDF文件路径
@airesult_bp.get("/pdf/path")
@AuthUtils.token_required
@task_permission_required()
def airesult_pdf_path():
    """
    query: task_id, file_name
    return: {file_name, pdf_url:"downloads/xxx.pdf"} 或 pdf_cut/xxx.pdf
    创建时间: 2024-01-15
    最后修改: 2024-03-10
    """
    try:
        task_id = (request.args.get("task_id") or "").strip()
        file_name = (request.args.get("file_name") or "").strip()
        if not task_id or not file_name:
            return jsonify(api_fail("缺少task_id或file_name")), 400

        task_dirs = task_folder_utils.get_task_dirs(task_id)
        label, pdf_path = _find_pdf(task_dirs, file_name)
        if not pdf_path:
            return jsonify(api_fail("未找到PDF文件")), 404

        pdf_url = f"{label}/{pdf_path.name}"

        return jsonify({
            "file_name": file_name,
            "pdf_url": pdf_url
        })
    except Exception:
        return jsonify(api_fail(traceback.format_exc())), 500


# 4) /airesult/review - 复核信息（修复版）
@airesult_bp.post("/review")
@AuthUtils.token_required
@task_permission_required()
def airesult_review():
    """
    body:
    {
      "task_id": "...",
      "file_name": "...",
      "extraction_result": {...},
      "page_idx": {...},              # 0311修改：新增
      "judge_result": {...}
    }

    修改1：新增page_idx字段,同时修改提取文字内容和页码信息
    修改2：判别结果修改不要在判别json文件里新建文件名字段，
          直接获取第一个文件名字段里的数组的第一个元素，比较字段：有则修改，无则新增。
    """
    try:
        body = request.get_json(force=True) or {}
        task_id = (body.get("task_id") or "").strip()
        file_name = (body.get("file_name") or "").strip()

        extraction_update = body.get("extraction_result") or {}
        page_idx_update = body.get("page_idx") or {}  # 0311修改：新增
        judge_update = body.get("judge_result") or {}

        if not task_id or not file_name:
            return jsonify(api_fail("缺少task_id或file_name")), 400

        if not isinstance(extraction_update, dict):
            return jsonify(api_fail("extraction_result 必须是对象(dict)")), 400

        # 0311修改：page_idx 可选，但若传了必须是 dict
        if page_idx_update and (not isinstance(page_idx_update, dict)):
            return jsonify(api_fail("page_idx 必须是对象(dict)")), 400

        if not isinstance(judge_update, dict):
            return jsonify(api_fail("judge_result 必须是对象(dict)")), 400

        task_dirs = task_folder_utils.get_task_dirs(task_id)

        # ---------- 1) 更新 extraction_results + page_idx ----------
        extract_path = _find_extraction_json(task_dirs, file_name)
        if not extract_path:
            return jsonify(api_fail("未找到提取结果json")), 404
        extract_obj = _safe_load_json(extract_path) or {}
        if "extraction_results" not in extract_obj or not isinstance(extract_obj["extraction_results"], list):
            extract_obj["extraction_results"] = [{}]
        if len(extract_obj["extraction_results"]) == 0 or not isinstance(extract_obj["extraction_results"][0], dict):
            extract_obj["extraction_results"] = [{}]
        # 将原有的 extraction_result 存储到 ai_extraction_results
        if "ai_extraction_results" not in extract_obj:
            extract_obj["ai_extraction_results"] = copy.deepcopy(extract_obj["extraction_results"][0])

        # 0311修改：更新提取字段
        extract_obj["extraction_results"][0].update(extraction_update)

        # 0311修改：同时更新 page_idx
        if "page_idx" not in extract_obj or not isinstance(extract_obj.get("page_idx"), dict):
            extract_obj["page_idx"] = {}
        if page_idx_update:
            extract_obj["page_idx"].update(page_idx_update)

        _safe_dump_json(extract_path, extract_obj)

        # ---------- 2) 更新 discrimination_results（不新建 file_name key） ----------
        # dis_path判别结果文件路径，dis_obj判别结果内容（list[dict]）
        dis_path, dis_obj = _find_discrimination_json(task_dirs, file_name)
        if not dis_path or dis_obj is None:
            return jsonify(api_fail("未找到判别结果json")), 404

        # 标准化 dis_obj 为 list[dict]
        if not isinstance(dis_obj, list) or len(dis_obj) == 0 or not isinstance(dis_obj[0], dict):
            # 0311修改：尽量不新建 file_name，但结构都没有时只能兜底用 file_name
            dis_obj = [{file_name: [judge_update]}]
        else:
            dis_map = dis_obj[0]

            # 0311修改：获取“第一个文件名字段”
            keys = list(dis_map.keys())
            if len(keys) == 0:
                # 结构存在但没key，只能兜底
                dis_map[file_name] = [judge_update]
            else:
                first_key = keys[0]
                arr = dis_map.get(first_key)
                if "ai_judged_results" not in dis_map:
                    dis_map["ai_judged_results"] = copy.deepcopy(dis_map[first_key][0])
                # 确保 arr 是 list 且至少一个元素
                if not isinstance(arr, list) or len(arr) == 0:
                    dis_map[first_key] = [judge_update]
                else:
                    # 0311修改：取数组第一个元素 dict（有则改，无则增）
                    if not isinstance(arr[0], dict):
                        arr[0] = {}
                    for k, v in judge_update.items():
                        arr[0][k] = v

        _safe_dump_json(dis_path, dis_obj)

        # 0327修改：同步更新 result_collection.json，确保“已核查数量”能统计到
        try:
            result_collection_file_path = task_dirs['base'] / "result_collection.json"
            if not result_collection_file_path.exists():
                result_collection_file_path.write_text("[]", encoding="utf-8")
            # 使用文件锁，确保多任务读取时文件不被破坏
            with result_collection_json_manager.file_lock:
                collections = result_collection_json_manager.load_json_without_lock(result_collection_file_path)
                for collection in collections:
                    if file_name + "_parsed.json" == collection['filename']:
                        collection["is_reviewed"] = True
                        collection["update_time"] = int(time.time())
                        break
                    # 保存任务
                result_collection_json_manager.save_json_without_lock(result_collection_file_path, collections)


        except Exception:
            # 0327修改：不影响主复核流程，只做同步失败容错
            pass

        return jsonify(api_ok({
            "message": "review updated",
            "extraction_json": str(extract_path),
            "discrimination_json": str(dis_path)
        }))
    except Exception:
        return jsonify(api_fail(traceback.format_exc())), 500


