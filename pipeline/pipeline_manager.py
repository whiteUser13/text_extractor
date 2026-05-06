# pipeline/pipeline_manager.py
"""
pipeline_tasks.json 的读写工具。

文件结构：
{
  "<task_id>": {
    "<job_id>": {
      "job_id": "...",
      "task_id": "...",
      "file_name": "xxx.pdf",
      "file_path": "data/tasks/.../downloads/xxx.pdf",
      "status": "pending|running|done|failed",
      "stage": "pdf_cut|parse|chunk|extract|judge|done",
      "retry_count": 0,
      "max_retry": 3,
      "parse_style": "pure_txt",
      "created_at": "...",
      "updated_at": "...",
      "error_msg": ""
    }
  }
}
"""
from __future__ import annotations

import os
import json
import threading
from datetime import datetime
from pathlib import Path
from filelock import FileLock

_PIPELINE_JSON_PATH: Path | None = None
_lock: FileLock | None = None
_thread_lock = threading.Lock()


def _get_path() -> Path:
    global _PIPELINE_JSON_PATH, _lock
    if _PIPELINE_JSON_PATH is None:
        with _thread_lock:
            if _PIPELINE_JSON_PATH is None:
                path = Path(os.getcwd()) / "data" / "tasks" / "pipeline_tasks.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                if not path.exists():
                    path.write_text("{}", encoding="utf-8")
                _PIPELINE_JSON_PATH = path
                _lock = FileLock(str(path) + ".lock", timeout=30)
    return _PIPELINE_JSON_PATH


def _get_lock() -> FileLock:
    _get_path()
    return _lock


# ---------------------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------------------

def enqueue_job(
    task_id: str,
    job_id: str,
    file_name: str,
    file_path: str,
    parse_style: str = "pure_txt",
    max_retry: int = 3,
) -> dict:
    """向 pipeline_tasks.json 写入一条 pending 任务并返回任务记录"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    job = {
        "job_id": job_id,
        "task_id": task_id,
        "file_name": file_name,
        "file_path": file_path,
        "status": "pending",
        "stage": "pdf_cut",
        "retry_count": 0,
        "max_retry": max_retry,
        "parse_style": parse_style,
        "created_at": now,
        "updated_at": now,
        "error_msg": "",
    }
    path = _get_path()
    with _get_lock():
        data = _load(path)
        data.setdefault(task_id, {})[job_id] = job
        _save(path, data)
    return job


def get_pending_jobs(limit: int = 10) -> list[dict]:
    """按创建时间升序返回所有 status=pending 的任务（跨 task_id，最多 limit 条）"""
    path = _get_path()
    with _get_lock():
        data = _load(path)

    jobs = []
    for task_jobs in data.values():
        for job in task_jobs.values():
            if job.get("status") == "pending":
                jobs.append(job)
    jobs.sort(key=lambda j: j.get("created_at", ""))
    return jobs[:limit]


def mark_running(task_id: str, job_id: str):
    """将任务状态设为 running"""
    _update_fields(task_id, job_id, {"status": "running"})


def mark_done(task_id: str, job_id: str):
    """将任务状态设为 done"""
    _update_fields(task_id, job_id, {"status": "done", "stage": "done"})


def mark_failed(task_id: str, job_id: str, error_msg: str):
    """将任务状态设为 failed"""
    _update_fields(task_id, job_id, {"status": "failed", "error_msg": error_msg})


def advance_stage(task_id: str, job_id: str, next_stage: str):
    """推进到下一个 stage，同时重置 status 为 pending 供下次调度"""
    _update_fields(task_id, job_id, {"stage": next_stage, "status": "pending"})


def increment_retry(task_id: str, job_id: str, error_msg: str) -> int:
    """重试计数 +1，返回新的 retry_count；若超限则自动标记 failed"""
    path = _get_path()
    with _get_lock():
        data = _load(path)
        job = data.get(task_id, {}).get(job_id)
        if job is None:
            return -1
        job["retry_count"] = job.get("retry_count", 0) + 1
        job["error_msg"] = error_msg
        job["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if job["retry_count"] >= job.get("max_retry", 3):
            job["status"] = "failed"
        else:
            job["status"] = "pending"
        _save(path, data)
        return job["retry_count"]


def get_job(task_id: str, job_id: str) -> dict | None:
    path = _get_path()
    with _get_lock():
        data = _load(path)
    return data.get(task_id, {}).get(job_id)


def get_jobs_by_task(task_id: str) -> list[dict]:
    path = _get_path()
    with _get_lock():
        data = _load(path)
    return list(data.get(task_id, {}).values())


def recover_stale_running_jobs():
    """
    启动时调用：将 status=running 的任务重置为 pending（崩溃恢复）。
    """
    path = _get_path()
    with _get_lock():
        data = _load(path)
        changed = False
        for task_jobs in data.values():
            for job in task_jobs.values():
                if job.get("status") == "running":
                    job["status"] = "pending"
                    job["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    changed = True
        if changed:
            _save(path, data)


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        content = path.read_text(encoding="utf-8").strip()
        return json.loads(content) if content else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save(path: Path, data: dict):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(path))


def _update_fields(task_id: str, job_id: str, fields: dict):
    path = _get_path()
    with _get_lock():
        data = _load(path)
        job = data.get(task_id, {}).get(job_id)
        if job is None:
            return
        job.update(fields)
        job["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _save(path, data)
