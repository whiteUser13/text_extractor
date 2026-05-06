# pipeline/stages/parse_stage.py
"""
解析阶段：将 PDF/Word 解析为 Markdown + content_list.json。
- 优先读 pdf_cut/ 目录中的文件（由前一 stage 生成）
- 自动检测是否为文本型 PDF，文本型走 pure_txt（快），扫描件/Word 回退 MinerU
幂等：若 parsed/{rid}/{rid}.md 已存在且非空，则跳过。
"""
from __future__ import annotations

import os
from pathlib import Path

from pipeline.stages.base import BaseStage, StageResult
from utils.status_tracker import StatusTracker, Stage, Status


class ParseStage(BaseStage):
    name = "parse"

    def execute(self, job: dict, task_dirs: dict) -> StageResult:
        task_id = job["task_id"]
        file_name = job["file_name"]
        rid = Path(file_name).stem

        parsed_dir: Path = task_dirs["parsed"]
        tracker = StatusTracker(task_id)
        pk = rid.split("_")[0] if "_" in rid else rid

        # 幂等检测
        md_file = parsed_dir / rid / f"{rid}.md"
        if self._is_done(md_file):
            return StageResult(success=True, skipped=True, message=f"解析结果已存在: {md_file}")

        # 获取 MinerU 服务地址（兼容 parse_port_state 模块）
        try:
            from scheduler.parse_port_state import get_parse_url, release_port
            mineru_url, port = get_parse_url()
        except Exception:
            mineru_url = os.getenv("MINERU_URL", "http://127.0.0.1:9087/file_parse")
            port = None

        # 解析模式：pure_txt 会自动回退到 mineru（扫描件）
        parse_style = job.get("parse_style", "pure_txt")

        try:
            from utils import util_parse
            result = util_parse.select_parse_style(
                use=parse_style,
                rid=rid,
                task_dirs=task_dirs,
                mineru_url=mineru_url,
                task_id=task_id,
            )
        except Exception as exc:
            result = {"success": False, "msg": str(exc)}
        finally:
            if port is not None:
                try:
                    release_port(port)
                except Exception:
                    pass

        if result.get("success"):
            return StageResult(success=True, output_path=str(parsed_dir / rid), message=result.get("msg", "解析成功"))
        else:
            return StageResult(success=False, message=result.get("msg", "解析失败"))
