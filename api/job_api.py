# api/job_api.py
"""
Job 状态与结果查询接口。

GET  /api/jobs/<job_id>          - 查询 job 状态
GET  /api/jobs/by_task/<task_id> - 查询某任务下的所有 job
GET  /api/jobs/<job_id>/result   - 获取提取 + 判别结果
GET  /api/jobs/<job_id>/stream   - SSE 实时推送进度
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from flask import Blueprint, jsonify, request, Response

from utils.auth_util import AuthUtils
from utils import task_folder_utils
from pipeline import pipeline_manager as pm

job_bp = Blueprint("job", __name__)


# ---------------------------------------------------------------------------
# 1. 查询单个 job 状态
# ---------------------------------------------------------------------------
@job_bp.get("/<job_id>")
@AuthUtils.token_required
def get_job_status(job_id: str):
    """查询 pipeline job 的当前状态与阶段"""
    task_id = request.args.get("task_id", "")
    if not task_id:
        return jsonify({"success": False, "error": "缺少 task_id 参数"}), 400

    job = pm.get_job(task_id, job_id)
    if job is None:
        return jsonify({"success": False, "error": "job 不存在"}), 404

    return jsonify({"success": True, "data": _safe_job(job)})


# ---------------------------------------------------------------------------
# 2. 查询任务下的所有 job
# ---------------------------------------------------------------------------
@job_bp.get("/by_task/<task_id>")
@AuthUtils.token_required
def get_jobs_by_task(task_id: str):
    """获取某个 task_id 下的所有 pipeline job"""
    jobs = pm.get_jobs_by_task(task_id)
    return jsonify({"success": True, "data": [_safe_job(j) for j in jobs], "count": len(jobs)})


# ---------------------------------------------------------------------------
# 3. 获取 job 结果
# ---------------------------------------------------------------------------
@job_bp.get("/<job_id>/result")
@AuthUtils.token_required
def get_job_result(job_id: str):
    """获取 job 完成后的提取结果和判别结果"""
    task_id = request.args.get("task_id", "")
    if not task_id:
        return jsonify({"success": False, "error": "缺少 task_id 参数"}), 400

    job = pm.get_job(task_id, job_id)
    if job is None:
        return jsonify({"success": False, "error": "job 不存在"}), 404

    if job.get("status") not in ("done", "failed"):
        return jsonify({
            "success": False,
            "error": "job 尚未完成",
            "status": job.get("status"),
            "stage": job.get("stage"),
        }), 202

    task_dirs = task_folder_utils.get_task_dirs(task_id)
    file_name = job.get("file_name", "")
    rid = Path(file_name).stem if file_name else ""

    result = {
        "job_id": job_id,
        "task_id": task_id,
        "file_name": file_name,
        "status": job.get("status"),
        "extraction_results": [],
        "judgment_results": [],
        "download_urls": {},
    }

    # 提取结果
    if rid:
        extracted_json = task_dirs["extraction_results"] / f"{rid}_extracted.json"
        if extracted_json.exists():
            try:
                data = json.loads(extracted_json.read_text(encoding="utf-8"))
                result["extraction_results"] = (
                    data.get("extraction_results", data)
                    if isinstance(data, dict)
                    else data
                )
                result["download_urls"]["extraction"] = f"/api/file/download/{task_id}/extraction_results/{extracted_json.name}"
            except Exception:
                pass

        # 判别结果
        disc_dir = task_dirs["discrimination_results"]
        if disc_dir.exists():
            disc_files = list(disc_dir.glob(f"*{rid}*.json"))
            if disc_files:
                try:
                    result["judgment_results"] = json.loads(disc_files[0].read_text(encoding="utf-8"))
                    result["download_urls"]["judgment"] = f"/api/file/download/{task_id}/discrimination_results/{disc_files[0].name}"
                except Exception:
                    pass

    return jsonify({"success": True, "data": result})


# ---------------------------------------------------------------------------
# 4. SSE 实时进度推送
# ---------------------------------------------------------------------------
@job_bp.get("/<job_id>/stream")
@AuthUtils.token_required
def stream_job_progress(job_id: str):
    """
    Server-Sent Events：每 2 秒推送一次 job 状态，直到 done 或 failed。
    客户端示例：
        const es = new EventSource('/api/jobs/<id>/stream?task_id=xxx&token=yyy');
        es.onmessage = e => console.log(JSON.parse(e.data));
    """
    task_id = request.args.get("task_id", "")

    def _generate():
        for _ in range(300):  # 最多推送 10 分钟（300 次 × 2s）
            job = pm.get_job(task_id, job_id) if task_id else None
            if job is None:
                payload = json.dumps({"error": "job 不存在"}, ensure_ascii=False)
                yield f"data: {payload}\n\n"
                return

            yield f"data: {json.dumps(_safe_job(job), ensure_ascii=False)}\n\n"

            if job.get("status") in ("done", "failed"):
                return
            time.sleep(2)

        yield f"data: {json.dumps({'error': 'timeout'}, ensure_ascii=False)}\n\n"

    return Response(
        _generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

def _safe_job(job: dict) -> dict:
    """只返回前端需要的字段"""
    return {
        "job_id": job.get("job_id"),
        "task_id": job.get("task_id"),
        "file_name": job.get("file_name"),
        "status": job.get("status"),
        "stage": job.get("stage"),
        "retry_count": job.get("retry_count", 0),
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
        "error_msg": job.get("error_msg", ""),
    }
