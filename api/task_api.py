from flask import Blueprint
import os, json
import uuid
from flask import request, jsonify, send_from_directory
from pathlib import Path
from datetime import datetime
import re
from utils import task_folder_utils,file_utils
from utils.status_tracker import StatusTracker             # 1224修改[总日志追踪]
import shutil
from utils.auth_util import AuthUtils
from utils.json_manager import DownloadJsonFileManager,ParseJsonFileManager,TaskJsonFileManager
from utils.task_verify import task_permission_required

# 导入Excel相关模块
try:
    from openpyxl import Workbook
    from openpyxl.utils import get_column_letter
    from openpyxl.styles import Font, Alignment, PatternFill
    OPENPYXL_AVAILABLE = True
except ImportError:
    OPENPYXL_AVAILABLE = False
    print("警告: openpyxl 未安装，Excel功能将不可用")

task_bp = Blueprint("task", __name__)

parse_json_manager = ParseJsonFileManager()
download_json_manager = DownloadJsonFileManager()
task_json_manager = TaskJsonFileManager()

# 0317修改：新增省份白名单，防止 tasks.json 中写入非标准省份名称
PROVINCE_OPTIONS = {
    "北京市", "天津市", "上海市", "重庆市",
    "河北省", "山西省", "辽宁省", "吉林省", "黑龙江省",
    "江苏省", "浙江省", "安徽省", "福建省", "江西省", "山东省",
    "河南省", "湖北省", "湖南省", "广东省", "海南省",
    "四川省", "贵州省", "云南省", "陕西省", "甘肃省", "青海省",
    "台湾省", "内蒙古自治区", "广西壮族自治区", "西藏自治区",
    "宁夏回族自治区", "新疆维吾尔自治区", "香港特别行政区", "澳门特别行政区",
}

# 计算项目根目录，确保路径与工作目录无关
BASE_DIR = Path(__file__).resolve().parent.parent
TASKS_DIR = BASE_DIR / "data" / "tasks"
DELETE_DIR = TASKS_DIR / "delete"

# 确保任务目录存在
os.makedirs(TASKS_DIR, exist_ok=True)
TASKS_FILE = TASKS_DIR / "tasks.json"
# 初始化任务文件（如果不存在）
if not TASKS_FILE.exists():
    TASKS_FILE.write_text("[]", encoding="utf-8")

# 确保delete目录存在
os.makedirs(DELETE_DIR, exist_ok=True)
DELETED_TASKS_FILE = DELETE_DIR / "deleted.json"
# 初始化已删除任务文件（如果不存在）
if not DELETED_TASKS_FILE.exists():
    DELETED_TASKS_FILE.write_text("[]", encoding="utf-8")

# 获取所有任务信息
@task_bp.get("/info")
@AuthUtils.token_required
def get_tasks():
    """获取所有任务"""
    try:
        # 在认证装饰器中有request.current_user = payload，这里直接用
        user_id = request.current_user.get("user_id",0)
        user_departments = request.current_user.get("group_name","unknown")
        print(user_departments)
        print("gettaskinfo",user_id)
        if not TASKS_FILE.exists():
            print("任务文件不存在，创建空文件")
            TASKS_FILE.write_text("[]", encoding="utf-8")
        with task_json_manager.file_lock:
            tasks = task_json_manager.load_json_without_lock(TASKS_FILE)
        # 确保任务数据格式正确
        formatted_tasks = []
        for task in tasks:
            task_group = str(task.get("group_name","unknown") or "unknown").strip()
            if task.get("share", False) or task.get("createdBy", 0) == user_id or task_group == user_departments:
                formatted_task = {
                    "id": task.get("id", str(uuid.uuid4())),
                    "share": task.get("share", False),
                    "group_name": task.get("group_name", "unknown"),
                    "name": task.get("name", "未命名任务"),
                    "province": task.get("province", ""),  # 0317修改：任务列表接口返回省份
                    "sceneId": task.get("sceneId", "default"),  # 添加场景ID
                    "sceneName": task.get("sceneName", ""),  # 添加场景名称
                    "description": task.get("description", ""),
                    "extractionRules": task.get("extractionRules", []),
                    "judgmentRules": task.get("judgmentRules", []),
                    "createdAt": task.get("createdAt", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                }
                formatted_tasks.append(formatted_task)
        return jsonify({
            "success": True,
            "tasks": formatted_tasks
        })
    except json.JSONDecodeError as e:
        print(f"JSON解析错误: {e}")
        # 如果JSON格式错误，创建新文件
        TASKS_FILE.write_text("[]", encoding="utf-8")
        return jsonify({
            "success": True,
            "tasks": []
        })
    except Exception as e:
        print(f"获取任务失败: {e}")
        import traceback
        print(f"详细错误: {traceback.format_exc()}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

# 根据任务id获取任务信息
@task_bp.get("/get_task/<task_id>")
@AuthUtils.token_required
@task_permission_required()
def get_task(task_id):
    """获取特定任务"""
    try:
        with task_json_manager.file_lock:
            tasks = task_json_manager.load_json_without_lock(TASKS_FILE)
        task = next((t for t in tasks if t.get("id") == task_id), None)
        if task:
            return jsonify({"task": task})
        else:
            return jsonify({"error": "任务不存在"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@task_bp.get("/deleted")
@AuthUtils.token_required
def get_deleted_tasks():
    """获取已删除任务列表，按场景过滤"""
    try:
        scene_id = request.args.get("sceneId", "").strip()
        if not DELETED_TASKS_FILE.exists():
            return jsonify({"success": True, "tasks": []})

        with open(DELETED_TASKS_FILE, "r", encoding="utf-8") as f:
            deleted_tasks = json.load(f)

        result_tasks = []
        for item in deleted_tasks:
            if scene_id and str(item.get("sceneId", "")).strip() != scene_id:
                continue

            task_id = item.get("id")
            task_scene = item.get("sceneId", "default")
            delete_folder = DELETE_DIR / f"{task_scene}_{task_id}"
            download_url = None
            if delete_folder.exists():
                xlsx_files = list(delete_folder.glob("*.xlsx"))
                if xlsx_files:
                    download_url = f"/api/task/deleted-download/{task_scene}_{task_id}/{xlsx_files[0].name}"

            result_tasks.append({
                "id": task_id,
                "name": item.get("name", "未命名任务"),
                "sceneId": task_scene,
                "deleted_at": item.get("deleted_at"),
                "downloadUrl": download_url,
                "share": False,
                "group_name": "",
                "description": "",
                "extractionRules": [],
                "judgmentRules": [],
                "createdAt": item.get("deleted_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                "enabled": False,
                "taskType": "parcel",
            })

        return jsonify({"success": True, "tasks": result_tasks})
    except Exception as e:
        print(f"获取已删除任务失败: {e}")
        return jsonify({"success": False, "tasks": [], "msg": str(e)}), 500

@task_bp.get("/deleted-download/<scene_task>/<path:filename>")
@AuthUtils.token_required
def download_deleted_result(scene_task, filename):
    """下载已删除任务生成的结果文件"""
    try:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", scene_task):
            return jsonify({"error": "invalid scene task"}), 400
        if ".." in filename or "/" in filename or "\\" in filename:
            return jsonify({"error": "invalid filename"}), 400

        delete_folder = DELETE_DIR / scene_task
        if not delete_folder.exists():
            return jsonify({"error": "file not found"}), 404

        return send_from_directory(str(delete_folder), filename, as_attachment=True, download_name=filename)
    except Exception as e:
        print(f"下载已删除任务结果失败: {e}")
        return jsonify({"error": str(e)}), 500

# 添加任务
@task_bp.post("/add_task")
@AuthUtils.token_required
def create_task():
    """创建新任务"""
    try:
        json_data = request.get_json()

        data = json_data.get("task")
        # 验证必要字段
        if not data.get("name"):
            return jsonify({"error": "任务名称不能为空"}), 400

        province = str(data.get("province", "")).strip()  # 0317修改：创建任务时读取所属省份
        if not province:
            return jsonify({"error": "所属省份不能为空"}), 400
        if province not in PROVINCE_OPTIONS:
            return jsonify({"error": "所属省份不合法"}), 400

        # 读取现有任务
        with task_json_manager.file_lock:
            tasks = task_json_manager.load_json_without_lock(TASKS_FILE)
            # 创建新任务
            new_task = {
                "id": data.get("id", str(uuid.uuid4())),
                "share": data.get("share", True),
                "group_name": str(data.get("group_name") or request.current_user.get("group_name", "unknown")).strip(),
                "createdBy": data.get("createdBy", request.current_user.get("user_id", 0)),
                "name": data.get("name"),
                "province": province,  # 0317修改：保存省份到 tasks.json
                "sceneId": data.get("sceneId", "default"),  # 添加场景ID
                "sceneName": data.get("sceneName", ""),
                "description": data.get("description", ""),
                "extractionRules": data.get("extractionRules", []),
                "judgmentRules": data.get("judgmentRules", []),
                "createdAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }

            tasks.append(new_task)
            # 初始化任务目录
            task_folder_utils.init_task_dirs(new_task["id"])
            # 保存任务
            task_json_manager.save_json_without_lock(TASKS_FILE, tasks)
        # ----------------1224修改[总日志追踪]：初始化总日志状态文件,直接写入空的 status_report.json-----------------
        try:
            status_file = Path(f"data/tasks/{new_task['id']}/status_report.json")
            status_file.parent.mkdir(parents=True, exist_ok=True)
            with open(status_file, "w", encoding="utf-8") as f:
                json.dump({}, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"警告: 初始化状态文件失败: {e}")
        # -------------------------------------------------------------------------------

        return jsonify({
            "message": "任务创建成功",
            "task": new_task
        }), 200

    except Exception as e:
        print(f"创建任务失败: {e}")
        return jsonify({"error": str(e)}), 500

# 更改任务信息
@task_bp.put("/update/<task_id>")
@AuthUtils.token_required
@task_permission_required()
def update_task(task_id):
    """更新任务"""
    try:
        data = request.get_json()
        update_task = data['task']

        with task_json_manager.file_lock:
            tasks = task_json_manager.load_json_without_lock(TASKS_FILE)
            # 查找任务
            task_index = next((i for i, t in enumerate(tasks) if t.get("id") == task_id), -1)
            if task_index == -1:
                return jsonify({"error": "任务不存在"}), 404

            province = str(update_task.get("province", tasks[task_index].get("province", ""))).strip()  # 0317修改：更新任务时同步校验省份
            if not province:
                return jsonify({"error": "所属省份不能为空"}), 400
            if province not in PROVINCE_OPTIONS:
                return jsonify({"error": "所属省份不合法"}), 400

            # 更新任务
            tasks[task_index].update({
                "name": update_task.get("name", tasks[task_index].get("name")),
                "share": update_task.get("share",tasks[task_index].get("share")),
                "province": province,  # 0317修改：更新任务时保存省份
                "sceneId": update_task.get("sceneId", tasks[task_index].get("sceneId", "default")),  # 添加场景ID
                "sceneName": update_task.get("sceneName", tasks[task_index].get("sceneName", "")),
                "description": update_task.get("description", tasks[task_index].get("description")),
                "extractionRules": update_task.get("extractionRules", tasks[task_index].get("extractionRules")),
                "judgmentRules": update_task.get("judgmentRules", tasks[task_index].get("judgmentRules")),
            })
            # 保存任务
            task_json_manager.save_json_without_lock(TASKS_FILE, tasks)
        return jsonify({
            "message": "任务更新成功",
            "task": tasks[task_index]
        })

    except Exception as e:
        print(f"更新任务失败: {e}")
        return jsonify({"error": str(e)}), 500

def _build_headers_from_task_data(task_data):
    """根据任务的提取规则和判别规则构建Excel表头配置。"""
    def _normalize_rule_name(rule):
        if not isinstance(rule, dict):
            return None
        return rule.get("name") or rule.get("fieldName")

    extraction_headers = []
    judgment_headers = []

    for rule in task_data.get("extractionRules", []) or []:
        name = _normalize_rule_name(rule)
        if name and name not in extraction_headers:
            extraction_headers.append(name)

    for rule in task_data.get("judgmentRules", []) or []:
        name = _normalize_rule_name(rule)
        if name and name not in judgment_headers:
            judgment_headers.append(name)

    base_headers = ["地块编号", "报告名称"]

    # 合并表头，保持提取规则在前，判别规则在后，避免重复
    all_headers = base_headers[:]
    for header in extraction_headers + judgment_headers:
        if header not in all_headers:
            all_headers.append(header)

    # 生成基于规则名称的提取和判别字段列表
    extraction_headers = base_headers + [h for h in extraction_headers if h not in base_headers]
    judgment_headers = base_headers + [h for h in judgment_headers if h not in base_headers]

    return {
        "all_headers": all_headers,
        "extraction_headers": extraction_headers,
        "judgment_headers": judgment_headers,
    }

def _generate_excel_for_deleted_task(task_id, deleted_task, delete_target_dir):
    """为已删除任务生成Excel报告"""
    if not OPENPYXL_AVAILABLE:
        print("openpyxl未安装，无法生成Excel报告")
        return False
        
    try:
        # 获取任务目录
        task_dirs = task_folder_utils.get_task_dirs(task_id)
        
        # 检查提取结果和判别结果
        extraction_dir = task_dirs["extraction_results"]
        discrimination_dir = task_dirs["discrimination_results"]

        if not extraction_dir.exists() and not discrimination_dir.exists():
            print("没有找到提取结果或判别结果")
            return False

        # 收集所有报告的数据 - 按报告基础名称分组
        all_reports_data = {}
        
        # 导入必要的工具函数
        from api.result_api import extract_report_base_name, extract_area_code_from_base_name
        from utils import util_result
        
        # 1. 先处理所有提取结果文件
        if extraction_dir.exists():
            extraction_files = list(extraction_dir.rglob("*_extracted.json"))
            for json_file in extraction_files:
                try:
                    with open(json_file, 'r', encoding='utf-8') as f:
                        extraction_data = json.load(f)
                    
                    # 提取报告基础名称和地块编号
                    filename = json_file.stem
                    report_base_name = extract_report_base_name(filename)
                    area_code = extract_area_code_from_base_name(report_base_name)
                    
                    extraction_results = extraction_data.get("extraction_results", extraction_data)
                    if report_base_name not in all_reports_data:
                        all_reports_data[report_base_name] = {
                            "_has_extraction": True,
                            "_has_discrimination": False,
                            "_extraction_data": extraction_results,
                            "_original_extracted_filename": filename,
                            "报告名称": report_base_name,
                            "地块编号": area_code
                        }
                    else:
                        all_reports_data[report_base_name]["_has_extraction"] = True
                        all_reports_data[report_base_name]["_extraction_data"] = extraction_results
                        all_reports_data[report_base_name]["_original_extracted_filename"] = filename
                        
                except Exception as e:
                    print(f"处理提取文件失败 {json_file}: {e}")
        
        # 2. 再处理所有判别结果文件
        if discrimination_dir.exists():
            discrimination_files = list(discrimination_dir.rglob("*_discrimination.json"))
            for json_file in discrimination_files:
                try:
                    with open(json_file, 'r', encoding='utf-8') as f:
                        discrimination_data = json.load(f)
                    
                    # 提取报告基础名称和地块编号
                    filename = json_file.stem
                    report_base_name = extract_report_base_name(filename)
                    area_code = extract_area_code_from_base_name(report_base_name)
                    
                    if report_base_name not in all_reports_data:
                        all_reports_data[report_base_name] = {
                            "_has_extraction": False,
                            "_has_discrimination": True,
                            "_discrimination_data": discrimination_data,
                            "_original_discrimination_filename": filename,
                            "报告名称": report_base_name,
                            "地块编号": area_code
                        }
                    else:
                        all_reports_data[report_base_name]["_has_discrimination"] = True
                        all_reports_data[report_base_name]["_discrimination_data"] = discrimination_data
                        all_reports_data[report_base_name]["_original_discrimination_filename"] = filename
                        
                except Exception as e:
                    print(f"处理判别文件失败 {json_file}: {e}")
        
        if not all_reports_data:
            print("没有找到可生成报告的数据")
            return False
        
        # 构建正确的表头
        headers_config = _build_headers_from_task_data(deleted_task)
        all_headers = headers_config["all_headers"]
        extraction_headers = headers_config["extraction_headers"]
        judgment_headers = headers_config["judgment_headers"]
        
        # 如果没有有效的表头，使用默认表头
        if len(all_headers) <= 2:  # 只有基础字段
            all_headers = ["地块编号", "报告名称", "提取结果", "判别结果"]
            extraction_headers = ["地块编号", "报告名称", "提取结果"]
            judgment_headers = ["地块编号", "报告名称", "判别结果"]
        
        # 生成最终数据行 - 每个报告一行
        final_data = []
        base_fields = ["地块编号", "报告名称"]
        
        for report_base_name, report_data in all_reports_data.items():
            data_row = {}
            
            # 添加基础字段
            data_row["地块编号"] = report_data.get("地块编号", "")
            data_row["报告名称"] = report_data.get("报告名称", report_base_name)
            
            # 填充提取字段
            for header in all_headers:
                if header in base_fields:
                    continue
                    
                if header in extraction_headers and report_data["_has_extraction"]:
                    value = _find_nested_value(report_data["_extraction_data"], header)
                    data_row[header] = value if value is not None else ""
                elif header not in data_row:
                    data_row[header] = ""
            
            # 填充判别字段
            for header in all_headers:
                if header in base_fields:
                    continue
                    
                if header in judgment_headers and report_data["_has_discrimination"]:
                    value = _find_nested_value(report_data["_discrimination_data"], header)
                    if value is not None:
                        # 如果已经存在值（来自提取数据），则优先使用判别数据
                        data_row[header] = value
            
            final_data.append(data_row)
        
        if not final_data:
            print("没有生成任何数据行")
            return False
        
        # 生成Excel文件
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        task_name = deleted_task.get("name", "未命名任务")
        safe_name = re.sub(r'[\\/:*?"<>|]', '_', str(task_name)).strip()
        excel_filename = f"{safe_name}_{timestamp}.xlsx"
        excel_path = delete_target_dir / excel_filename
        
        wb = Workbook()
        ws = wb.active
        ws.title = safe_name[:31]  # Excel工作表名称限制为31字符
        
        # 写入表头
        for col_idx, header in enumerate(all_headers, 1):
            cell = ws.cell(row=1, column=col_idx, value=header)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.alignment = Alignment(horizontal='center', vertical='center')
            cell.fill = PatternFill(start_color="366092", end_color="366092", fill_type="solid")
        
        # 写入数据
        for row_idx, data_row in enumerate(final_data, 2):
            for col_idx, header in enumerate(all_headers, 1):
                value = data_row.get(header, "")
                cell = ws.cell(row=row_idx, column=col_idx, value=value)
                cell.alignment = Alignment(wrap_text=True, vertical='top')
        
        # 自动调整列宽
        for col_idx, header in enumerate(all_headers, 1):
            column_width = len(str(header)) + 2
            for row_idx in range(2, len(final_data) + 2):
                cell_value = str(ws.cell(row=row_idx, column=col_idx).value)
                column_width = max(column_width, len(cell_value) + 2)
            ws.column_dimensions[get_column_letter(col_idx)].width = min(column_width, 50)
        
        wb.save(excel_path)
        return True
        
    except Exception as e:
        print(f"生成Excel报告时出错: {e}")
        import traceback
        print(f"详细错误: {traceback.format_exc()}")
        return False

def _find_nested_value(data, key):
    """在嵌套字典中查找值"""
    if isinstance(data, dict):
        if key in data:
            return data[key]
        for k, v in data.items():
            if isinstance(v, (dict, list)):
                result = _find_nested_value(v, key)
                if result is not None:
                    return result
    elif isinstance(data, list):
        for item in data:
            result = _find_nested_value(item, key)
            if result is not None:
                return result
    return None

# 删除任务
@task_bp.delete("/delete/<task_id>")
@AuthUtils.token_required
@task_permission_required()
def delete_task(task_id):
    """删除任务"""
    try:
        # 1.删除tasks.json存储的任务，并获取被删除任务的信息
        deleted_task_info = None
        deleted_task = None
        with task_json_manager.file_lock:
            tasks = task_json_manager.load_json_without_lock(TASKS_FILE)
            # 查找要删除的任务信息
            deleted_task = next((t for t in tasks if t.get("id") == task_id), None)
            if deleted_task is None:
                return jsonify({"error": "任务不存在"}), 404
            
            # 保存被删除任务的元数据
            deleted_task_info = {
                "id": deleted_task.get("id"),
                "sceneId": deleted_task.get("sceneId", "default"),
                "name": deleted_task.get("name", "未命名任务")
            }
            
            # 过滤掉要删除的任务
            filtered_tasks = [t for t in tasks if t.get("id") != task_id]
            task_json_manager.save_json_without_lock(TASKS_FILE, filtered_tasks)
        
        # 2.处理Excel文件 - 移动或生成
        scene_id = deleted_task_info.get("sceneId", "default")
        task_name = deleted_task_info.get("name", "未命名任务")
        delete_target_dir = DELETE_DIR / f"{scene_id}_{task_id}"
        os.makedirs(delete_target_dir, exist_ok=True)
        
        # 获取原任务的results目录
        task_dirs = task_folder_utils.get_task_dirs(task_id)
        original_results_dir = task_dirs["results"]
        
        # 检查原results目录中是否有xlsx文件
        xlsx_files = list(original_results_dir.glob("*.xlsx")) if original_results_dir.exists() else []
        
        excel_generated = False
        if xlsx_files:
            # 如果有xlsx文件，移动到delete目录
            for xlsx_file in xlsx_files:
                target_file = delete_target_dir / xlsx_file.name
                shutil.move(str(xlsx_file), str(target_file))
            excel_generated = True
        else:
            # 如果没有xlsx文件，尝试生成Excel报告到delete目录
            if OPENPYXL_AVAILABLE:
                excel_generated = _generate_excel_for_deleted_task(task_id, deleted_task, delete_target_dir)
            else:
                print("openpyxl未安装，跳过Excel生成")
        
        # 3.删除任务文件夹
        task_id_dir = task_folder_utils.get_task_id_folder(task_id)
        if os.path.exists(task_id_dir):
            shutil.rmtree(task_id_dir)
        
        # 4.删除download_tasks.json存储的下载信息
        download_json_path = file_utils.get_download_task_json_path()
        with download_json_manager.file_lock:
            download_tasks_dict = download_json_manager.load_json_without_lock(download_json_path)
            download_tasks_dict.pop(task_id, None)  # 不会报错
            download_json_manager.save_json_without_lock(download_json_path, download_tasks_dict)
        
        # 5.删除parse_tasks.json存储的下载信息
        parse_json_path = file_utils.get_parse_task_json_path()
        with parse_json_manager.file_lock:
            parse_tasks_dict = parse_json_manager.load_json_without_lock(parse_json_path)
            parse_tasks_dict.pop(task_id, None)
            parse_json_manager.save_json_without_lock(parse_json_path, parse_tasks_dict)
        
        # 6.将已删除任务的元数据保存到delete/deleted.json
        if deleted_task_info:
            try:
                # 读取现有的已删除任务列表
                if DELETED_TASKS_FILE.exists():
                    with open(DELETED_TASKS_FILE, 'r', encoding='utf-8') as f:
                        deleted_tasks = json.load(f)
                else:
                    deleted_tasks = []
                
                # 添加新的已删除任务
                deleted_task_info["has_excel"] = excel_generated
                deleted_tasks.append(deleted_task_info)
                
                # 保存更新后的已删除任务列表
                with open(DELETED_TASKS_FILE, 'w', encoding='utf-8') as f:
                    json.dump(deleted_tasks, f, ensure_ascii=False, indent=2)
            except Exception as e:
                print(f"警告: 保存已删除任务元数据失败: {e}")
                # 即使保存元数据失败，也不影响任务删除的主要流程
        
        return jsonify({
            "message": "任务删除成功",
            "excel_generated": excel_generated
        })

    except Exception as e:
        print(f"删除任务失败: {e}")
        return jsonify({"error": str(e)}), 500
