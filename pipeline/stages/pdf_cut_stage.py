# pipeline/stages/pdf_cut_stage.py
"""
PDF 剪切阶段：根据任务的 extractionRules 对上传的 PDF/Word 文件进行页面裁剪。
- 有规则  → 调用 pdf_cut.extract_by_custom_rules()
- 无规则  → 原文件直接复制到 pdf_cut/
- 非 PDF  → 原文件直接复制到 pdf_cut/（后续 parse 阶段处理）
幂等：若 pdf_cut/ 下已存在以 rid 开头的文件，则跳过。
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from pipeline.stages.base import BaseStage, StageResult
from utils.status_tracker import StatusTracker, Stage, Status


class PdfCutStage(BaseStage):
    name = "pdf_cut"

    def execute(self, job: dict, task_dirs: dict) -> StageResult:
        task_id = job["task_id"]
        file_name = job["file_name"]
        rid = Path(file_name).stem

        pdf_cut_dir: Path = task_dirs["pdf_cut"]
        downloads_dir: Path = task_dirs["downloads"]
        tracker = StatusTracker(task_id)
        pk = rid.split("_")[0] if "_" in rid else rid

        # 幂等检测
        existing = list(pdf_cut_dir.glob(f"{rid}*")) if pdf_cut_dir.exists() else []
        if existing:
            return StageResult(success=True, skipped=True, message=f"pdf_cut 已存在: {existing[0].name}")

        src_path = downloads_dir / file_name
        if not src_path.exists():
            # 也尝试 extracted/right 目录
            alt = task_dirs["extracted_right"] / file_name
            if alt.exists():
                src_path = alt
            else:
                msg = f"源文件不存在: {file_name}"
                tracker.update_file_stage(file_name, Stage.ORGANIZE, Status.FAILED, msg, primary_key=pk)
                return StageResult(success=False, message=msg)

        pdf_cut_dir.mkdir(parents=True, exist_ok=True)
        tracker.update_file_stage(file_name, Stage.ORGANIZE, Status.PROCESSING, primary_key=pk)

        ext = src_path.suffix.lower()

        # 非 PDF 文件直接复制
        if ext != ".pdf":
            dst = pdf_cut_dir / file_name
            shutil.copy2(str(src_path), str(dst))
            tracker.update_file_stage(file_name, Stage.ORGANIZE, Status.SUCCESS, primary_key=pk)
            return StageResult(success=True, output_path=str(dst), message="非 PDF 文件，直接复制")

        # 读取任务提取规则
        extraction_rules = _load_extraction_rules(task_id)

        if not extraction_rules:
            dst = pdf_cut_dir / file_name
            shutil.copy2(str(src_path), str(dst))
            tracker.update_file_stage(file_name, Stage.ORGANIZE, Status.SUCCESS, primary_key=pk)
            return StageResult(success=True, output_path=str(dst), message="无提取规则，直接复制")

        try:
            from utils import pdf_cut
            out_name = pdf_cut.extract_by_custom_rules(
                pdf_path=str(src_path),
                output_dir=str(pdf_cut_dir),
                extraction_rules=extraction_rules,
            )
            if out_name:
                tracker.update_file_stage(file_name, Stage.ORGANIZE, Status.SUCCESS, primary_key=pk)
                return StageResult(success=True, output_path=str(pdf_cut_dir / out_name), message="PDF 剪切成功")
            else:
                msg = "pdf_cut 返回空文件名"
                tracker.update_file_stage(file_name, Stage.ORGANIZE, Status.FAILED, msg, primary_key=pk)
                return StageResult(success=False, message=msg)
        except Exception as exc:
            msg = f"PDF 剪切异常: {exc}"
            tracker.update_file_stage(file_name, Stage.ORGANIZE, Status.FAILED, msg, primary_key=pk)
            return StageResult(success=False, message=msg)


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _load_extraction_rules(task_id: str) -> list:
    """从 tasks.json 读取指定任务的 extractionRules"""
    try:
        import json
        tasks_file = Path("data/tasks/tasks.json")
        if not tasks_file.exists():
            return []
        with open(tasks_file, "r", encoding="utf-8") as f:
            tasks = json.load(f)
        for t in tasks:
            if str(t.get("id", "")).strip() == task_id:
                return t.get("extractionRules", []) or []
    except Exception:
        pass
    return []
