# api/judge_api.py
from flask import Blueprint
import os, json
from flask import Flask, request, send_from_directory
from flask import jsonify
from pathlib import Path
import time
from utils.ai_validator import AIValidator
from utils import task_folder_utils, util_judge
from utils.status_tracker import StatusTracker, Stage, Status  # 1224修改[总日志追踪]
from utils.auth_util import AuthUtils
from utils.task_verify import task_permission_required
from utils.knowledge_judge import KnowledgeEnhancedValidator

judge_bp = Blueprint("judge", __name__)


# 1225[新增] 辅助函数：统一提取主键逻辑
def _get_pk(name):
    """从名称中提取主键(地块编码)"""
    return name.split('_')[0] if '_' in name else name


@judge_bp.post("/task/<task_id>/discriminate")  # 12.15改
@AuthUtils.token_required
@task_permission_required()
def task_discriminate_content(task_id):
    """
    任务特定的内容判别接口 - 使用AIValidator生成JSON和MD文件
    """
    try:
        data = request.get_json()
        judgment_rules = data.get("judgment_rules", [])

        # 获取任务目录
        task_dirs = task_folder_utils.get_task_dirs(task_id)
        result_collection_file_path = task_dirs["base"] / "result_collection.json"

        # 检查提取结果目录
        extraction_dir = task_dirs["extraction_results"]
        if not extraction_dir.exists():
            return jsonify({"error": "内容提取结果目录不存在，请先完成内容提取"}), 404

        # 搜索提取结果JSON文件
        json_files = list(extraction_dir.rglob("*_extracted.json"))

        if not json_files:
            return jsonify({"error": "没有找到提取结果文件"}), 404

        # 创建判别结果目录
        discrimination_dir = task_dirs["discrimination_results"]
        os.makedirs(discrimination_dir, exist_ok=True)

        tracker = StatusTracker(task_id)  # 1224修改[总日志追踪]：初始化状态追踪器

        processed_files = []
        error_files = []

        for json_file in json_files:

            folder_name = json_file.stem.replace('_extracted', '')  # 1224修改[总日志追踪]：获取文件标识 (通常是文件名去掉后缀)
            # [新增] 计算主键
            pk = _get_pk(folder_name)

            try:
                tracker.update_file_stage(folder_name, Stage.JUDGE, Status.PROCESSING,
                                          primary_key=pk)  # 1224修改[总日志追踪]：记录开始判别

                # 读取提取结果
                with open(json_file, 'r', encoding='utf-8') as f:
                    extraction_result = json.load(f)

                # 从提取结果中获取实际的提取数据
                extracted_data = []
                if isinstance(extraction_result, dict) and "extraction_results" in extraction_result:
                    # 新的元数据格式 - 提取实际数据
                    extracted_data = extraction_result["extraction_results"]
                else:
                    # 旧的直接提取数据格式
                    extracted_data = extraction_result

                # 确保是列表格式
                if not isinstance(extracted_data, list):
                    if isinstance(extracted_data, dict):
                        extracted_data = [extracted_data]
                    else:
                        extracted_data = []

                # print(f"提取数据条数: {len(extracted_data)}")
                task_base_path = task_dirs.get("task_root") or task_dirs.get("base") or str(
                    Path(task_dirs.get("extraction_results", ".").parent))
                validator = KnowledgeEnhancedValidator(task_id, task_base_path, use_mapping_table=True)
                processed_results = {
                    folder_name: {
                        'json_data': extracted_data,
                        'json_path': str(json_file),
                        'original_folder': folder_name
                    }
                }

                successful_count, failed_count, validation_results = validator.process_validation_results(
                    processed_results,
                    str(discrimination_dir),  # 输出目录
                    judgment_rules,  # 判别规则列表（包含知识库和普通规则）
                    result_collection_file_path
                )

                # 修正成功计数：如果生成了文件就算成功
                actual_successful = 0
                if successful_count > 0 or failed_count > 0:
                    # 检查实际生成的文件
                    generated_files = list(discrimination_dir.glob(f"*{folder_name}*"))
                    if generated_files:
                        actual_successful = 1

                # 检查生成的文件
                generated_files = list(discrimination_dir.glob(f"*{folder_name}*"))
                json_files_found = [f for f in generated_files if f.suffix == '.json']
                md_files_found = [f for f in generated_files if f.suffix == '.md']

                # print(f"生成的文件统计:")
                # print(f"  JSON文件: {[f.name for f in json_files_found]}")
                # print(f"  MD文件: {[f.name for f in md_files_found]}")

                # 如果没有生成MD文件，手动生成
                if not md_files_found and json_files_found:
                    print(f"警告: 未生成MD文件，尝试手动生成")
                    # 读取生成的JSON文件
                    for json_result_file in json_files_found:
                        try:
                            with open(json_result_file, 'r', encoding='utf-8') as f:
                                validation_data = json.load(f)

                            # 确保validation_data是字典格式
                            if isinstance(validation_data, list):
                                if validation_data and isinstance(validation_data[0], dict):
                                    # 使用第一个元素
                                    validation_dict = {folder_name: {folder_name: validation_data[0]}}
                                else:
                                    validation_dict = {folder_name: {folder_name: {}}}
                            else:
                                validation_dict = {folder_name: {folder_name: validation_data}}

                            # 为手动生成MD文件创建规则描述（使用单个文件的处理）
                            temp_single_result = {folder_name: processed_results[folder_name]}
                            rules_description_for_md = validator._combine_rules_with_knowledge([], judgment_rules,
                                                                                               temp_single_result,
                                                                                               folder_name)

                            # 调用save_validation_markdown生成MD文件
                            md_path = validator.save_validation_markdown(
                                validation_dict,  # 修正：传入正确的字典格式
                                str(discrimination_dir),
                                folder_name,
                                rules_description_for_md  # 使用为当前文件生成的规则描述
                            )
                            print(f"✅ 手动生成MD文件成功: {md_path}")
                            md_files_found.append(Path(md_path))
                        except Exception as e:
                            print(f"❌ 手动生成MD文件失败: {e}")
                            import traceback
                            print(f"详细错误: {traceback.format_exc()}")

                # 1227修改 插入新的状态判断逻辑

                # 重新计算是否成功：只要最终存在 JSON 或 MD 任意一种文件，即视为该步骤有产出
                # 这里的 md_files_found 包含了补救生成的那个文件
                has_valid_results = len(json_files_found) > 0 or len(md_files_found) > 0

                # 为当前文件生成规则描述
                temp_single_result = {folder_name: processed_results[folder_name]}
                rules_description_for_current = validator._combine_rules_with_knowledge([], judgment_rules,
                                                                                        temp_single_result, folder_name)

                processed_files.append({
                    "extraction_file": json_file.name,
                    "folder_name": folder_name,
                    "successful_count": 1 if has_valid_results else 0,  # 更新统计用的计数
                    "failed_count": 0 if has_valid_results else 1,
                    "generated_json": [f.name for f in json_files_found],
                    "generated_md": [f.name for f in md_files_found],
                    "rules_description": rules_description_for_current
                })

                # 1224修改[总日志追踪]：根据最终文件存在情况更新状态
                if has_valid_results:
                    tracker.update_file_stage(folder_name, Stage.JUDGE, Status.SUCCESS, primary_key=pk)
                else:
                    tracker.update_file_stage(folder_name, Stage.JUDGE, Status.FAILED, "未生成结果文件", primary_key=pk)

                # 1227修改结束

            except Exception as e:
                error_msg = str(e)
                print(f"❌ 判别失败 {json_file.name}: {error_msg}")
                import traceback
                print(f"详细错误: {traceback.format_exc()}")
                error_files.append({
                    "filename": json_file.name,
                    "error": error_msg
                })
                tracker.update_file_stage(folder_name, Stage.JUDGE, Status.FAILED, error_msg,
                                          primary_key=pk)  # 1224修改[总日志追踪]：记录失败状态

        # 获取最终的生成文件列表
        final_files_info = []
        for file_path in discrimination_dir.rglob("*"):
            if file_path.is_file():
                stat = file_path.stat()
                file_info = {
                    "filename": file_path.name,
                    "relative_path": str(file_path.relative_to(discrimination_dir)),
                    "full_path": str(file_path),
                    "size": stat.st_size,
                    "modified_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
                    "file_type": file_path.suffix.lower().replace('.', '')
                }
                final_files_info.append(file_info)

        # 统计总体结果
        total_successful = sum(1 for f in processed_files if f["successful_count"] > 0)
        total_failed = sum(1 for f in processed_files if f["failed_count"] > 0)

        # 返回结果
        result_info = {
            "task_id": task_id,
            "total_files": len(json_files),
            "processed_files": len(processed_files),
            "error_files": len(error_files),
            "successful_files": total_successful,
            "failed_files": total_failed,
            "discrimination_results": {
                "directory": str(discrimination_dir),
                "generated_files": final_files_info,
                "file_count": len(final_files_info),
                "json_files": [f for f in final_files_info if f["file_type"] == "json"],
                "md_files": [f for f in final_files_info if f["file_type"] == "md"]
            }
        }

        if error_files:
            result_info["errors"] = error_files

        return jsonify({
            "message": "内容判别完成",
            "data": result_info
        }), 200

    except Exception as e:
        print(f"任务判别接口异常: {e}")
        import traceback
        print(f"详细错误: {traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500


@judge_bp.get("/task/<task_id>/discrimination_results")
@AuthUtils.token_required
@task_permission_required()
def get_task_discrimination_results(task_id):
    """
    获取任务内容判别结果
    """
    try:
        task_dirs = task_folder_utils.get_task_dirs(task_id)
        discrimination_dir = task_dirs["discrimination_results"]

        if not discrimination_dir.exists():
            return jsonify({"error": "内容判别结果目录不存在"}), 404

        files_info = []

        # 遍历判别结果目录中的所有文件
        for file_path in discrimination_dir.rglob("*"):
            if file_path.is_file():
                # 获取文件信息
                stat = file_path.stat()
                file_info = {
                    "filename": file_path.name,
                    "relative_path": str(file_path.relative_to(discrimination_dir)),
                    "full_path": str(file_path),
                    "size": stat.st_size,
                    "modified_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
                    "file_type": file_path.suffix.lower().replace('.', ''),
                    "is_discrimination_result": "_discrimination" in file_path.name
                }

                files_info.append(file_info)

        return jsonify({
            "task_id": task_id,
            "count": len(files_info),
            "files": files_info
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ========================
# 新增接口：获取 markdown_extraction 文件夹中的 JSON 文件
# ========================
@judge_bp.get("/markdown_extraction/json_files")
@AuthUtils.token_required
def get_markdown_extraction_json_files():
    """
    获取 markdown_extraction 文件夹中所有 JSON 文件的信息
    """
    try:
        markdown_extraction_base = task_folder_utils.get_extracted_dir() / "markdown_extraction"
        json_files_info = []

        # 如果目录不存在，返回空列表
        if not markdown_extraction_base.exists():
            return jsonify({
                "status": "success",
                "count": 0,
                "json_files": []
            })

        # 遍历 markdown_extraction 下的所有JSON文件
        for item_path in markdown_extraction_base.rglob("*_extracted.json"):
            # 获取相对路径
            relative_path = item_path.relative_to(markdown_extraction_base)

            stat = item_path.stat()
            modify_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime))
            size_kb = round(stat.st_size / 1024, 2)

            # 提取文件夹名和文件名
            if len(relative_path.parts) > 1:
                folder_name = str(relative_path.parent)
                filename = item_path.name
            else:
                folder_name = "root"
                filename = item_path.name

            # 提取规则标识符
            rule_identifier = "default"
            if "_extracted.json" in filename:
                base_name = filename.replace("_extracted.json", "")
                parts = base_name.split("_")
                if len(parts) > 1:
                    rule_identifier = parts[-1]

            json_files_info.append({
                "id": f"{folder_name}/{filename}",
                "folder_name": folder_name,
                "filename": filename,
                "origin_name": item_path.stem,
                "size_kb": size_kb,
                "upload_time": modify_time,
                "full_path": str(item_path),
                "rule_identifier": rule_identifier
            })

        return jsonify({
            "status": "success",
            "count": len(json_files_info),
            "json_files": json_files_info
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ========================
# 新增接口：获取判别结果文件夹信息
# ========================
@judge_bp.get("/results/validated/folders_info")
@AuthUtils.token_required
def get_judged_folders_info():
    """
    获取所有判别结果文件夹的信息
    """
    try:
        # 获取基础目录
        base_dir = Path("tasks")
        folders_info = []

        if not base_dir.exists():
            return jsonify({
                "status": "success",
                "folders": []
            })

        # 遍历所有任务目录
        for task_dir in base_dir.iterdir():
            if task_dir.is_dir():
                discrimination_dir = task_dir / "discrimination_results"

                if discrimination_dir.exists():
                    # 获取目录中的文件
                    files = list(discrimination_dir.glob("*"))

                    if files:  # 如果有文件，才把这个文件夹加入列表
                        # 统计文件类型
                        json_files = [f for f in files if f.suffix.lower() == '.json']
                        md_files = [f for f in files if f.suffix.lower() == '.md']

                        # 获取目录修改时间
                        stat = discrimination_dir.stat()
                        update_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime))

                        # 从任务ID中提取区域代码
                        task_id = task_dir.name
                        area_code = task_id.split('_')[0] if '_' in task_id else task_id

                        folders_info.append({
                            "area_code": area_code,
                            "folder_name": task_id,  # 使用任务ID作为文件夹名
                            "origin_folder_name": task_id,
                            "status": "completed",
                            "update_time": update_time,
                            "file_type": "discrimination_results",
                            "file_count": len(files),
                            "json_count": len(json_files),
                            "md_count": len(md_files)
                        })

        # 如果没有找到判别结果，检查全局的 discrimination_results 目录
        if not folders_info:
            global_discrimination_dir = Path("documents") / "discrimination_results"
            if global_discrimination_dir.exists():
                for folder_path in global_discrimination_dir.iterdir():
                    if folder_path.is_dir():
                        files = list(folder_path.glob("*"))

                        if files:
                            json_files = [f for f in files if f.suffix.lower() == '.json']
                            md_files = [f for f in files if f.suffix.lower() == '.md']

                            stat = folder_path.stat()
                            update_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime))

                            folder_name = folder_path.name
                            area_code = folder_name.split('_')[0] if '_' in folder_name else folder_name

                            folders_info.append({
                                "area_code": area_code,
                                "folder_name": folder_name,
                                "origin_folder_name": folder_name,
                                "status": "completed",
                                "update_time": update_time,
                                "file_type": "discrimination_results",
                                "file_count": len(files),
                                "json_count": len(json_files),
                                "md_count": len(md_files)
                            })

        return jsonify({
            "status": "success",
            "folders": folders_info
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ========================
# 新增接口：获取提取结果文件夹信息
# ========================
@judge_bp.get("/results/extracted/folders_info")
@AuthUtils.token_required
def get_extracted_folders_info():
    """
    获取所有提取结果文件夹的信息
    """
    try:
        # 获取基础目录
        base_dir = Path("tasks")
        folders_info = []

        if not base_dir.exists():
            return jsonify({
                "status": "success",
                "folders": []
            })

        # 遍历所有任务目录
        for task_dir in base_dir.iterdir():
            if task_dir.is_dir():
                extraction_dir = task_dir / "extraction_results"

                if extraction_dir.exists():
                    # 获取目录中的文件
                    files = list(extraction_dir.glob("*"))

                    if files:  # 如果有文件，才把这个文件夹加入列表
                        # 统计文件类型
                        json_files = [f for f in files if f.suffix.lower() == '.json']
                        md_files = [f for f in files if f.suffix.lower() == '.md']

                        # 获取目录修改时间
                        stat = extraction_dir.stat()
                        update_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime))

                        # 从任务ID中提取区域代码
                        task_id = task_dir.name
                        area_code = task_id.split('_')[0] if '_' in task_id else task_id

                        folders_info.append({
                            "area_code": area_code,
                            "folder_name": task_id,  # 使用任务ID作为文件夹名
                            "origin_folder_name": task_id,
                            "status": "completed",
                            "update_time": update_time,
                            "file_type": "extraction_results",
                            "file_count": len(files),
                            "json_count": len(json_files),
                            "md_count": len(md_files)
                        })

        # 如果没有找到提取结果，检查全局的 markdown_extraction 目录
        if not folders_info:
            markdown_extraction_dir = Path("documents") / "extracted" / "markdown_extraction"
            if markdown_extraction_dir.exists():
                for folder_path in markdown_extraction_dir.iterdir():
                    if folder_path.is_dir():
                        files = list(folder_path.glob("*"))

                        if files:
                            json_files = [f for f in files if f.suffix.lower() == '.json']
                            md_files = [f for f in files if f.suffix.lower() == '.md']

                            stat = folder_path.stat()
                            update_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime))

                            folder_name = folder_path.name
                            area_code = folder_name.split('_')[0] if '_' in folder_name else folder_name

                            folders_info.append({
                                "area_code": area_code,
                                "folder_name": folder_name,
                                "origin_folder_name": folder_name,
                                "status": "completed",
                                "update_time": update_time,
                                "file_type": "markdown_extraction",
                                "file_count": len(files),
                                "json_count": len(json_files),
                                "md_count": len(md_files)
                            })

        return jsonify({
            "status": "success",
            "folders": folders_info
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500