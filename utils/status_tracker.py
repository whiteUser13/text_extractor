"""
总状态追踪模块
"""

import json
import os
import time
from pathlib import Path
from threading import Lock
from datetime import datetime
from filelock import FileLock


class Stage:
    # 1. 获取文件阶段
    UPLOAD = "upload"  # 本地上传
    DOWNLOAD = "download"  # 服务器下载

    # 2. 预处理阶段
    DECOMPRESS = "decompress"  # 解压缩
    CONVERT = "convert"  # 格式转换 (Word -> PDF)
    ORGANIZE = "organize"  # 整理/剪切/拼接 (PDF处理)

    # 3. 内容解析阶段
    PARSE = "parse"  # MinerU 解析 (PDF -> MD)
    CHUNK = "chunk"  # 文本分块

    # 4. 业务分析阶段
    EXTRACT = "extract"  # 关键指标提取
    JUDGE = "judge"  # 智能判别

    # 5. 结果生成阶段
    GENERATE = "generate"  # 生成最终 Excel


class Status:
    PENDING = "pending"  # 等待中
    PROCESSING = "processing"  # 进行中
    SUCCESS = "success"  # 成功
    FAILED = "failed"  # 失败
    SKIPPED = "skipped"  # 跳过


class StatusTracker:
    def __init__(self, task_id: str):
        self.task_id = task_id
        # 数据存储路径：data/tasks/{task_id}/status_report.json
        self.base_dir = Path("data") / "tasks" / task_id
        self.report_path = self.base_dir / "status_report.json"
        self.base_dir.mkdir(parents=True, exist_ok=True)

        #  跨进程文件锁：同一个 task_id 共享同一把锁
        self._file_lock = FileLock(str(self.report_path) + ".lock", timeout=30)

        # 初始化文件（如果不存在）
        if not self.report_path.exists():
            # 初始化也要在锁内，避免并发首次创建互相覆盖
            with self._file_lock:
                if not self.report_path.exists():
                    self._save_json({"summary": {}, "files": {}})

    def _load_json(self):
        """
        读取 JSON（增强并发容错）：
        - 并发 replace / 瞬态读异常时：重试
        - 读失败：抛异常，避免返回空结构导致“覆盖清空历史”
        """
        if not self.report_path.exists():
            return {"files": {}, "summary": {}}

        last_err = None
        for _ in range(5):  # 重试 5 次应对瞬态问题
            try:
                with open(self.report_path, "r", encoding="utf-8") as f:
                    content = f.read().strip()

                if not content:
                    raise json.JSONDecodeError("empty content", "", 0)

                data = json.loads(content)

                if not isinstance(data, dict):
                    data = {}
                data.setdefault("files", {})
                data.setdefault("summary", {})
                return data

            except (json.JSONDecodeError, OSError) as e:
                last_err = e
                time.sleep(0.05)

        # 最终失败：不要返回空结构（否则下一次写会把历史清空）
        raise RuntimeError(f"[StatusTracker] Load status json failed after retries: {last_err}")

    def _save_json(self, data):
        """
        原子写入 + flush/fsync（降低 Windows 瞬态风险）：
        - 写临时文件 -> flush+fsync -> replace
        - 可选：保存 .bak 备份（强烈建议）
        """
        temp_path = self.report_path.with_suffix(".tmp")
        bak_path = self.report_path.with_suffix(".bak")

        try:
            # 写临时文件
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())

            # 可选备份：避免极端情况下可恢复
            if self.report_path.exists():
                try:
                    # 不用 shutil 也行，这里用最稳的方式
                    with open(self.report_path, "rb") as rf, open(bak_path, "wb") as wf:
                        wf.write(rf.read())
                except Exception:
                    pass

            # 原子替换
            os.replace(temp_path, self.report_path)

        except PermissionError:
            # Windows 下偶发占用：重试
            max_retries = 3
            for i in range(max_retries):
                try:
                    time.sleep(0.1)
                    os.replace(temp_path, self.report_path)
                    break
                except PermissionError:
                    if i == max_retries - 1:
                        print("[StatusTracker] Save failed after retries: File is locked.")
                except Exception as e:
                    print(f"[StatusTracker] Save error: {e}")
                    break
        except Exception as e:
            print(f"[StatusTracker] Critical Save failed: {e}")

    def update_file_stage(
        self,
        file_name: str,
        stage: str,
        status: str,
        error_msg: str = None,
        primary_key: str = None
    ):
        """
        更新单个文件在特定阶段的状态
        - 用 FileLock 包住“读-改-写”，避免并发覆盖（核心修复）
        """
        file_name = str(file_name).replace("\\", "/")

        with self._file_lock:  # 跨进程互斥
            data = self._load_json()

            # key 规则：优先 primary_key，否则文件 stem
            key = primary_key or Path(file_name).stem

            # 1) 初始化文件记录
            if key not in data["files"]:
                data["files"][key] = {
                    "final_status": Status.PROCESSING,
                    "updated_at": "",
                    "original_name": key,
                    "related_files": []
                }

            # 2) 记录关联文件
            if file_name not in data["files"][key].get("related_files", []):
                data["files"][key].setdefault("related_files", [])
                data["files"][key]["related_files"].append(file_name)

            # 3) 写入阶段信息
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            stage_info = {
                "status": status,
                "time": now_str,
                "file_used": file_name
            }
            if error_msg:
                stage_info["error"] = error_msg

            data["files"][key][stage] = stage_info
            data["files"][key]["updated_at"] = now_str

            # 4) 重新计算 final_status
            has_failure = False
            for _, v in data["files"][key].items():
                if isinstance(v, dict) and v.get("status") == Status.FAILED:
                    has_failure = True
                    break

            if has_failure:
                data["files"][key]["final_status"] = Status.FAILED
            else:
                data["files"][key]["final_status"] = Status.PROCESSING
                if stage == Stage.GENERATE and status == Status.SUCCESS:
                    data["files"][key]["final_status"] = Status.SUCCESS

            # 5) 更新 summary（仅更新当前 stage 的统计，保持你原逻辑）
            stats = data.get("summary", {})
            s_cnt, f_cnt, sk_cnt, total = 0, 0, 0, 0
            for f_info in data["files"].values():
                if stage in f_info:
                    st = f_info[stage]["status"]
                    if st == Status.SUCCESS:
                        s_cnt += 1
                        total += 1
                    elif st == Status.FAILED:
                        f_cnt += 1
                        total += 1
                    elif st == Status.PROCESSING:
                        total += 1
                    elif st == Status.SKIPPED:
                        sk_cnt += 1

            stats[stage] = {
                "success": s_cnt,
                "failed": f_cnt,
                "skipped": sk_cnt,
                "total": total
            }
            data["summary"] = stats

            self._save_json(data)

    def mark_complete(self, file_name: str, primary_key: str = None):
        """标记文件全流程结束（同样要在 FileLock 内）"""
        file_name = str(file_name).replace("\\", "/")

        with self._file_lock:
            data = self._load_json()
            key = primary_key or Path(file_name).stem

            if key in data["files"]:
                has_remaining_failure = False
                for _, v in data["files"][key].items():
                    if isinstance(v, dict) and v.get("status") == Status.FAILED:
                        has_remaining_failure = True
                        break
                if not has_remaining_failure:
                    data["files"][key]["final_status"] = Status.SUCCESS

            self._save_json(data)
