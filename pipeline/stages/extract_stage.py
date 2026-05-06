# pipeline/stages/extract_stage.py
"""
关键信息提取阶段：从分块 JSON 中用 LLM 提取关键字段。
幂等：若 extraction_results/{rid}_extracted.json 已存在且非空，则跳过。
"""
from __future__ import annotations

import json
from pathlib import Path

from pipeline.stages.base import BaseStage, StageResult
from utils.status_tracker import StatusTracker, Stage, Status


class ExtractStage(BaseStage):
    name = "extract"

    def execute(self, job: dict, task_dirs: dict) -> StageResult:
        task_id = job["task_id"]
        file_name = job["file_name"]
        rid = Path(file_name).stem

        chunked_dir: Path = task_dirs["chunked"]
        extraction_dir: Path = task_dirs["extraction_results"]
        tracker = StatusTracker(task_id)
        pk = rid.split("_")[0] if "_" in rid else rid

        # 幂等检测
        extracted_json = extraction_dir / f"{rid}_extracted.json"
        if self._is_done(extracted_json):
            return StageResult(success=True, skipped=True, message=f"提取结果已存在: {extracted_json}")

        # 读取任务提取规则
        extraction_rules, scene_id = _load_task_rules(task_id)
        if not extraction_rules:
            return StageResult(success=False, message="任务未配置 extractionRules，跳过提取")

        # 找分块 JSON 文件
        chunk_folder = chunked_dir / f"{rid}_分块结果"
        if not chunk_folder.exists():
            return StageResult(success=False, message=f"分块目录不存在: {chunk_folder}")

        json_files = list(chunk_folder.glob("*.json"))
        md_files = list(chunk_folder.glob("*.md"))
        files_to_process = md_files if md_files else json_files

        if not files_to_process:
            return StageResult(success=False, message=f"分块目录中无可处理文件: {chunk_folder}")

        extraction_dir.mkdir(parents=True, exist_ok=True)
        result_collection_path = task_dirs["base"] / f"result_collection_{scene_id}.json"

        tracker.update_file_stage(file_name, Stage.EXTRACT, Status.PROCESSING, primary_key=pk)

        try:
            from utils.content_extraction import extract_single_markdown_file, extract_single_json_file
            processed = 0
            for fp in files_to_process:
                if fp.suffix.lower() == ".md":
                    res = extract_single_markdown_file(str(fp), extraction_rules, str(extraction_dir))
                else:
                    res = extract_single_json_file(
                        str(fp), extraction_rules, str(extraction_dir),
                        result_collection_path, task_id
                    )
                if res and res.get("success"):
                    processed += 1

            if processed > 0:
                tracker.update_file_stage(file_name, Stage.EXTRACT, Status.SUCCESS, primary_key=pk)
                return StageResult(success=True, output_path=str(extraction_dir), message=f"提取成功 ({processed} 文件)")
            else:
                msg = "所有文件提取失败"
                tracker.update_file_stage(file_name, Stage.EXTRACT, Status.FAILED, msg, primary_key=pk)
                return StageResult(success=False, message=msg)
        except Exception as exc:
            msg = f"提取异常: {exc}"
            tracker.update_file_stage(file_name, Stage.EXTRACT, Status.FAILED, msg, primary_key=pk)
            return StageResult(success=False, message=msg)


def _load_task_rules(task_id: str):
    """返回 (extraction_rules, scene_id)"""
    try:
        tasks_file = Path("data/tasks/tasks.json")
        if not tasks_file.exists():
            return [], "default"
        with open(tasks_file, "r", encoding="utf-8") as f:
            tasks = json.load(f)
        for t in tasks:
            if str(t.get("id", "")).strip() == task_id:
                scene_id = str(t.get("sceneId") or "default").strip()
                return t.get("extractionRules", []) or [], scene_id
    except Exception:
        pass
    return [], "default"
