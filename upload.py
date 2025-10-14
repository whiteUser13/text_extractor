import os, json
import uuid
from flask import Flask, request, jsonify, send_from_directory, render_template_string, abort
from werkzeug.utils import secure_filename
from flask import jsonify
from flask_cors import CORS
from pathlib import Path
import time
from datetime import datetime
# ========================
# 配置项
# ========================

# ------上传文件配置项------
UPLOAD_FOLDER = "uploads"
ALLOWED_EXTENSIONS = {'.pdf', '.docx', '.doc', '.jpg', '.png', '.bmp', '.zip'}
MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50 MB

# 文件名映射日志
MAP_UPLOAD_FILE = Path("data/uploads/upload_file_map.json")
if not MAP_UPLOAD_FILE.exists():
    MAP_UPLOAD_FILE.write_text("{}", encoding="utf-8")  # 存储为字典结构

# Flask 应用配置
app = Flask(__name__)
CORS(app)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH

# 确保上传目录存在
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


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
# 文件上传部分
# ========================
# 单文件上传
@app.route('/upload', methods=['POST'])
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


# ========================
# 压缩文件部分
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
    map_path = Path("data/uploads/upload_file_map.json")
    if not map_path.exists():
        return jsonify({"error": "映射文件不存在"}), 404

    with open(map_path, "r", encoding="utf-8") as f:
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
# ------------------根据压缩文件名对文件进行解压操作------------


# ------------------将上传文件夹uploads设置为静态资源文件夹-----------------
@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    """提供静态文件访问"""
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)





if __name__ == '__main__':
    # host='0.0.0.0' 允许外部访问
    app.run(debug=True, host='0.0.0.0', port=8000)
