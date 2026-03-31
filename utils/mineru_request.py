# -*- coding: utf-8 -*-
import os
import re
import io
import sys
import json
import logging
import base64
import shutil
import tempfile
import subprocess
from pathlib import Path
from typing import Any, Dict, Tuple, Optional

import requests
from filelock import FileLock

from utils.status_tracker import StatusTracker, Stage, Status      #1224修改[总日志追踪]：引入状态追踪模块

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
    force=True
)

"""
批处理解析脚本
- 新增：DOCX -> PDF -> MinerU（via_pdf / hybrid / local）
- 新增：SAVE_CONVERTED_PDF 开关，保存转换后的 PDF 以便人工检查
- 新增：跳过临时/隐藏文件（如 ~$.docx、.DS_Store、Thumbs.db、空文件）
- 移除：对 markitdown 的依赖，仅使用 python-docx 进行本地解析

使用：
1) 将待解析文件放入 BATCH_INPUT_DIR
2) 运行：python mineru_request.py
3) 输出：OUTPUT_ROOT/<doc_id>/ 下生成 <doc_id>.md、images/、<doc_id>_content_list.json
   （若开启 SAVE_CONVERTED_PDF，还会保存 <doc_id>.pdf 供检查）
"""

# ===================== 可按需改动的路径/配置 =====================
BATCH_INPUT_DIR = r"data\downloads"
OUTPUT_ROOT     = r"data\mineru_outputs"

# MinerU 服务地址（用于 PDF/JPG/PNG 以及 DOCX->PDF 后）
MINERU_URL = "http://10.100.3.110:9087/file_parse"

# 如果服务端要求 'file' 而不是 'files'，改 True
USE_FILE_FIELD_SINGULAR = False

# 支持后缀（DOCX 可 via_pdf/local/hybrid；其他走 MinerU）
SUPPORTED_SUFFIXES = (".pdf", ".jpg", ".jpeg", ".png", ".docx")

# DOCX 解析策略（local | via_pdf | hybrid）
DOCX_PARSE_MODE = os.getenv("DOCX_PARSE_MODE", "via_pdf").lower()

# 是否保存 DOCX→PDF 的副本到输出目录（与 .md 同级）。设置为 1/true/yes/on/y 开启。
SAVE_CONVERTED_PDF = os.getenv("SAVE_CONVERTED_PDF", "0").lower() in ("1", "true", "yes", "on", "y")
# ================================================================


# ------------------ 工具：过滤临时/隐藏文件 ------------------
def should_skip_file(path: str) -> bool:
    name = os.path.basename(path)
    # 跳过 Word 临时锁文件、隐藏文件、系统文件、空文件、目录
    if name.startswith("~$"):  # Word lock
        return True
    if name.startswith("."):   # 隐藏文件（如 .DS_Store）
        return True
    if name.lower() in {"thumbs.db", "desktop.ini"}:
        return True
    if os.path.isdir(path):
        return True
    try:
        if os.path.getsize(path) == 0:
            return True
    except OSError:
        return True
    return False


# ------------------ DOCX 本地解析（不依赖 MinerU） ------------------
# 1224修改[总日志追踪]：增加参数 task_id
def parse_docx_locally(docx_path: str, md_out: str, img_dir: str, content_list_file: Optional[str] = None, task_id: str = "default") -> bool:
    """
    直接解析 DOCX -> Markdown + images（保留加粗、斜体、表格等样式）
    - 文本：使用 python-docx 提取文本及样式（加粗、斜体等）
    - 图片：提取嵌入图片到 img_dir
    """

    # ------------------1224修改[总日志追踪]：初始化追踪器------------------
    tracker = StatusTracker(task_id)
    file_name = os.path.basename(docx_path)
    # [新增] 计算主键
    file_stem = os.path.splitext(file_name)[0]
    pk = file_stem.split('_')[0] if '_' in file_stem else file_stem

    tracker.update_file_stage(file_name, Stage.PARSE, Status.PROCESSING, primary_key=pk )
    # ------------------------------------------------------------------

    from docx import Document

    os.makedirs(img_dir, exist_ok=True)

    # 1) 抽取图片
    images = []
    try:
        doc = Document(docx_path)
        part = doc.part
        idx = 1
        for rel in part.rels.values():
            # reltype 含有 image 的关系即为嵌入图片
            if "image" in rel.reltype:
                blob = rel.target_part.blob
                # 文件名优先取原名；若无则生成占位名
                orig = os.path.basename(rel.target_part.partname) or f"image_{idx}.png"
                out_path = os.path.join(img_dir, orig)
                # 避免重名覆盖
                base, ext = os.path.splitext(orig)
                c = 1
                while os.path.exists(out_path):
                    out_path = os.path.join(img_dir, f"{base}_{c}{ext or '.png'}")
                    c += 1
                with open(out_path, "wb") as f:
                    f.write(blob)
                images.append(os.path.basename(out_path))
                idx += 1
    except Exception as e:
        logging.warning(f"[DOCX] 提取图片失败（继续，仅无图片）：{e}")

    # 2) 文本 -> Markdown（保留基本样式）
    try:
        def parse_paragraph(paragraph):
            md_text = ""
            for run in paragraph.runs:
                text = run.text or ""
                if not text:
                    continue
                if run.bold:
                    text = f"**{text}**"
                if run.italic:
                    text = f"*{text}*"
                md_text += text
            return md_text

        doc = Document(docx_path)
        md_text = ""
        for para in doc.paragraphs:
            para_text = parse_paragraph(para)
            if para_text.strip():
                md_text += para_text + "\n\n"

        # 3) 处理表格
        for table in doc.tables:
            for row in table.rows:
                md_text += "| " + " | ".join((cell.text or "").strip() for cell in row.cells) + " |\n"
            md_text += "\n"

    except Exception as e:
        logging.error(f"[DOCX] python-docx 转换失败：{e}")
        tracker.update_file_stage(file_name, Stage.PARSE, Status.FAILED, f"本地解析失败: {str(e)}", primary_key=pk)      # 1224修改[总日志追踪]：记录失败
        return False

    # 4) 在文末追加图片清单，保证 VSCode 预览可见
    if images:
        md_text += "\n\n## 附件图片\n"
        for name in images:
            md_text += f"\n![{name}](./images/{name})"

    # 5) 写入 Markdown
    with open(md_out, "w", encoding="utf-8") as f:
        f.write(md_text)
    logging.info(f"[DOCX] Markdown 已保存：{md_out}")

    # 6) 写入 content_list 占位（可扩展）
    if content_list_file:
        content_stub = {
            "doc_type": "docx",
            "source_file": os.path.basename(docx_path),
            "images": images,
            "notes": "由本地 python-docx 生成（非 MinerU）。"
        }
        with open(content_list_file, "w", encoding="utf-8") as f:
            json.dump(content_stub, f, ensure_ascii=False, indent=2)
        logging.info(f"[DOCX] content_list 保存：{content_list_file}")

    tracker.update_file_stage(file_name, Stage.PARSE, Status.SUCCESS, primary_key=pk)     # 1224修改[总日志追踪]：记录成功
    return True


# ------------------ MinerU 客户端（用于 PDF/JPG/PNG） ------------------
def parse_file_to_md(file_path: str, start_page: int = 0, end_page: int = 99999, mineru_url: Optional[str] = None):
    """
    调用 MinerU /file_parse，返回结果 JSON（或 None）
    """
    # 说明：
    # 1) MinerU 服务端通常不支持高并发（或会排队很久）；
    # 2) Word/Office 的 DOCX->PDF 转换也不是线程安全的。
    # 因此我们提供一个“可选串行化”开关：默认开启串行化，避免并发时只有一个成功。
    # 如你确认服务端/转换器可并发，可设置 MINERU_SERIALIZE=0 关闭。
    serialize = os.getenv("MINERU_SERIALIZE", "1").lower() not in ("0", "false", "no", "off", "n")
    lock_dir = tempfile.gettempdir()
    lock_path = os.path.join(lock_dir, "mineru_request.lock")

    lock_ctx = FileLock(lock_path, timeout=3600) if serialize else None

    # 用一个小的上下文管理器，统一处理是否加锁
    class _NullCtx:
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb):
            return False

    with (lock_ctx or _NullCtx()):
        headers = {"accept": "application/json"}
        MIME_TYPES = {
            ".pdf":  "application/pdf",
            ".jpg":  "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png":  "image/png",
        }
        data = {
            "return_middle_json": False,
            "return_model_output": False,
            "return_md": True,
            "return_images": True,
            "end_page_id": end_page,
            "parse_method": "auto",
            "start_page_id": start_page,
            "lang_list": "ch",
            "output_dir": "./output",
            "server_url": "",
            "return_content_list": True,
            "backend": "pipeline",
            "table_enable": True,
            "formula_enable": True, 
        }
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")
        mime_type = MIME_TYPES.get(path.suffix.lower(), "application/octet-stream")
        url = mineru_url or MINERU_URL
        logging.info(f"MinerU URL: {url}")
        logging.info(f"解析文件: {file_path}")
        try:
            with open(file_path, "rb") as f:
                if USE_FILE_FIELD_SINGULAR:
                    files = {"file": (path.name, f, mime_type)}
                else:
                    files = {"files": (path.name, f, mime_type)}
                # 将超时从 120 提高到 300，更稳妥处理大 PDF
                # 将超时从 120 提高到 300，更稳妥处理大 PDF
                response = requests.post(url, files=files, headers=headers, data=data, timeout=600)
            logging.info(f"status={response.status_code}")
            # 增加：如果状态码不是 200，打印详细服务端返回的错误信息
            if response.status_code != 200:
                logging.error(f"MinerU 服务端错误: {response.status_code} - {response.text}")
                return None
            try:
                j = response.json()
                logging.warning(f"[DEBUG] MinerU raw json: {json.dumps(j, ensure_ascii=False)[:800]}")
                logging.debug(f"response.json keys: {list(j.keys())}")
            except Exception:
                logging.error(f"返回不是 JSON：{response.text[:1000]}")
                return None
            if response.status_code != 200:
                logging.error(f"解析失败，HTTP {response.status_code}: {response.text[:1000]}")
                return None
            result = j.get("results") or j.get("result") or j.get("data")
            if not result:
                logging.error(f"解析失败，未找到 results/result/data 字段：{j}")
                return None
            logging.info("文档解析成功")
            return result
        except requests.Timeout:
            logging.error("请求 MinerU 超时")
            return None
        except Exception as e:
            logging.error(f"解析文档失败: {str(e)}")
            return None

# 使用mineru官网申请的apikey
def parse_file_to_md_with_mineru(filename: str, task_id: str):
    """
    调用 MinerU /file_parse，返回结果 JSON（或 None）
    """
    # 说明：
    # 1) MinerU 服务端通常不支持高并发（或会排队很久）；
    # 2) Word/Office 的 DOCX->PDF 转换也不是线程安全的。
    # 因此我们提供一个“可选串行化”开关：默认开启串行化，避免并发时只有一个成功。
    # 如你确认服务端/转换器可并发，可设置 MINERU_SERIALIZE=0 关闭。
    token = "eyJ0eXBlIjoiSldUIiwiYWxnIjoiSFM1MTIifQ.eyJqdGkiOiI1ODkwMDg4NSIsInJvbCI6IlJPTEVfUkVHSVNURVIiLCJpc3MiOiJPcGVuWExhYiIsImlhdCI6MTc2NjMwMjQyNSwiY2xpZW50SWQiOiJsa3pkeDU3bnZ5MjJqa3BxOXgydyIsInBob25lIjoiMTMxMDM2MDIwNjEiLCJvcGVuSWQiOm51bGwsInV1aWQiOiIzMmUxYWU3Zi1hNGIxLTQ5YWEtOTlhZi1lZTBhYWMyOWM2MTYiLCJlbWFpbCI6IiIsImV4cCI6MTc2NzUxMjAyNX0.OojyYPD0eqtdByXyniWzAUDOqNEwM-dJTpPyDNqhPEu3-ziIclYPcC3wAlegyp-ROF9QyJbO-zuIjl-Nx1E4dA"
    url = "https://mineru.net/api/v4/extract/task"
    header = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}"
    }
    data = {
        "url": f"http://10.100.56.155:8000/task-extracted/{task_id}/{filename}",
        "model_version": "vlm"
    }

    res = requests.post(url, headers=header, json=data)
    print(res.status_code)
    print(res.json())
    print(res.json()["data"])
    if res.status_code != 200:
        logging.error(f"解析失败，HTTP {res.status_code}: {res.text[:1000]}")
        return None
    j = res.json()
    result = j.get("results") or j.get("result") or j.get("data")
    if not result:
        logging.error(f"解析失败，未找到 results/result/data 字段：{j}")
        return None
    logging.info("文档解析成功")
    return result

def _normalize_images(images_any: Any) -> Dict[str, str]:
    normalized: Dict[str, str] = {}
    if not images_any:
        return normalized
    if isinstance(images_any, dict):
        for k, v in images_any.items():
            if isinstance(v, str) and v.startswith("data:image"):
                normalized[k] = v
            elif isinstance(v, str):
                ext = Path(k).suffix.lower()
                mime = "image/png" if ext == ".png" else "image/jpeg"
                normalized[k] = f"data:{mime};base64,{v}"
        return normalized
    if isinstance(images_any, list):
        for item in images_any:
            if not isinstance(item, dict):
                continue
            name = item.get("name") or item.get("filename") or "image.jpg"
            b64  = item.get("data") or item.get("content") or item.get("base64") or ""
            mime = item.get("mime") or item.get("mimetype") or "image/jpeg"
            if isinstance(b64, str) and b64.startswith("data:image"):
                normalized[name] = b64
            elif isinstance(b64, str) and b64:
                normalized[name] = f"data:{mime};base64,{b64}"
        return normalized
    return normalized


def normalize_result(result_any: Any) -> Tuple[str, Dict[str, str], Any]:
    obj: Any = result_any
    if isinstance(obj, dict) and "md_content" not in obj and "markdown" not in obj and "md" not in obj:
        if len(obj) == 1:
            obj = next(iter(obj.values()))
    if isinstance(obj, list) and obj:
        obj = obj[0]
    if not isinstance(obj, dict):
        raise ValueError(f"无法解析结果结构：{type(obj)}")
    md_content  = obj.get("md_content") or obj.get("markdown") or obj.get("md") or ""
    images_any  = obj.get("images") or obj.get("images_base64") or {}
    content_list = obj.get("content_list") or obj.get("contentlist") or []
    images = _normalize_images(images_any)
    return md_content, images, content_list


def process_md_images_to_local(md_content: str) -> str:
    img_pattern = re.compile(r'!\[.*?\]\((.*?)\)|<\s*img\s+.*?src\s*=\s*["\'](.*?)["\'].*?>', re.IGNORECASE)
    def _replace_one(match: re.Match) -> str:
        original_link = match.group(1) or match.group(2)
        filename = Path(original_link).name
        new_link = f"./images/{filename}"
        if match.group(1) is not None:
            # 将 Markdown 图片链接替换为相对路径
            alt_text = match.group(0)[2:match.group(0).find(']')]
            return f"![{alt_text}]({new_link})"
        else:
            # 替换 HTML <img> 标签中的 src
            return match.group(0).replace(original_link, new_link)
    return img_pattern.sub(_replace_one, md_content)


def save_base64_images_locally(images_dict: Dict[str, str], output_dir: str):
    os.makedirs(output_dir, exist_ok=True)
    for image_name, dataurl in images_dict.items():
        try:
            if "," not in dataurl:
                logging.warning(f"无效 dataurl：{image_name}")
                continue
            _, b64 = dataurl.split(",", 1)
            binary = base64.b64decode(b64)
            file_path = os.path.join(output_dir, image_name)
            with open(file_path, "wb") as f:
                f.write(binary)
        except Exception as e:
            logging.error(f"保存图片失败 {image_name}: {e}")


# 1224修改[总日志追踪]：增加参数 task_id
def parse_file(file_path, output_file, image_path, content_list_file=None,
               start_page: int = 0, end_page: int = 99999, mineru_url: str = None, task_id: str = "default"):
    
    # -------------------1224修改[总日志追踪]：初始化追踪器-------------------
    tracker = StatusTracker(task_id)
    file_name = os.path.basename(file_path)
    # [新增] 计算主键
    file_stem = os.path.splitext(file_name)[0]
    pk = file_stem.split('_')[0] if '_' in file_stem else file_stem
    # --------------------------------------------------------------------

    try:
        tracker.update_file_stage(file_name, Stage.PARSE, Status.PROCESSING, primary_key=pk)   # 1224修改[总日志追踪]：记录开始解析
        result = parse_file_to_md(file_path, start_page, end_page, mineru_url)
        if result is None:
            logging.error("解析返回为空")

            tracker.update_file_stage(file_name, Stage.PARSE, Status.FAILED, "MinerU接口返回空", primary_key=pk) # 1224修改[总日志追踪]：记录解析失败

            return {"success": False, "msg":"解析返回为空"}
        md_content, images, content_list = normalize_result(result)
        if content_list_file and content_list is not None:
            if isinstance(content_list, str):
                try:
                    content_list = json.loads(content_list)
                except json.JSONDecodeError:
                    logging.warning("content_list 是字符串但不是有效 JSON，将按原样写入")
            with open(content_list_file, "w", encoding="utf-8") as f:
                json.dump(content_list, f, ensure_ascii=False, indent=2)
            logging.info(f"内容列表已保存到: {content_list_file}")
        if md_content:
            res_md_content = process_md_images_to_local(md_content)
            if images:
                save_base64_images_locally(images, image_path)
            with open(output_file, "w", encoding="utf-8") as f:
                f.write(res_md_content)
            logging.info(f"Markdown 文件已保存到: {output_file}")

            tracker.update_file_stage(file_name, Stage.PARSE, Status.SUCCESS, primary_key=pk)        # 1224修改[总日志追踪]：记录成功

            return {"success": True, "msg": "解析成功"}
        else:
            logging.warning("解析成功但 md_content 为空")
            if images:
                save_base64_images_locally(images, image_path)
                #1224修改[总日志追踪]：虽然内容为空，但接口调用是成功的，视情况记录为 SUCCESS 或 FAILED。这里建议记录为 SUCCESS，但在备注里说明为空
                tracker.update_file_stage(file_name, Stage.PARSE, Status.SUCCESS, "内容为空", primary_key=pk)
            return {"success": True, "msg": "解析成功"}
    except Exception as e:
        logging.error(f"解析文件失败: {str(e)}")
        # 1224修改[总日志追踪]
        tracker.update_file_stage(file_name, Stage.PARSE, Status.FAILED, str(e), primary_key=pk)
        return {"success": False, "msg": f"解析文件失败: {str(e)}"}


# ------------------ DOCX -> PDF -> MinerU ------------------
def convert_docx_to_pdf(docx_path: str, pdf_out: str) -> bool:
    """
    尝试将 DOCX 转为 PDF：
    1) docx2pdf（需安装 Word / Office）
    2) win32com（pywin32，直接驱动 Word 导出，仅 Windows）
    3) LibreOffice soffice（跨平台，需本机安装 LibreOffice）
    成功返回 True，失败返回 False
    """
    try:
        os.makedirs(os.path.dirname(pdf_out), exist_ok=True)
    except Exception:
        pass

    # 1) docx2pdf
    try:
        from docx2pdf import convert as d2p_convert
        d2p_convert(docx_path, pdf_out)
        if os.path.exists(pdf_out) and os.path.getsize(pdf_out) > 0:
            logging.info("[DOCX->PDF] 通过 docx2pdf 成功")
            return True
    except ImportError:
        logging.info("[DOCX->PDF] 未安装 docx2pdf，跳过该方案")
    except Exception as e:
        logging.warning(f"[DOCX->PDF] docx2pdf 失败：{e}")

    # 2) Word + pywin32（注意：COM 不是线程安全的，且异常时容易遗留 WINWORD 进程）
    #    因此：
    #    - 这里强制使用文件锁串行化 DOCX->PDF 转换
    #    - 并用 try/finally 确保 doc/word 一定 Close/Quit
    try:
        convert_lock = FileLock(os.path.join(tempfile.gettempdir(), "docx_convert.lock"), timeout=3600)
        with convert_lock:
            import win32com.client  # type: ignore

            # 某些环境需要显式初始化/反初始化 COM（尤其在多线程里）
            try:
                import pythoncom  # type: ignore
                pythoncom.CoInitialize()
            except Exception:
                pythoncom = None  # type: ignore

            word = None
            doc = None
            try:
                word = win32com.client.DispatchEx("Word.Application")
                word.Visible = False
                word.DisplayAlerts = 0
                doc = word.Documents.Open(docx_path)
                # FileFormat = 17 -> PDF
                doc.SaveAs(pdf_out, FileFormat=17)
            finally:
                try:
                    if doc is not None:
                        doc.Close(False)
                except Exception:
                    pass
                try:
                    if word is not None:
                        word.Quit()
                except Exception:
                    pass
                try:
                    if pythoncom is not None:
                        pythoncom.CoUninitialize()
                except Exception:
                    pass

        if os.path.exists(pdf_out) and os.path.getsize(pdf_out) > 0:
            logging.info("[DOCX->PDF] 通过 Word COM 成功")
            return True
    except ImportError:
        logging.info("[DOCX->PDF] 未安装 pywin32，跳过 Word COM 方案")
    except Exception as e:
        logging.warning(f"[DOCX->PDF] Word COM 失败：{e}")

    # 3) LibreOffice
    try:
        soffice = shutil.which("soffice") or r"C:\Program Files\LibreOffice\program\soffice.exe"
        if soffice and os.path.exists(soffice):
            outdir = os.path.dirname(pdf_out) or os.path.abspath(".")
            cmd = [soffice, "--headless", "--convert-to", "pdf", "--outdir", outdir, docx_path]
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
            # LibreOffice 会生成同名 pdf 到 outdir
            produced = os.path.join(outdir, Path(docx_path).stem + ".pdf")
            if os.path.exists(produced):
                if os.path.abspath(produced) != os.path.abspath(pdf_out):
                    try:
                        os.replace(produced, pdf_out)
                    except Exception:
                        shutil.copyfile(produced, pdf_out)
                        os.remove(produced)
                logging.info("[DOCX->PDF] 通过 LibreOffice 成功")
                return True
        else:
            logging.info("[DOCX->PDF] 未找到 soffice，可安装 LibreOffice 后再用此方案")
    except Exception as e:
        logging.warning(f"[DOCX->PDF] LibreOffice 失败：{e}")

    logging.error("[DOCX->PDF] 三种方案均失败")
    return False


def parse_docx_via_mineru(docx_path: str, md_out: str, img_dir: str, content_list_file: Optional[str] = None,
                          start_page: int = 0, end_page: int = 99999, mineru_url: Optional[str] = None) -> dict:
    """
    DOCX -> PDF -> MinerU /file_parse -> 本地化图片/Markdown
    成功返回 True，失败返回 False；失败会考虑回退到本地解析（在 hybrid 模式下）
    """
    tmpdir = tempfile.mkdtemp(prefix="docx2pdf_")
    pdf_out = os.path.join(tmpdir, Path(docx_path).stem + ".pdf")
    try:
        ok_pdf = convert_docx_to_pdf(docx_path, pdf_out)
        if not ok_pdf:
            return {"success": False,"msg": "docx转换为pdf失败"}

        # 如需保留转换后的 PDF，复制到与 .md 同一路径
        if SAVE_CONVERTED_PDF:
            try:
                outdir = os.path.dirname(md_out) or os.path.abspath(".")
                os.makedirs(outdir, exist_ok=True)
                dest_pdf = os.path.join(outdir, Path(docx_path).stem + ".pdf")
                if os.path.abspath(pdf_out) != os.path.abspath(dest_pdf):
                    shutil.copyfile(pdf_out, dest_pdf)
                logging.info(f"[DOCX->PDF] 已保存转换 PDF：{dest_pdf}")
            except Exception as e:
                logging.warning(f"[DOCX->PDF] 保存 PDF 副本失败：{e}")

        # 复用现有的 PDF 解析流程
        result = parse_file(
            file_path=pdf_out,
            output_file=md_out,
            image_path=img_dir,
            content_list_file=content_list_file,
            start_page=start_page,
            end_page=end_page,
            mineru_url=mineru_url
        )
        return result
    finally:
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass


# ------------------ 批处理入口 ------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

    os.makedirs(OUTPUT_ROOT, exist_ok=True)

    if not os.path.isdir(BATCH_INPUT_DIR):
        logging.error(f"输入目录不存在：{BATCH_INPUT_DIR}")
        raise SystemExit(1)

    # 递归遍历 BATCH_INPUT_DIR，包含所有子目录中的文件
    to_parse = []
    for root, dirs, files in os.walk(BATCH_INPUT_DIR):
        # 可选：跳过隐藏/临时目录
        dirs[:] = [d for d in dirs if not d.startswith(".") and not d.startswith("~$")]

        for name in files:
            p = os.path.join(root, name)
            if should_skip_file(p):
                logging.info(f"跳过临时/隐藏文件：{p}")
                continue
            ext = os.path.splitext(name)[1].lower()
            if os.path.isfile(p) and ext in SUPPORTED_SUFFIXES:
                to_parse.append(p)

    if not to_parse:
        logging.warning("没有找到可解析的文件。请将 PDF/DOCX/JPG/PNG 放入输入目录或其子目录后重试。")
        raise SystemExit(0)

    logging.info(f"发现 {len(to_parse)} 个文件，将开始解析...")
    logging.info(f"DOCX_PARSE_MODE = {DOCX_PARSE_MODE}")
    logging.info(f"SAVE_CONVERTED_PDF = {SAVE_CONVERTED_PDF}")

    for fp in to_parse:
        ext = Path(fp).suffix.lower()
        doc_id = Path(fp).stem
        out_dir = os.path.join(OUTPUT_ROOT, doc_id)
        img_dir = os.path.join(out_dir, "images")
        os.makedirs(img_dir, exist_ok=True)
        md_file = os.path.join(out_dir, f"{doc_id}.md")
        content_list_file = os.path.join(out_dir, f"{doc_id}_content_list.json")

        logging.info(f"开始解析：{fp}")

        if ext == ".docx":
            if DOCX_PARSE_MODE == "via_pdf":
                logging.info("检测到 DOCX，先转换为 PDF，再通过 MinerU 解析...")
                ok = parse_docx_via_mineru(
                    docx_path=fp,
                    md_out=md_file,
                    img_dir=img_dir,
                    content_list_file=content_list_file,
                    start_page=0,
                    end_page=99999,
                    mineru_url=MINERU_URL
                )
                if ok:
                    logging.info(f"✅ DOCX(via_pdf) 完成：{fp} -> {md_file}")
                else:
                    logging.error(f"❌ DOCX(via_pdf) 解析失败：{fp}")
                continue

            elif DOCX_PARSE_MODE == "hybrid":
                logging.info("DOCX hybrid 模式：优先 via_pdf，失败则回退到本地解析...")
                ok = parse_docx_via_mineru(
                    docx_path=fp,
                    md_out=md_file,
                    img_dir=img_dir,
                    content_list_file=content_list_file,
                    start_page=0,
                    end_page=99999,
                    mineru_url=MINERU_URL
                )
                if not ok:
                    logging.warning("via_pdf 失败，回退到本地解析（python-docx）")
                    ok = parse_docx_locally(fp, md_out=md_file, img_dir=img_dir, content_list_file=content_list_file)

                if ok:
                    logging.info(f"✅ DOCX(hybrid) 完成：{fp} -> {md_file}")
                else:
                    logging.error(f"❌ DOCX(hybrid) 解析失败：{fp}")
                continue

            else:
                logging.info("检测到 DOCX，使用本地解析（python-docx）...")
                ok = parse_docx_locally(fp, md_out=md_file, img_dir=img_dir, content_list_file=content_list_file)
                if ok:
                    logging.info(f"✅ DOCX(local) 完成：{fp} -> {md_file}")
                else:
                    logging.error(f"❌ DOCX(local) 解析失败：{fp}")
                continue

        # 其他格式（PDF/JPG/PNG）走 MinerU
        ok = parse_file(
            file_path=fp,
            output_file=md_file,
            image_path=img_dir,
            content_list_file=content_list_file,
            start_page=0,
            end_page=99999,
            mineru_url=MINERU_URL
        )

        if ok is True:
            logging.info(f" 完成：{fp} -> {md_file}")
        elif ok is None:
            logging.error(f" 失败（返回为空）：{fp}")
        else:
            logging.error(f" 失败（异常）：{fp}；错误信息：{ok}")

    logging.info("全部任务结束。")

