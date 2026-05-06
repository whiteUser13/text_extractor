# pipeline/stages/base.py
"""
所有流水线 Stage 的基类接口。
每个 Stage 只负责单一职责，对外提供统一的 execute() 入口。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class StageResult:
    """Stage 执行结果"""
    success: bool
    skipped: bool = False          # True 表示幂等检测到输出已存在，直接跳过
    message: str = ""
    output_path: Optional[str] = None


class BaseStage:
    """所有 Stage 必须继承此基类并实现 execute()"""

    name: str = "base"

    def execute(self, job: dict, task_dirs: dict) -> StageResult:
        """
        执行当前 stage。

        Parameters
        ----------
        job      : pipeline_tasks.json 中的单条任务记录字典
        task_dirs: task_folder_utils.get_task_dirs(task_id) 的返回值

        Returns
        -------
        StageResult
        """
        raise NotImplementedError

    def _is_done(self, *paths) -> bool:
        """检查所有路径是否都已存在且非空（幂等判断）"""
        from pathlib import Path
        for p in paths:
            path = Path(p)
            if not path.exists():
                return False
            if path.is_file() and path.stat().st_size == 0:
                return False
        return True
