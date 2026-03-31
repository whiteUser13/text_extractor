# docx_converter.py将word 文档转换成 PDF
# -*- coding: utf-8 -*-
import os,signal
import sys
import shutil
import logging
import tempfile
import subprocess
import platform
import time
from pathlib import Path
from filelock import FileLock
from utils.status_tracker import StatusTracker, Stage, Status    # 1224修改[总日志追踪]

# 配置日志
logger = logging.getLogger(__name__)
PDF_MIN_SIZE = 1024  # 1KB，防止空 PDF

# ========== DOCX 转 PDF 工具函数 ==========

def _is_valid_pdf(path: str) -> bool:
    return os.path.exists(path) and os.path.getsize(path) >= PDF_MIN_SIZE

def _convert_with_win32com(docx_path: str, pdf_out: str) -> bool:
    """Windows 专用：使用 Word COM 接口转换"""
    try:
        # 强制使用文件锁串行化，防止多线程冲突
        lock_path = os.path.join(tempfile.gettempdir(), "docx_convert.lock")
        convert_lock = FileLock(lock_path, timeout=30)
        
        with convert_lock:
            import win32com.client
            import pythoncom
            
            # 必须在线程中初始化 COM
            pythoncom.CoInitialize()
            
            word = None
            doc = None
            try:
                # 尝试连接现有的 Word 实例，如果失败则启动新的
                try:
                    word = win32com.client.GetActiveObject("Word.Application")
                except Exception:
                    word = win32com.client.Dispatch("Word.Application")
                
                word.Visible = False
                word.DisplayAlerts = 0

                # 绝对路径转换
                abs_docx = os.path.abspath(docx_path)
                abs_pdf = os.path.abspath(pdf_out)

                logger.info(f"[COM] 打开文档: {abs_docx}")
                doc = word.Documents.Open(abs_docx, ConfirmConversions=False, ReadOnly=True, AddToRecentFiles=False)
                
                logger.info(f"[COM] 保存PDF: {abs_pdf}")
                # FileFormat=17 (wdFormatPDF)
                doc.SaveAs(abs_pdf, FileFormat=17)
                
                doc.Close(False)
                doc = None
                
                if os.path.exists(abs_pdf) and os.path.getsize(abs_pdf) > 100:
                    return True
                return False

            except Exception as e:
                logger.warning(f"[COM] 转换异常: {e}")
                # 尝试强制关闭文档
                if doc:
                    try: doc.Close(False)
                    except: pass
                return False
            finally:
                # 注意：不要轻易 word.Quit()，因为可能影响服务器上其他正在运行的任务
                # 但在 Web 服务中，为了稳定性，通常建议保持一个 Word 实例或每次都关闭
                # 这里选择不关闭 Word 进程，只释放 COM
                pythoncom.CoUninitialize()

    except ImportError:
        return False
    except Exception as e:
        logger.warning(f"[COM] 致命错误: {e}")
        return False

def _convert_with_libreoffice(docx_path: str, pdf_out: str) -> bool:
    """跨平台通用：使用 LibreOffice 命令行转换"""
    try:
        soffice = shutil.which("soffice")
        if not soffice:
            if platform.system().lower() == "windows":
                candidates = [
                    r"C:\Program Files\LibreOffice\program\soffice.exe",
                    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"
                ]
                for c in candidates:
                    if os.path.exists(c):
                        soffice = c
                        break
        
        if not soffice:
            return False

        out_dir = os.path.dirname(pdf_out)
        # 必须使用绝对路径
        abs_docx = os.path.abspath(docx_path)
        
        cmd = [
            soffice,
            "--headless",
            "--convert-to", "pdf",
            "--outdir", out_dir,
            abs_docx
        ]

        # subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=300)
        try:
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=300)
        except subprocess.TimeoutExpired as e:
            # 保险补刀
            subprocess.run(["pkill", "-9", "-f", "soffice"])
        # LibreOffice 输出的文件名可能与预期不同（它使用原文件名.pdf）
        # 我们需要找到它并重命名为 pdf_out
        base_name = Path(abs_docx).stem
        possible_output = os.path.join(out_dir, base_name + ".pdf")
        
        if os.path.exists(possible_output):
            if os.path.abspath(possible_output) != os.path.abspath(pdf_out):
                if os.path.exists(pdf_out): os.remove(pdf_out)
                os.rename(possible_output, pdf_out)
            return True
            
        return False

    except Exception as e:
        logger.warning(f"[LibreOffice] 转换失败: {e}")
        return False


def convert_docx_to_pdf(docx_path: str, pdf_out: str) -> bool:
    """
    统一 DOCX -> PDF 转换入口
    规则：
    1. LibreOffice 是唯一主路径（Windows / Linux 一致）
    2. Word COM 仅作为 Windows 开发兜底
    3. pure python 不再参与成功判断
    """
    docx_path = os.path.abspath(docx_path)
    pdf_out = os.path.abspath(pdf_out)
    os.makedirs(os.path.dirname(pdf_out), exist_ok=True)

    # 0. 已存在且有效，直接返回
    if _is_valid_pdf(pdf_out):
        return True

    # ---------- 1. 主路径：LibreOffice ----------
    logger.info(f"[DOCX->PDF] 使用 LibreOffice: {Path(docx_path).name}")
    try:
        if _convert_with_libreoffice(docx_path, pdf_out) and _is_valid_pdf(pdf_out):
            logger.info("[DOCX->PDF] LibreOffice 转换成功")
            return True
    except Exception as e:
        logger.warning(f"[DOCX->PDF] LibreOffice 异常: {e}")

    # ---------- 2. Windows 兜底：Word COM（仅开发环境） ----------
    if platform.system().lower() == "windows":
        logger.warning("[DOCX->PDF] LibreOffice 失败，尝试 Word COM（仅 Windows 调试兜底）")
        try:
            if _convert_with_win32com(docx_path, pdf_out) and _is_valid_pdf(pdf_out):
                logger.info("[DOCX->PDF] Word COM 转换成功（兜底）")
                return True
        except Exception as e:
            logger.warning(f"[DOCX->PDF] Word COM 异常: {e}")

    # ---------- 3. 明确失败 ----------
    logger.error("[DOCX->PDF] 转换失败（LibreOffice / Word COM 均不可用）")
    return False


# ========= 业务流程封装 ==========
# def batch_convert_docx_to_pdf(task_dirs: dict):
#     """
#     Finalize 阶段核心逻辑：
#     1. 递归扫描 extracted/right 下的所有 .doc/.docx
#     2. 忽略临时文件 (~$)
#     3. 转换为同名 .pdf
#     4. 校验生成的 PDF 是否存在且大小正常
#     5. 收集所有失败情况，最后统一抛出异常
#     """
#     os.makedirs(downloads_dir, exist_ok=True)
#     # 从 task_folder_utils 的返回字典中获取路径
#     right_dir = task_dirs.get("extracted_right")
#     if not right_dir:
#         right_dir = task_dirs["extracted"] / "right"
        
#     downloads_dir = task_dirs["downloads"]
#     # if not right_dir.exists():
#     #     return

#     failed = []
# 1229修改[总日志追踪]：添加任务ID参数，用于状态追踪
def batch_convert_docx_to_pdf(task_dirs: dict, task_id: str = "None"):
        """
        Finalize 阶段核心逻辑：
        1. 递归扫描 extracted/right 和 downloads 下的所有 .doc/.docx
        2. 忽略临时文件 (~$)
        3. 转换为同名 .pdf
        4. 校验生成的 PDF 是否存在且大小正常
        5. 收集所有失败情况，最后统一抛出异常
        """
        tracker = StatusTracker(task_id) if task_id else None        # 1229修改[总日志追踪]：初始化状态追踪器
        # 获取路径
        right_dir = task_dirs.get("extracted_right")
        if not right_dir:
            right_dir = task_dirs["extracted"] / "right"

        downloads_dir = task_dirs.get("downloads")

        failed = []

        def process_directory(directory: Path, output_in_same_dir: bool = False):
            if not directory.exists():
                logger.warning(f"Directory does not exist: {directory}")
                return

            # --------------------1229新增：先处理源 PDF 文件--------------------
            # 将目录下的所有 PDF 文件先标记为 SKIPPED。
            pdf_candidates = list(directory.rglob("*.pdf"))
            for pdf_file in pdf_candidates:
                if not pdf_file.is_file(): continue
                
                file_stem = pdf_file.stem
                pk = file_stem.split('_')[0] if '_' in file_stem else file_stem
                
                if tracker:
                    tracker.update_file_stage(pdf_file.name, Stage.CONVERT, Status.SKIPPED, primary_key=pk)
            # ----------------------------------------------------------------

            # 递归查找所有 Word 文档
            candidates = list(directory.rglob("*.docx")) + list(directory.rglob("*.doc"))

            for p in candidates:
                if not p.is_file():
                    continue
                # 忽略 Word 临时文件
                if p.name.startswith("~$"):
                    continue
                
                # --------------------1229修改[总日志追踪]：计算主键 pk，确保状态更新能找到对应的记录，增加处理中状态---------------
                file_stem = p.stem
                pk = file_stem.split('_')[0] if '_' in file_stem else file_stem
                
                if tracker:
                    tracker.update_file_stage(p.name, Stage.CONVERT, Status.PROCESSING, primary_key=pk)
                # ---------------------------------------------------------------------------------------------
                # 输出路径
                pdf = p.with_suffix(".pdf") if output_in_same_dir else directory.parent / "converted" / p.relative_to(directory).with_suffix(".pdf")

                # 检查是否已存在有效的 PDF
                if pdf.exists() and pdf.stat().st_size >= PDF_MIN_SIZE:
                    logger.info(f"PDF already exists and is valid: {pdf}")
                    # 1229修改[总日志追踪]：标记已存在为成功
                    if tracker:
                        tracker.update_file_stage(p.name, Stage.CONVERT, Status.SUCCESS, primary_key=pk)
                    continue

                logger.info(f"Converting: {p} -> {pdf}")

                try:
                    # 调用转换工具
                    ok = convert_docx_to_pdf(str(p), str(pdf))

                    # 校验结果
                    if not ok or not pdf.exists() or pdf.stat().st_size < PDF_MIN_SIZE:
                        failed.append(f"{p.name} (转换失败或文件无效)")
                        # 1229修改[总日志追踪]：标记失败状态
                        if tracker:
                            tracker.update_file_stage(p.name, Stage.CONVERT, Status.FAILED, "转换失败或文件无效", primary_key=pk)
                    else:
                        logger.info(f"Conversion successful: {pdf}")
                        # 1229修改[总日志追踪]：标记成功
                        if tracker:
                            tracker.update_file_stage(p.name, Stage.CONVERT, Status.SUCCESS, primary_key=pk)
                except Exception as e:
                    logger.error(f"Error converting {p}: {e}")
                    failed.append(f"{p.name} (异常: {str(e)})")
                    # 1229修改[总日志追踪]：标记异常
                    if tracker:
                        tracker.update_file_stage(p.name, Stage.CONVERT, Status.FAILED, str(e), primary_key=pk)

        # 处理 extracted/right 目录
        process_directory(right_dir, output_in_same_dir=True)

        # 处理 downloads 目录
        if downloads_dir:
            process_directory(downloads_dir, output_in_same_dir=True)

        if failed:
            detail = "\n".join(failed)
            logger.error(f"以下文件转换失败 (已跳过，不中断整体流程)：\n{detail}")
            return failed
        else:
            logger.info("所有文件转换成功")
            return []
    