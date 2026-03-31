"""
总日志处理数据导出API
"""

from flask import Blueprint, jsonify, request, send_file
from utils.status_tracker import StatusTracker
import pandas as pd
import io
import os
import json
from utils.auth_util import AuthUtils
from utils.task_verify import task_permission_required

processStatus_bp = Blueprint("processStatus", __name__)

# ========== 统一阶段定义（用于重算 summary/导出） ==========
ALL_STAGES = [
    "upload", "download",
    "decompress", "convert", "organize",
    "parse", "chunk",
    "extract", "judge",
    "generate"
]

STAGE_CN_MAP = {
    "upload": "上传",
    "download": "下载",
    "decompress": "解压",
    "convert": "转换",
    "organize": "裁剪",
    "parse": "解析",
    "chunk": "分块",
    "extract": "提取",
    "judge": "判别",
    "generate": "生成"
}

STATUS_CN = {
    "pending": "等待处理",
    "processing": "处理中",
    "success": "成功",
    "failed": "失败",
    "skipped": "跳过",
    "unknown": "未知"
}


def _basename(p) -> str:
    """把 file_used 的路径变成纯文件名，避免前端显示一串路径。"""
    if p is None:
        return ""
    try:
        return os.path.basename(str(p))
    except Exception:
        return str(p)


def safe_read_status(tracker: StatusTracker) -> dict:
    """
    安全读取 status_report.json：
    1) 优先 tracker._load_json()（你现在可能会 raise）
    2) 失败回退读 .bak
    3) 都失败返回空结构，避免接口 500
    """
    try:
        data = tracker._load_json()
        if not isinstance(data, dict):
            data = {}
        data.setdefault("files", {})
        data.setdefault("summary", {})
        return data
    except Exception as e:
        # 回退读取 .bak
        try:
            bak_path = tracker.report_path.with_suffix(".bak")
            if bak_path.exists():
                with open(bak_path, "r", encoding="utf-8") as f:
                    bak = json.load(f)
                if not isinstance(bak, dict):
                    bak = {}
                bak.setdefault("files", {})
                bak.setdefault("summary", {})
                return bak
        except Exception:
            pass

        print(f"[processStatus] safe_read_status failed: {e}")
        return {"files": {}, "summary": {}}


def recompute_summary_from_files(files: dict) -> dict:
    """
    summary 缺失/为空时，从 files 即时重算，保证顶栏统计稳定。
    口径对齐你 StatusTracker.update_file_stage 的统计方式：
    - success/failed/processing 计入 total
    - skipped 不计入 total
    """
    summary = {}
    for stage in ALL_STAGES:
        s_cnt = f_cnt = sk_cnt = total = 0
        for info in files.values():
            stage_info = info.get(stage)
            if isinstance(stage_info, dict) and "status" in stage_info:
                st = stage_info.get("status")
                if st == "success":
                    s_cnt += 1
                    total += 1
                elif st == "failed":
                    f_cnt += 1
                    total += 1
                elif st == "processing":
                    total += 1
                elif st == "skipped":
                    sk_cnt += 1

        summary[stage] = {
            "success": s_cnt,
            "failed": f_cnt,
            "skipped": sk_cnt,
            "total": total
        }
    return summary


@processStatus_bp.get("/task/<task_id>/processStatus")
@AuthUtils.token_required
@task_permission_required()
def get_dashboard_data(task_id: str):
    """
    获取看板数据 (支持分页)
    Query Params: page=1, size=50, status=failed/success/processing
    """
    try:
        page = int(request.args.get("page", 1))
        size = int(request.args.get("size", 50))
        status_filter = request.args.get("status")  # failed, success, processing

        tracker = StatusTracker(task_id)

        # ✅ 安全读取：避免并发瞬态导致 500 / 空 summary
        data = safe_read_status(tracker)

        all_files = data.get("files", {}) or {}
        summary = data.get("summary", {}) or {}

        # ✅ summary 缺失/为空时，从 files 即时重算（顶栏永不归零）
        if not summary:
            summary = recompute_summary_from_files(all_files)

        # 转换为列表
        file_list = []
        for fname, info in all_files.items():
            if not isinstance(info, dict):
                continue

            # 筛选逻辑
            if status_filter and info.get("final_status") != status_filter:
                continue

            # 解析显示用的文件名：优先 upload/download 的 file_used（取 basename）
            display_name = (
                _basename(info.get("upload", {}).get("file_used")) or
                _basename(info.get("download", {}).get("file_used")) or
                fname
            )
            # ==================20260303修改：优化注释说明=============
            # 构造前端悬浮显示的文本：
            # - failed：显示失败原因
            # - 其他状态：若存在 error 字段（用于“成功但需标注”的提示，比如“无符合条件文件”），优先显示该提示；否则显示 time
            stage_hover = {}
            for stage in ALL_STAGES:
                s_info = info.get(stage, {}) or {}
                st = s_info.get("status")
                if st == "failed":
                    # 优先取 error，没有则取 time，再没有提示无详情
                    stage_hover[stage] = f"失败原因: {s_info.get('error', '无详细日志')}"
                else:
                    # 成功/进行中/跳过：若有 error（作为提示信息），则优先显示
                    stage_hover[stage] = s_info.get("error") or s_info.get("time") or ""
            # ==================20260303修改：优化注释说明（结束）=============

            item = {
                "primary_key": fname,        # 地块编码 / 主键
                "file_name": display_name,   # 实际文件名(带后缀)
                "stage_hover": stage_hover, # [0109新增] 各阶段悬浮提示信息
            }
            item.update(info)
            file_list.append(item)

        # 按更新时间倒序
        file_list.sort(key=lambda x: x.get("updated_at", ""), reverse=True)

        # 分页
        total = len(file_list)
        start = (page - 1) * size
        end = start + size
        paged_list = file_list[start:end]

        return jsonify({
            "code": 0,
            "data": {
                "summary": summary,
                "files": {
                    "list": paged_list,
                    "total": total,
                    "page": page,
                    "size": size
                }
            }
        })

    except Exception as e:
        print(f"Dashboard error: {str(e)}")
        return jsonify({"code": 1, "msg": str(e)}), 500


@processStatus_bp.get("/task/<task_id>/export/failures")
@AuthUtils.token_required
@task_permission_required()
def export_failures(task_id: str):
    """
    导出任务完整处理日志（包含详情、失败记录两张表）
    """
    print(f"Received export request for task: {task_id}")
    try:
        tracker = StatusTracker(task_id)

        # ✅ 安全读取：避免并发瞬态导致导出失败
        data = safe_read_status(tracker)
        files = data.get("files", {}) or {}

        if not files:
            return jsonify({"code": 1, "msg": "任务数据不存在或为空，无法导出"}), 404

        details_list = []
        failure_list = []

        for fname, info in files.items():
            if not isinstance(info, dict):
                continue

            final_status = info.get("final_status", "unknown")

            # 获取真实文件名（取 basename）
            original_file = (
                _basename(info.get("upload", {}).get("file_used")) or
                _basename(info.get("download", {}).get("file_used")) or
                fname
            )

            row = {
                "地块编码": fname,
                "文件名": original_file,
                "整体状态": STATUS_CN.get(final_status, final_status),
                "最后更新时间": info.get("updated_at", "")
            }

            # 填充各阶段状态
            for stage in ALL_STAGES:
                stage_status = info.get(stage, {}).get("status", "pending")
                col_name = f"{STAGE_CN_MAP.get(stage, stage)}状态"
                row[col_name] = STATUS_CN.get(stage_status, stage_status)

            details_list.append(row)

            # 失败文件
            if final_status == "failed":
                fail_row = row.copy()
                error_msg = []
                for stage in ALL_STAGES:
                    stage_info = info.get(stage, {})
                    if stage_info.get("status") == "failed":
                        err = stage_info.get("error", "")
                        if err:
                            error_msg.append(f"[{STAGE_CN_MAP.get(stage, stage)}]: {err}")
                fail_row["错误详情"] = "; ".join(error_msg)
                failure_list.append(fail_row)

        # 生成 Excel
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            if not details_list:
                details_list = [{"提示": "暂无数据"}]
            pd.DataFrame(details_list).to_excel(writer, index=False, sheet_name="文件处理详情")

            if failure_list:
                pd.DataFrame(failure_list).to_excel(writer, index=False, sheet_name="失败文件(重点关注)")
            else:
                pd.DataFrame([{"提示": "无失败文件"}]).to_excel(writer, index=False, sheet_name="失败文件(无)")

        output.seek(0)
        return send_file(
            output,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True,
            download_name=f"task_{task_id}_report.xlsx"
        )

    except Exception as e:
        print(f"Export error: {str(e)}")
        return jsonify({"code": 1, "msg": f"导出失败: {str(e)}"}), 500
