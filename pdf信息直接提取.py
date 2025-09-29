# -*- coding: utf-8 -*-
# 文件编码声明：使用UTF-8编码支持中文
import os  # 导入操作系统接口模块
import re  # 导入正则表达式模块
import logging  # 导入日志记录模块
from collections import OrderedDict  # 导入有序字典
from typing import Optional, Dict, List, Tuple, Any  # 导入类型注解

# 尝试导入 PyMuPDF (fitz) - PDF处理库
try:
    import fitz  # PyMuPDF
except ImportError:
    print("错误: 未找到 PyMuPDF (fitz) 库。请通过 'pip install PyMuPDF' 安装。")
    exit(1)  # 如果导入失败，退出程序

# --- 配置 ---
# 输入目录 - 存储待处理的PDF文件
INPUT_DIR = r"D:\信息中心\大模型\部里工作\土壤司\建设用地\地块开发建设情况信息提取\报告自动下载\4000个报告批量下载\2500-3000-上传服务器"
# 输出目录 - 存储处理后的PDF文件
OUTPUT_DIR = r"D:\信息中心\大模型\部里工作\土壤司\建设用地\地块开发建设情况信息提取\报告自动下载\4000个报告批量下载\2500-3000-使用情况细化提取-上传服务器"
# 临时目录 - 存储中间提取的章节文件
TEMP_DIR = os.path.join(INPUT_DIR, "temp_extracted_chapters")

# 确保输出和临时目录存在，如果不存在则创建
os.makedirs(OUTPUT_DIR, exist_ok=True)  # 创建输出目录
os.makedirs(TEMP_DIR, exist_ok=True)  # 创建临时目录

# --- 设置日志 ---
# 创建 logger - 日志记录器
logger = logging.getLogger(__name__)  # 获取当前模块的日志记录器
logger.setLevel(logging.DEBUG)  # 设置 logger 本身的级别为最低（DEBUG）

# 创建文件处理器，记录所有级别的日志到文件
file_handler = logging.FileHandler(os.path.join(OUTPUT_DIR, "processing_log.log"), encoding='utf-8')
file_handler.setLevel(logging.DEBUG)  # 文件处理器记录所有级别的日志
file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')  # 设置日志格式
file_handler.setFormatter(file_formatter)  # 为文件处理器设置格式

# 创建控制台处理器，但只显示 ERROR 及以上级别的日志
console_handler = logging.StreamHandler()  # 创建控制台输出处理器
console_handler.setLevel(logging.ERROR)  # 只在控制台显示错误及以上级别的日志
console_formatter = logging.Formatter('%(levelname)s - %(message)s')  # 设置控制台日志格式
console_handler.setFormatter(console_formatter)  # 为控制台处理器设置格式

# 添加处理器到 logger
logger.addHandler(file_handler)  # 添加文件处理器
logger.addHandler(console_handler)  # 添加控制台处理器

# ANSI 颜色代码 - 用于控制台彩色输出
GREEN = '\033[92m'  # 绿色ANSI代码
RESET = '\033[0m'   # 重置颜色ANSI代码
CHECKMARK = '✓'     # 对勾符号

# --- 常量定义 ---
# 现状/历史关键词：必须包含 "现状" 或 "历史"
STATUS_HISTORY_KEYWORDS = [["地块", "现状"],  # 关键词组1：必须同时包含"地块"和"现状"
    ["地块", "历史"],  # 关键词组2：必须同时包含"地块"和"历史"
    ["历史", "用地"],  # 关键词组3：必须同时包含"历史"和"用地"
    ["地块", "现状", "历史"]]  # 关键词组4：必须同时包含"地块"、"现状"和"历史"

# 规划关键词：必须包含 "规划"
PLANNING_KEYWORDS = [ ["地块", "规划"],  # 关键词组1：必须同时包含"地块"和"规划"
    ["区域", "规划"]]  # 关键词组2：必须同时包含"区域"和"规划"

# 排除关键词列表 - 包含这些关键词的标题将被排除
EXCLUDE_KEYWORDS = ["相邻地块", "周边地块", "污染", "地块周边", "附", "附件"]


# --- 辅助函数 ---

def is_excluded(title: str) -> bool:
    """检查标题是否包含排除关键词"""
    return any(ex_word in title for ex_word in EXCLUDE_KEYWORDS)  # 如果标题包含任何排除关键词返回True


def parse_toc(toc_items: List[List]) -> Dict[str, int]:
    """
    解析从 fitz 获得的目录项，生成标题到文档标注页码的映射。
    同时根据关键词过滤，只保留目标标题和后续的结束位置标题。
    :param toc_items: fitz Document.get_toc() 的输出
    :return: OrderedDict of {title: page_number}
    """
    title_page_map = OrderedDict()  # 创建有序字典存储标题和页码映射

    # 首先收集所有有效的目录项
    all_valid_items = []
    for item in toc_items:  # 遍历目录中的所有项
        # item 格式: [level, title, page_num]
        # 注意：page_num 是基于 1 的物理页码
        level, title, page_num = item  # 解构目录项：层级、标题、页码
        # fitz 的页码有时可能不是整数，需要处理
        try:
            physical_page_num = int(page_num)  # 将页码转换为整数
        except (ValueError, TypeError):  # 如果页码转换失败
            logger.debug(f"目录项页码无效: '{title}', 页码: {page_num}")  # 记录调试信息
            continue  # 跳过此项

        # 清理标题：移除目录格式的点号和页码（如".......13"）
        clean_title = re.sub(r'\s*\.{3,}\s*\d+$', '', title).strip()  # 移除末尾的点和数字

        # 过滤附件和不需要的内容（附录、附图、附表等）
        exclude_patterns = ['附件', '附', '附图', '附表', '附录', '图', '表', '参考文献', '致谢']
        if any(pattern in clean_title for pattern in exclude_patterns):  # 检查是否包含排除关键词
            logger.debug(f"跳过附件内容: '{clean_title}'")  # 记录调试信息
            continue  # 跳过此项

        # 过滤纯数字和证书号（如"123"或"京证字第xxx号"）
        if re.fullmatch(r'^\d+$', clean_title) or re.fullmatch(r'.*证字第.*号.*', clean_title):
            continue  # 跳过此项

        # 使用fitz提供的level作为标题级别，而不是点号数量
        # 级别数字越小表示级别越高（1>2>3...）
        all_valid_items.append((clean_title, physical_page_num, level))

    # 分别处理现状历史标题和规划标题
    status_history_titles = []
    planning_titles = []

    for i, (title, page_num, level) in enumerate(all_valid_items):
        # 检查排除关键词
        if any(ex_word in title for ex_word in EXCLUDE_KEYWORDS):
            continue

        # 清理标题文字部分（移除编号）
        clean_text = re.sub(r'^\d+(?:\.\d+)*\s*', '', title).strip()
        clean_text = re.sub(r'^\(\d+\)\s*', '', clean_text).strip()  # 移除(1)、(2)等编号

        # 检查是否匹配现状历史关键词
        is_status_history = any(all(kw in clean_text for kw in group) for group in STATUS_HISTORY_KEYWORDS)

        # 检查是否匹配规划关键词（需要排除同时包含现状历史的）
        is_planning = any(all(kw in clean_text for kw in group) for group in PLANNING_KEYWORDS)
        contains_status_history = any(all(kw in clean_text for kw in group) for group in STATUS_HISTORY_KEYWORDS)
        is_planning_only = is_planning and not contains_status_history

        if is_status_history:
            status_history_titles.append((title, page_num, level, i))
            logger.debug(f"找到现状历史标题: '{title}' -> 页码: {page_num}, 级别: {level}")

        if is_planning_only:
            planning_titles.append((title, page_num, level, i))
            logger.debug(f"找到规划标题: '{title}' -> 页码: {page_num}, 级别: {level}")

    # 为现状历史标题查找结束位置
    if status_history_titles:
        # 找到最后一个现状历史标题
        last_status_history = max(status_history_titles, key=lambda x: x[3])
        last_title, last_page, last_level, last_index = last_status_history

        # 查找下一个同级或更高级别标题
        for j in range(last_index + 1, len(all_valid_items)):
            next_title, next_page, next_level = all_valid_items[j]
            if next_level <= last_level:  # 同级或更高级别
                title_page_map[f"__END_STATUS__{next_title}"] = next_page
                logger.debug(f"找到现状历史结束位置: '{next_title}' -> 页码: {next_page}")
                break

        # 添加所有现状历史标题
        for title, page_num, level, index in status_history_titles:
            title_page_map[title] = page_num

    # 为规划标题查找结束位置（单独处理）
    if planning_titles:
        # 规划标题通常只有一个
        planning_title, planning_page, planning_level, planning_index = planning_titles[0]

        # 查找下一个同级或更高级别标题
        for j in range(planning_index + 1, len(all_valid_items)):
            next_title, next_page, next_level = all_valid_items[j]
            if next_level <= planning_level:  # 同级或更高级别
                title_page_map[f"__END_PLANNING__{next_title}"] = next_page
                logger.debug(f"找到规划结束位置: '{next_title}' -> 页码: {next_page}")
                break

        title_page_map[planning_title] = planning_page

    return title_page_map  # 返回标题到页码的映射字典

def find_chapter_range_from_toc_specific(title_page_map: Dict[str, int], target_keywords: List[List[str]],
                                         additional_check=None) -> Optional[Dict[str, Any]]:
    """
    根据目录查找特定类型章节的范围。
    返回匹配的标题和页码范围。
    """
    # 记录传入的目录信息
    logger.debug(f"传入的目录项: {list(title_page_map.items())}")

    matched_titles = []  # 存储所有匹配的标题
    matched_pages = []  # 存储所有匹配的页码
    end_title_page = None  # 存储结束位置标题的页码

    # 确定是现状历史还是规划搜索
    is_status_search = target_keywords == STATUS_HISTORY_KEYWORDS
    end_prefix = "__END_STATUS__" if is_status_search else "__END_PLANNING__"

    # 1. 查找所有匹配的标题和结束位置
    for title, page_num in title_page_map.items():
        if title.startswith(end_prefix):
            # 这是结束位置标记
            end_title_page = page_num
            logger.debug(f"找到预定义的结束位置: {title} -> 页码: {page_num}")
            continue

        if title.startswith('__END__'):  # 跳过另一种类型的结束标记
            continue

        if is_excluded(title):  # 检查排除关键词
            logger.debug(f"跳过排除标题: '{title}'")
            continue

        # 清理标题：移除编号和页码，只保留纯文字
        clean_title = re.sub(r'^\d+(?:\.\d+)*\s*', '', title)  # 移除开头的编号
        clean_title = re.sub(r'[\s\.\-]+\d+[\s\.\-]*$', '', clean_title)  # 移除末尾的页码
        clean_title = clean_title.strip()

        # 匹配关键词
        if any(all(kw in clean_title for kw in group) for group in target_keywords):
            if additional_check and not additional_check(title):
                logger.debug(f"额外检查未通过: '{title}'")
                continue
            matched_titles.append(title)
            matched_pages.append(page_num)
            logger.debug(f"匹配到目标标题: '{title}' -> 页码: {page_num}")

    if not matched_titles:
        logger.debug(f"目录中未找到包含目标关键词 {target_keywords} 的章节。")
        return None

    # 2. 确定页码范围
    # 按照页码排序匹配的标题
    sorted_matches = sorted(zip(matched_titles, matched_pages), key=lambda x: x[1])
    sorted_titles = [x[0] for x in sorted_matches]
    sorted_pages = [x[1] for x in sorted_matches]

    # 起始页是第一个匹配标题的页码
    start_page = sorted_pages[0]

    # 优化：如果第一个匹配标题有子章节，应该从子章节开始
    first_matched_title = sorted_titles[0]
    first_matched_level = first_matched_title.count('.')

    # 检查第一个匹配标题后面是否有子章节
    if len(sorted_titles) > 1:
        second_title = sorted_titles[1]
        second_level = second_title.count('.')
        if second_level > first_matched_level:  # 如果有子章节
            start_page = sorted_pages[1]  # 从第一个子章节开始
            # 更新匹配标题列表，移除父标题
            sorted_titles = sorted_titles[1:]

    # 确定结束页
    if end_title_page:
        end_page = end_title_page - 1  # 结束位置标题的前一页
        logger.debug(f"使用预定义的结束位置: {end_title_page} -> 结束页码: {end_page}")
    else:
        # 如果没有预定义的结束位置，使用文档最后一页
        end_page = max(title_page_map.values())
        logger.debug(f"未找到预定义结束位置，使用文档最后一页: {end_page}")

    # 确保结束页不小于开始页
    if start_page > end_page:
        logger.error(f"计算页码范围失败: start_page={start_page}, end_page={end_page}")
        return None

    # 格式化页码范围
    if start_page == end_page:
        page_range_str = str(start_page)
    else:
        page_range_str = f"{start_page}-{end_page}"

    logger.debug(f"最终页码范围: {page_range_str}")
    return {
        "目标章节标题": sorted_titles,
        "页码范围": page_range_str
    }

def extract_pages_to_pdf(doc: fitz.Document, page_range_str: str, output_path: str):
    """
    根据页码范围字符串提取页面并保存为新PDF。
    假设 page_range_str 中的页码是文档标注页码，并且与物理页码一致。
    如果不一致，需要更复杂的映射逻辑。
    :param doc: fitz Document 对象
    :param page_range_str: "12-27" 或 "33" 格式的字符串 (文档标注页码)
    :param output_path: 输出PDF路径
    """
    try:
        if '-' in page_range_str:  # 如果页码范围包含连字符
            start_page_str, end_page_str = page_range_str.split('-')  # 分割开始和结束页码
            start_page = int(start_page_str)  # 转换为整数
            end_page = int(end_page_str)  # 转换为整数
        else:  # 如果是单个页码
            start_page = end_page = int(page_range_str)  # 开始和结束页码相同

        # *** 关键假设 ***
        # 假设 文档标注页码 == 物理页码 (基于1)
        # 因此，物理页码索引 = 文档标注页码 - 1 (转换为基于0)
        physical_start_index = start_page - 1  # 计算基于0的开始索引
        physical_end_index = end_page - 1  # 计算基于0的结束索引

        # 检查页码是否有效
        if not (0 <= physical_start_index < len(doc)) or not (
                0 <= physical_end_index < len(doc)) or physical_start_index > physical_end_index:  # 如果页码无效
            logger.error(f"页码范围无效: {page_range_str} for document with {len(doc)} pages.")  # 记录错误信息
            return False  # 返回失败

        new_doc = fitz.open()  # 创建一个新的空PDF
        # 注意：如果 start_page == end_page，这个循环会执行一次
        for page_index in range(physical_start_index, physical_end_index + 1):  # 遍历页码范围
            new_doc.insert_pdf(doc, from_page=page_index, to_page=page_index)  # 插入页面到新文档

        new_doc.save(output_path)  # 保存新文档
        new_doc.close()  # 关闭新文档
        logger.debug(f"成功提取页面 {page_range_str} 到 {output_path}")  # 记录调试信息
        return True  # 返回成功
    except Exception as e:  # 捕获异常
        logger.error(f"提取页面到PDF时出错: {e}", exc_info=True)  # 记录错误信息（包含堆栈跟踪）
        return False  # 返回失败


# --- 主要处理函数 ---

def extract_status_history_chapter(pdf_path: str, temp_dir: str) -> Optional[str]:
    """
    提取 "现状 / 历史类" 章节并生成独立 PDF。
    使用 find_chapter_range_from_toc_specific 函数返回的标题和页码范围进行提取。
    """
    logger.debug(f"开始处理 (现状/历史): {pdf_path}")  # 记录开始处理
    try:
        doc = fitz.open(pdf_path)  # 打开PDF文件
    except Exception as e:  # 如果打开失败
        logger.error(f"无法打开PDF文件 {pdf_path}: {e}")  # 记录错误信息
        return None  # 返回None

    try:
        chapter_info = None  # 初始化章节信息
        toc_items = doc.get_toc()  # 获取目录项

        # 优先从目录中查找
        if toc_items:  # 如果有目录项
            title_page_map = parse_toc(toc_items)  # 解析目录
            if title_page_map:  # 如果成功解析目录
                # 使用 find_chapter_range_from_toc_specific 查找现状/历史章节范围
                chapter_info = find_chapter_range_from_toc_specific(title_page_map, STATUS_HISTORY_KEYWORDS)  # 查找章节范围

        # 如果目录中未找到，则跳过不在正文中查找（现状/历史章节通常在目录中）
        if not chapter_info:  # 如果没有找到章节信息
            logger.debug(f"PDF {pdf_path} 未在目录中找到符合条件的 '现状/历史' 章节。")  # 记录调试信息
            doc.close()  # 关闭文档
            return None  # 返回None

        page_range_str = chapter_info["页码范围"]  # 获取页码范围
        # --- 打印绿色成功信息 ---
        matched_titles = chapter_info["目标章节标题"]  # 获取匹配的标题
        for title in matched_titles:  # 遍历所有匹配的标题
            print(f"{GREEN}{CHECKMARK} 匹配到 '现状/历史' 章节: '{title}', 页码范围: {page_range_str}{RESET}")  # 打印成功信息
        # --- 结束打印 ---
        temp_filename = f"{os.path.splitext(os.path.basename(pdf_path))[0]}_现状历史章节.pdf"  # 生成临时文件名
        temp_output_path = os.path.join(temp_dir, temp_filename)  # 生成临时文件路径

        success = extract_pages_to_pdf(doc, page_range_str, temp_output_path)  # 提取页面到PDF
        doc.close()  # 关闭文档

        if success:  # 如果提取成功
            logger.debug(f"成功提取 '现状/历史' 章节: {temp_output_path}")  # 记录调试信息
            return temp_output_path  # 返回临时文件路径
        else:
            logger.error(f"提取 '现状/历史' 章节失败: {pdf_path}")  # 记录错误信息
            return None  # 返回None

    except Exception as e:  # 捕获异常
        logger.error(f"处理PDF {pdf_path} 时发生未预期错误: {e}", exc_info=True)  # 记录错误信息（包含堆栈跟踪）
        doc.close()  # 确保文档关闭
        return None  # 返回None


def extract_planning_chapter(pdf_path: str, temp_dir: str) -> Optional[str]:
    """
    提取 "规划类" 章节并生成独立 PDF。
    使用 find_chapter_range_from_toc_specific 函数返回的标题和页码范围进行提取。
    """

    # 特殊处理：规划关键词需要排除同时包含现状/历史的标题
    def is_planning_title(title):  # 定义规划标题检查函数
        contains_planning = any(all(kw in title for kw in group) for group in PLANNING_KEYWORDS)  # 检查是否包含规划关键词
        contains_status_history = any(all(kw in title for kw in group) for group in STATUS_HISTORY_KEYWORDS)  # 检查是否包含现状历史关键词
        return contains_planning and not contains_status_history  # 必须包含规划关键词但不包含现状历史关键词

    logger.debug(f"开始处理 (规划): {pdf_path}")  # 记录开始处理
    try:
        doc = fitz.open(pdf_path)  # 打开PDF文件
    except Exception as e:  # 如果打开失败
        logger.error(f"无法打开PDF文件 {pdf_path}: {e}")  # 记录错误信息
        return None  # 返回None

    try:
        chapter_info = None  # 初始化章节信息
        toc_items = doc.get_toc()  # 获取目录项

        # 优先从目录中查找
        if toc_items:  # 如果有目录项
            title_page_map = parse_toc(toc_items)  # 解析目录
            if title_page_map:  # 如果成功解析目录
                # 使用 find_chapter_range_from_toc_specific 查找规划章节范围，并传入额外检查函数
                chapter_info = find_chapter_range_from_toc_specific(title_page_map, PLANNING_KEYWORDS,
                                                                    additional_check=is_planning_title)  # 查找章节范围（带额外检查）

        # 如果目录中未找到规划章节，则跳过不在正文中查找（规划章节通常在目录中）
        if not chapter_info:  # 如果没有找到章节信息
            logger.debug(f"PDF {pdf_path} 未在目录中找到符合条件的 '规划' 章节。")  # 记录调试信息
            doc.close()  # 关闭文档
            return None  # 返回None

        page_range_str = chapter_info["页码范围"]  # 获取页码范围
        # --- 打印绿色成功信息 ---
        matched_titles = chapter_info["目标章节标题"]  # 获取匹配的标题
        for title in matched_titles:  # 遍历所有匹配的标题
            print(f"{GREEN}{CHECKMARK} 匹配到 '规划' 章节: '{title}', 页码范围: {page_range_str}{RESET}")  # 打印成功信息
        # --- 结束打印 ---
        temp_filename = f"{os.path.splitext(os.path.basename(pdf_path))[0]}_规划章节.pdf"  # 生成临时文件名
        temp_output_path = os.path.join(temp_dir, temp_filename)  # 生成临时文件路径

        success = extract_pages_to_pdf(doc, page_range_str, temp_output_path)  # 提取页面到PDF
        doc.close()  # 关闭文档

        if success:  # 如果提取成功
            logger.debug(f"成功提取 '规划' 章节: {temp_output_path}")  # 记录调试信息
            return temp_output_path  # 返回临时文件路径
        else:
            logger.error(f"提取 '规划' 章节失败: {pdf_path}")  # 记录错误信息
            return None  # 返回None

    except Exception as e:  # 捕获异常
        logger.error(f"处理PDF {pdf_path} 时发生未预期错误: {e}", exc_info=True)  # 记录错误信息（包含堆栈跟踪）
        doc.close()  # 确保文档关闭
        return None  # 返回None


def merge_chapter_pdfs(status_pdf_path: str, planning_pdf_path: str, output_dir: str) -> Optional[str]:
    """
    合并两类章节 PDF 并保存至指定目录。
    """
    original_filename = None  # 初始化原始文件名
    if status_pdf_path and os.path.exists(status_pdf_path):  # 如果现状历史PDF存在
        original_filename = os.path.splitext(os.path.basename(status_pdf_path))[0].replace('_现状历史章节',
                                                                                           '')  # 从文件名中移除后缀
    elif planning_pdf_path and os.path.exists(planning_pdf_path):  # 如果规划PDF存在
        original_filename = os.path.splitext(os.path.basename(planning_pdf_path))[0].replace('_规划章节',
                                                                                             '')  # 从文件名中移除后缀
    else:
        # 修改：将错误日志级别从 ERROR 改为 DEBUG，因为这种情况是正常的（可能只有一个章节）
        logger.debug("合并PDF失败：两个输入文件都不存在或无效。")  # 记录调试信息
        return None  # 返回None

    if not original_filename:  # 如果无法确定原始文件名
        original_filename = "未知文件"  # 使用默认文件名

    output_filename = f"{original_filename}_地块信息提取.pdf"  # 生成输出文件名
    output_path = os.path.join(output_dir, output_filename)  # 生成输出文件路径

    merge_doc = fitz.open()  # 创建新的PDF文档用于合并
    files_merged = False  # 初始化文件合并标志

    try:
        if status_pdf_path and os.path.exists(status_pdf_path):  # 如果现状历史PDF存在
            status_doc = fitz.open(status_pdf_path)  # 打开现状历史PDF
            merge_doc.insert_pdf(status_doc)  # 插入到合并文档
            status_doc.close()  # 关闭现状历史文档
            files_merged = True  # 设置合并标志
            logger.debug(f"已合并 '现状/历史' 章节: {status_pdf_path}")  # 记录调试信息
        else:
            if status_pdf_path:  # 如果路径存在但文件不存在
                logger.debug(f"'现状/历史' 章节文件不存在或路径无效: {status_pdf_path}")  # 记录调试信息

        if planning_pdf_path and os.path.exists(planning_pdf_path):  # 如果规划PDF存在
            planning_doc = fitz.open(planning_pdf_path)  # 打开规划PDF
            merge_doc.insert_pdf(planning_doc)  # 插入到合并文档
            planning_doc.close()  # 关闭规划文档
            files_merged = True  # 设置合并标志
            logger.debug(f"已合并 '规划' 章节: {planning_pdf_path}")  # 记录调试信息
        else:
            if planning_pdf_path:  # 如果路径存在但文件不存在
                logger.debug(f"'规划' 章节文件不存在或路径无效: {planning_pdf_path}")  # 记录调试信息

        if files_merged:  # 如果有文件被合并
            merge_doc.save(output_path)  # 保存合并后的文档
            logger.debug(f"成功合并PDF并保存至: {output_path}")  # 记录调试信息
            result_path = output_path  # 设置结果路径
        else:
            logger.debug(f"没有有效的章节文件可合并 for {original_filename}。未生成输出文件。")  # 记录调试信息
            result_path = None  # 设置结果路径为None

    except Exception as e:  # 捕获异常
        logger.error(f"合并PDF时出错 {original_filename}: {e}", exc_info=True)  # 记录错误信息（包含堆栈跟踪）
        result_path = None  # 设置结果路径为None
    finally:
        merge_doc.close()  # 确保合并文档关闭

    return result_path  # 返回结果路径


# --- 主执行逻辑 ---

def main():
    """主函数，遍历目录并处理所有PDF文件"""
    if not os.path.exists(INPUT_DIR):  # 检查输入目录是否存在
        logger.error(f"输入目录不存在: {INPUT_DIR}")  # 记录错误信息
        return  # 退出函数

    pdf_files = [f for f in os.listdir(INPUT_DIR) if f.lower().endswith('.pdf')]  # 获取所有PDF文件

    if not pdf_files:  # 如果没有找到PDF文件
        logger.info(f"输入目录 {INPUT_DIR} 中没有找到PDF文件。")  # 记录信息
        return  # 退出函数

    logger.info(f"找到 {len(pdf_files)} 个PDF文件待处理。")  # 记录找到的文件数量

    processed_count = 0  # 初始化处理计数器
    for filename in pdf_files:  # 遍历所有PDF文件
        pdf_path = os.path.join(INPUT_DIR, filename)  # 生成完整文件路径
        # --- 打印正在处理的文件名 ---
        print(f"正在处理文件: {filename}")  # 打印当前处理的文件名
        # --- 结束打印 ---
        logger.debug(f"--- 开始处理文件 ({processed_count + 1}/{len(pdf_files)}): {filename} ---")  # 记录开始处理

        # 1. 提取现状/历史章节
        status_pdf = extract_status_history_chapter(pdf_path, TEMP_DIR)  # 提取现状历史章节

        # 2. 提取规划章节
        planning_pdf = extract_planning_chapter(pdf_path, TEMP_DIR)  # 提取规划章节

        # 3. 合并章节
        final_pdf = merge_chapter_pdfs(status_pdf, planning_pdf, OUTPUT_DIR)  # 合并章节

        if final_pdf:  # 如果成功生成最终PDF
            logger.debug(f"文件处理完成: {final_pdf}")  # 记录调试信息
        else:
            logger.debug(f"文件处理完成，但未生成合并PDF: {filename} (可能未找到相关章节)")  # 记录调试信息

        processed_count += 1  # 增加处理计数器
        logger.debug(f"--- 文件处理结束 ({processed_count}/{len(pdf_files)}): {filename} ---\n")  # 记录处理结束

    logger.info("所有文件处理完毕。")  # 记录所有文件处理完成


if __name__ == "__main__":
    main()  # 如果是直接运行此脚本，则执行main函数