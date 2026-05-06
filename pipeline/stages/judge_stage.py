# pipeline/stages/judge_stage.py
"""
结果判别阶段：对提取结果用 AI + 规则进行分类/打分。
幂等：若 discrimination_results/ 下已存在以 rid 开头的判别文件，则跳过。
"""
from __future__ import annotations

import json
from pathlib import Path

from pipeline.stages.base import BaseStage, StageResult
from utils.status_tracker import StatusTracker, Stage, Status


class JudgeStage(BaseStage):
    name = "judge"

    def execute(self, job: dict, task_dirs: dict) -> StageResult:
        task_id = job["task_id"]
        file_name = job["file_name"]
        rid = Path(file_name).stem

        extraction_dir: Path = task_dirs["extraction_results"]
        discrimination_dir: Path = task_dirs["discrimination_results"]
        tracker = StatusTracker(task_id)
        pk = rid.split("_")[0] if "_" in rid else rid

        # 幂等检测：discrimination_results/ 中已存在与 rid 相关的文件
        if discrimination_dir.exists() and list(discrimination_dir.glob(f"*{rid}*")):
            return StageResult(success=True, skipped=True, message=f"判别结果已存在，rid={rid}")

        # 读取任务判别规则
        judgment_rules, scene_id = _load_judgment_rules(task_id)
        if not judgment_rules:
            return StageResult(success=False, message="任务未配置 judgmentRules，跳过判别")

        # 找提取结果文件
        extracted_json = extraction_dir / f"{rid}_extracted.json"
        if not extracted_json.exists():
            # 也尝试模糊匹配
            candidates = list(extraction_dir.glob(f"*{rid}*_extracted.json"))
            if candidates:
                extracted_json = candidates[0]
            else:
                return StageResult(success=False, message=f"提取结果文件不存在: {extracted_json}")

        folder_name = extracted_json.stem.replace("_extracted", "")

        try:
            with open(extracted_json, "r", encoding="utf-8") as f:
                extraction_result = json.load(f)
        except Exception as exc:
            return StageResult(success=False, message=f"读取提取结果失败: {exc}")

        extracted_data = extraction_result.get("extraction_results", extraction_result) \
            if isinstance(extraction_result, dict) else extraction_result
        if not isinstance(extracted_data, list):
            extracted_data = [extracted_data] if isinstance(extracted_data, dict) else []

        discrimination_dir.mkdir(parents=True, exist_ok=True)
        result_collection_path = task_dirs["base"] / f"result_collection_{scene_id}.json"

        tracker.update_file_stage(folder_name, Stage.JUDGE, Status.PROCESSING, primary_key=pk)
        try:
            from utils.knowledge_judge import KnowledgeEnhancedValidator
            task_base_path = str(task_dirs["base"].parent)
            validator = KnowledgeEnhancedValidator(task_id, task_base_path, use_mapping_table=True)
            processed_results = {
                folder_name: {
                    "json_data": extracted_data,
                    "json_path": str(extracted_json),
                    "original_folder": folder_name,
                }
            }
            s_count, f_count, _ = validator.process_validation_results(
                processed_results,
                str(discrimination_dir),
                judgment_rules,
                result_collection_path,
            )
            has_output = bool(list(discrimination_dir.glob(f"*{folder_name}*")))
            if has_output or s_count > 0:
                tracker.update_file_stage(folder_name, Stage.JUDGE, Status.SUCCESS, primary_key=pk)
                return StageResult(success=True, output_path=str(discrimination_dir), message="判别成功")
            else:
                msg = f"判别完成但无输出文件，failed={f_count}"
                tracker.update_file_stage(folder_name, Stage.JUDGE, Status.FAILED, msg, primary_key=pk)
                return StageResult(success=False, message=msg)
        except Exception as exc:
            msg = f"判别异常: {exc}"
            tracker.update_file_stage(folder_name, Stage.JUDGE, Status.FAILED, msg, primary_key=pk)
            return StageResult(success=False, message=msg)


def _load_judgment_rules(task_id: str):
    """返回 (judgment_rules, scene_id)"""
    try:
        tasks_file = Path("data/tasks/tasks.json")
        if not tasks_file.exists():
            return [], "default"
        with open(tasks_file, "r", encoding="utf-8") as f:
            tasks = json.load(f)
        for t in tasks:
            if str(t.get("id", "")).strip() == task_id:
                scene_id = str(t.get("sceneId") or "default").strip()
                return t.get("judgmentRules", []) or [], scene_id
    except Exception:
        pass
    return [], "default"
