# -*- coding: utf-8 -*-
import os
import re
import logging
import json
import time
import subprocess
from collections import OrderedDict
from typing import Optional, Dict, List, Tuple, Any
import shutil
from utils.status_tracker import StatusTracker, Stage, Status  # 1224修改[总日志追踪]

try:
    import fitz
except ImportError:
    print("错误: 未找到 PyMuPDF (fitz) 库。请通过 'pip install PyMuPDF' 安装。")
    exit(1)

# ANSI 颜色代码
GREEN = '\033[92m'
RESET = '\033[0m'
CHECKMARK = '✓'

EXCLUDE_KEYWORDS = ["相邻地块", "周边地块", "污染", "地块周边", "附", "附件"]

# --- 设置日志 ---
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

if logger.hasHandlers():
    logger.handlers.clear()


def setup_environment(input_dir: str, output_dir: str, temp_dir: str):
    input_dir = os.path.abspath(input_dir)
    output_dir = os.path.abspath(output_dir)
    temp_dir = os.path.abspath(temp_dir)

    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(temp_dir, exist_ok=True)

    # log_file_path = os.path.join(output_dir, "processing_log.log")
    # file_handler = logging.FileHandler(log_file_path, encoding='utf-8')
    # file_handler.setLevel(logging.DEBUG)
    # file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    # file_handler.setFormatter(file_formatter)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.ERROR)
    console_formatter = logging.Formatter('%(levelname)s - %(message)s')
    console_handler.setFormatter(console_formatter)

    # logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return {
        'input_dir': input_dir,
        'output_dir': output_dir,
        'temp_dir': temp_dir,
        'logger': logger
    }


def is_pdf_file(pdf_path: str) -> bool:
    try:
        with open(pdf_path, "rb") as f:
            return f.read(4) == b"%PDF"
    except Exception:
        return False


import subprocess
import os


def repair_pdf_with_qpdf(src_path: str, dst_path: str) -> bool:
    try:
        cmd = [
            "qpdf",
            "--repair",
            "--linearize",
            src_path,
            dst_path
        ]
        subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True
        )
        return os.path.exists(dst_path) and os.path.getsize(dst_path) > 0
    except Exception:
        return False


def safe_open_pdf(pdf_path: str, temp_dir: Optional[str] = None):
    """
    返回:
      doc, opened_path, status
    status:
      OPEN_OK | OPEN_REPAIRED | INVALID_PDF | OPEN_FAILED
    """
    # 0️⃣ 基础校验
    if not os.path.exists(pdf_path):
        return None, pdf_path, "INVALID_PDF"

    if os.path.getsize(pdf_path) < 1024:
        return None, pdf_path, "INVALID_PDF"

    if not is_pdf_file(pdf_path):
        return None, pdf_path, "INVALID_PDF"

    # 1️⃣ 直接打开
    try:
        return fitz.open(pdf_path), pdf_path, "OPEN_OK"
    except Exception:
        pass

    # 2️⃣ qpdf 修复
    repaired_name = (
            os.path.splitext(os.path.basename(pdf_path))[0]
            + "_qpdf_repaired_"
            + str(int(time.time()))[-6:]
            + ".pdf"
    )
    repaired_path = os.path.join(temp_dir, repaired_name)

    if repair_pdf_with_qpdf(pdf_path, repaired_path):
        try:
            return fitz.open(repaired_path), repaired_path, "OPEN_REPAIRED"
        except Exception:
            pass

    # 3️⃣ 彻底失败
    return None, pdf_path, "OPEN_FAILED"


def is_excluded(title: str) -> bool:
    """判断标题是否应该被排除 - 智能版本"""
    # 清理标题
    clean_title = re.sub(r'\s*\.{3,}\s*\d+$', '', title).strip()
    clean_title = re.sub(r'^\d+(?:\.\d+)*\s*', '', clean_title).strip()

    # 重要章节关键词 - 这些不应该被排除
    # important_sections = [
    #     "污染识别", "污染状况", "污染源识别", "污染物识别",
    #     "污染状况调查", "土壤污染", "地下水污染", "污染分析"

    # ]

    # 如果标题包含重要章节关键词、，不应该排除
    # if any(important_section in clean_title for important_section in important_sections):
    #     return False

    # 次要排除关键词 - 这些应该被排除
    minor_exclude_keywords = ["相邻地块", "周边地块", "地块周边", "附", "附件"]

    # 检查是否包含次要排除关键词
    return any(ex_word in clean_title for ex_word in minor_exclude_keywords)


def get_pdf_page_mapping(pdf_path: str) -> Dict[int, int]:
    """获取PDF页码映射：目录页码 -> 物理页码"""
    try:
        doc, opened_path, open_statu = safe_open_pdf(pdf_path)
        if doc is None:
            logger.error("PDF无法打开且修复失败，回退原文件")
        mapping = {}

        # 简化映射逻辑：直接使用1:1映射
        for i in range(len(doc)):
            mapping[i] = i  # 目录页码直接对应物理页码

        doc.close()
        return mapping

    except Exception as e:
        print(f"获取PDF页码映射时出错: {e}")
        # 创建默认映射
        default_mapping = {}
        try:
            doc = fitz.open(pdf_path)
            for i in range(len(doc)):
                default_mapping[i] = i
            doc.close()
        except:
            pass
        return default_mapping


def convert_page_range(page_range_str: str, page_mapping: Dict[int, int]) -> str:
    """将目录页码范围转换为物理页码范围"""
    try:
        if '-' in page_range_str:
            start_str, end_str = page_range_str.split('-')
            catalog_start = int(start_str.strip()) - 1  # 目录页码（基于0）
            catalog_end = int(end_str.strip()) - 1  # 目录页码（基于0）

            # 转换为物理页码
            physical_start = page_mapping.get(catalog_start, catalog_start) + 1
            physical_end = page_mapping.get(catalog_end, catalog_end) + 1

            print(f"页码范围转换: {page_range_str} -> {physical_start}-{physical_end}")
            return f"{physical_start}-{physical_end}"
        else:
            catalog_page = int(page_range_str.strip()) - 1
            physical_page = page_mapping.get(catalog_page, catalog_page) + 1
            print(f"单一页码转换: {page_range_str} -> {physical_page}")
            return str(physical_page)

    except Exception as e:
        print(f"转换页码范围时出错: {e}")
        return page_range_str


def extract_pages_by_range(doc: fitz.Document, page_range_str: str, output_path: str):
    """根据页码范围提取页面"""
    try:
        print(f"提取页码范围: {page_range_str}")
        print(f"文档总页数: {len(doc)}")

        # 解析页码范围
        if '-' in page_range_str:
            start_page_str, end_page_str = page_range_str.split('-')
            start_page = int(start_page_str.strip())
            end_page = int(end_page_str.strip())
        else:
            start_page = end_page = int(page_range_str.strip())

        # 转换为基于0的索引
        physical_start_index = start_page - 1
        physical_end_index = end_page - 1

        print(f"解析结果: 开始页={start_page}(索引{physical_start_index}), 结束页={end_page}(索引{physical_end_index})")

        # 验证并提取
        if not (0 <= physical_start_index < len(doc)):
            print(f"错误: 开始页码 {start_page} 超出范围")
            return False
        if not (0 <= physical_end_index < len(doc)):
            print(f"错误: 结束页码 {end_page} 超出范围")
            return False
        if physical_start_index > physical_end_index:
            print(f"错误: 开始页码 {start_page} 大于结束页码 {end_page}")
            return False

        new_doc = fitz.open()
        for page_index in range(physical_start_index, physical_end_index + 1):
            new_doc.insert_pdf(doc, from_page=page_index, to_page=page_index)

        new_doc.save(output_path)
        new_doc.close()

        print(f"成功提取页面 {start_page}-{end_page} 到 {output_path}")
        return True

    except Exception as e:
        print(f"提取页面到PDF时出错: {e}")
        logger.error(f"提取页面到PDF时出错: {e}", exc_info=True)
        return False


def find_chapters_by_custom_rules(doc: fitz.Document, extraction_rules: List[Dict]) -> List[Dict]:
    """根据自定义规则查找匹配的章节"""
    matched_chapters = []

    # 获取目录
    toc_items = doc.get_toc()
    if not toc_items:
        print("文档没有目录，无法进行章节提取")
        return matched_chapters

    # print(f"=== PDF目录结构 ===")
    # for i, item in enumerate(toc_items):
    #     level, title, page_num = item
    #     print(f"目录项 {i}: 级别{level} - '{title}' -> 页码{page_num}")
    # print(f"===================")

    # 对每个规则进行匹配
    for rule in extraction_rules:
        rule_name = rule.get('name', '未知规则')
        keywords = rule.get('keywords', [])
        sections = rule.get('sections', [])

        # print(f"{GREEN}=== 应用规则: {rule_name} ===")
        # # print(f"关键词: {keywords}")
        # print(f"章节名称: {sections}")
        # print(f"======================={RESET}")

        # 查找匹配的章节
        for i, (level, title, page_num) in enumerate(toc_items):
            if is_excluded(title):
                # print(f"跳过排除标题: '{title}'")
                continue

            # 清理标题：移除页码和编号
            clean_title = re.sub(r'\s*\.{3,}\s*\d+$', '', title).strip()  # 移除末尾的页码
            clean_title = re.sub(r'^\d+(?:\.\d+)*\s*', '', clean_title).strip()  # 移除开头的编号
            clean_title = re.sub(r'^\(\d+\)\s*', '', clean_title).strip()  # 移除括号编号

            # 检查是否匹配章节名称
            section_match = False
            if sections:
                for section in sections:
                    if section.strip() and section.strip() in clean_title:
                        section_match = True
                        break

            # 检查是否匹配关键词
            keyword_match = False
            if keywords:
                for keyword in keywords:
                    if keyword.strip() and keyword.strip() in clean_title:
                        keyword_match = True
                        break

            # 如果匹配任一条件
            if section_match or keyword_match:
                # print(f"{GREEN}✓ 匹配到章节: '{clean_title}' (原标题: '{title}') -> 页码: {page_num}{RESET}")

                # 查找章节结束位置
                end_page = len(doc)  # 默认到文档末尾

                # 查找下一个同级或更高级别的标题
                for j in range(i + 1, len(toc_items)):
                    next_level, next_title, next_page = toc_items[j]
                    if next_level <= level:  # 同级或更高级别标题
                        end_page = next_page - 1  # 结束于下一个标题的前一页
                        break

                if page_num < 0:
                    page_num = 0
                # 确保结束页码不超过文档总页数
                if end_page > len(doc):
                    end_page = len(doc)

                # 确保结束页码不小于开始页码
                if end_page < page_num:
                    end_page = page_num


                matched_chapters.append({
                    "rule_name": rule_name,
                    "original_title": title,
                    "clean_title": clean_title,
                    "start_page": page_num,
                    "end_page": end_page,
                    "page_range": f"{page_num}-{end_page}",
                    "matched_by": "section" if section_match else "keyword"
                })

    return matched_chapters


def extract_by_custom_rules(pdf_path: str, output_dir: str, extraction_rules: List[Dict] = None) -> Optional[str]:
    """根据自定义规则提取内容 - 支持章节名称、关键词和页码范围"""
    logger.debug(f"开始自定义规则提取: {pdf_path}")
    # 没有目标章节信息时，直接复制原文件到输出目录
    if not extraction_rules or not isinstance(extraction_rules, list) or len(extraction_rules) == 0:
        logger.error("未提供有效的提取规则")
        # 规则无效时，直接复制原文件到输出目录
        try:
            original_filename = os.path.splitext(os.path.basename(pdf_path))[0]
            # timestamp = str(int(time.time()))[-6:]
            output_filename = f"{original_filename}_无规则.pdf"

            # 删除具有相同原始文件名的旧文件
            delete_existing_files_with_same_base(output_dir, original_filename)

            output_path = os.path.join(output_dir, output_filename)
            shutil.copy2(pdf_path, output_path)
            logger.debug(f"规则无效，原文件已复制到输出目录: {output_path}")
            return output_filename
        except Exception as e:
            logger.error(f"复制文件失败: {e}")
            return None

    try:
        # 尝试打开PDF文件，添加更健壮的错误处理
        doc, opened_path, open_status = safe_open_pdf(pdf_path, output_dir)

        if doc is None:
            logger.error(f"PDF无法打开，状态={open_status}，回退原文件")

            try:
                original_filename = os.path.splitext(os.path.basename(pdf_path))[0]
                # timestamp = str(int(time.time()))[-6:]
                output_filename = f"{original_filename}_无法打开.pdf"

                delete_existing_files_with_same_base(output_dir, original_filename)

                output_path = os.path.join(output_dir, output_filename)
                shutil.copy2(pdf_path, output_path)

                logger.debug(f"PDF无法打开，原文件已复制到输出目录: {output_path}")
                return output_filename
            except Exception as copy_e:
                logger.error(f"复制无法打开的PDF文件失败: {copy_e}")
                return None

        logger.info(f"PDF打开成功，状态={open_status}，路径={opened_path}")

        # 获取页码映射
        page_mapping = get_pdf_page_mapping(opened_path)

        print(f"{GREEN}=== 使用自定义规则提取 ===")
        # print(f"规则数量: {len(extraction_rules)}")
        for i, rule in enumerate(extraction_rules):
            # print(f"规则 {i}: {rule.get('name', '未知规则')}")
            # print(f"  - 关键词: {rule.get('keywords', [])}")
            print(f"  - 章节: {rule.get('sections', [])}")
            # print(f"  - 页码范围: {rule.get('page_range', '未设置')}")
        print(f"======================={RESET}")

        extracted_pages = []

        # 处理每个规则
        for rule in extraction_rules:
            rule_name = rule.get('name', '未知规则')
            page_range = rule.get('page_range', '')
            keywords = rule.get('keywords', [])
            sections = rule.get('sections', [])

            # 1. 如果有页码范围，直接按页码范围提取
            if page_range:
                # print(f"{GREEN}=== 规则 '{rule_name}': 按页码范围提取 ===")
                physical_page_range = convert_page_range(page_range, page_mapping)
                print(f"提取页码范围: {physical_page_range}")

                # 解析页码范围
                if '-' in physical_page_range:
                    start_str, end_str = physical_page_range.split('-')
                    start_page = int(start_str.strip())
                    end_page = int(end_str.strip())
                else:
                    start_page = end_page = int(physical_page_range.strip())

                # 添加到提取页面列表
                for page in range(start_page, end_page + 1):
                    if page not in extracted_pages:
                        extracted_pages.append(page)

                print(f"✓ 规则 '{rule_name}' 添加页面范围: {start_page}-{end_page}")

            # 2. 如果有章节名称或关键词，进行智能章节提取
            if keywords or sections:
                # print(f"{GREEN}=== 规则 '{rule_name}': 按章节/关键词提取 ===")

                # 查找匹配的章节
                matched_chapters = find_chapters_by_custom_rules(doc, [rule])

                if matched_chapters:
                    for chapter in matched_chapters:
                        start_page = chapter["start_page"]
                        end_page = chapter["end_page"]

                        # 添加到提取页面列表
                        for page in range(start_page, end_page + 1):
                            if page not in extracted_pages:
                                extracted_pages.append(page)

                        # print(
                        #     f"✓ 规则 '{rule_name}' 添加章节: '{chapter['clean_title']}' -> 页码范围: {chapter['page_range']}")
                else:
                    print(f"规则 '{rule_name}' 未找到匹配的章节")

        # 如果没有找到任何匹配的页面
        if not extracted_pages:
            print(f"{GREEN}未找到任何匹配的页面，复制原文件到输出目录{RESET}")
            doc.close()
            # 没有找到匹配页面时，直接复制原文件到输出目录
            try:
                original_filename = os.path.splitext(os.path.basename(pdf_path))[0]
                # timestamp = str(int(time.time()))[-6:]
                output_filename = f"{original_filename}.pdf"

                # 删除具有相同原始文件名的旧文件
                delete_existing_files_with_same_base(output_dir, original_filename)

                output_path = os.path.join(output_dir, output_filename)
                shutil.copy2(pdf_path, output_path)
                logger.debug(f"未找到匹配页面，原文件已复制到输出目录: {output_path}")
                return output_filename
            except Exception as e:
                logger.error(f"复制文件失败: {e}")
                return None

        # 排序并去重
        extracted_pages = sorted(set(extracted_pages))
        valid_pages = [p for p in extracted_pages if isinstance(p, int) and 1 <= p <= len(doc)]
        if not valid_pages:
            print("页码不合法")
            raise Exception(f"页码不合法")
        if len(valid_pages) > 50:
            preview = valid_pages[:20] + ["..."] + valid_pages[-20:]
            print(f"{GREEN}最终提取页面数量: {len(valid_pages)} 预览: {preview}{RESET}")
        else:
            print(f"{GREEN}最终提取页面: {valid_pages}{RESET}")

        # 生成输出文件名
        original_filename = os.path.splitext(os.path.basename(pdf_path))[0]
        # timestamp = str(int(time.time()))[-6:]

        # 根据规则生成描述性文件名
        rule_descriptions = []
        for rule in extraction_rules:
            rule_name = rule.get('name', '规则')
            if rule.get('page_range'):
                rule_descriptions.append(f"{rule_name}_页码")
            if rule.get('keywords'):
                rule_descriptions.append(f"{rule_name}_关键词")
            if rule.get('sections'):
                rule_descriptions.append(f"{rule_name}_章节")

        rule_desc = "_".join(rule_descriptions) if rule_descriptions else "自定义规则"
        output_filename = f"{original_filename}.pdf"

        # 删除具有相同原始文件名的旧文件
        delete_existing_files_with_same_base(output_dir, original_filename)

        # output_path = os.path.join(output_dir, output_filename)
        #
        # # 提取页面
        # new_doc = fitz.open()
        # for page_num in extracted_pages:
        #     if 1 <= page_num <= len(doc):
        #         new_doc.insert_pdf(doc, from_page=page_num - 1, to_page=page_num - 1)
        #
        # new_doc.save(output_path)
        # new_doc.close()
        # doc.close()

        # 2026.1.18
        page_expr = pages_to_qpdf_expr(valid_pages)
        print(page_expr)
        output_path = os.path.join(output_dir, output_filename)

        doc.close()  # ✅ 先关掉 fitz doc，释放缓存（很重要）

        qpdf_extract_pages(opened_path, output_path, page_expr)

        print(f"{GREEN}✓ 自定义规则提取成功: {output_filename}{RESET}")
        print(f"{GREEN}提取页面数量: {len(extracted_pages)}{RESET}")
        logger.debug(f"成功按自定义规则提取: {output_path}")
        return output_filename

    except Exception as e:
        logger.error(f"按自定义规则提取失败: {pdf_path}, 错误: {e}")
        # 提取出错时，直接复制原文件到输出目录
        try:
            original_filename = os.path.splitext(os.path.basename(pdf_path))[0]
            # timestamp = str(int(time.time()))[-6:]

            output_filename = f"{original_filename}_错误.pdf"

            output_filename = f"{original_filename}.pdf"


            # 删除具有相同原始文件名的旧文件
            delete_existing_files_with_same_base(output_dir, original_filename)

            output_path = os.path.join(output_dir, output_filename)
            shutil.copy2(pdf_path, output_path)
            logger.debug(f"提取出错，原文件已复制到输出目录: {output_path}")
            return output_filename
        except Exception as copy_e:
            logger.error(f"复制文件失败: {copy_e}")
            return None


def delete_existing_files_with_same_base(output_dir: str, original_filename: str):
    """
    删除输出目录中具有相同原始文件名的旧文件
    """
    import glob

    # 定义可能的文件名模式
    patterns = [
        f"{original_filename}_已剪切_*.pdf",
        f"{original_filename}_错误_*.pdf",
        f"{original_filename}_*.pdf",
        f"{original_filename}_无法打开_*.pdf",
        f"{original_filename}_无规则_*.pdf"
    ]

    # 搜索并删除匹配的文件
    for pattern in patterns:
        matching_files = glob.glob(os.path.join(output_dir, pattern))
        for file_path in matching_files:
            try:
                os.remove(file_path)
                # print(f"已删除旧文件: {file_path}")
                # logger.debug(f"已删除旧文件: {file_path}")
            except Exception as e:
                # print(f"删除旧文件失败 {file_path}: {e}")
                logger.error(f"删除旧文件失败 {file_path}: {e}")


# --- 主执行逻辑 ---
# --- 主执行逻辑 ---
# 1224修改[总日志追踪]：添加任务ID参数，用于状态追踪
def extract_dpf(downloads_dir: str, extracted_right_dir: str, output_dir: str, temp_dir: str, extraction_rules: list,
                task_id: str = None, selected_files: list = None):
    """
    主函数，使用自定义规则或默认规则进行提取
    :param downloads_dir: 第一个输入目录 (data/tasks/task-<id>/downloads)
    :param extracted_right_dir: 第二个输入目录 (extracted/right)
    :param output_dir: 输出目录
    :param temp_dir: 临时目录
    :param extraction_rules: 提取规则（sections,章节信息）
    :return: 处理结果列表
    """

    tracker = StatusTracker(task_id) if task_id else None  # 1224修改[总日志追踪]：初始化追踪器

    # 检查两个输入目录是否存在
    if not os.path.exists(downloads_dir):
        logger.error(f"输入目录不存在: {downloads_dir}")
        return []
    if not os.path.exists(extracted_right_dir):
        logger.error(f"输入目录不存在: {extracted_right_dir}")
        return []

    setup_environment(downloads_dir, output_dir, temp_dir)
    from pathlib import Path  # 添加这一行
    # 合并两个目录中的 PDF 文件
    pdf_files = []
     # 处理用户选择的文件
    if selected_files:
        for file_name in selected_files:
            # 检查是否是压缩包
            file_ext = Path(file_name).suffix.lower()
            archive_extensions = ['.zip', '.rar', '.7z', '.tar', '.gz', '.bz2']
            
            if file_ext in archive_extensions:
                # 如果是压缩包，查找同名文件夹
                folder_name = Path(file_name).stem  # 获取不带扩展名的文件名
                target_folder = Path(extracted_right_dir) / folder_name
                print(f"1111111111111{file_name}")
                
                if target_folder.exists():
                    # 遍历文件夹中的PDF文件
                    for root, dirs, files in os.walk(target_folder):
                        for file in files:
                            if file.lower().endswith('.pdf'):
                                pdf_files.append(str(Path(root) / file))
                else:
                    print(f"警告: 未找到压缩包对应的文件夹 {target_folder}")
            else:
                # 如果不是压缩包，按原逻辑处理
                for root_dir in [Path(downloads_dir), Path(extracted_right_dir)]:
                    if root_dir.exists():
                        for root, dirs, files in os.walk(root_dir):
                            for file in files:
                                if file.lower().endswith('.pdf') and file == file_name:
                                    pdf_files.append(str(Path(root) / file))
    else:
        # 如果没有指定文件，处理所有PDF文件
        for root_dir in [Path(downloads_dir), Path(extracted_right_dir)]:
            if root_dir.exists():
                for root, dirs, files in os.walk(root_dir):
                    for file in files:
                        if file.lower().endswith('.pdf'):
                            pdf_files.append(str(Path(root) / file))

    if not pdf_files:
        logger.info(f"在目录 {downloads_dir} 和 {extracted_right_dir} 及其子目录中没有找到PDF文件。")
        return []

    logger.info(f"找到 {len(pdf_files)} 个PDF文件待处理。")

    processed_count = 0
    pdf_names = []

    for pdf_path in pdf_files:

        filename = os.path.basename(pdf_path)
        file_key = filename  # 1224修改[总日志追踪]：获取文件名用于状态追踪 Key (去除路径)
        file_stem = os.path.splitext(file_key)[0]
        pk = file_stem.split('_')[0] if '_' in file_stem else file_stem

        # pdf_path = os.path.join(root_dir, filename)
        print(f"正在处理文件: {filename}")
        logger.debug(f"--- 开始处理文件 ({processed_count + 1}/{len(pdf_files)}): {filename} ---")

        # 1224修改[总日志追踪]更新状态：处理中 (Stage.ORGANIZE)
        if tracker:
            tracker.update_file_stage(file_key, Stage.ORGANIZE, Status.PROCESSING, primary_key=pk)

        # 根据是否有自定义规则选择不同的提取方式
        final_pdf_name = extract_by_custom_rules(pdf_path, output_dir, extraction_rules)

        if final_pdf_name:
            pdf_names.append(final_pdf_name)
            # 1224修改[总日志追踪]更新状态：处理完成
            if tracker:
                tracker.update_file_stage(file_key, Stage.ORGANIZE, Status.SUCCESS, primary_key=pk)

        processed_count += 1
        logger.debug(f"--- 文件处理结束 ({processed_count}/{len(pdf_files)}): {filename} ---\n")

    logger.info("所有文件处理完毕。")
    return pdf_names


# 2026.1.18张博
def pages_to_qpdf_expr(pages: list[int]) -> str:
    pages = sorted(set(pages))

    ranges = []
    start = prev = pages[0]
    for p in pages[1:]:
        if p == prev + 1:
            prev = p
        else:
            ranges.append(f"{start}-{prev}" if start != prev else f"{start}")
            start = prev = p
    ranges.append(f"{start}-{prev}" if start != prev else f"{start}")
    return ",".join(ranges)
def qpdf_extract_pages(src_pdf: str, dst_pdf: str, page_expr: str) -> None:
    # qpdf --empty --pages src.pdf 1-3,8,10-20 -- out.pdf
    cmd = ["qpdf", "--empty", "--pages", src_pdf, page_expr, "--", dst_pdf]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)