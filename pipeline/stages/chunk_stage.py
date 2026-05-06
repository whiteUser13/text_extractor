# pipeline/stages/chunk_stage.py
"""
分块阶段：将解析输出的 content_list.json 转换为标准化分块 JSON + Excel。
幂等：若 chunked/{rid}_分块结果/{rid}_parsed.json 已存在且非空，则跳过。
"""
from __future__ import annotations

from pathlib import Path

from pipeline.stages.base import BaseStage, StageResult
from utils.status_tracker import StatusTracker, Stage, Status


class ChunkStage(BaseStage):
    name = "chunk"

    def execute(self, job: dict, task_dirs: dict) -> StageResult:
        task_id = job["task_id"]
        file_name = job["file_name"]
        rid = Path(file_name).stem

        parsed_dir: Path = task_dirs["parsed"]
        chunked_dir: Path = task_dirs["chunked"]
        tracker = StatusTracker(task_id)
        pk = rid.split("_")[0] if "_" in rid else rid

        # 幂等检测
        chunked_json = chunked_dir / f"{rid}_分块结果" / f"{rid}_parsed.json"
        if self._is_done(chunked_json):
            return StageResult(success=True, skipped=True, message=f"分块结果已存在: {chunked_json}")

        # 找到 content_list 文件
        report_dir = parsed_dir / rid
        if not report_dir.exists():
            return StageResult(success=False, message=f"解析目录不存在: {report_dir}")

        from utils import util_chunk
        json_path = util_chunk._find_content_list_file(report_dir, rid)
        if json_path is None or not json_path.exists():
            return StageResult(success=False, message=f"未找到 content_list 文件，rid={rid}")

        tracker.update_file_stage(rid, Stage.CHUNK, Status.PROCESSING, primary_key=pk)
        try:
            from utils import parse as parser
            parsed = parser.parse_content_list(str(json_path))
            parser.export_report_files(parsed, output_root=str(chunked_dir))
            tracker.update_file_stage(rid, Stage.CHUNK, Status.SUCCESS, primary_key=pk)
            return StageResult(success=True, output_path=str(chunked_dir / f"{rid}_分块结果"), message="分块成功")
        except Exception as exc:
            msg = f"分块异常: {exc}"
            tracker.update_file_stage(rid, Stage.CHUNK, Status.FAILED, msg, primary_key=pk)
            return StageResult(success=False, message=msg)
