from flask import Blueprint
import os, json
from flask import Flask, request, send_from_directory
from flask import jsonify
from pathlib import Path
from datetime import datetime
import time
import re
from utils import task_folder_utils,util_result
from utils.status_tracker import StatusTracker, Stage, Status        # 1224修改[总日志追踪]        
from utils.auth_util import AuthUtils
from utils.task_verify import task_permission_required
try:
    from openpyxl import Workbook
    from openpyxl.utils import get_column_letter
    from openpyxl.styles import Font, Alignment, PatternFill
    OPENPYXL_AVAILABLE = True
except ImportError:
    OPENPYXL_AVAILABLE = False
    print("警告: openpyxl 未安装，Excel功能将不可用")

result_bp = Blueprint("result", __name__)

# 新增：从文件名提取报告基础名称
def extract_report_base_name(filename):
    """
    从文件名提取报告基础名称
    """
    # 去除扩展名
    name_without_ext = Path(filename).stem
    
    # 只移除处理过程的后缀，保留字段信息
    patterns_to_remove = [
        r'_extracted$',
        r'_discrimination$',
        r'_validation$',
        r'_结果$',
        r'_分析$',
        r'_提取$',
        r'_判别$',
        r'_使用判别规则_.*$',  # 移除"使用判别规则"及其后面的所有内容
        r'_使用提取规则_.*$',  # 移除"使用提取规则"及其后面的所有内容
    ]
    
    cleaned_name = name_without_ext
    for pattern in patterns_to_remove:
        cleaned_name = re.sub(pattern, '', cleaned_name)
    
    return cleaned_name.rstrip('_')

# 新增：从报告基础名称提取地块编号
def extract_area_code_from_base_name(base_name):
    """
    从报告基础名称提取地块编号
    """
    # 按第一个下划线分割
    if '_' in base_name:
        return base_name.split('_')[0]
    else:
        return base_name


# ========================
# 12.11新增：获取任务相关的结果文件夹
# ========================
@result_bp.get("/task/<task_id>/result_folders")
@AuthUtils.token_required
@task_permission_required()
def get_task_result_folders(task_id):
    """
    获取指定任务的结果文件夹（extraction_results 和 discrimination_results）
    """
    try:
        if not task_id:
            return jsonify({"error": "缺少任务ID"}), 400

        task_dirs = task_folder_utils.get_task_dirs(task_id)
        result_folders = []

        # 检查并获取 extraction_results 文件夹
        extraction_dir = task_dirs["extraction_results"]
        if extraction_dir.exists():
            extraction_files = list(extraction_dir.rglob("*_extracted.json"))

            area_codes = set()
            for file_path in extraction_files:
                base_name = extract_report_base_name(file_path.stem)
                area_code = extract_area_code_from_base_name(base_name)
                area_codes.add(area_code)

            for area_code in area_codes:
                result_folders.append({
                    "area_code": area_code,
                    "folder_name": area_code,
                    "status": "extracted",
                    "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "task_id": task_id,
                    "result_type": "extraction"
                })

        # 检查并获取 discrimination_results 文件夹
        discrimination_dir = task_dirs["discrimination_results"]
        if discrimination_dir.exists():
            discrimination_files = list(discrimination_dir.rglob("*_discrimination.json"))

            area_codes = set()
            for file_path in discrimination_files:
                base_name = extract_report_base_name(file_path.stem)
                area_code = extract_area_code_from_base_name(base_name)
                area_codes.add(area_code)

            for area_code in area_codes:
                existing = next((f for f in result_folders if f["area_code"] == area_code), None)
                if existing:
                    existing["result_type"] = "both"
                else:
                    result_folders.append({
                        "area_code": area_code,
                        "folder_name": area_code,
                        "status": "validated",
                        "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "task_id": task_id,
                        "result_type": "discrimination"
                    })

        return jsonify({
            "task_id": task_id,
            "folders": result_folders,
            "count": len(result_folders)
        })

    except Exception as e:
        print(f"获取任务结果文件夹失败: {e}")
        return jsonify({"error": str(e)}), 500


# ========================
# 核心：批量生成Excel报告（一个报告一行）
# ========================
@result_bp.post("/task/<task_id>/generate_batch_excel")
@AuthUtils.token_required
@task_permission_required()
def generate_batch_excel(task_id):
    """
    批量生成Excel报告 - 一个Excel文件包含所有报告的数据
    同一报告的提取和判别结果合并为一行
    地块编号从文件名中提取，报告名称使用基础文件名
    """
    try:
        data = request.get_json()
        headers = data.get("headers", [])
        # 1229修改：获取前端传来的 task_name
        task_name = data.get("task_name")
        report_name = data.get("report_name", f"任务_{task_id}_报告")
        extraction_headers = data.get("extraction_headers", [])
        judgment_headers = data.get("judgment_headers", [])
        
        # print(f"=== 开始批量生成Excel报告 ===")
        # print(f"任务ID: {task_id}")
        # print(f"报告名称: {report_name}")
        # print(f"表头数量: {len(headers)}")
        # print(f"提取字段数: {len(extraction_headers)}")
        # print(f"判别字段数: {len(judgment_headers)}")

        # -----------------------------1224修改[总日志追踪]：初始化状态追踪器---------
        tracker = StatusTracker(task_id)    
        # [新增] 计算主键 1229修改：注释掉，不记录总任务的状态
        # 逻辑：从报告名称中提取地块编码（如果有下划线取前面，否则取全部）
        # pk = report_name.split('_')[0] if '_' in report_name else report_name
        # 记录开始生成
        # tracker.update_file_stage(report_name, Stage.GENERATE, Status.PROCESSING, primary_key=pk)
        # -----------------------------------------------------------------------
        if not headers:
            # tracker.update_file_stage(report_name, Stage.GENERATE, Status.FAILED, "缺少表头配置", primary_key=pk)   # 1224修改[总日志追踪]：记录失败状态
            print("缺少表头配置")
            return jsonify({"error": "缺少表头配置"}), 400

        # 获取任务目录
        task_dirs = task_folder_utils.get_task_dirs(task_id)

        # 检查提取结果和判别结果
        extraction_dir = task_dirs["extraction_results"]
        discrimination_dir = task_dirs["discrimination_results"]

        if not extraction_dir.exists() and not discrimination_dir.exists():
            # 1224修改[总日志追踪]：记录失败状态
            # tracker.update_file_stage(report_name, Stage.GENERATE, Status.FAILED, "没有找到提取结果或判别结果", primary_key=pk)
            print("没有找到提取结果或判别结果")
            return jsonify({"error": "没有找到提取结果或判别结果"}), 404

        # 创建结果目录
        results_dir = task_dirs["results"]
        os.makedirs(results_dir, exist_ok=True)

        # 收集所有报告的数据 - 按报告基础名称分组
        all_reports_data = {}
        
        # print(f"\n=== 开始收集提取结果数据 ===")
        # 1. 先处理所有提取结果文件
        if extraction_dir.exists():
            extraction_files = list(extraction_dir.rglob("*_extracted.json"))
            # print(f"找到 {len(extraction_files)} 个提取结果文件")
            
            for json_file in extraction_files:
                try:
                    # 提取报告基础名称
                    filename = json_file.stem
                    report_base_name = extract_report_base_name(filename)
                    area_code = extract_area_code_from_base_name(report_base_name)
                    
                    # print(f"处理提取文件: {filename}")
                    # print(f"  报告基础名称: {report_base_name}")
                    # print(f"  地块编号: {area_code}")

                    with open(json_file, 'r', encoding='utf-8') as f:
                        extraction_data = json.load(f)
                    
                    # 使用报告基础名称作为键
                    if report_base_name not in all_reports_data:
                        all_reports_data[report_base_name] = {
                            "报告名称": report_base_name,
                            "地块编号": area_code,
                            "_extraction_data": extraction_data["extraction_results"],
                            "_discrimination_data": None,
                            "_has_extraction": True,
                            "_has_discrimination": False,
                            "_original_extracted_filename": filename  # 保存提取文件名
                        }
                        print(f"  新增报告: {report_base_name}")
                    else:
                        # 更新提取数据
                        all_reports_data[report_base_name]["_extraction_data"] = extraction_data["extraction_results"]
                        all_reports_data[report_base_name]["_has_extraction"] = True
                        print(f"  更新报告的提取数据: {report_base_name}")

                except Exception as e:
                    print(f"处理提取文件失败 {json_file}: {e}")
                    # ----------------------------------1229[新增]：记录单文件读取失败 ---------------------------------------
                    try:
                        fname = extract_report_base_name(json_file.stem)
                        # 计算主键
                        pk = extract_area_code_from_base_name(fname)
                        tracker.update_file_stage(fname, Stage.GENERATE, Status.FAILED, f"读取失败: {str(e)}", primary_key=pk)
                    except:
                        pass
                    # -------------------------------------------------------------------------------------------------------
        else:
            print("提取结果目录不存在")

        # print(f"\n=== 开始收集判别结果数据 ===")
        # 2. 再处理所有判别结果文件
        if discrimination_dir.exists():
            discrimination_files = list(discrimination_dir.rglob("*_discrimination.json"))
            # print(f"找到 {len(discrimination_files)} 个判别结果文件")
            
            for json_file in discrimination_files:
                try:
                    # 提取报告基础名称
                    filename = json_file.stem
                    report_base_name = extract_report_base_name(filename)
                    area_code = extract_area_code_from_base_name(report_base_name)
                    
                    # print(f"处理判别文件: {filename}")
                    # print(f"  报告基础名称: {report_base_name}")
                    # print(f"  地块编号: {area_code}")
                    
                    with open(json_file, 'r', encoding='utf-8') as f:
                        discrimination_data = json.load(f)
                    
                    # 使用报告基础名称作为键
                    if report_base_name not in all_reports_data:
                        all_reports_data[report_base_name] = {
                            "报告名称": report_base_name,
                            "地块编号": area_code,
                            "_extraction_data": None,
                            "_discrimination_data": discrimination_data,
                            "_has_extraction": False,
                            "_has_discrimination": True,
                            "_original_discrimination_filename": filename  # 保存判别文件名
                        }
                        print(f"  新增报告（只有判别）: {report_base_name}")
                    else:
                        # 更新判别数据
                        all_reports_data[report_base_name]["_discrimination_data"] = discrimination_data
                        all_reports_data[report_base_name]["_has_discrimination"] = True
                        if "_original_discrimination_filename" not in all_reports_data[report_base_name]:
                            all_reports_data[report_base_name]["_original_discrimination_filename"] = filename
                        print(f"  更新报告的判别数据: {report_base_name}")

                except Exception as e:
                    print(f"处理判别文件失败 {json_file}: {e}")
                    # --------------------------------1229[新增]：记录单文件读取失败 --------------------------------------------
                    try:
                        fname = extract_report_base_name(json_file.stem)
                        pk = extract_area_code_from_base_name(fname)
                        tracker.update_file_stage(fname, Stage.GENERATE, Status.FAILED, f"读取失败: {str(e)}", primary_key=pk)
                    except:
                        pass
                    # --------------------------------------------------------------------------------------------------------
        else:
            print("判别结果目录不存在")

        if not all_reports_data:
            # 1224修改[总日志追踪]：记录失败状态,如果所有报告数据为空，则返回错误
            # tracker.update_file_stage(report_name, Stage.GENERATE, Status.FAILED, "没有找到可生成报告的数据", primary_key=pk)
            return jsonify({"error": "没有找到可生成报告的数据"}), 404

        # print(f"\n=== 数据收集完成 ===")
        # print(f"总共找到 {len(all_reports_data)} 个独立报告")
        # --------------------1229[新增] 批量更新状态为处理中 (针对具体文件)----------------
        for report_base_name in all_reports_data.keys():
            # 计算主键
            pk = extract_area_code_from_base_name(report_base_name)
            tracker.update_file_stage(report_base_name, Stage.GENERATE, Status.PROCESSING, primary_key=pk)
        # ------------------------------------------------------------------------------
        # print("报告列表:")
        # for report_name, data in all_reports_data.items():
        #     print(f"  {report_name}: 地块={data['地块编号']}, 提取={data['_has_extraction']}, 判别={data['_has_discrimination']}")

        # 生成最终数据行 - 每个报告一行
        final_data = []
        base_fields = ["报告名称", "地块编号"]
        
        # print(f"\n=== 开始生成数据行 ===")
        for report_base_name, report_data in all_reports_data.items():
            # print(f"\n处理报告: {report_base_name}")
            # print(f"  地块编号: {report_data['地块编号']}")
            # print(f"  有提取数据: {report_data['_has_extraction']}")
            # print(f"  有判别数据: {report_data['_has_discrimination']}")
            
            data_row = {}
            
            # 添加基础字段
            data_row["报告名称"] = report_data.get("报告名称", report_base_name)
            data_row["地块编号"] = report_data.get("地块编号", "")
            
            # 保存用于图片查找的文件名（优先使用提取文件名，因为提取文件更可能有图片）
            if "_original_extracted_filename" in report_data:
                data_row["_image_lookup_filename"] = report_data["_original_extracted_filename"]
                # print(f"  用于图片查找的文件名（提取）: {report_data['_original_extracted_filename']}")
            elif "_original_discrimination_filename" in report_data:
                data_row["_image_lookup_filename"] = report_data["_original_discrimination_filename"]
                # print(f"  用于图片查找的文件名（判别）: {report_data['_original_discrimination_filename']}")
            else:
                data_row["_image_lookup_filename"] = report_base_name
                # print(f"  用于图片查找的文件名（基础）: {report_base_name}")
            
            # 填充提取字段
            extraction_count = 0
            for header in headers:

                if header in base_fields:
                    continue
                    
                if header in extraction_headers and report_data["_has_extraction"]:
                    value = util_result.find_nested_value(report_data["_extraction_data"], header)
                    if value:
                        data_row[header] = value
                        extraction_count += 1
                        # print(f"  提取字段 '{header}': 找到值")
                    else:
                        data_row[header] = ""
                elif header not in data_row:
                    data_row[header] = ""
            
            # 填充判别字段
            judgment_count = 0
            for header in headers:
                if header in base_fields:
                    continue
                    
                if header in judgment_headers and report_data["_has_discrimination"]:
                    value = util_result.find_nested_value(report_data["_discrimination_data"], header)
                    if value:
                        # 如果已经存在值（来自提取数据），则优先使用判别数据
                        data_row[header] = value
                        judgment_count += 1
                        # print(f"  判别字段 '{header}': 找到值")
            
            final_data.append(data_row)
            # print(f"  完成报告数据，提取字段找到 {extraction_count} 个，判别字段找到 {judgment_count} 个")

        # print(f"\n=== 数据行生成完成 ===")
        # print(f"总数据行数: {len(final_data)} 行")
        # if final_data:
        #     print("前3行数据预览:")
        #     for i, row in enumerate(final_data[:3]):
        #         print(f"  第{i+1}行: 报告='{row.get('报告名称', '')}', 地块='{row.get('地块编号', '')}'")

        # 生成Excel文件名
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        # -----------------------1229修改：将生成的excel以任务名或者任务id命名---------------------
        # excel_filename = f"{report_name}_{timestamp}.xlsx"
        if task_name:
            # 如果有任务名：清洗非法字符 (如 \ / : * ? " < > |)，替换为下划线
            safe_name = re.sub(r'[\\/:*?"<>|]', '_', str(task_name)).strip()
            excel_filename = f"{safe_name}_{timestamp}.xlsx"
            sheet_title = safe_name
        else:
            # 如果没有任务名：使用任务ID
            excel_filename = f"任务_{task_id}_{timestamp}.xlsx"
            sheet_title = f"任务_{task_id}"
        # --------------------------------------------------------------------------------------
        excel_path = results_dir / excel_filename

        # 创建工作簿
        wb = Workbook()
        ws = wb.active
        ws.title = sheet_title[:31]         # 1229设置 Sheet 名称也是任务名称或者id
        # ws.title = report_name[:31]

        # 写入表头
        for col_idx, header in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col_idx, value=header)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.alignment = Alignment(horizontal='center', vertical='center')
            
            # 根据字段类型设置不同的背景色
            if header in extraction_headers:
                cell.fill = PatternFill(start_color="4F81BD", end_color="4F81BD", fill_type="solid")  # 蓝色-提取
            elif header in judgment_headers:
                cell.fill = PatternFill(start_color="9BBB59", end_color="9BBB59", fill_type="solid")  # 绿色-判别
            elif header in base_fields:
                cell.fill = PatternFill(start_color="366092", end_color="366092", fill_type="solid")  # 深蓝-基础
            else:
                cell.fill = PatternFill(start_color="366092", end_color="366092", fill_type="solid")  # 默认

        # 写入数据 - 关键修复：确保图片嵌入被调用
        row_index = 2
        for data_row in final_data:
            # print(f"\n--- 处理第 {row_index} 行 ---")
            for col_idx, header in enumerate(headers, 1):
                value = data_row.get(header, "")
                cell = ws.cell(row=row_index, column=col_idx, value=value)
                
                # 关键修复：检查是否需要嵌入图片
                if value and isinstance(value, str):
                    # 检查是否是图片路径
                    is_image_path = False
                    image_keywords = ["images/", ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"]
                    
                    # 将值转为小写进行比较
                    value_lower = value.lower()
                    for keyword in image_keywords:
                        if keyword in value_lower:
                            is_image_path = True
                            print(f"  [图片检测] 在字段 '{header}' 发现图片路径: {value[:50]}...")
                            break
                    
                    if is_image_path:
                        # 获取用于图片查找的报告名称
                        image_lookup_name = data_row.get("_image_lookup_filename", data_row.get("报告名称", ""))
                        
                        # 移除文件名中的_extracted或_discrimination后缀
                        if image_lookup_name.endswith("_extracted"):
                            image_lookup_name = image_lookup_name[:-10]
                        elif image_lookup_name.endswith("_discrimination"):
                            image_lookup_name = image_lookup_name[:-15]
                        
                        print(f"  [图片嵌入] 使用报告名称: {image_lookup_name}")
                        print(f"  [图片嵌入] 任务目录: {task_dirs['base']}")
                        
                        # 调用图片嵌入函数
                        try:
                            image_embedded = util_result._maybe_embed_image(
                                ws, 
                                row_index, 
                                col_idx, 
                                value, 
                                str(task_dirs['base']), 
                                image_lookup_name
                            )
                            
                            if image_embedded:
                                print(f"  [图片嵌入] ✓ 图片嵌入成功")
                                # 由于插入了图片，需要调整行高列宽
                            else:
                                print(f"  [图片嵌入] ✗ 图片嵌入失败")
                        except Exception as e:
                            print(f"  [图片嵌入] ! 图片嵌入异常: {e}")
            
            row_index += 1

        # 调整列宽
        # print(f"\n=== 调整列宽 ===")
        for col_idx, header in enumerate(headers, 1):
            if col_idx in util_result._COLS_WITH_IMAGES:
                continue
            max_len = len(str(header))
            for r in range(2, row_index):
                v = ws.cell(row=r, column=col_idx).value
                if v:
                    max_len = max(max_len, len(str(v)))
            width = min(max_len + 2, 50)
            col_letter = get_column_letter(col_idx)
            ws.column_dimensions[col_letter].width = max(width, 12)
            # print(f"  列 {col_letter} ({header}): 宽度={ws.column_dimensions[col_letter].width}")

        # 保存Excel文件
        print(f"\n=== 保存Excel文件 ===")
        wb.save(excel_path)
        file_size = os.path.getsize(excel_path)
        
        # print(f"文件路径: {excel_path}")
        # print(f"文件大小: {file_size} 字节 ({file_size/1024:.2f} KB)")

        # 构建下载URL
        download_url = f"/api/file/task-results/{task_id}/{excel_filename}"

        # print(f"\n=== Excel报告生成完成 ===")
        # print(f"数据行数: {len(final_data)}")
        # print(f"下载URL: {download_url}")

        # -----------------------------1224修改[总日志追踪]：记录成功,针对每份报告----------------------------
        # tracker.update_file_stage(report_name, Stage.GENERATE, Status.SUCCESS, primary_key=pk)    # 1224修改[总日志追踪]：记录成功状态
        try:
            # final_data 是生成 Excel 的数据源列表
            for row in final_data:
                # 如果是字典列表：
                candidate_name = row.get('文件名') or row.get('报告名称') or row.get('地块编码')
                
                if candidate_name:
                    # 计算该行对应的主键
                    row_pk = str(candidate_name).split('_')[0] if '_' in str(candidate_name) else str(candidate_name)
                    
                    # 更新该地块的生成状态
                    tracker.update_file_stage(str(candidate_name), Stage.GENERATE, Status.SUCCESS, primary_key=row_pk)
            
            # 同时保留总任务的记录（可选，如果不需要可以注释掉）
            # tracker.update_file_stage(report_name, Stage.GENERATE, Status.SUCCESS, primary_key=pk)
            
        except Exception as e:
            print(f"更新生成状态时出错: {e}")
            # 兜底：至少更新总任务状态
            # tracker.update_file_stage(report_name, Stage.GENERATE, Status.SUCCESS, primary_key=pk)

        return jsonify({
            "message": "Excel报告生成成功",
            "task_id": task_id,
            "excel_filename": excel_filename,
            "download_url": download_url,
            "file_path": str(excel_path),
            "data_count": len(final_data),
            "file_size": file_size,
            "extraction_fields_count": len(extraction_headers),
            "judgment_fields_count": len(judgment_headers),
            "status": "completed"
        }), 200

    except Exception as e:
        print(f"批量生成Excel报告出错: {e}")
        import traceback
        print(f"详细错误: {traceback.format_exc()}")
        # -----------------------------1224修改[总日志追踪] 记录生成失败----------------------------
        # 注意：这里可能需要重新初始化 tracker，或者确保 tracker 在 try 块外部初始化
        # 但由于 task_id 是在 try 块内部获取的，所以这里简单处理
        try:
            if 'task_id' in locals() and 'report_name' in locals():
                StatusTracker(task_id).update_file_stage(report_name, Stage.GENERATE, Status.FAILED, str(e))
        except:
            pass
        # -----------------------------------------------------------------------------
        
        return jsonify({"error": str(e)}), 500


# ========================
# 统一接口（用于前端调用）
# ========================
@result_bp.post('/generate-excel')
@AuthUtils.token_required
def generate_excel():
    """
    统一的Excel报告生成接口 - 只支持批量生成
    """
    try:
        # 检查openpyxl是否可用
        if not OPENPYXL_AVAILABLE:
            return jsonify({"error": "openpyxl未安装，无法生成Excel报告"}), 500

        data = request.get_json()
        
        # 只支持批量生成
        generation_mode = "batch"
        
        print(f"=== 开始生成Excel报告 ===")
        print(f"生成模式: {generation_mode}")

        # 从数据中提取必要信息
        task_id = data.get("task_id")
        if not task_id:
            return jsonify({"error": "缺少任务ID"}), 400

        # 直接调用批量生成函数
        return generate_batch_excel(task_id)

    except Exception as e:
        print(f"Excel报告生成出错: {e}")
        import traceback
        print(f"详细错误: {traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500
