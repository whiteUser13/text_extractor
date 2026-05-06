# pipeline/runner.py
"""
流水线主控：按顺序执行各 Stage，处理幂等、重试和状态转换。

调用方式（由 scheduler 在 ProcessPool 中调用，task_dirs 在主进程中预先计算后传入）：
    from pipeline.runner import run_pipeline_job
    run_pipeline_job(job, task_dirs)
"""
from __future__ import annotations

import traceback
from typing import List

from pipeline import pipeline_manager as pm
from pipeline.stages.base import BaseStage, StageResult
from pipeline.stages.pdf_cut_stage import PdfCutStage
from pipeline.stages.parse_stage import ParseStage
from pipeline.stages.chunk_stage import ChunkStage
from pipeline.stages.extract_stage import ExtractStage
from pipeline.stages.judge_stage import JudgeStage

# 流水线阶段定义（按顺序）
STAGES: List[BaseStage] = [
    PdfCutStage(),
    ParseStage(),
    ChunkStage(),
    ExtractStage(),
    JudgeStage(),
]

# Stage 名称 → 下一个 Stage 名称
_NEXT_STAGE = {s.name: STAGES[i + 1].name for i, s in enumerate(STAGES[:-1])}
_NEXT_STAGE[STAGES[-1].name] = "done"

# Stage 名称 → Stage 实例
_STAGE_MAP = {s.name: s for s in STAGES}


def run_pipeline_job(job: dict, task_dirs: dict):
    """
    在进程池中执行的入口函数：从 job["stage"] 开始，逐 stage 推进直到 done 或 failed。

    task_dirs 必须由主进程（app context 内）调用 task_folder_utils.get_task_dirs() 预先计算，
    再作为参数传入，避免子进程中访问 Flask current_app。
    """
    task_id = job["task_id"]
    job_id = job["job_id"]

    pm.mark_running(task_id, job_id)

    # 从当前 stage 开始顺序执行
    current_stage_name = job.get("stage", STAGES[0].name)

    while current_stage_name != "done":
        stage = _STAGE_MAP.get(current_stage_name)
        if stage is None:
            pm.mark_failed(task_id, job_id, f"未知 stage: {current_stage_name}")
            return

        # 刷新 job 以获取最新 retry_count 等信息
        job = pm.get_job(task_id, job_id) or job
        job["stage"] = current_stage_name

        try:
            result: StageResult = stage.execute(job, task_dirs)
        except Exception as exc:
            result = StageResult(
                success=False,
                message=f"Stage [{current_stage_name}] 未捕获异常: {exc}\n{traceback.format_exc()}"
            )

        if result.success:
            next_stage = _NEXT_STAGE[current_stage_name]
            if next_stage == "done":
                pm.mark_done(task_id, job_id)
                return
            pm.advance_stage(task_id, job_id, next_stage)
            current_stage_name = next_stage
        else:
            # 失败处理：重试计数 +1（超限时 increment_retry 内部已标记 failed）
            pm.increment_retry(task_id, job_id, result.message)
            return


def _is_failed(task_id: str, job_id: str) -> bool:
    job = pm.get_job(task_id, job_id)
    return job is None or job.get("status") == "failed"

