import os
import glob  # 1.9修改
from utils import mineru_request
import shutil
from pathlib import Path
from utils import mineru_request
from utils import pdf_text_fast  # 12.25新增（纯文本提取模块）
from utils.status_tracker import StatusTracker, Stage, Status

# 1.9修改：新增安全匹配函数
def _safe_rglob_matches(directory: Path, rid: str, ext: str):
    """
    更宽松但安全的匹配：文件名只要以 rid 开头并以 ext 结尾就视为匹配，
    能覆盖 4406063010550_1.pdf 与 4406063010550_1.佛山市...pdf 等情况。
    """
    if not directory.exists():
        return []
    matches = []
    for p in directory.rglob(f"*{ext}"):
        try:
            # p.stem 是去掉最后一个后缀的文件名，使用 startswith 可匹配额外说明文本
            if p.suffix.lower() == ext.lower() and p.stem.startswith(rid):
                matches.append(p)
        except Exception:
            continue
    return matches

def select_parse_style(use:str,rid: str, task_dirs: dict, mineru_url: str, task_id: str = None):
    if use == "mineru":
        result = _run_single_parse_for_task(rid,task_dirs,mineru_url,task_id)
        return result
    elif use == "pure_txt":
        result = _run_single_puretext_parse_for_task(rid, task_dirs, mineru_url, task_id)
        return result
    else:
        return {"success":False, "msg":"invalid 'use' style filed"}

def _run_single_parse_for_task(rid: str, task_dirs: dict, mineru_url: str, task_id: str = None):
    """
    rid为去除后缀的文件名
    单个报告的 MinerU 解析 - 从 extracted/right 目录读取，支持 PDF 和 Word
    """
    # [0109修改] 提前初始化 tracker，保证任何失败路径都能写入状态
    pk = rid.split('_')[0] if '_' in rid else rid
    tracker = StatusTracker(task_id or "default") if task_id else None
    file_name = f"{rid}.unknown"

    pdf_cut_dir = task_dirs["pdf_cut"]
    extracted_right_dir = task_dirs["extracted_right"]
    downloads_dir = task_dirs["downloads"]
    parsed_dir = task_dirs["parsed"]

    candidates = []
    # 检查 pdf_cut 目录是否为空，如果为空则从 extracted_right 和 downloads 目录搜索
    for ext in (".pdf", ".docx", ".doc"):
        matches = _safe_rglob_matches(pdf_cut_dir, rid, ext)
        for p in matches:
            candidates.append((ext.lower(), p))

    if not candidates:
        for ext in (".pdf", ".docx", ".doc"):
           matches = _safe_rglob_matches(downloads_dir, rid, ext)
           for p in matches:
                candidates.append((ext.lower(), p))
           matches = _safe_rglob_matches(extracted_right_dir, rid, ext)
           for p in matches:
                candidates.append((ext.lower(), p))

    if not candidates:
        uploads_dir = task_dirs.get("uploads")
        if uploads_dir:
            for ext in (".pdf", ".docx", ".doc"):
                matches = _safe_rglob_matches(uploads_dir, rid, ext)
                for p in matches:
                    candidates.append((ext.lower(), p))
            
    def _priority(ext: str) -> int:
        return 1 if ext == ".pdf" else 2  # PDF 优先
    src_ext, src_path = sorted(candidates, key=lambda x: (_priority(x[0]), str(x[1])))[0]

    # [0109修改] 进入解析前写 PROCESSING，重解析时也会刷新
    file_name = os.path.basename(str(src_path))
    # [0109修改] 进入解析前写 PROCESSING，重解析时也会刷新
    if tracker:
        tracker.update_file_stage(file_name, Stage.PARSE, Status.PROCESSING, primary_key=pk)

    out_dir = parsed_dir / rid
    out_dir.mkdir(parents=True, exist_ok=True)
    md_output = out_dir / f"{rid}.md"
    content_list_output = out_dir / f"{rid}_content_list.json"
    images_dir = out_dir / "images"
    images_dir.mkdir(exist_ok=True)
    ok = False
    result = {} # 0109 初始化 result，防止下面未使用 mineru_request 时报错
    try:

        if src_ext == ".pdf":
            result = mineru_request.parse_file(
                file_path=str(src_path),
                output_file=str(md_output),
                image_path=str(images_dir),
                content_list_file=str(content_list_output),
                mineru_url=mineru_url,
                task_id=task_id  # 1224修改[总日志修改]： 传入 task_id
            )
        else:
            mode = mineru_request.DOCX_PARSE_MODE


            if mode == "via_pdf":
                result = mineru_request.parse_docx_via_mineru(
                    docx_path=str(src_path),
                    md_out=str(md_output),
                    img_dir=str(images_dir),
                    content_list_file=str(content_list_output),
                    start_page=0,
                    end_page=99999,
                    mineru_url=mineru_url,
                )
            elif mode == "hybrid":
                result = mineru_request.parse_docx_via_mineru(
                    docx_path=str(src_path),
                    md_out=str(md_output),
                    img_dir=str(images_dir),
                    content_list_file=str(content_list_output),
                    start_page=0,
                    end_page=99999,
                    mineru_url=mineru_url,
                )
                ok = result.get("success", False)
                if not ok:
                    ok = mineru_request.parse_docx_locally(
                        docx_path=str(src_path),
                        md_out=str(md_output),
                        img_dir=str(images_dir),
                        content_list_file=str(content_list_output),
                        task_id=task_id  # 1224修改[总日志修改]： 传入 task_id
                    )
            else:
                ok = mineru_request.parse_docx_locally(
                    docx_path=str(src_path),
                    md_out=str(md_output),
                    img_dir=str(images_dir),
                    content_list_file=str(content_list_output),
                    task_id=task_id  # 1224修改[总日志修改]： 传入 task_id
                )
    except Exception as e:
        # 0109[新增] 捕获异常并更新状态为 FAILED
        msg = f"解析过程发生异常: {str(e)}"
        if tracker:
            tracker.update_file_stage(file_name, Stage.PARSE, Status.FAILED, msg, primary_key=pk)
        return {"success": False, "used": "mineru", "msg": msg}

    if result.get("success"):
        _ensure_preferred_content_list(out_dir, rid)
        # 0109[新增]
        if tracker:
            tracker.update_file_stage(file_name, Stage.PARSE, Status.SUCCESS, primary_key=pk)
        return {"success": result.get("success",False), "used":"mineru" ,"msg": result.get("msg")}
    elif ok:
        _ensure_preferred_content_list(out_dir, rid)
        # 0109[新增]
        if tracker:
            tracker.update_file_stage(file_name, Stage.PARSE, Status.SUCCESS, primary_key=pk)
        return {"success": True, "used":"mineru" ,"msg": "本地解析成功"}
    else:
        
        # 0109如果解析结果不成功，必须手动更新状态为 FAILED
        msg = result.get("msg", "解析失败")
        if tracker:
            tracker.update_file_stage(file_name, Stage.PARSE, Status.FAILED, msg, primary_key=pk)
        return {"success": False,"used":"mineru", "msg": result.get("msg","解析失败")}
def _ensure_preferred_content_list(report_dir: Path, rid: str) -> Path | None:
    """
    如果只有 legacy 的 content_list.json，就把它拷贝/重命名为 <rid>_content_list.json，
    以保证后续 /parsed 与 /chunk 一致工作。
    """

    preferred = report_dir / f"{rid}_content_list.json"
    legacy = report_dir / "content_list.json"
    if preferred.exists():
        return preferred
    if legacy.exists():
        try:
            shutil.copyfile(legacy, preferred)
            return preferred
        except Exception:
            return legacy
    return None

# ==============================
# 12.25新增：PDF 纯文本解析（更快）
# 扫描件/低文本：自动回退 MinerU
# ==============================

def _is_all_image_pdf(pdf_path: str, min_text_len: int = 20):
    import fitz
    doc = fitz.open(pdf_path)
    page_text_lens = []
    for page in doc:
        txt = page.get_text("text") or ""
        page_text_lens.append(len(txt.strip()))
        if len(txt.strip()) >= min_text_len:
            return False, {
                "reason": "found_text_page",
                "page_text_lens": page_text_lens,
            }
    return True, {
        "reason": "all_pages_below_threshold",
        "page_text_lens": page_text_lens,
    }


def _smart_sample_is_text_pdf(pdf_path: str, threshold: int = 200) -> tuple[bool, dict]:
    """
    三点抽样判断 PDF 是否为“文本型”：
    - 页数<=3：全页检查
    - 否则检查：第1页、第中间页、最后一页
    只要任意抽样页文本长度 >= threshold，就认为是文本型PDF（不回退）。
    """
    try:
        import fitz  # PyMuPDF
    except Exception:
        # 没装 PyMuPDF 时，直接判为非文本型，交给 MinerU（更稳）
        return False, {"err": "pymupdf_not_installed"}

    doc = fitz.open(pdf_path)
    page_count = doc.page_count

    if page_count <= 0:
        doc.close()
        return False, {"page_count": 0, "sample_pages": [], "text_lens": {}, "threshold": threshold}

    if page_count <= 3:
        pages = list(range(page_count))
    else:
        pages = [0, page_count // 2, page_count - 1]

    text_lens = {}
    for i in pages:
        try:
            txt = (doc.load_page(i).get_text("text") or "").strip()
            text_lens[i] = len(txt)
        except Exception:
            text_lens[i] = 0

    doc.close()

    is_text_pdf = any(v >= threshold for v in text_lens.values())
    return is_text_pdf, {
        "page_count": page_count,
        "sample_pages": pages,
        "text_lens": text_lens,
        "threshold": threshold,
    }


def _run_single_puretext_parse_for_task(rid: str, task_dirs: dict, mineru_url: str, task_id: str = None):
    """
    单个报告的 PDF 纯文本解析（更快）：
    - 仅对 PDF 生效；若源是 doc/docx，则直接回退 MinerU（保持行为一致）
    - 若 PDF 为扫描件/低文本：自动回退 MinerU
    输出目录/文件与 MinerU 保持一致：
      parsed/<rid>/<rid>.md
      parsed/<rid>/<rid>_content_list.json
    """
    # [0109修改] 提前初始化 tracker，保证回退/失败能写状态
    pk = rid.split('_')[0] if '_' in rid else rid
    tracker = StatusTracker(task_id or "default") if task_id else None
    file_name = f"{rid}.unknown"

    pdf_cut_dir = task_dirs["pdf_cut"]
    extracted_right_dir = task_dirs["extracted_right"]
    downloads_dir = task_dirs["downloads"]
    parsed_dir = task_dirs["parsed"]
    

    candidates = []
    # # 先尝试从 pdf_cut 搜索（如果有匹配就用它），否则回退到 downloads/extracted_right，再回退到 uploads
    # for ext in (".pdf", ".docx", ".doc"):
    #     matches = _safe_rglob_matches(pdf_cut_dir, rid, ext)
    #     for p in matches:
    #         candidates.append((ext.lower(), p))

    # if not candidates:
    #     for ext in (".pdf", ".docx", ".doc"):
    #         matches = _safe_rglob_matches(downloads_dir, rid, ext)
    #         for p in matches:
    #             candidates.append((ext.lower(), p))
    #         matches = _safe_rglob_matches(extracted_right_dir, rid, ext)
    #         for p in matches:
    #             candidates.append((ext.lower(), p))

    # if not candidates:
    #     uploads_dir = task_dirs.get("uploads")
    #     if uploads_dir:
    #         for ext in (".pdf", ".docx", ".doc"):
    #             matches = _safe_rglob_matches(uploads_dir, rid, ext)
    #             for p in matches:
    #                 candidates.append((ext.lower(), p))

    
    # 检查 pdf_cut 目录是否为空，如果为空则从 extracted_right 和 downloads 目录搜索
    if pdf_cut_dir.exists() and any(pdf_cut_dir.iterdir()):
        # pdf_cut 目录不为空，从 pdf_cut 获取文件
        for ext in (".pdf", ".docx", ".doc"):
            matches = _safe_rglob_matches(pdf_cut_dir, rid, ext)  # 1.9修改
            for p in matches:
                candidates.append((ext.lower(), p))
    else:
        # pdf_cut 目录为空，从 downloads 和 extracted/right 获取文件
        for ext in (".pdf", ".docx", ".doc"):
            # 从 downloads 目录搜索
            matches = _safe_rglob_matches(downloads_dir, rid, ext)  # 1.9修改
            for p in matches:
                candidates.append((ext.lower(), p))
            # 从 extracted_right 目录搜索
            matches = _safe_rglob_matches(extracted_right_dir, rid, ext)  # 1.9修改
            for p in matches:
                candidates.append((ext.lower(), p))

    if not candidates:
        uploads_dir = task_dirs["uploads"]
        for ext in (".pdf", ".docx", ".doc"):
            matches = _safe_rglob_matches(uploads_dir, rid, ext)  # 1.9修改
            for p in matches:
                candidates.append((ext.lower(), p))
        if not candidates:
            # [0109修改] 找不到文件，写 FAILED
            msg = f"未找到源文件：{rid}.pdf/.docx/.doc"
            if tracker:
                tracker.update_file_stage(file_name, Stage.PARSE, Status.FAILED, msg, primary_key=pk)
            return {"success": False, "used": "pure_txt", "msg": f"未找到源文件：{rid}.pdf/.docx/.doc"}
            

    def _priority(ext: str) -> int:
        return 1 if ext == ".pdf" else 2  # PDF 优先

    src_ext, src_path = sorted(candidates, key=lambda x: (_priority(x[0]), str(x[1])))[0]
    file_name = os.path.basename(str(src_path)) # 0109 更新 file_name

    out_dir = parsed_dir /rid
    out_dir.mkdir(parents=True, exist_ok=True)
    md_output = out_dir / f"{rid}.md"
    content_list_output = out_dir / f"{rid}_content_list.json"
    images_dir = out_dir / "images"
    images_dir.mkdir(exist_ok=True)

    # 计算当前正在解析的文件名 & 主键，并初始化追踪器（便于记录状态）
    file_name = os.path.basename(str(src_path))
    file_stem = os.path.splitext(file_name)[0]
    pk = file_stem.split('_')[0] if '_' in file_stem else file_stem
    tracker = StatusTracker(task_id or "default")

    # 如果最终选中的不是 PDF，那"纯文本解析"对它没意义：回退 MinerU
    if src_ext != ".pdf":

        # 回退 MinerU（Word 文档等），先记录状态
        if tracker:
            tracker.update_file_stage(file_name, Stage.PARSE, Status.PROCESSING, primary_key=pk)
        result = _run_single_parse_for_task(rid, task_dirs, mineru_url, task_id)
        # 0109
        if tracker:
            status = Status.SUCCESS if result.get("success") else Status.FAILED
            # tracker.update_file_stage(file_name, Stage.PARSE, status, primary_key=pk, info=result.get("msg"))
            # tracker.update_file_stage(file_name, Stage.PARSE, status, result.get("msg", str(e)), primary_key=pk)
            msg = result.get("msg")
            if msg is None:
                msg = str(e) if 'e' in locals() else ""
            tracker.update_file_stage(file_name, Stage.PARSE, status, msg, primary_key=pk)
        return {"success": result.get("success", False), "used": "mineru", "msg": result.get("msg")}

    # ✅ 新逻辑：只有当"所有页几乎无可抽文本"(全图片/全扫描件) 才回退 MinerU
    min_text_len = int(os.getenv("FAST_PDF_MIN_TEXT_LEN", "20"))
    is_all_image, debug = _is_all_image_pdf(str(src_path), min_text_len=min_text_len)

    if is_all_image:
        # 全扫描件，使用 MinerU；先记录状态
        if tracker:
            tracker.update_file_stage(file_name, Stage.PARSE, Status.PROCESSING, primary_key=pk)
        result = _run_single_parse_for_task(rid, task_dirs, mineru_url, task_id)
        # 0109
        if tracker:
            status = Status.SUCCESS if result.get("success") else Status.FAILED
            # tracker.update_file_stage(file_name, Stage.PARSE, status, primary_key=pk, info=result.get("msg"))
            # tracker.update_file_stage(file_name, Stage.PARSE, status, result.get("msg", str(e)), primary_key=pk)
            msg = result.get("msg")
            if msg is None:
                msg = str(e) if 'e' in locals() else ""
        return {"success": result.get("success", False), "used": "mineru", "msg": result.get("msg")}

    
    # 纯文本提取（快）
    # ✅ 文本 + 图片提取（快）：会在 parsed/<rid>/images/ 落盘图片，并在 content_list 写入 image 项
    # 1229修改[总日志追踪]：在开始快速解析前，标记状态为处理中
    if tracker:
        tracker.update_file_stage(file_name, Stage.PARSE, Status.PROCESSING, primary_key=pk)
    try:
        pdf_text_fast.extract_pdf_to_md_and_content_list_with_images(
            pdf_path=str(src_path),
            md_out=str(md_output),
            content_list_out=str(content_list_output),
            images_dir=str(images_dir),
        )
        _ensure_preferred_content_list(out_dir, rid)
        # 记录成功
        if tracker:
            tracker.update_file_stage(file_name, Stage.PARSE, Status.SUCCESS, primary_key=pk)
        return {"success": True, "used": "pure_txt", "msg": "纯文本解析成功"}


    except Exception as e:
        # 纯文本失败也回退 MinerU（保证不中断）；先记录正在回退的情况
        if tracker:
            tracker.update_file_stage(file_name, Stage.PARSE, Status.PROCESSING, primary_key=pk)
        result = _run_single_parse_for_task(rid, task_dirs, mineru_url, task_id)
        # ---------------------- 0109重点修复：回退后的状态更新 ----------------------------
        if tracker:
            status = Status.SUCCESS if result.get("success") else Status.FAILED
            # tracker.update_file_stage(file_name, Stage.PARSE, status, result.get("msg", str(e)), primary_key=pk)
            msg = result.get("msg")
            if msg is None:
                msg = str(e) if 'e' in locals() else ""
        return {"success": result.get("success", False), "used": "mineru", "msg": result.get("msg")}



