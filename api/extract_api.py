from flask import Blueprint
import os, json
from flask import Flask, request, send_from_directory
from flask import jsonify
from pathlib import Path
import time
from utils import task_folder_utils, util_extract
from utils.status_tracker import StatusTracker, Stage, Status
from utils.auth_util import AuthUtils
from utils.task_verify import task_permission_required

extract_bp = Blueprint("extract", __name__)

# 页面索引未知的默认值
PAGE_IDX_UNKNOWN = -1

def _get_pk(file_path_or_name):
    """
    从文件路径或文件名提取主键(地块编码)。
    逻辑：如果有下划线，取第一部分；否则取文件名(不含后缀)。
    """
    if isinstance(file_path_or_name, Path):
        stem = file_path_or_name.stem
    else:
        stem = Path(file_path_or_name).stem
        
    return stem.split('_')[0] if '_' in stem else stem


def _ensure_page_idx_in_results(extracted_data):
    """
    确保提取结果中包含page_idx字段
    """
    if not extracted_data or not isinstance(extracted_data, list):
        return extracted_data
    
    for item in extracted_data:
        if isinstance(item, dict):
            # 检查是否有任何字段缺少对应的page_idx字段
            fields_to_check = []
            for key in item.keys():
                if not key.endswith('_page_idx') and not key in ['id', 'fileId', 'ruleId']:
                    fields_to_check.append(key)
            
            for field in fields_to_check:
                page_idx_field = f"{field}_page_idx"
                if page_idx_field not in item:
                    item[page_idx_field] = PAGE_IDX_UNKNOWN
    
    return extracted_data


@extract_bp.get("/task/<task_id>/chunked/files")
@AuthUtils.token_required
@task_permission_required()
def get_task_chunked_files(task_id):
    """
    获取指定任务分块目录中的文件列表
    """
    try:
        task_dirs = task_folder_utils.get_task_dirs(task_id)
        chunked_dir = task_dirs["chunked"]

        if not chunked_dir.exists():
            return jsonify({"error": "任务分块目录不存在"}), 404

        files_info = []

        for file_path in chunked_dir.rglob("*"):
            if file_path.is_file():
                stat = file_path.stat()
                file_info = {
                    "filename": file_path.name,
                    "relative_path": str(file_path.relative_to(chunked_dir)),
                    "full_path": str(file_path),
                    "size": stat.st_size,
                    "modified_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
                    "file_type": file_path.suffix.lower().replace('.', ''),
                    "area_code": file_path.stem.split('_')[0] if '_' in file_path.stem else file_path.stem,
                    "has_page_idx": True  # 标记分块文件包含页面索引
                }

                files_info.append(file_info)

        return jsonify({
            "task_id": task_id,
            "count": len(files_info),
            "files": files_info
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@extract_bp.post("/task/<task_id>/extract")
@AuthUtils.token_required
@task_permission_required()
def task_extract_content(task_id):
    """
    任务特定的内容提取接口 - 修复版本，支持page_idx提取
    """
    try:
        data = request.get_json()
        extraction_rules = data.get("extraction_rules", [])

        if not extraction_rules:
            print("错误: 缺少提取规则")
            return jsonify({"error": "缺少提取规则"}), 400

        task_dirs = task_folder_utils.get_task_dirs(task_id)

        chunked_dir = task_dirs["chunked"]
        if not chunked_dir.exists():
            print(f"错误: 分块目录不存在: {chunked_dir}")
            return jsonify({"error": "任务分块目录不存在，请先完成分块"}), 404

        markdown_files = list(chunked_dir.rglob("*.md"))
        json_files = list(chunked_dir.rglob("*.json"))
        
        print(f"找到 {len(markdown_files)} 个Markdown文件")
        print(f"找到 {len(json_files)} 个JSON文件")

        files_to_process = markdown_files if markdown_files else json_files
        
        if not files_to_process:
            return jsonify({"error": "没有找到可处理的文件（需要.md或.json格式）"}), 404

        extraction_dir = task_dirs["extraction_results"]

        os.makedirs(extraction_dir, exist_ok=True)

        tracker = StatusTracker(task_id)
        result_collection_file_path = task_dirs["base"] / "result_collection.json"

        try:
            from utils.content_extraction import custom_extraction, extract_single_markdown_file, extract_single_json_file

            processed_count = 0
            result_files = []

            for file_path in files_to_process:
                track_name = file_path.name
                try:
                    tracker.update_file_stage(track_name, Stage.EXTRACT, Status.PROCESSING, primary_key=_get_pk(file_path))

                    if file_path.suffix.lower() == '.md':
                        result = extract_single_markdown_file(
                            str(file_path),
                            extraction_rules,
                            str(extraction_dir)
                        )
                    else:
                        result = extract_single_json_file(
                            str(file_path),
                            extraction_rules,
                            str(extraction_dir),
                            result_collection_file_path
                        )

                    if result.get("success"):
                        processed_count += 1
                        
                        # 确保提取结果中包含page_idx字段
                        if result.get("extracted_data"):
                            result["extracted_data"] = _ensure_page_idx_in_results(result["extracted_data"])
                        
                        if result.get("full_result"):
                            full_result = result.get("full_result")
                            if "extraction_results" in full_result and full_result["extraction_results"]:
                                full_result["extraction_results"] = _ensure_page_idx_in_results(full_result["extraction_results"])
                    
                    if result:
                        tracker.update_file_stage(track_name, Stage.EXTRACT, Status.SUCCESS, primary_key=_get_pk(file_path))
                    else:
                        tracker.update_file_stage(track_name, Stage.EXTRACT, Status.FAILED, "提取返回失败", primary_key=_get_pk(file_path))

                except Exception as file_error:
                    print(f"处理文件 {file_path.name} 失败: {file_error}")
                    tracker.update_file_stage(track_name, Stage.EXTRACT, Status.FAILED, str(file_error), primary_key=_get_pk(file_path))

            if processed_count > 0:
                for result_file in extraction_dir.glob("*"):
                    if result_file.is_file():
                        result_files.append(result_file.name)

                return jsonify({
                    "message": "内容提取完成",
                    "data": {
                        "task_id": task_id,
                        "total_files": len(files_to_process),
                        "processed_files": processed_count,
                        "result_files": result_files,
                        "extraction_results": {
                            "directory": str(extraction_dir),
                            "files": result_files
                        },
                        "page_idx_support": True,  # 标记支持页面索引
                        "page_idx_unknown_value": PAGE_IDX_UNKNOWN
                    }
                }), 200
            else:
                return jsonify({"error": "所有文件处理失败"}), 500

        except ImportError as e2:
            return jsonify({"error": f"内容提取模块导入失败: {str(e2)}"}), 500

    except Exception as e:
        print(f"任务提取接口异常: {e}")
        import traceback
        print(f"详细错误: {traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500


@extract_bp.get("/task/<task_id>/extraction_results")
@AuthUtils.token_required
@task_permission_required()
def get_task_extraction_results(task_id):
    """
    获取任务内容提取结果
    """
    try:
        task_dirs = task_folder_utils.get_task_dirs(task_id)
        extraction_dir = task_dirs["extraction_results"]

        if not extraction_dir.exists():
            return jsonify({"error": "内容提取结果目录不存在"}), 404

        files_info = []

        for file_path in extraction_dir.rglob("*"):
            if file_path.is_file():
                stat = file_path.stat()
                
                # 尝试读取文件内容以检查是否包含page_idx
                has_page_idx = False
                if file_path.suffix.lower() == '.json':
                    try:
                        with open(file_path, 'r', encoding='utf-8') as f:
                            content = json.load(f)
                            if isinstance(content, dict):
                                extraction_results = content.get('extraction_results', [])
                                if extraction_results and isinstance(extraction_results, list) and extraction_results:
                                    first_item = extraction_results[0]
                                    if isinstance(first_item, dict):
                                        has_page_idx = any(key.endswith('_page_idx') for key in first_item.keys())
                    except:
                        pass
                
                file_info = {
                    "filename": file_path.name,
                    "relative_path": str(file_path.relative_to(extraction_dir)),
                    "full_path": str(file_path),
                    "size": stat.st_size,
                    "modified_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
                    "file_type": file_path.suffix.lower().replace('.', ''),
                    "is_extraction_result": "_extracted" in file_path.name,
                    "has_page_idx": has_page_idx  # 标记是否包含页面索引
                }

                files_info.append(file_info)

        return jsonify({
            "task_id": task_id,
            "count": len(files_info),
            "files": files_info,
            "page_idx_support": True,
            "page_idx_unknown_value": PAGE_IDX_UNKNOWN
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500