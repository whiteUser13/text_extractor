import os, json
import uuid
from flask import Flask, request, jsonify, send_from_directory, render_template_string, abort ,Response
from werkzeug.utils import secure_filename
from flask import jsonify
from flask_cors import CORS
from pathlib import Path
import time
from datetime import datetime
from file_processor import process_compressed_file,process_compressed_file_with_yield

from pdf信息直接提取 import extract_dpf
from mineru_request import parse_file
from 大模型处理 import ai_judge


# ====================配置项=====================

# Flask 应用配置
app = Flask(__name__)
# CORS(app)  # 跨域配置
CORS(app,supports_credentials=True)

# 文件配置
UPLOAD_FOLDER = "data/uploads"
DOWNLOAD_FOLDER = "data/downloads"
IMAGE_FOLDER = "data/images"
PDF_TO_MD_FOLDER = "data/output/pdf2md"
FILTER_PDF_FOLDER = "data/output/filter_pdf"
EXCEL_RESULTS_FOLDER = "data/output/results"
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['DOWNLOAD_FOLDER'] = DOWNLOAD_FOLDER
app.config['IMAGE_FOLDER'] = IMAGE_FOLDER
app.config['PDF_TO_MD_FOLDER'] = PDF_TO_MD_FOLDER
app.config['FILTER_PDF_FOLDER'] = FILTER_PDF_FOLDER
app.config['EXCEL_RESULTS_FOLDER'] = EXCEL_RESULTS_FOLDER

# 确保上传目录存在
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)
os.makedirs(IMAGE_FOLDER, exist_ok=True)
os.makedirs(PDF_TO_MD_FOLDER, exist_ok=True)
os.makedirs(FILTER_PDF_FOLDER, exist_ok=True)
os.makedirs(EXCEL_RESULTS_FOLDER, exist_ok=True)

# 配置允许上传的文件格式、大小
ALLOWED_EXTENSIONS = {'.pdf', '.docx', '.doc', '.jpg', '.png', '.bmp', '.zip'}
MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50 MB
app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH

# 上传文件名映射词典
MAP_UPLOAD_FILE = Path("data/uploads/upload_file_map.json")
if not MAP_UPLOAD_FILE.exists():
    MAP_UPLOAD_FILE.write_text("{}", encoding="utf-8")  # 存储为字典结构
# 下载文件名映射词典
MAP_DOWNLOAD_ZIP = Path("data/downloads/download_zip_map.json")
if not MAP_UPLOAD_FILE.exists():
    MAP_DOWNLOAD_ZIP.write_text("{}", encoding="utf-8")  # 存储为字典结构
# 筛选文件名映射词典
MAP_FILTER_PDF = Path("data/output/filter_pdf/folder_map.json")
if not MAP_FILTER_PDF.exists():
    MAP_FILTER_PDF.write_text("{}", encoding="utf-8")  # 存储为字典结构
# 提取文件名映射词典
MAP_TO_MD = Path("data/output/pdf2md/folder_map.json")
if not MAP_TO_MD.exists():
    MAP_TO_MD.write_text("{}", encoding="utf-8")  # 存储为字典结构
# 结果文件名映射词典
MAP_RESULT_EXCEL = Path("data/output/results/folder_map.json")
if not MAP_RESULT_EXCEL.exists():
    MAP_RESULT_EXCEL.write_text("{}", encoding="utf-8")  # 存储为字典结构
# ========================
# 工具函数
# ========================
def allowed_file(filename):
    """检查文件扩展名是否合法"""
    ext = os.path.splitext(filename)[1].lower()
    return ext in ALLOWED_EXTENSIONS
def safe_filename(filename):
    """生成唯一文件名（UUID + 原扩展名）"""
    ext = os.path.splitext(filename)[1].lower()
    return f"{uuid.uuid4().hex}{ext}"
def get_file_type(filename: str) -> str:
    """根据扩展名或 MIME 判断文件类型"""
    ext = Path(filename).suffix.lower()
    if ext in [".doc", ".docx"]:
        return "word"
    elif ext in [".jpg", ".jpeg", ".png", ".gif", ".bmp"]:
        return "image"
    elif ext in [".pdf"]:
        return "pdf"
    elif ext in [".zip"]:
        return "zip"
    else:
        return "other"


# ========================
# 1.文件上传下载部分
# ========================

# 单文件上传
@app.route('/upload/file', methods=['POST'])
def upload_file():
    """单文件上传"""
    if 'file' not in request.files:
        return jsonify({"error": "未选择文件"}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "未选择文件"}), 400

    if not allowed_file(file.filename):
        return jsonify({"error": f"只允许上传 {', '.join(ALLOWED_EXTENSIONS)}"}), 401

    # 生成安全文件名并保存
    original_name = file.filename
    safe_name = safe_filename(file.filename)
    save_path = os.path.join(app.config['UPLOAD_FOLDER'], safe_name)
    try:
        # 保存文件
        file.save(save_path)
        size_kb = os.path.getsize(save_path)
        file_size_kb = round(size_kb / 1024, 2)
        file_type = get_file_type(safe_name)
    except Exception as e:
        print(e)
        return jsonify({"error": "文件保存失败"}), 402

    # 追加写入日志文件
    # 读取原有映射
    with open(MAP_UPLOAD_FILE, "r", encoding="utf-8") as f:
        file_map = json.load(f)

    # 更新映射：安全文件名 → 原始文件名
    # file_map[safe_name] = original_name
    file_map[safe_name] = {
        "id":safe_name,
        "origin_name": original_name,
        "size": file_size_kb,
        "type": file_type,
        "upload_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "status":"completed"
    }

    # 写回文件
    with open(MAP_UPLOAD_FILE, "w", encoding="utf-8") as f:
        json.dump(file_map, f, ensure_ascii=False, indent=2)

    file_url = f"/uploads/{safe_name}"
    return jsonify({
        "id":safe_name,
        "origin_name": original_name,
        "size": size_kb,
        "type": file_type,
        "upload_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "status":"completed",
        "file_url": file_url
    })
# 多文件上传
@app.route('/upload-multiple', methods=['POST'])
def upload_multiple():
    """多文件上传"""
    if 'files' not in request.files:
        return jsonify({"error": "未选择文件"}), 400

    files = request.files.getlist('files')
    results = []

    for file in files:
        if file.filename == '':
            continue
        if not allowed_file(file.filename):
            results.append({
                "original_filename": file.filename,
                "status": "skipped",
                "reason": "类型不允许"
            })
            continue

        filename = safe_filename(file.filename)
        save_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(save_path)

        results.append({
            "original_filename": file.filename,
            "filename": filename,
            "url": f"/uploads/{filename}",
            "status": "ok"
        })

    return jsonify({"files": results})
# 根据地块编码下载文件

# 获取已下载的文件信息
@app.route("/files_info/downloads", methods=["GET"])
def get_downloads_file_info():
    if not MAP_DOWNLOAD_ZIP.exists():
        return jsonify({"error": "映射文件缺失"}), 404
    try:
        # 读取映射文件
        with open(MAP_DOWNLOAD_ZIP, "r", encoding="utf-8") as f:
            file_map = json.load(f)
        with open(MAP_RESULT_EXCEL, "r", encoding="utf-8") as f:
            result_map = json.load(f)
    except json.JSONDecodeError:
        return jsonify({"error": "JSON 文件格式错误"}), 500
    # 过滤出 status 不为 undownload和downloading 的项
    files_info = []
    for area_code, info in file_map.items():
        if info.get("status") not in ["undownload", "downloading"]:
            status = info.get("status")
            if result_map.get(area_code) is not None:
                status = result_map[area_code].get("status")
            files_info.append({
                "id": info.get("area_code"),
                "filename": info.get("origin_name"),
                "size": info.get("size"),
                "type": info.get("type"),
                "download_time": info.get("download_time", ""),
                "status": status
            })
    return jsonify({
        "count": len(files_info),
        "files": files_info
    })

# ========================
# 2.解压缩文件部分
# ========================
# ------------------获取全部zip文件信息-----------------
@app.route("/files_info/all_zip", methods=["GET"])
def list_zip_files():
    """
    筛选上传文件夹中所有 .zip 文件，并返回文件名列表
    """
    folder = Path(app.config['UPLOAD_FOLDER'])
    if not folder.exists():
        return jsonify({"error": "上传目录不存在"}), 404

    # 获取所有 zip 文件
    zip_files = [{
        "filename": f.name,
        "size_kb": round(f.stat().st_size / 1024, 2)
    } for f in folder.glob("*.zip") if f.is_file()]


    return jsonify({
        "count": len(zip_files),
        "zip_files": zip_files
    })
# ------------------获取未解压zip文件信息-----------------
@app.route("/files_info/zip", methods=["GET"])
def list_unextracted_zip_files():
    """
    列出未解压的 ZIP 文件及其大小信息
    逻辑：
      - 仅返回 .zip 文件
      - 若存在与文件同名的文件夹，则认为该 zip 已解压过，不返回
    """
    folder = Path(app.config['UPLOAD_FOLDER'])
    if not folder.exists():
        return jsonify({"error": "上传目录不存在"}), 404

    """返回安全文件名与原始文件名的映射"""
    # map_path = Path("data/uploads/upload_file_map.json")
    if not MAP_UPLOAD_FILE.exists():
        return jsonify({"error": "映射文件不存在"}), 404

    with open(MAP_UPLOAD_FILE, "r", encoding="utf-8") as f:
        file_map = json.load(f)

    zip_files_info = []

    for file in folder.glob("*.zip"):
        if not file.is_file():
            continue

        # 检查是否已解压：是否存在同名文件夹
        extracted_folder = folder / file.stem  # 例如 data.zip -> data/
        if extracted_folder.exists() and extracted_folder.is_dir():
            continue  # 已解压，跳过
        stat = file.stat()
        modify_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime))
        size_kb = round(stat.st_size / 1024, 2)
        file_info = file_map.get(file.name, {})
        zip_files_info.append({
            "id": file.name,
            "origin_name":file_info.get("origin_name", file.name),
            "size_kb": size_kb,
            "size_mb": round(size_kb / 1024, 2),
            "type": file_info.get("type", get_file_type(file.name)),
            "upload_time":file_info.get("upload_time", modify_time),
            "status":file_info.get("type", "uploaded"),
            "url": f"/uploads/{file.name}"
        })

    return jsonify({
        "status": "success",
        "count": len(zip_files_info),
        "zip_files": zip_files_info
    })
# ------------------获取文件信息--------------------
@app.route("/files_info/all", methods=["GET"])
def list_all_files():
    """获取上传目录所有文件的完整信息"""
    folder = Path(UPLOAD_FOLDER)
    if not folder.exists():
        return jsonify({"error": "上传目录不存在"}), 404

    with open(MAP_UPLOAD_FILE, "r", encoding="utf-8") as f:
        file_map = json.load(f)

    files_info = []
    for f in folder.iterdir():
        if f.is_file():
            stat = f.stat()
            modify_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime))

            info = file_map.get(f.name, {})

            files_info.append({
                "filename": f.name,
                "origin_name": info.get("origin_name", f.name),
                "size": info.get("size", round(stat.st_size / 1024, 2)),
                "type": info.get("type", get_file_type(f.name)),
                "upload_time": info.get("upload_time", modify_time),
                "status": info.get("status", "uploaded")
            })

    return jsonify({
        "count": len(files_info),
        "files": files_info
    })

# ------------------根据压缩文件名对文件进行解压操作，并将日志写在前端------------
# 文件解压成果同时在data/output/filter_pdf、pdf2md、results、images中生成对应名称的文件夹
@app.route("/extract-by-filename-stream", methods=["GET"])
def extract_by_filename_stream():
    filename = request.args.get("filename")
    plot_code = request.args.get("plot_code", os.path.splitext(filename)[0])

    if not filename:
        return jsonify({"status": "error", "message": "缺少 filename 参数"}), 400

    file_path = os.path.join(UPLOAD_FOLDER, filename)
    if not os.path.exists(file_path):
        return jsonify({"status": "error", "message": f"文件 {filename} 不存在"}), 404

    def generate_logs():
        try:
            yield f"data: 开始处理文件 {filename}\n\n"
            for log in process_compressed_file_with_yield(file_path, plot_code, DOWNLOAD_FOLDER):
                yield f"data: {log}\n\n"
            yield f"data: 文件 {filename} 处理完成 ✅\n\n"
        except Exception as e:
            yield f"data: 出错：{str(e)}\n\n"
        yield "data: 处理结束\n\n"

    return Response(generate_logs(), mimetype="text/event-stream")

# ------------------获取上传的文件信息------------
@app.route("/list-uploads", methods=["GET"])
def list_uploads():
    try:
        if not os.path.exists(UPLOAD_FOLDER):
            os.makedirs(UPLOAD_FOLDER)

        files = os.listdir(UPLOAD_FOLDER)
        files = [f for f in files if os.path.isfile(os.path.join(UPLOAD_FOLDER, f))]

        return jsonify({"status": "success", "files": files})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})
# ========================
# 3.内容提取
# ========================
# (1)获取downloads文件夹下已解压的文件信息，即status=decompressed
@app.route("/downloads/decompressed", methods=["GET"])
def get_decompressed_downloads():
    """
    获取 downloads 文件夹中已解压（status = decompressed）的所有文件夹信息
    """
    folder_path_base = Path(app.config['DOWNLOAD_FOLDER'])
    if not MAP_DOWNLOAD_ZIP.exists():
        return jsonify({"error": "映射文件不存在"}), 404
    try:
        # 读取映射文件
        with open(MAP_DOWNLOAD_ZIP, "r", encoding="utf-8") as f:
            download_map = json.load(f)
    except json.JSONDecodeError:
        return jsonify({"error": "JSON 文件格式错误"}), 500

    # 过滤出 status 为 decompressed 的项
    decompressed_folders = []
    for folder_name, info in download_map.items():
        if info.get("status") == "decompressed":
            folder_path = folder_path_base / folder_name
            # 保证加压文件夹存在
            if folder_path.exists() and folder_path.is_dir():
                decompressed_folders.append({
                    "area_code": info.get("area_code", folder_name),
                    "folder_name": info.get("folder_name", folder_name),
                    "download_time": info.get("download_time", ""),
                    "status": info.get("status")
                })

    return jsonify({
        "count": len(decompressed_folders),
        "folders": decompressed_folders
    })

# (2)根据文件夹名称提取里面的文件内容(先不考虑带有提取规则参数的情况),保存到filter_pdf/地块编码文件夹
@app.route("/extractor_folder", methods=["POST"])
def extractor_folder():
    try:
        data = request.get_json()
        folder_name = data.get("folder_name")
        if not folder_name:
            return jsonify({"error": "缺少参数 folder_name"}), 400

        # 拼接文件夹路径
        input_folder_base = Path(app.config['DOWNLOAD_FOLDER'])
        output_folder_base = Path(app.config['FILTER_PDF_FOLDER'])
        input_folder_path = input_folder_base / folder_name
        output_folder_path = output_folder_base / folder_name
        filter_folder_map_path = output_folder_base / "folder_map.json"

        if not input_folder_path.exists():
            return jsonify({"error": f"文件夹 {folder_name} 不存在"}), 401
            # 检查文件夹是否为空（忽略folder_map.json自身）
        files_in_folder = [f for f in input_folder_path.iterdir() if f.name != "download_zip_map.json"]
        if len(files_in_folder) == 0:
            return jsonify({"error": f"文件夹 {folder_name} 为空，无法处理"}), 401
        if not filter_folder_map_path.exists():
            return jsonify({"error": f"文件夹 {folder_name} 下缺少 folder_map.json"}), 402

        # 读取映射文件
        with open(filter_folder_map_path, "r", encoding="utf-8") as f:
            filter_folder_map = json.load(f)
        print(filter_folder_map)
        selected_folder_info = filter_folder_map.get(folder_name)
        if selected_folder_info is None:
            return jsonify({"error": f"文件夹 {folder_name} 不存在"}), 401
        if selected_folder_info["status"] == "extracting":
            return jsonify({"error": f"文件夹 {folder_name} 正在提取"}), 403

        # 修改状态为 extracting
        selected_folder_info["status"] = "extracting"
        with open(filter_folder_map_path, "w", encoding="utf-8") as f:
            json.dump(filter_folder_map, f, indent=4, ensure_ascii=False)

        # 处理过程
        temp_dir = os.path.join(input_folder_path, "temp_extracted_chapters")
        filter_pdf_names = extract_dpf(input_folder_path,output_folder_path, temp_dir)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        selected_folder_info["update_time"] = now_str
        if filter_pdf_names:
            # 处理完成后改为 extracted
            selected_folder_info["status"] = "extracted"
            with open(filter_folder_map_path, "w", encoding="utf-8") as f:
                json.dump(filter_folder_map, f, indent=4, ensure_ascii=False)

            # 修改pdf2md的映射文件状态为unconvert,表示还未转换为md格式

            print(f"{folder_name} 处理完成 ✅")

            return jsonify({
                "message": f"{folder_name} 处理成功",
                "folder_info":selected_folder_info,
                "pdf_names":filter_pdf_names
            }), 200
        else:
            # 处理完成后改为 未提取
            selected_folder_info["status"] = "unextracted"
            with open(filter_folder_map_path, "w", encoding="utf-8") as f:
                json.dump(filter_folder_map, f, indent=4, ensure_ascii=False)
            return jsonify({
                "message": f"{folder_name}提取失败",
                "folder_info":selected_folder_info
            }), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# 获取filter_pdf下已提取的文件夹信息
@app.route("/filter_pdf/extracted/folders_info", methods=["GET"])
def get_filter_pdf_extracted_folders_info():
    if not MAP_FILTER_PDF.exists():
        return jsonify({"error": "映射文件不存在"}), 404
    try:
        # 读取映射文件
        with open(MAP_FILTER_PDF, "r", encoding="utf-8") as f:
            folder_map = json.load(f)
    except json.JSONDecodeError:
        return jsonify({"error": "JSON 文件格式错误"}), 500
    folders_info = []
    for area_code, info in folder_map.items():
        if info.get("status") == "extracted":
            folders_info.append(info)
    return jsonify({
        "count": len(folders_info),
        "folders": folders_info
    })
# 获取results下已判别的文件夹信息
@app.route("/results/judged/folders_info", methods=["GET"])
def get_results_results_folders_info():
    if not MAP_RESULT_EXCEL.exists():
        return jsonify({"error": "映射文件不存在"}), 404
    try:
        # 读取映射文件
        with open(MAP_RESULT_EXCEL, "r", encoding="utf-8") as f:
            folder_map = json.load(f)
    except json.JSONDecodeError:
        return jsonify({"error": "JSON 文件格式错误"}), 500
    folders_info = []
    for area_code, info in folder_map.items():
        if info.get("status") == "judged":
            folders_info.append(info)
    return jsonify({
        "count": len(folders_info),
        "folders": folders_info
    })
# 根据地块编码获取已经提取的pdf文件名称
@app.route("/filter_pdf/extracteds", methods=["GET"])
def get_filter_pdf_by_ared_code():
    # 获取GET请求参数
    area_code = request.args.get("area_code")  #

    if not area_code:
        return jsonify({"error": "缺少必要参数 folder_name"}), 400

    excluded_exts = ['.json', '.txt', '.log']  # 要排除的后缀列表
    folder_path = Path(app.config['FILTER_PDF_FOLDER']) / area_code
    files = [
        f for f in os.listdir(folder_path)
        if os.path.isfile(os.path.join(folder_path, f))
           and not os.path.splitext(f)[1].lower() in excluded_exts
    ]
    return jsonify({
        "count": len(files),
        "files": files
    })

#动态 URL 路径参数 来实现获取outpurt下的文件夹里的文件信息
@app.route("/get_folder_info/<folder_name>/<area_code>", methods=["GET"])
def get_folder_info(folder_name, area_code):
    """
    根据 folder_name 和 area_code 获取文件夹信息
    """

    if folder_name == "filter_pdf":
        folder_path = Path(app.config['FILTER_PDF_FOLDER']) / area_code
        excluded_exts = ['.json', '.txt', '.log']  # 要排除的后缀列表
        files = [
            f for f in os.listdir(folder_path)
            if os.path.isfile(os.path.join(folder_path, f))
               and not os.path.splitext(f)[1].lower() in excluded_exts
        ]
        return jsonify({
            "count": len(files),
            "files": files
        })
    elif folder_name == "results":
        folder_path = Path(app.config['EXCEL_RESULTS_FOLDER']) / area_code
        excluded_exts = ['.json', '.txt', '.log']  # 要排除的后缀列表
        files = [
            f for f in os.listdir(folder_path)
            if os.path.isfile(os.path.join(folder_path, f))
               and not os.path.splitext(f)[1].lower() in excluded_exts
        ]
        return jsonify({
            "count": len(files),
            "files": files
        })
    else:
        return jsonify({"error": "路径参数有误"}), 400

# ========================
# 4.智能判别
# ========================
# (1)获取filter_pdf文件夹下已提取的文件名称
@app.route("/downloads/extracteds", methods=["GET"])
def get_extracted_downloads():
    """
    获取 filter_pdf 文件夹中已提取（status = extracted）的所有文件夹信息
    """
    filter_folder_path_base = Path(app.config['FILTER_PDF_FOLDER'])
    if not MAP_FILTER_PDF.exists():
        return jsonify({"error": "映射文件不存在"}), 404
    try:
        # 读取映射文件
        with open(MAP_FILTER_PDF, "r", encoding="utf-8") as f:
            download_map = json.load(f)
    except json.JSONDecodeError:
        return jsonify({"error": "JSON 文件格式错误"}), 500

    # 过滤出 status 为 extracted 的项
    extracted_folder = []
    for folder_name, info in download_map.items():
        if info.get("status") == "extracted":
            folder_path = filter_folder_path_base / folder_name
            # 保证加压文件夹存在
            if folder_path.exists() and folder_path.is_dir():
                extracted_folder.append({
                    "area_code": info.get("area_code", folder_name),
                    "folder_name": info.get("folder_name", folder_name),
                    "origin_folder_name":info.get("origin_folder_name", folder_name),
                    "update_time": info.get("update_time", ""),
                    "status": info.get("status")
                })

    return jsonify({
        "count": len(extracted_folder),
        "folders": extracted_folder
    })

# (2)智能提取，将生成的excel表格保存到results文件夹
@app.route("/ai_analysis", methods=["POST"])
def ai_analysis():
    """
    根据文件夹名称，对其中的 PDF 文件进行处理。
    处理期间修改 folder_map.json 状态为 handling，完成后改为 handled。
    """
    try:
        data = request.get_json()
        folder_name = data.get("folder_name")

        if not folder_name:
            return jsonify({"error": "缺少参数 folder_name"}), 400
        # 拼接文件夹路径
        filter_pdf_folder_base = Path(app.config['FILTER_PDF_FOLDER'])
        md_folder_base = Path(app.config['PDF_TO_MD_FOLDER'])
        results_folder_base = Path(app.config['EXCEL_RESULTS_FOLDER'])
        image_folder_base = Path(app.config['IMAGE_FOLDER'])
        image_folder_path = image_folder_base / folder_name
        filter_pdf_folder_path = filter_pdf_folder_base / folder_name
        md_folder_path = md_folder_base / folder_name
        results_folder_path = results_folder_base / folder_name
        md_folder_map_path = md_folder_base / "folder_map.json"
        pdf_folder_map_path = filter_pdf_folder_base / "folder_map.json"
        results_folder_map_path = results_folder_base / "folder_map.json"
        # 拼接文件夹路径

        # 判断md文件夹是否存在
        if not md_folder_path.exists():
            return jsonify({"error": f"文件夹 {folder_name} 不存在"}), 401
        if not results_folder_path.exists():
            return jsonify({"error": f"结果文件夹 {folder_name} 不存在"}), 401
        if not md_folder_map_path.exists():
            return jsonify({"error": f"文件夹 {folder_name} 下缺少 folder_map.json"}), 402
        # 读取pdf2md文件夹下的映射文件folder_map
        with open(md_folder_map_path, "r", encoding="utf-8") as f:
            md_folder_map = json.load(f)
        selected_md_folder_info = md_folder_map.get(folder_name)
        if selected_md_folder_info is None:
            return jsonify({"error": f"文件夹 {folder_name} 不存在"}), 401
        if selected_md_folder_info["status"] == "converting":
            return jsonify({"error": f"文件夹 {folder_name} 正在转换"}), 403
        elif selected_md_folder_info["status"] == "unconvert":
            #判断pdf文件夹是否存在或为空
            if not filter_pdf_folder_path.exists() or not filter_pdf_folder_path.is_dir():
                return jsonify({"error": f"文件夹 {folder_name} 不存在"}), 401
            # 检查是pdf文件夹否为空文件夹
            files_in_folder = [f for f in filter_pdf_folder_path.iterdir()]
            if len(files_in_folder) == 0:
                return jsonify({"error": f"文件夹 {folder_name} 为空，无法处理"}), 401

            # 判断filter_map.json的status是否未extracted
            if not pdf_folder_map_path.exists():
                return jsonify({"error": f"文件夹 {folder_name} 下缺少 folder_map.json"}), 402
            with open(pdf_folder_map_path, "r", encoding="utf-8") as f:
                pdf_folder_map = json.load(f)
            selected_pdf_folder_info = pdf_folder_map.get(folder_name)
            if selected_pdf_folder_info is None:
                return jsonify({"error": f"文件夹 {folder_name} 不存在"}), 401
            if selected_pdf_folder_info["status"] == "extracted":
                # 修改pdf2md的映射文件状态为 converting
                selected_md_folder_info['status'] = "converting"
                with open(md_folder_map_path, "w", encoding="utf-8") as f:
                    json.dump(md_folder_map, f, indent=4, ensure_ascii=False)
                # 转换操作
                print(f"开始处理文件夹：{folder_name}")
                pdf_files = [f for f in filter_pdf_folder_path.glob("*.pdf")]
                is_success = True
                for pdf_file in pdf_files:
                    print(f"处理文件：{pdf_file.name}")
                    input_file_path = filter_pdf_folder_path/pdf_file.name
                    md_file_name = pdf_file.stem + ".md"
                    output_file_path = md_folder_path/md_file_name
                    is_success = parse_file(input_file_path,output_file_path,folder_name,image_folder_path)
                # 修改更新时间
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                selected_md_folder_info["update_time"] = now_str
                if is_success == True:
                    print("转换成功")
                    # 修改pdf2md的映射文件状态为 converted
                    selected_md_folder_info['status'] = "converted"
                    with open(md_folder_map_path, "w", encoding="utf-8") as f:
                        json.dump(md_folder_map, f, indent=4, ensure_ascii=False)
                else:
                    selected_md_folder_info['status'] = "unconvert"
                    with open(md_folder_map_path, "w", encoding="utf-8") as f:
                        json.dump(md_folder_map, f, indent=4, ensure_ascii=False)
                    return jsonify({"error": f"文件转换为markdown文件失败"}), 403

            else:
                return jsonify({"error": f"文件夹 {folder_name} 还没有进行文本提取或正在提取"}), 403

        # 智能判别
        is_judge_success = ai_judge(md_folder_path,results_folder_path,image_folder_base)
        if is_judge_success == True:
            with open(results_folder_map_path, "r", encoding="utf-8") as f:
                results_folder_map = json.load(f)
            selected_results_folder_info = results_folder_map.get(folder_name)
            selected_results_folder_info['status'] = "judged"
            with open(results_folder_map_path, "w", encoding="utf-8") as f:
                json.dump(results_folder_map, f, indent=4, ensure_ascii=False)
            return jsonify({
                "message": f"{folder_name} 文件夹处理完成",
                "results_folder_info": selected_results_folder_info,
                "status": selected_results_folder_info["status"]
            }), 200
        else:
            return jsonify({"error": "处理失败"}), 500

    except Exception as e:
        return jsonify({"error": str(e)}), 500
# (3)获取results文件夹下的



# 配置静态资源文件夹
# 上传文件夹
@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    """提供静态文件访问"""
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)
# 转换md时输出图片文件夹
@app.route('/documents/images/<path:filename>')
def serve_image(filename):
    # filename 可以包含子目录，例如 doc1/img1.jpg
    return send_from_directory(app.config['IMAGE_FOLDER'], filename)
# 判别结果文件夹
@app.route('/documents/results/<path:filename>')
def serve_results(filename):
    # filename 可以包含子目录，例如 doc1/img1.jpg
    return send_from_directory(app.config['EXCEL_RESULTS_FOLDER'], filename)
# 内容提取生成的pdf文件夹
@app.route('/documents/filter_pdf/<path:filename>')
def serve_filter_pdf(filename):
    # filename 可以包含子目录，例如 doc1/img1.jpg
    return send_from_directory(app.config['FILTER_PDF_FOLDER'], filename)
if __name__ == '__main__':
    # host='0.0.0.0' 允许外部访问
    app.run(debug=True, host='0.0.0.0', port=8000)