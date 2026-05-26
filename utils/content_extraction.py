import os
import json
import time
import requests
import re
import logging
import hashlib
from pathlib import Path
from openai import OpenAI
from utils.json_manager import ResultCollectionJsonFileManager
from datetime import datetime
"""
从Markdown文件和JSON文件中提取内容 - 增强上下文关联提取
智能搜索：在JSON的title字段中查找目标章节，把所有找到的内容都提取出来
未找到的字段明确标记为"未找到"
"""
SIM_THRESHOLD = 0.92  # 0123修复：用于纯文本相似合并阈值

# 设置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ============ 配置参数 ============
# 扩大搜索范围和上下文长度
DEFAULT_CONTEXT_SIZE = 500  # 从300增加到500
MAX_CONTEXT_LENGTH = 2000  # 从800增加到2000
MAX_LOCATED_SEGMENTS = 5  # 从2-3增加到最多5个段落
MAX_ORGANIZED_SECTIONS = 20  # 最多组织20个文本块，避免过多

# 0123修复：跨章节相似去重阈值
SIM_THRESHOLD = 0.92

# 0123修改：表格型文件常见场景下，需要给大模型更完整的块内容（尤其是tables/images）
MAX_CONTEXT_LENGTH_TABLE = 16000  # 0123修改：提高上限，避免表18等靠后的表格被截断

# 新增：页面索引相关的配置
PAGE_IDX_UNKNOWN = -1  # 未知页面索引的默认值


def smart_truncate(content, max_length=MAX_CONTEXT_LENGTH):
    """智能截断，尽量在句子或段落边界处截断"""
    if not content or len(content) <= max_length:
        return content

    truncated = content[:max_length]

    # 查找合适的截断点（按优先级从高到低）
    truncate_points = [
        truncated.rfind('\n\n'),  # 段落分隔（空行）
        truncated.rfind('。\n'),  # 句号后换行
        truncated.rfind('。'),  # 中文句号
        truncated.rfind('；'),  # 中文分号
        truncated.rfind('\n'),  # 换行
        truncated.rfind('. '),  # 英文句号加空格
        truncated.rfind('！'),  # 感叹号
        truncated.rfind('？'),  # 问号
        truncated.rfind('，'),  # 中文逗号
        truncated.rfind(', '),  # 英文逗号加空格
    ]

    # 找到最合适的截断点（在80%位置之后）
    for point in truncate_points:
        if point > max_length * 0.8 and point > 0:
            return content[:point + 1] + "..."

    # 如果找不到合适的标点，在单词边界处截断
    last_space = truncated.rfind(' ')
    if last_space > max_length * 0.9:
        return content[:last_space] + "..."

    return truncated + "..."


def is_image_field(rule_or_name: object) -> bool:
    """判断某个提取规则是否为"图片/表格截图路径"类字段"""
    if not rule_or_name:
        return False
    if isinstance(rule_or_name, dict):
        name = str(rule_or_name.get("name", "") or "")
        prompt = str(rule_or_name.get("prompt", "") or "")
        text = f"{name} {prompt}"
    else:
        text = str(rule_or_name)
    text_low = text.lower()
    keywords = ["图片", "图", "截图", "img_path", "image", "照片", "示意图", "范围图", "附图", "表格截图"]
    return any(k.lower() in text_low for k in keywords)


def is_short_title_content(content_value, title_value=None, max_title_length=30, similarity_threshold=0.8):
    """
    判断content内容是否是短标题（而非真正的正文内容）
    简化规则：
    1. 如果content是字符串且长度小于阈值
    2. 如果content与title高度相似（如果提供了title）
    
    参数:
        content_value: 要检查的content值
        title_value: 对应的title值（可选）
        max_title_length: 最大标题长度阈值
        similarity_threshold: 相似度阈值，超过则认为高度相似
    """
    if not isinstance(content_value, str):
        return False
    
    content_clean = content_value.strip()
    
    # 规则1: 长度判断
    if len(content_clean) > max_title_length:
        return False
    
    # 规则2: 如果提供了title，检查content与title的相似度
    if title_value and isinstance(title_value, str):
        similarity = calculate_text_similarity(content_clean, title_value.strip())
        if similarity >= similarity_threshold:
            return True
    
    return False


def calculate_text_similarity(text1, text2):
    """
    计算两个文本的相似度（简单版，基于最长公共子序列）
    返回0-1之间的相似度分数
    """
    if not text1 or not text2:
        return 0.0
    
    # 如果完全相同
    if text1 == text2:
        return 1.0
    
    # 如果一个是另一个的子串
    if text1 in text2 or text2 in text1:
        return 0.9
    
    # 简单分词比较（针对中文）
    # 移除空白字符和标点
    import re
    clean_text1 = re.sub(r'[^\w\u4e00-\u9fa5]', '', text1)
    clean_text2 = re.sub(r'[^\w\u4e00-\u9fa5]', '', text2)
    
    # 如果清理后相同或互为子串
    if clean_text1 == clean_text2:
        return 1.0
    if clean_text1 in clean_text2 or clean_text2 in clean_text1:
        return 0.9
    
    # 基于集合的Jaccard相似度
    words1 = set(re.findall(r'[\u4e00-\u9fa5]', text1))
    words2 = set(re.findall(r'[\u4e00-\u9fa5]', text2))
    
    if not words1 or not words2:
        # 如果没有中文字符，使用字符级别的比较
        chars1 = set(text1)
        chars2 = set(text2)
        intersection = len(chars1 & chars2)
        union = len(chars1 | chars2)
    else:
        intersection = len(words1 & words2)
        union = len(words1 | words2)
    
    if union == 0:
        return 0.0
    
    return intersection / union


def get_page_idx_from_object(obj):
    """
    从对象中提取页面索引，支持多种字段名，包括从tables/images数组中提取
    增强版：递归搜索tables和images数组
    """
    if isinstance(obj, dict):
        # 1. 直接检查page_idx字段
        for key in ['page_idx', 'page_index', 'page_number', 'page_num', '页码', '页号']:
            if key in obj:
                return obj[key]
        
        # 2. 检查tables数组中的page_idx
        if 'tables' in obj and isinstance(obj['tables'], list) and obj['tables']:
            for table in obj['tables']:
                if isinstance(table, dict):
                    for key in ['page_idx', 'page_index', 'page_number']:
                        if key in table:
                            return table[key]
        
        # 3. 检查images数组中的page_idx
        if 'images' in obj and isinstance(obj['images'], list) and obj['images']:
            for img in obj['images']:
                if isinstance(img, dict):
                    for key in ['page_idx', 'page_index', 'page_number']:
                        if key in img:
                            return img[key]
    
    return PAGE_IDX_UNKNOWN


def filter_json_content(content, max_title_length=30):
    """过滤JSON内容，移除不需要的字段，但保留page_idx用于内部处理"""
    if isinstance(content, dict):
        filtered = {}
        
        # 获取title值（如果存在）
        title_value = content.get("title", "")
        
        # 保留必要的字段
        if "title" in content:
            filtered["title"] = content["title"]
        
        # 保留section_code用于追踪
        if "section_code" in content:
            filtered["section_code"] = content["section_code"]
        
        # 保留page_idx用于内部处理（但最终不会在extraction_results中显示）
        if "page_idx" in content:
            filtered["page_idx"] = content["page_idx"]
        
        # 只有当content存在且不是短标题时才保留
        if "content" in content and content["content"]:
            content_value = content["content"]
            if isinstance(content_value, str) and is_short_title_content(
                content_value, title_value, max_title_length
            ):
                print(f"  检测到短标题content，已过滤: {content_value[:50]}...")
                if title_value:
                    print(f"  对应的title: {title_value[:50]}")
            else:
                filtered["content"] = content_value
        
        # 只有当tables存在且非空时才保留
        if "tables" in content and content["tables"] and len(content["tables"]) > 0:
            filtered["tables"] = content["tables"]
        
        # 只有当images存在且非空时才保留
        if "images" in content and content["images"] and len(content["images"]) > 0:
            filtered["images"] = content["images"]
        
        return filtered
    elif isinstance(content, list):
        filtered_list = []
        for item in content:
            filtered_item = filter_json_content(item, max_title_length)
            if filtered_item:
                filtered_list.append(filtered_item)
        return filtered_list
    else:
        return content


def generate_rule_identifier(extraction_rules):
    """根据提取规则生成规则标识符"""
    if not extraction_rules:
        return "default"

    rule_names = []
    for rule in extraction_rules:
        if rule.get('name'):
            clean_name = re.sub(r'[^\w\u4e00-\u9fa5]', '_', rule['name'])
            rule_names.append(clean_name)

    if rule_names:
        if len(rule_names) == 1:
            identifier = rule_names[0]
        else:
            identifier = f"{rule_names[0]}_{rule_names[1]}"

        if len(identifier) > 30:
            identifier = identifier[:30]
        return identifier

    rule_ids = []
    for rule in extraction_rules:
        if rule.get('id'):
            rule_ids.append(rule['id'][:8])

    if rule_ids:
        return "_".join(rule_ids)

    return f"rules_{len(extraction_rules)}"


def decode_response_text(response_text):
    """解码API响应文本"""
    if not response_text:
        return ""

    encodings = ['utf-8', 'gbk', 'gb2312', 'latin-1', 'iso-8859-1']

    for encoding in encodings:
        try:
            if isinstance(response_text, bytes):
                decoded = response_text.decode(encoding)
            else:
                encoded = response_text.encode('latin-1', errors='ignore')
                decoded = encoded.decode(encoding, errors='ignore')

            if 'æ' in decoded or 'å' in decoded or 'è' in decoded:
                continue

            return decoded
        except:
            continue

    return response_text if isinstance(response_text, str) else str(response_text)


def find_context_around_text(document_str, search_text, context_size=DEFAULT_CONTEXT_SIZE):
    """在文档中查找包含搜索文本的上下文段落 - 扩大搜索范围"""
    if not document_str or not search_text:
        return []

    sections = []

    # 将搜索文本按标点分割成多个搜索项
    search_items = re.split(r'[，。；！？、\s]+', search_text.strip())
    search_items = [item.strip() for item in search_items if item.strip() and len(item.strip()) >= 2]

    # 如果没有有效的搜索项，使用字段名
    if not search_items:
        return []

    found_items = []

    for search_item in search_items:
        # 查找所有匹配位置 - 使用更宽松的匹配
        pattern = re.escape(search_item)
        # 增加查找次数，找到更多匹配
        matches = list(re.finditer(pattern, document_str))

        # 如果匹配太少，尝试模糊匹配（包含部分词）
        if len(matches) < 3 and len(search_item) > 2:
            # 将长词拆分成子词
            sub_items = []
            for i in range(len(search_item) - 1):
                sub_items.append(search_item[i:i + 2])

            for sub_item in sub_items:
                if len(sub_item) >= 2:
                    sub_pattern = re.escape(sub_item)
                    sub_matches = list(re.finditer(sub_pattern, document_str))
                    matches.extend(sub_matches[:2])  # 每个子词最多加2个匹配

        # 限制匹配数量，避免太多
        matches = matches[:10]

        for match in matches:
            # 扩大上下文范围
            start_pos = max(0, match.start() - context_size)
            end_pos = min(len(document_str), match.end() + context_size)

            # 调整到句子边界
            # 向后找段落开始
            while start_pos > 0 and document_str[start_pos] not in ['。', '；', '！', '？', '\n', '\r', '【', '『', '「']:
                start_pos -= 1
            if start_pos > 0:
                start_pos += 1

            # 向前找段落结束
            while end_pos < len(document_str) and document_str[end_pos] not in ['。', '；', '！', '？', '\n', '\r', '】',
                                                                                '』', '」']:
                end_pos += 1
            if end_pos < len(document_str):
                end_pos += 1

            section = document_str[start_pos:end_pos].strip()
            if section and len(section) > 20:
                sections.append({
                    'search_item': search_item,
                    'content': section,
                    'position': match.start()
                })
                found_items.append(search_item)

    # 按位置排序并去重
    sections.sort(key=lambda x: x['position'])
    unique_sections = []
    seen_contents = set()

    for section in sections:
        content_hash = hash(section['content'][:100])  # 使用前100字符的hash去重
        if content_hash not in seen_contents:
            unique_sections.append(section)
            seen_contents.add(content_hash)

    return unique_sections[:MAX_LOCATED_SEGMENTS]  # 限制段落数量


def _is_toc_like_content(text: str) -> bool:
    """粗略识别“目录/条目列表”型content，避免误把目录当正文。"""
    if not isinstance(text, str):
        return False
    t = text.strip()
    if len(t) < 40:
        return False
    cnt = len(re.findall(r'（[一二三四五六七八九十]+）', t))
    # 还有一种：很多行都很短（像条目）
    lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
    short_lines = sum(1 for ln in lines if len(ln) <= 18)
    if cnt >= 3:
        return True
    if len(lines) >= 10 and short_lines >= 8:
        return True
    return False


def _score_block_for_keyword(block: dict, keyword: str) -> int:
    """对候选块打分，优先：title命中、表格caption/body命中；其次content命中。"""
    if not isinstance(block, dict) or not keyword:
        return -999

    score = 0
    title = str(block.get("title", "") or "")
    content = block.get("content", "")
    tables = block.get("tables") or []

    if keyword in title:
        score += 12

    if isinstance(tables, list):
        for tb in tables:
            try:
                if not isinstance(tb, dict):
                    tb_str = json.dumps(tb, ensure_ascii=False)
                    if keyword in tb_str:
                        score += 8
                    continue
                cap = str(tb.get("caption", "") or tb.get("title", "") or "")
                tc = tb.get("table_caption", None)
                if tc:
                    try:
                        if isinstance(tc, list):
                            cap = cap + " " + " ".join([str(x) for x in tc if x is not None])
                        else:
                            cap = cap + " " + str(tc)
                    except Exception:
                        pass
                if keyword in cap:
                    score += 20
                body = tb.get("table_body", tb.get("body", tb.get("rows", "")))
                if isinstance(body, str) and keyword in body:
                    score += 14
                elif not isinstance(body, str):
                    body_str = json.dumps(body, ensure_ascii=False)
                    if keyword in body_str:
                        score += 10
            except Exception:
                continue

    if isinstance(content, str) and keyword in content:
        score += 4
        if _is_toc_like_content(content):
            score -= 6

    if block.get("tables"):
        score += 2
    if block.get("images") or block.get("figures"):
        score += 1

    return score


_STOPWORDS_ANCHOR = {
    "提取","输出","格式","示例","要求","步骤","规则","表格","表头","匹配","定位","找到","内容","数据","列","行","字段",
    "关键","重要","注意","处理","去除","空格","对应","下方","上方","如下","生成","字符串","合并","分隔","缺失","如果","则",
}

def _extract_anchor_keywords(prompt_text: str, field_name: str = "", section_name: str = "", max_kws: int = 10):
    """从prompt/字段名中提取更“可检索”的锚点短语（优先引号/反引号内容），用于表格筛选与排序。"""
    text = (prompt_text or "")
    kws = []

    # 1) 优先抓取引号/反引号内的内容（用户通常在这里写了“必须命中”的词）
    patterns = [
        r"“([^”]{2,40})”",
        r'"([^"]{2,40})"',
        r"`([^`]{2,40})`",
    ]
    for p in patterns:
        for m in re.findall(p, text):
            s = str(m).strip()
            if s and s not in kws:
                kws.append(s)

    # 2) 再补充：字段名/章节名中的中文短语
    for base in (field_name or "", section_name or ""):
        for m in re.findall(r"[\u4e00-\u9fa5]{2,12}", base):
            if m and m not in kws:
                kws.append(m)

    # 3) 再补充：prompt中的中文短语（限制长度，避免噪声）
    for m in re.findall(r"[\u4e00-\u9fa5]{2,12}", text):
        if m in _STOPWORDS_ANCHOR:
            continue
        # 过滤掉过于泛化的词
        if m in ("污染物","设施","治理","排放","许可","信息","名称","编号"):
            continue
        if m not in kws:
            kws.append(m)

    # 4) 把一些高价值常见锚点显式提前
    for high in ("全厂排放口总计","第一年","许可年排放量限值","许可年排放量限值（t/a）"):
        if high in text and high not in kws:
            kws.insert(0, high)

    # 5) 清洗
    cleaned = []
    for k in kws:
        k2 = k.strip()
        if not k2:
            continue
        if k2 in _STOPWORDS_ANCHOR:
            continue
        if len(k2) <= 1:
            continue
        if k2 not in cleaned:
            cleaned.append(k2)

    return cleaned[:max_kws]


def filter_block_by_keyword(block: dict, keyword_or_keywords):
    """将一个块裁剪成“更利于提取”的版本：
    - 尽量只保留命中关键词(或关键词列表)的表格，避免把“章节目录/第一张无关表”发给大模型；
    - content 做窗口化（尤其是目录型/超长content）。
    """
    if not isinstance(block, dict):
        return block

    # 兼容：传入 str 或 list[str]
    if isinstance(keyword_or_keywords, str):
        keywords = [keyword_or_keywords]
    elif isinstance(keyword_or_keywords, list):
        keywords = [k for k in keyword_or_keywords if isinstance(k, str) and k.strip()]
    else:
        keywords = []

    b = filter_json_content(block)

    def _hit_any(text: str) -> bool:
        if not text or not keywords:
            return False
        for kw in keywords:
            if kw and kw in text:
                return True
        return False

    # 1) tables：优先保留命中任一关键词的表
    tables = b.get("tables")
    if isinstance(tables, list) and tables:
        hit_tables = []
        weak_hit_tables = []
        for tb in tables:
            try:
                tb_str = json.dumps(tb, ensure_ascii=False)
            except Exception:
                tb_str = str(tb)

            if _hit_any(tb_str):
                hit_tables.append(tb)
            else:
                if any(x in tb_str for x in ("表", "table_caption", "table_body")):
                    weak_hit_tables.append(tb)

        if hit_tables:
            b["tables"] = hit_tables[:10]
        else:
            if len(tables) <= 3:
                b["tables"] = tables
            else:
                b["tables"] = tables[:2] + tables[-1:]

    # 2) content：目录型/超长时做窗口化
    content = b.get("content")
    if isinstance(content, str) and content:
        if _is_toc_like_content(content) or len(content) > 2500:
            b["content"] = _clip_text_by_keywords(content, keywords, window_chars=520, max_snippets=8)

    return b


def find_blocks_containing_text_in_json(json_obj, keyword, max_blocks=3, anchor_kws=None):
    """在结构化JSON里查找包含keyword的块，并按相关性排序返回（修复4：TOC降权、表格优先、锚点加权）。"""
    if isinstance(anchor_kws, str):
        anchor_kws = [anchor_kws]
    if not isinstance(anchor_kws, list):
        anchor_kws = []
    anchor_kws = [k.strip() for k in anchor_kws if isinstance(k, str) and k.strip()]

    candidates = []

    def score_block(block: dict) -> float:
        title = str(block.get("title","") or "")
        content = block.get("content")
        content_s = content if isinstance(content, str) else ""
        score = 0.0

        has_tbl = _block_has_tables_or_images(block)
        if has_tbl:
            score += 6.0

        if _is_strong_toc_block(block) and not has_tbl:
            score -= 20.0

        if keyword and keyword in title:
            score += 6.0
        if keyword and keyword in content_s:
            score += 2.0

        if anchor_kws:
            tables = block.get("tables")
            if isinstance(tables, list) and tables:
                for tb in tables:
                    ttxt = _table_text(tb)
                    for ak in anchor_kws:
                        if ak in ttxt:
                            cap = tb.get("table_caption", "")
                            cap_s = " ".join(cap) if isinstance(cap, list) else str(cap or "")
                            if ak in cap_s:
                                score += 12.0
                            else:
                                score += 6.0
            for ak in anchor_kws:
                if ak in title:
                    score += 2.0
                if ak in content_s:
                    score += 1.0

        return score

    def visit(obj):
        if isinstance(obj, dict):
            if any(k in obj for k in ("title","content","tables","images")):
                title = obj.get("title")
                content = obj.get("content")
                hit = False
                if isinstance(title, str) and keyword and keyword in title:
                    hit = True
                if isinstance(content, str) and keyword and keyword in content:
                    hit = True
                if not hit and anchor_kws:
                    try:
                        blob = json.dumps(obj, ensure_ascii=False)
                    except Exception:
                        blob = str(obj)
                    if any(ak in blob for ak in anchor_kws):
                        hit = True
                if hit:
                    candidates.append(obj)
            for v in obj.values():
                visit(v)
        elif isinstance(obj, list):
            for it in obj:
                visit(it)

    visit(json_obj)

    seen = set()
    scored = []
    for b in candidates:
        key = (str(b.get("title","")), str(b.get("section_code","")), str(b.get("page_idx","")))
        if key in seen:
            continue
        seen.add(key)
        scored.append((score_block(b), b))
    scored.sort(key=lambda x: x[0], reverse=True)

    return [b for _, b in scored[:max_blocks]]


def find_sections_by_title_in_json(json_obj, target_sections, title_exclude_keywords=None):
    """在JSON对象的title字段中查找目标章节，并提取page_idx"""
    found_sections = []

    def search_in_obj(obj, path=""):
        if isinstance(obj, dict):
            # 检查当前对象是否有title字段
            if 'title' in obj and isinstance(obj['title'], str):
                title = obj['title'].strip()
                exclude_keywords = title_exclude_keywords or []  
                title_lower = title.lower()  
                hit = [kw for kw in exclude_keywords if isinstance(kw, str) and kw.strip() and kw.strip().lower() in title_lower]  
                if hit:  
                    print(f"     ⛔ [TitleExclude] skip title='{title}' hit={hit} path={path}")  
                if not hit:  
                    for section in target_sections:
                        if section and section in title:
                            # 找到匹配的章节 - 提取page_idx
                            page_idx = get_page_idx_from_object(obj)
                            
                            section_info = {
                                'section_name': section,
                                'title': title,
                                'content': obj,
                                'path': f"{path}.title" if path else "title",
                                'page_idx': page_idx
                            }
                            found_sections.append(section_info)

            # 递归搜索所有子对象
            for key, value in obj.items():
                new_path = f"{path}.{key}" if path else key
                search_in_obj(value, new_path)

        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                new_path = f"{path}[{i}]"
                search_in_obj(item, new_path)

    search_in_obj(json_obj)
    return found_sections


def smart_locate_in_json_batch(json_content, extraction_rules, is_markdown=False):
    """批量定位：把所有规则涉及的'章节'一次性定位出来，返回 {section: {"content": text, "page_idx": idx}} 和对应的page_idx信息"""
    _ = is_markdown

    if not extraction_rules:
        return {}, {}

    section_exclude_map = {}
    all_sections = set()
    
    # 收集所有章节名
    for rule in extraction_rules:
        if not isinstance(rule, dict):
            continue
        secs = rule.get("sections") or []
        if not secs:
            name = str(rule.get("name", "") or "")
            secs = re.findall(r"[\u4e00-\u9fa5]{2,}", name)
        for sec in secs:
            sec = (sec or "").strip()
            if not sec:
                continue
            all_sections.add(sec)

            excludes = rule.get("title_exclude_keywords", []) or []
            if not isinstance(excludes, list):
                excludes = []
            norm = []
            for kw in excludes:
                if isinstance(kw, str) and kw.strip():
                    norm.append(kw.strip())
            if sec not in section_exclude_map:
                section_exclude_map[sec] = set()
            for kw in norm:
                section_exclude_map[sec].add(kw)

    section_contents = {}  # 改为 {section: {"content": text, "page_idx": idx}}
    section_page_idx = {}  # 保留原有结构

    for sec in list(all_sections):
        temp_rule = {
            "name": sec,
            "sections": [sec],
            "title_exclude_keywords": sorted(list(section_exclude_map.get(sec, set()))),
        }
        try:
            located, found_sections, page_idx_values = smart_locate_in_json(json_content, temp_rule, return_page_idx=True)
        except Exception as e:
            print(f"  ⚠️ smart_locate_in_json_batch 定位章节失败: '{sec}', err={e}")
            located = ""
            page_idx_values = []
            
        if located and isinstance(located, str) and located.strip():
            # 保存内容和page_idx
            page_idx = PAGE_IDX_UNKNOWN
            if page_idx_values:
                # 取第一个找到的page_idx（通常是最相关的）
                for info in page_idx_values:
                    if info.get('section') == sec or sec in info.get('section', ''):
                        page_idx = info.get('page_idx', PAGE_IDX_UNKNOWN)
                        print(f"  章节 '{sec}' 关联到 page_idx: {page_idx}")
                        break
                else:
                    page_idx = page_idx_values[0].get('page_idx', PAGE_IDX_UNKNOWN) if page_idx_values else PAGE_IDX_UNKNOWN
                    print(f"  章节 '{sec}' 使用第一个page_idx: {page_idx}")
            else:
                print(f"  章节 '{sec}' 无page_idx信息，设为默认值: {PAGE_IDX_UNKNOWN}")
            
            section_contents[sec] = {
                "content": located,
                "page_idx": page_idx
            }
            section_page_idx[sec] = page_idx

    return section_contents, section_page_idx


def smart_locate_in_json(json_text, extraction_rule, return_page_idx=False, **_kwargs):
    """智能定位：在JSON文本中找到相关信息的位置 - 优先在title字段中查找，并返回page_idx"""
    import json
    
    field_name = extraction_rule.get('name', '')
    prompt_text = extraction_rule.get('prompt', '')
    target_sections = extraction_rule.get('sections', [])

    print(f"\n=== 智能定位JSON字段: '{field_name}' ===")
    if prompt_text:
        print(f"  提示词: {prompt_text[:100]}... (仅用于大模型查询，不用于搜索)")
    if target_sections:
        print(f"  目标章节: {target_sections}")

    # 存储所有找到的内容
    all_found_content = []
    found_sections = []
    page_idx_values = []  # 存储找到的页面索引

    # 1. 首先在JSON的title字段中搜索所有目标章节
    if target_sections and isinstance(target_sections, list):
        print(f"  优先在JSON的title字段中搜索所有目标章节 ({len(target_sections)}个章节)")

        if isinstance(json_text, str):
            try:
                json_obj = json.loads(json_text)
            except:
                json_obj = {"content": json_text}
        else:
            json_obj = json_text

        title_exclude_keywords = extraction_rule.get('title_exclude_keywords', []) if isinstance(extraction_rule, dict) else [] 
        if not isinstance(title_exclude_keywords, list): 
            title_exclude_keywords = []  
        print(f"  [DEBUG] title_exclude_keywords={title_exclude_keywords}")

        # 在title字段中查找目标章节（带排除关键词）
        title_matches = find_sections_by_title_in_json(json_obj, target_sections, title_exclude_keywords)  

        if title_matches:
            for match in title_matches:
                section_name = match['section_name']
                title = match['title']
                content = match['content']
                page_idx = match.get('page_idx', PAGE_IDX_UNKNOWN)
                
                # 如果page_idx还是未知，尝试从content的tables中提取
                if page_idx == PAGE_IDX_UNKNOWN and isinstance(content, dict):
                    page_idx = get_page_idx_from_object(content)
                
                found_sections.append(section_name)
                page_idx_values.append({
                    'section': section_name,
                    'page_idx': page_idx
                })

                print(f"  ✅ 在title字段中找到章节 '{section_name}' (page_idx: {page_idx})")
                print(f"     匹配的title: {title}")

                # 过滤JSON内容，但保留page_idx
                filtered_content = filter_json_content(content)
                
                # 检查过滤后的content是否为空或太短
                if "content" in filtered_content and is_short_title_content(filtered_content["content"]):
                    print(f"     注意: content为短标题，已从提取内容中移除")
                    del filtered_content["content"]
                
                # 提取该章节的完整内容
                section_str = json.dumps(filtered_content, ensure_ascii=False, indent=2)
                
                # 在内容开头添加页面索引信息（仅用于提示词，不会保存到最终结果）
                page_info = f"【页面索引: {page_idx if page_idx != PAGE_IDX_UNKNOWN else '未知'}】"
                
                if len(section_str) > MAX_CONTEXT_LENGTH:
                    truncated_content = smart_truncate(section_str, MAX_CONTEXT_LENGTH)
                    all_found_content.append(f"【{section_name}】JSON内容 {page_info}（title: {title}）:\n{truncated_content}")
                else:
                    all_found_content.append(f"【{section_name}】JSON内容 {page_info}（title: {title}）:\n{section_str}")

            print(f"  在title字段中找到 {len(found_sections)}/{len(target_sections)} 个章节: {found_sections}")
            print(f"  页面索引: {page_idx_values}")

        # 2. 对于在title中没找到的章节，尝试在全JSON中搜索章节关键词
        missing_sections = [s for s in target_sections if s not in found_sections]
        if missing_sections:
            print(f"  以下章节在title字段中未找到，尝试在全JSON中搜索: {missing_sections}")

            if isinstance(json_text, (dict, list)):
                json_str = json.dumps(json_text, ensure_ascii=False, indent=2)
            else:
                json_str = str(json_text)

            for section_name in missing_sections:
                print(f"  🔍 在全JSON中搜索章节关键词: '{section_name}'")

                anchor_kws = _extract_anchor_keywords(prompt_text, field_name, section_name)
                blocks = find_blocks_containing_text_in_json(json_obj, section_name, max_blocks=3, anchor_kws=anchor_kws)
                
                if blocks:
                    found_sections.append(section_name)
                    block_texts = []
                    for b in blocks:
                        # 提取page_idx
                        page_idx = get_page_idx_from_object(b)
                        
                        page_idx_values.append({
                            'section': section_name,
                            'page_idx': page_idx
                        })
                        
                        filtered_json = filter_block_by_keyword(b, anchor_kws or section_name)
                        block_texts.append(dump_filtered_json_for_llm(filtered_json))
                    
                    combined_content = "\n\n---\n\n".join(block_texts)
                    all_found_content.append(f"【{section_name}】命中块（全文搜索）:\n{combined_content}")
                    print(f"  ✅ 在全JSON中找到章节 '{section_name}' 的命中块，共 {len(blocks)} 个")
                    continue

                # 兜底：仍保留旧的“附近上下文截取”策略
                sections = find_context_around_text(json_str, section_name, context_size=600)
                if sections:
                    all_content = [s['content'] for s in sections[:MAX_LOCATED_SEGMENTS]]
                    if all_content:
                        found_sections.append(section_name)
                        combined_content = "\n\n...\n\n".join(all_content)
                        if len(combined_content) > MAX_CONTEXT_LENGTH:
                            combined_content = smart_truncate(combined_content, MAX_CONTEXT_LENGTH)
                        all_found_content.append(f"【{section_name}】相关内容（截取）:\n{combined_content}")
                        print(f"  ✅ 在全JSON中找到章节 '{section_name}' 的相关内容（截取）")
                        
                        # 对于截取的内容，无法确定page_idx，使用默认值
                        page_idx_values.append({
                            'section': section_name,
                            'page_idx': PAGE_IDX_UNKNOWN
                        })
                    else:
                        print(f"  ⚠️ 在全JSON中也未找到章节 '{section_name}' 的相关内容")
                else:
                    print(f"  ⚠️ 在全JSON中也未找到章节 '{section_name}' 的相关内容")

    # 3. 如果没有目标章节，或者目标章节都没找到内容，使用字段名在JSON中搜索
    if not all_found_content and field_name:
        print(f"  ⚠️ 未找到任何目标章节内容，使用字段名在JSON中搜索: '{field_name}'")

        if isinstance(json_text, (dict, list)):
            json_str = json.dumps(json_text, ensure_ascii=False, indent=2)
        else:
            json_str = str(json_text)

        # 从字段名中提取关键词（中文词汇）
        chinese_keywords = re.findall(r'[\u4e00-\u9fa5]{2,}', field_name)
        if chinese_keywords:
            print(f"  从字段名中提取中文关键词: {chinese_keywords}")
            for keyword in chinese_keywords[:3]:
                blocks = find_blocks_containing_text_in_json(json_obj, keyword, max_blocks=2)
                if blocks:
                    block_texts = []
                    for b in blocks:
                        # 提取page_idx
                        page_idx = get_page_idx_from_object(b)
                        
                        page_idx_values.append({
                            'section': field_name,
                            'page_idx': page_idx
                        })
                        
                        filtered_json = filter_block_by_keyword(b, keyword)
                        block_texts.append(dump_filtered_json_for_llm(filtered_json))
                    
                    combined_content = "\n\n---\n\n".join(block_texts)
                    all_found_content.append(f"【{keyword}】命中块（字段关键词）:\n{combined_content}")
                    print(f"  ✅ 通过中文关键词 '{keyword}' 命中块，共 {len(blocks)} 个")
                    break

                sections = find_context_around_text(json_str, keyword, context_size=600)
                if sections:
                    all_content = [s['content'] for s in sections[:MAX_LOCATED_SEGMENTS]]
                    combined_content = "\n\n...\n\n".join(all_content)
                    
                    try:
                        import json
                        if combined_content.strip().startswith('{') or combined_content.strip().startswith('['):
                            json_obj = json.loads(combined_content)
                            filtered_json = filter_json_content(json_obj)
                            combined_content = json.dumps(filtered_json, ensure_ascii=False, indent=2)
                    except:
                        pass
                    
                    if len(combined_content) > MAX_CONTEXT_LENGTH:
                        combined_content = smart_truncate(combined_content, MAX_CONTEXT_LENGTH)
                    all_found_content.append(f"【{keyword}】相关内容:\n{combined_content}")
                    print(f"  ✅ 通过中文关键词 '{keyword}' 找到相关内容")
                    
                    page_idx_values.append({
                        'section': field_name,
                        'page_idx': PAGE_IDX_UNKNOWN
                    })
                    break

    # 4. 合并所有找到的内容
    located_content = ""
    if all_found_content:
        located_content = "\n\n---\n\n".join(all_found_content)
        prefer_long = ('"tables"' in located_content) or ('<table' in located_content) or ('"images"' in located_content) or ('"figures"' in located_content)
        max_total = (MAX_CONTEXT_LENGTH_TABLE if prefer_long else MAX_CONTEXT_LENGTH * 3)
        if len(located_content) > max_total:
            located_content = smart_truncate(located_content, max_total)

    # 输出结果
    if located_content:
        total_sections_found = len(found_sections)
        print(f"✅ 定位成功，共找到 {len(all_found_content)} 段内容，{total_sections_found}/{len(target_sections) if target_sections else 0} 个章节，总长度: {len(located_content)} 字符")
    else:
        print(f"❌ 未定位到相关内容")

    if return_page_idx:
        return located_content, found_sections, page_idx_values
    return located_content




# ==================== 0324修改：页码准确性优化辅助函数 ====================
def _is_meaningful_extracted_value(value) -> bool:
    """0324修改：判断提取结果是否为有效结果；无效结果不应再继承页码"""
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return True
    txt = str(value).strip()
    if not txt:
        return False
    invalid_tokens = [
        "未找到", "未提及", "未明确", "无法判断", "无", "暂无", "未知",
        "未查询到", "未发现", "未获取到"
    ]
    # 纯否定性结果直接视为无有效证据页
    if any(tok == txt for tok in invalid_tokens):
        return False
    if txt.startswith("未提及") or txt.startswith("未找到") or txt.startswith("未明确") or txt.startswith("无法判断"):
        return False
    return True


def _normalize_search_text(text: str) -> str:
    """0324修改：归一化文本，便于章节/片段匹配"""
    if not text:
        return ""
    s = str(text)
    s = s.replace("　", " ")
    s = re.sub(r"[\t\r\n]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _build_value_search_snippets(value: str, max_snippets: int = 4):
    """0324修改：从提取结果中提取较稳的短片段，用于证据页反查"""
    txt = _normalize_search_text(value)
    if not txt:
        return []

    # 去掉目标章节说明，避免把“目标章节”本身当作正文证据
    txt = re.sub(r"\(目标章节[:：][^\)]*\)", "", txt)
    txt = re.sub(r"（目标章节[:：][^）]*）", "", txt)

    snippets = []

    # 先提取带章节锚点前的正文描述
    for part in re.split(r"[。；！？;!?]", txt):
        part = part.strip(" ，,：:;；。")
        if len(part) >= 8 and not part.startswith("依据") and "目标章节" not in part:
            snippets.append(part[:80])

    # 提取包含数字的关键短语（面积/时间/编号类）
    numeric_phrases = re.findall(r"[^。；！？]{0,20}\d[^。；！？]{0,30}", txt)
    for p in numeric_phrases:
        p = p.strip()
        if len(p) >= 6:
            snippets.append(p[:80])

    # 去重保序
    seen = set()
    out = []
    for s in snippets:
        key = s.lower()
        if key not in seen:
            out.append(s)
            seen.add(key)
        if len(out) >= max_snippets:
            break
    return out


def _extract_section_anchors_from_value(value: str):
    """0324修改：从提取结果文本中提取章节锚点，如 调查范围（2.2）、目标章节: 结论"""
    txt = _normalize_search_text(value)
    if not txt:
        return []

    anchors = []

    # 形式1：章节名（2.2）/ 章节名(2.2)
    pattern = r"([一-龥A-Za-z0-9、，,《》\-—]{2,40})[（(]\s*((?:\d+\.)*\d+)\s*[）)]"
    for m in re.finditer(pattern, txt):
        title = m.group(1).strip(" ：:，,;；。")
        code = m.group(2).strip()
        if title:
            anchors.append({"title": title, "code": code})

    # 形式2：目标章节: 范围, 结论 / 目标章节：范围
    m = re.search(r"目标章节\s*[:：]\s*([^)）]+)", txt)
    if not m:
        m = re.search(r"目标章节\s*[:：]\s*([^。；;\n]+)", txt)
    if m:
        raw = m.group(1)
        for part in re.split(r"[、,，/\s]+", raw):
            part = part.strip(" ：:，,;；。")
            if part:
                anchors.append({"title": part, "code": None})

    # 去重
    seen = set()
    out = []
    for a in anchors:
        key = (a.get("title") or "", a.get("code") or "")
        if key not in seen:
            out.append(a)
            seen.add(key)
    return out


def _iter_section_candidates(json_content):
    """0324修改：遍历 parsed.json 中的小节候选，提取标题/编号/内容/page_idx"""
    if not isinstance(json_content, list):
        return
    for idx, sec in enumerate(json_content):
        if not isinstance(sec, dict):
            continue
        title = str(sec.get("title") or "").strip()
        code = str(sec.get("section_code") or "").strip()
        content = str(sec.get("content") or "")
        page_idx = get_page_idx_from_object(sec)
        yield {
            "idx": idx,
            "title": title,
            "code": code,
            "content": content,
            "page_idx": page_idx,
            "raw": sec,
        }


def _anchor_title_variants(title: str):
    """0324修改：章节别名/包含匹配，降低“概述/摘要”误命中"""
    t = str(title or "").strip()
    variants = [t] if t else []
    alias_map = {
        "范围": ["范围", "调查范围"],
        "结论": ["结论", "调查结论", "土壤调查结论", "地下水调查结论"],
        "规划": ["规划", "场地利用规划", "地块利用规划"],
        "概况": ["概况", "概述", "项目概况"],
        "概述": ["概述", "前言", "摘要"],
        "现状": ["现状", "地块现状", "地块现状情况"],
        "历史": ["历史", "地块历史", "地块历史变迁"],
    }
    for k, vals in alias_map.items():
        if t == k or k in t:
            variants.extend(vals)
    out, seen = [], set()
    for v in variants:
        if v and v not in seen:
            out.append(v)
            seen.add(v)
    return out


def _find_page_idx_by_anchor(json_content, value: str, rule_sections=None):
    """0324修改：优先按提取结果中的章节锚点定位页码；对概述/摘要/前言降权"""
    anchors = _extract_section_anchors_from_value(value)
    if rule_sections:
        for s in rule_sections:
            anchors.append({"title": str(s), "code": None})

    candidates = list(_iter_section_candidates(json_content))
    best = None
    best_score = -10**9

    for sec in candidates:
        page_idx = sec["page_idx"]
        if page_idx == PAGE_IDX_UNKNOWN:
            continue
        title = _normalize_search_text(sec["title"])
        code = sec["code"]
        content = _normalize_search_text(sec["content"])

        for a in anchors:
            score = 0
            atitle = _normalize_search_text(a.get("title") or "")
            acode = str(a.get("code") or "").strip()

            if acode and code == acode:
                score += 120
            if atitle:
                variants = _anchor_title_variants(atitle)
                for v in variants:
                    vnorm = _normalize_search_text(v)
                    if vnorm and title == vnorm:
                        score = max(score, 100)
                    elif vnorm and (vnorm in title or title in vnorm):
                        score = max(score, 80)
                    elif vnorm and vnorm in content[:400]:
                        score = max(score, 50)

            if any(x in title for x in ["概述", "摘要", "前言"]):
                score -= 35

            if score > best_score:
                best_score = score
                best = sec

    if best is not None and best_score >= 50:
        return best["page_idx"]
    return None


def _find_page_idx_by_value_snippets(json_content, value: str, current_page_idx=None):
    """0324修改：按值片段在 parsed.json 中搜索，更倾向命中正文证据页；支持下一页微调"""
    snippets = _build_value_search_snippets(value)
    if not snippets:
        return None

    candidates = list(_iter_section_candidates(json_content))
    scored = []
    for sec in candidates:
        page_idx = sec["page_idx"]
        if page_idx == PAGE_IDX_UNKNOWN:
            continue
        title = _normalize_search_text(sec["title"])
        content = _normalize_search_text(sec["content"])
        score = 0
        hit_count = 0
        for snip in snippets:
            s = _normalize_search_text(snip)
            if not s:
                continue
            if s in content:
                score += 35
                hit_count += 1
            elif len(s) >= 8:
                s2 = s[: min(20, len(s))]
                if s2 and s2 in content:
                    score += 18
                    hit_count += 1
        if hit_count:
            if any(x in title for x in ["概述", "摘要", "前言"]):
                score -= 20
            scored.append((score, sec))

    if not scored:
        return None

    scored.sort(key=lambda x: x[0], reverse=True)
    best = scored[0][1]
    best_page = best["page_idx"]

    for score, sec in scored[1:4]:
        if isinstance(sec["page_idx"], int) and sec["page_idx"] == best_page + 1 and score >= scored[0][0] - 5:
            return sec["page_idx"]

    return best_page


def _resolve_field_page_idx(json_content, field_name: str, value, current_page_idx, rule_sections=None):
    """0324修改：统一的字段页码决策：无效值 -> -1；锚点 -> 值片段 -> 原页码"""
    if not _is_meaningful_extracted_value(value):
        return PAGE_IDX_UNKNOWN

    by_anchor = _find_page_idx_by_anchor(json_content, str(value), rule_sections=rule_sections)
    if by_anchor is not None and by_anchor != PAGE_IDX_UNKNOWN:
        return by_anchor

    by_snip = _find_page_idx_by_value_snippets(json_content, str(value), current_page_idx=current_page_idx)
    if by_snip is not None and by_snip != PAGE_IDX_UNKNOWN:
        return by_snip

    return current_page_idx if current_page_idx is not None else PAGE_IDX_UNKNOWN


def extract_content_from_json(json_content, extraction_rules, folder_name, filename):
    """从JSON内容中提取信息 - 优化版，使用统一文本块组织，并添加page_idx到外部结构"""
    try:
        # 获取预期的字段名
        expected_fields = []
        field_to_rule_map = {}  # 字段名到规则的映射
        for rule in extraction_rules:
            field_name = rule.get('name', '')
            if field_name:
                expected_fields.append(field_name)
                field_to_rule_map[field_name] = rule

        print(f"\n=== 开始内容提取，期望字段: {expected_fields} ===")

        # 1. 统一组织文本块阶段（返回包含内容和page_idx的结构）
        section_contents, section_page_idx = smart_locate_in_json_batch(json_content, extraction_rules, is_markdown=False)
        
        print(f"\n=== 章节页面索引信息 ===")
        for section, data in section_contents.items():
            if isinstance(data, dict):
                page_idx = data.get('page_idx', PAGE_IDX_UNKNOWN)
                print(f"  章节 '{section}' -> page_idx: {page_idx}")
            else:
                print(f"  章节 '{section}' -> page_idx: {section_page_idx.get(section, PAGE_IDX_UNKNOWN)}")
        
        # 构建字段到页面索引的映射
        page_idx_map = {}
        
        # 方法1: 通过规则中的sections关联
        for field_name, rule in field_to_rule_map.items():
            sections = rule.get('sections', [])
            if sections:
                # 尝试每个章节
                for section in sections:
                    if section in section_page_idx:
                        page_idx_map[field_name] = section_page_idx[section]
                        print(f"  字段 '{field_name}' 通过章节 '{section}' 关联到 page_idx: {page_idx_map[field_name]}")
                        break
                else:
                    # 没有匹配的章节，使用默认值
                    page_idx_map[field_name] = PAGE_IDX_UNKNOWN
                    print(f"  字段 '{field_name}' 无匹配章节，设为默认值: {PAGE_IDX_UNKNOWN}")
            else:
                # 没有指定章节，尝试用字段名匹配章节名
                for section, data in section_contents.items():
                    if section in field_name or field_name in section:
                        if isinstance(data, dict):
                            page_idx_map[field_name] = data.get('page_idx', PAGE_IDX_UNKNOWN)
                        else:
                            page_idx_map[field_name] = section_page_idx.get(section, PAGE_IDX_UNKNOWN)
                        print(f"  字段 '{field_name}' 通过模糊匹配章节 '{section}' 关联到 page_idx: {page_idx_map[field_name]}")
                        break
                else:
                    page_idx_map[field_name] = PAGE_IDX_UNKNOWN
                    print(f"  字段 '{field_name}' 无匹配章节，设为默认值: {PAGE_IDX_UNKNOWN}")
        
        # 方法2: 如果还有字段没有page_idx，尝试用section_contents中的第一个值
        if section_contents and not page_idx_map:
            first_section = next(iter(section_contents))
            first_data = section_contents[first_section]
            if isinstance(first_data, dict):
                first_page_idx = first_data.get('page_idx', PAGE_IDX_UNKNOWN)
            else:
                first_page_idx = section_page_idx.get(first_section, PAGE_IDX_UNKNOWN)
            for field_name in expected_fields:
                if field_name not in page_idx_map:
                    page_idx_map[field_name] = first_page_idx
                    print(f"  字段 '{field_name}' 使用第一个章节的page_idx: {first_page_idx}")
        
        # 确保所有期望字段都有page_idx
        for field_name in expected_fields:
            if field_name not in page_idx_map:
                page_idx_map[field_name] = PAGE_IDX_UNKNOWN
                print(f"  字段 '{field_name}' 未找到任何匹配，设为默认值: {PAGE_IDX_UNKNOWN}")
        
        # 2. 组织文本块（同时返回page_idx信息）
        organized_contexts, organized_page_idx = organize_context_for_extraction(section_contents, similarity_threshold=SIM_THRESHOLD)
        
        # 3. 构建提示词，使用组织好的文本块
        user_content = build_extraction_prompt_from_organized_context(
            organized_contexts, extraction_rules, filename, is_markdown=False
        )

        # 4. 调用大模型进行内容提取
        result_text = deepseek_generate_stream(user_content)

        if not result_text:
            print(f"❌ API调用失败，返回'未找到'结果")
            not_found_result = {}
            for field in expected_fields:
                not_found_result[field] = "未找到"
                if field not in page_idx_map:
                    page_idx_map[field] = PAGE_IDX_UNKNOWN
            return [not_found_result], page_idx_map

        # 5. 解析响应文本
        extracted_data = []

        try:
            # 查找JSON部分
            json_match = re.search(r'\[\s*\{.*\}\s*\]', result_text, re.DOTALL)

            if json_match:
                json_str = json_match.group(0)
                print(f"找到JSON字符串，长度: {len(json_str)}")

                # 清理JSON字符串
                json_str = json_str.replace('\n', ' ').replace('\r', '')

                # 确保所有字段都有值
                for field in expected_fields:
                    if f'"{field}": ""' in json_str:
                        json_str = json_str.replace(f'"{field}": ""', f'"{field}": "未找到"')

                # 解析JSON
                parsed_data = json.loads(json_str)

                if isinstance(parsed_data, list) and parsed_data:
                    extracted_data = parsed_data
                    print(f"✅ 成功解析JSON，找到 {len(parsed_data[0])} 个字段")
                else:
                    raise ValueError("解析的JSON不是有效格式")
            else:
                # 尝试直接提取字段
                api_extracted = extract_api_response_fields(result_text, expected_fields)
                if api_extracted:
                    for field in expected_fields:
                        if field not in api_extracted:
                            api_extracted[field] = "未找到"
                    extracted_data = [api_extracted]
                    print(f"⚠️ 未找到完整JSON，但提取并补全了 {len(api_extracted)} 个字段")
                else:
                    raise ValueError("无法从响应中提取任何字段")

        except Exception as e:
            print(f"❌ 解析响应失败: {e}")
            not_found_result = {}
            for field in expected_fields:
                not_found_result[field] = "未找到"
            extracted_data = [not_found_result]

        # 6. 最终验证和补全
        final_result = {}
        
        for field in expected_fields:
            if extracted_data and isinstance(extracted_data[0], dict):
                value = extracted_data[0].get(field, "未找到")
                if not value or (isinstance(value, str) and value.strip() == ""):
                    value = "未找到"
                final_result[field] = value
            else:
                final_result[field] = "未找到"

        extracted_data = [final_result]

        # 0324修改：基于最终提取结果，重新修正字段页码
        for field in expected_fields:
            value = final_result.get(field)
            rule_sections = []
            if field in field_to_rule_map and isinstance(field_to_rule_map[field], dict):
                rule_sections = field_to_rule_map[field].get('sections', []) or []
            current_page_idx = page_idx_map.get(field, PAGE_IDX_UNKNOWN)
            resolved_page_idx = _resolve_field_page_idx(
                json_content, field, value, current_page_idx, rule_sections=rule_sections
            )
            page_idx_map[field] = resolved_page_idx
            print(f"  0324修改：字段 '{field}' 页码修正 -> {resolved_page_idx}")

        # 统计提取结果
        print(f"\n=== 最终提取结果 ===")
        extracted_count = 0
        not_found_count = 0
        for field, value in final_result.items():
            page_idx = page_idx_map.get(field, PAGE_IDX_UNKNOWN)
            if value and value != "未找到":
                print(f"  ✅ {field}: {value[:100]} (page_idx: {page_idx})")
                extracted_count += 1
            else:
                print(f"  ⭕ {field}: 未找到 (page_idx: {page_idx})")
                not_found_count += 1

        return extracted_data, page_idx_map

    except Exception as e:
        print(f"❌ JSON内容提取过程中出错: {e}")
        import traceback
        print(f"详细错误:\n{traceback.format_exc()}")

        error_data = {}
        error_page_idx = {}
        for rule in extraction_rules:
            field_name = rule.get('name', '')
            if field_name:
                error_data[field_name] = "未找到"
                error_page_idx[field_name] = PAGE_IDX_UNKNOWN
        return [error_data], error_page_idx


def extract_single_json_file(json_file_path, extraction_rules, output_dir, result_collection_file_path, task_id=None):
    """处理单个JSON文件 - 生成包含元信息和page_idx的提取结果，page_idx保存在外部结构中"""
    try:
        folder_name = os.path.basename(os.path.dirname(json_file_path))
        filename = os.path.basename(json_file_path)
        rule_identifier = generate_rule_identifier(extraction_rules)

        # 读取JSON内容
        with open(json_file_path, "r", encoding="utf-8") as f:
            json_content = json.load(f)

        # 使用优化版的JSON提取函数
        extracted_data, page_idx_map = extract_content_from_json(
            json_content,
            extraction_rules,
            folder_name,
            filename
        )

        # 创建包含元信息的完整结果
        full_result = create_extraction_result_with_metadata(
            extracted_data,
            extraction_rules,
            folder_name,
            filename,
            rule_identifier,
            markdown_content=json.dumps(json_content, ensure_ascii=False),
            file_path=json_file_path,
            page_idx_map=page_idx_map
        )

        full_result["extraction_metadata"]["extraction_mode"] = "JSON智能提取"
        full_result["extraction_metadata"]["file_type"] = "JSON"
        full_result["extraction_metadata"]["search_strategy"] = "先收集所有章节，统一去重定位"

        # 保存结果
        base_filename = os.path.splitext(filename)[0]
        os.makedirs(output_dir, exist_ok=True)

        # 保存JSON结果（包含元信息）
        json_output_filename = f"{base_filename}_extracted.json"
        json_output_path = os.path.join(output_dir, json_output_filename)

        with open(json_output_path, "w", encoding="utf-8") as f:
            json.dump(full_result, f, ensure_ascii=False, indent=2)
        result_collection_json_manager = ResultCollectionJsonFileManager()
        # if not result_collection_file_path.exists():
        # import json
        # from pathlib import Path

        # 从 tasks.json 获取 scene_id
        tasks_file = Path("data/tasks/tasks.json")
        scene_id = "default"
        if tasks_file.exists():
            try:
                with open(tasks_file, "r", encoding="utf-8") as f:
                    tasks = json.load(f)
                    for task in tasks:
                        if str(task.get("id")) == task_id:
                            scene_id = str(task.get("sceneId") or "default").strip()
                            break
            except Exception:
                pass

        result_collection_file_path = Path(result_collection_file_path).parent / f"result_collection_{scene_id}.json"

        if not result_collection_file_path.exists():
            result_collection_file_path.write_text("[]", encoding="utf-8")

        with result_collection_json_manager.file_lock:
            collections = result_collection_json_manager.load_json_without_lock(result_collection_file_path)
            is_extracted = False
            for collection in collections:
                if filename == collection['filename']:
                    collection["foldername"] = folder_name
                    collection["filename"] = filename
                    collection["extraction_results"] = extracted_data
                    collection["extraction_rules"] = [item['name'] for item in extraction_rules]
                    collection["update_time"] = int(time.time())
                    collection["is_extracted"] = True
                    is_extracted = True
            if not is_extracted:
                # 创建新任务
                new_collection = {
                    "foldername": folder_name,
                    "filename": filename,
                    "extraction_results": extracted_data,
                    "extraction_rules": [item['name'] for item in extraction_rules],
                    "judge_results": {},
                    "is_extracted":True,
                    "is_judged":False,
                    "is_valid":"unknown",
                    "is_reviewed":False,
                    "is_correct":"unknown",
                    "update_time":int(time.time())
                }
                collections.append(new_collection)
            # 保存任务
            result_collection_json_manager.save_json_without_lock(result_collection_file_path, collections)
        # 生成并保存Markdown结果
        md_output_filename = f"{base_filename}_extracted.md"
        md_output_path = os.path.join(output_dir, md_output_filename)

        markdown_content_result = json_to_markdown(
            extracted_data,
            extraction_rules,
            folder_name,
            filename,
            rule_identifier,
            page_idx_map=page_idx_map
        )

        markdown_content_result = markdown_content_result.replace(
            "提取模式: Markdown智能提取",
            "提取模式: JSON智能提取（统一文本块优化）"
        )

        with open(md_output_path, "w", encoding="utf-8") as f:
            f.write(markdown_content_result)

        return {
            "success": True,
            "file_path": json_file_path,
            "json_output_path": json_output_path,
            "md_output_path": md_output_path,
            "extracted_data": extracted_data,
            "page_idx_map": page_idx_map,
            "full_result": full_result,
            "markdown_content": markdown_content_result,
            "rule_identifier": rule_identifier
        }

    except Exception as e:
        print(f"❌ 处理JSON文件时出错: {e}")
        import traceback
        print(f"详细错误:\n{traceback.format_exc()}")
        return {
            "success": False,
            "file_path": json_file_path,
            "error": str(e)
        }


def json_to_markdown(extracted_data, extraction_rules, folder_name, filename, rule_identifier, page_idx_map=None):
    """将提取的JSON数据转换为Markdown格式，包含page_idx信息"""
    if page_idx_map is None:
        page_idx_map = {}
        
    markdown_content = f"""# 内容提取结果

## 文件信息
- **源文件**: {filename}
- **源文件夹**: {folder_name}
- **提取时间**: {time.strftime("%Y-%m-%d %H:%M:%S")}
- **提取规则数**: {len(extraction_rules)} 条
- **规则标识**: {rule_identifier}
- **提取模式**: 统一文本块优化提取

## 提取规则详情
"""

    # 添加提取规则信息
    for i, rule in enumerate(extraction_rules, 1):
        field_name = rule.get('name', f'字段{i}')
        prompt_text = rule.get('prompt', '')
        sections = rule.get('sections', [])

        markdown_content += f"\n### {i}. {field_name}\n"
        if prompt_text:
            markdown_content += f"- **提取要求**: {prompt_text}\n"
        if sections:
            markdown_content += f"- **目标章节**: {', '.join(sections)}\n"

    markdown_content += "\n## 提取内容\n\n"

    # 添加提取的具体内容
    if isinstance(extracted_data, list) and extracted_data:
        item = extracted_data[0]
        if isinstance(item, dict):
            markdown_content += "| 字段 | 页面索引 | 提取结果 | 状态 |\n|------|----------|----------|------|\n"

            for rule in extraction_rules:
                field_name = rule.get('name', '')
                if field_name and field_name in item:
                    value = item[field_name]
                    page_idx = page_idx_map.get(field_name, PAGE_IDX_UNKNOWN)
                    
                    page_idx_display = "未知" if page_idx == -1 else f"第{page_idx}页" if page_idx >= 0 else f"第{page_idx+1}页"
                    
                    if value and value != "未找到":
                        display_value = str(value).replace('|', '\\|')
                        if len(display_value) > 200:
                            display_value = display_value[:200] + "..."
                        markdown_content += f"| {field_name} | {page_idx_display} | {display_value} | ✅ 已提取 |\n"
                    else:
                        markdown_content += f"| {field_name} | {page_idx_display} | 未找到 | ❌ 未找到 |\n"
        else:
            markdown_content += f"提取数据格式异常: {type(item)}\n"
    else:
        markdown_content += "⚠️ 未提取到任何数据\n"

    # 统计信息
    extracted_count = 0
    not_found_count = 0

    if isinstance(extracted_data, list) and extracted_data and isinstance(extracted_data[0], dict):
        for field in extraction_rules:
            field_name = field.get('name', '')
            if field_name and field_name in extracted_data[0]:
                value = extracted_data[0][field_name]
                if value and value != "未找到":
                    extracted_count += 1
                else:
                    not_found_count += 1

    success_rate = extracted_count / len(extraction_rules) * 100 if extraction_rules else 0

    markdown_content += f"\n## 提取统计\n"
    markdown_content += f"- **提取完成时间**: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
    markdown_content += f"- **规则标识符**: {rule_identifier}\n"
    markdown_content += f"- **总字段数**: {len(extraction_rules)}\n"
    markdown_content += f"- **成功提取**: {extracted_count} 个字段\n"
    markdown_content += f"- **未找到**: {not_found_count} 个字段\n"
    markdown_content += f"- **成功率**: {extracted_count}/{len(extraction_rules)} ({success_rate:.1f}%)\n"
    markdown_content += f"- **提取模式**: 统一文本块优化提取（带外部页面索引，避免重复，减少Token使用）\n"
    
    # 添加页面索引信息表
    if page_idx_map:
        markdown_content += f"\n## 页面索引信息\n\n"
        markdown_content += "| 字段 | 页面索引 |\n|------|----------|\n"
        for field_name, page_idx in page_idx_map.items():
            page_idx_display = "未知" if page_idx == -1 else f"第{page_idx}页" if page_idx >= 0 else f"第{page_idx+1}页"
            markdown_content += f"| {field_name} | {page_idx_display} |\n"

    return markdown_content


def create_extraction_result_with_metadata(extracted_data, extraction_rules, folder_name, filename, rule_identifier,
                                           markdown_content=None, file_path=None, page_idx_map=None):
    """创建包含元信息的完整提取结果，page_idx保存在外部结构中"""
    if page_idx_map is None:
        page_idx_map = {}
        
    if not extracted_data or not isinstance(extracted_data, list):
        base_data = [{}]
    else:
        base_data = extracted_data

    # 确保所有字段都有值（未找到的标记为"未找到"）
    if base_data and base_data[0]:
        for rule in extraction_rules:
            field_name = rule.get('name', '')
            if field_name:
                if field_name not in base_data[0]:
                    base_data[0][field_name] = "未找到"
                elif field_name in base_data[0]:
                    value = base_data[0][field_name]
                    if not value or (isinstance(value, str) and value.strip() == ""):
                        base_data[0][field_name] = "未找到"

    # 构建完整的元信息
    metadata = {
        "extraction_metadata": {
            "source_file": filename,
            "source_folder": folder_name,
            "extraction_time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "rule_identifier": rule_identifier,
            "extraction_mode": "统一文本块优化提取（带外部页面索引）",
            "total_fields": len(extraction_rules),
            "content_length": len(markdown_content) if markdown_content else 0,
            "page_idx_available": True,  # 标记支持页面索引
            "page_idx_unknown_value": PAGE_IDX_UNKNOWN,  # 未知页面索引的默认值
            "extraction_rules": []
        },
        "extraction_results": base_data,
        "page_idx": page_idx_map,  # 在外部结构中保存页面索引
        "is_correct": "unknown" #最终判别是否正确
    }

    # 添加提取规则详情
    for i, rule in enumerate(extraction_rules, 1):
        rule_info = {
            "index": i,
            "field_name": rule.get('name', f'字段{i}'),
            "prompt": rule.get('prompt', ''),
            "sections": rule.get('sections', []),
            "description": f"提取规则 {i}: {rule.get('name', '未命名字段')}"
        }
        metadata["extraction_metadata"]["extraction_rules"].append(rule_info)

    # 统计提取结果
    if base_data and base_data[0]:
        extracted_count = 0
        for field in extraction_rules:
            field_name = field.get('name', '')
            if field_name and field_name in base_data[0]:
                value = base_data[0][field_name]
                if value and value != "未找到":
                    extracted_count += 1
        not_found_count = len(extraction_rules) - extracted_count
        metadata["extraction_metadata"]["extraction_statistics"] = {
            "fields_with_content": extracted_count,
            "not_found_fields": not_found_count,
            "success_rate": f"{extracted_count}/{len(extraction_rules)} ({extracted_count / len(extraction_rules) * 100:.1f}%)"
        }

    return metadata


def _block_has_tables_or_images(block: dict) -> bool:
    try:
        if isinstance(block, dict):
            if isinstance(block.get("tables"), list) and len(block.get("tables")) > 0:
                return True
            if isinstance(block.get("images"), list) and len(block.get("images")) > 0:
                return True
        if isinstance(block, str) and ('"tables"' in block or '"images"' in block or "<table" in block.lower()):
            return True
    except Exception:
        pass
    return False


def _is_strong_toc_block(block: dict) -> bool:
    """目录/页码型块：例如 '一、...2 (二)...4' 这类。"""
    try:
        if not isinstance(block, dict):
            return False
        title = str(block.get("title","") or "")
        content = block.get("content")
        if not isinstance(content, str) or not content.strip():
            return False
        c = content.strip()
        if len(c) < 80:
            return False
        lines = [x.strip() for x in c.splitlines() if x.strip()]
        hit = 0
        for ln in lines[:30]:
            if re.search(r"[\.\s]\d{1,3}$", ln) or re.search(r"\.{1,2}\s*\d{1,3}$", ln):
                hit += 1
        if hit >= 3 and ("." in title or "目录" in title):
            return True
        if hit >= 5:
            return True
        return False
    except Exception:
        return False


def _table_text(tb: dict) -> str:
    """把 table_caption(list/str) + body 组合成可检索文本。"""
    try:
        if not isinstance(tb, dict):
            return str(tb)
        cap = tb.get("table_caption", "")
        if isinstance(cap, list):
            cap_s = " ".join([str(x) for x in cap if x])
        else:
            cap_s = str(cap or "")
        body = str(tb.get("table_body","") or "")
        return (cap_s + "\n" + body)[:20000]
    except Exception:
        return str(tb)


def _clip_text_by_keywords(text: str, keywords, window_chars: int = 320, max_snippets: int = 8) -> str:
    """按关键词截取若干窗口片段，降低无关文本占比。"""
    if not text:
        return ""
    if not keywords:
        return text
    lower = text.lower()
    hits = []
    for kw in keywords:
        if not kw:
            continue
        kw_l = kw.lower()
        start = 0
        while True:
            idx = lower.find(kw_l, start)
            if idx == -1:
                break
            s = max(0, idx - window_chars)
            e = min(len(text), idx + len(kw) + window_chars)
            hits.append((s, e))
            start = idx + len(kw_l)
    if not hits:
        return text
    hits.sort()
    merged = []
    for s, e in hits:
        if not merged or s > merged[-1][1]:
            merged.append([s, e])
        else:
            merged[-1][1] = max(merged[-1][1], e)
    snippets = []
    for s, e in merged[:max_snippets]:
        snippet = text[s:e].strip()
        if snippet:
            snippets.append(snippet)
    return "\n\n---\n\n".join(snippets) if snippets else text


def _clip_section_content(section: str, text: str) -> str:
    """对非核心章节做关键词窗口裁剪，减少token浪费。核心章节默认不裁剪。"""
    if not text:
        return ""
    sec = (section or "").strip()
    core = {"现状", "历史", "踏勘", "现场"}
    if sec in core:
        return text

    keyword_map = {
        "许可证": ["许可证", "规划许可证", "施工许可证", "建设用地规划许可证", "建设工程规划许可证", "证号", "编号", "发证", "地字", "建字", "施工许", "许可", "批复", "批准"],
        "概述": ["面积", "㎡", "m2", "亩", "总面积", "调查面积", "占地", "规划为", "用地", "A33", "预规划条件"],
        "概况": ["面积", "㎡", "m2", "亩", "总面积", "调查面积", "占地", "规划为", "用地", "A33"],
        "规划": ["规划", "用地", "A33", "用途", "预规划条件", "规划为", "第一类用地", "地下水利用规划"],
        "结论": ["结论", "初步认为", "无需", "需要进一步", "超标", "未超出", "筛选值", "可接受", "污染", "潜在污染", "污染源", "建议", "验证性采样"],
        "重点行业": ["重点行业", "重点监管", "土壤污染重点", "不存在", "属于", "重点污染", "监管单位"],
    }
    kws = keyword_map.get(sec)
    if not kws:
        if len(text) <= 6000:
            return text
        return text[:3500].rstrip() + "\n\n---\n\n" + text[-1500:].lstrip()

    clipped = _clip_text_by_keywords(text, kws, window_chars=360, max_snippets=10)
    if len(clipped) > 9000:
        clipped = clipped[:9000]
    return clipped


def organize_context_for_extraction(section_contents: dict, similarity_threshold: float = None):
    """
    修复4：对含 tables/images 的块 **不做相似度合并**，避免丢表。
    修改：返回合并后的内容和对应的page_idx信息
    """
    if similarity_threshold is None:
        similarity_threshold = SIM_THRESHOLD

    merged_contents = {}
    merged_page_idx = {}  # 新增：保存合并后的page_idx

    for section, data in section_contents.items():
        # 提取实际内容和page_idx
        if isinstance(data, dict):
            content_text = data.get('content', '')
            page_idx = data.get('page_idx', PAGE_IDX_UNKNOWN)
        else:
            content_text = data
            page_idx = PAGE_IDX_UNKNOWN

        # 如果当前 section 的内容本身含表，直接保留，不与任何其他 section 合并
        if isinstance(content_text, str) and ('"tables"' in content_text or '"images"' in content_text or "<table" in content_text.lower()):
            merged_contents[section] = content_text
            merged_page_idx[section] = page_idx
            continue

        # 否则走原合并逻辑，但遇到已存在的含表块，也不合并进去
        merged = False
        for existing_section in list(merged_contents.keys()):
            existing_content = merged_contents[existing_section]
            if isinstance(existing_content, str) and ('"tables"' in existing_content or '"images"' in existing_content or "<table" in existing_content.lower()):
                continue
            sim = calculate_text_similarity(str(existing_content), str(content_text))
            if sim >= similarity_threshold:
                merged_contents[existing_section] = str(existing_content) + "\n\n" + str(content_text)
                # 合并时保留最小的page_idx（取更早出现的页）
                merged_page_idx[existing_section] = min(
                    merged_page_idx.get(existing_section, PAGE_IDX_UNKNOWN),
                    page_idx
                )
                print(f"    🔁 章节 '{section}' 与已有文本块高相似(sim≥{similarity_threshold:.2f})，已合并为: '{existing_section}｜{section}'")
                merged = True
                break

        if not merged:
            merged_contents[section] = content_text
            merged_page_idx[section] = page_idx

    return merged_contents, merged_page_idx


def build_extraction_prompt_from_organized_context(organized_contexts, extraction_rules, filename, is_markdown=False):
    """使用组织好的文本块构建提取提示词（通用版：按字段相关性挑选上下文，并做总长度预算控制）"""
    import json

    PROMPT_CHAR_BUDGET = 32000
    MIN_CONTEXT_BUDGET = 8000
    MAX_SECTION_CHARS_TEXT = 6000
    MAX_SECTION_CHARS_STRUCT = 12000

    def _has_structured(text: str) -> bool:
        if not text:
            return False
        return any(x in text for x in ('"tables"', '<table', '"images"', '"figures"'))

    def _rule_anchors(rule: dict) -> list:
        try:
            return _extract_anchor_keywords(rule.get('prompt', ''), rule.get('name', ''), '', max_kws=10)
        except Exception:
            name = str(rule.get('name', '') or '')
            return re.findall(r'[\u4e00-\u9fa5]{2,12}', name)[:10]

    rules = extraction_rules or []
    rule_meta = []
    all_anchors = []
    for r in rules:
        secs = r.get('sections', []) or []
        if not isinstance(secs, list):
            secs = []
        anchors = _rule_anchors(r)
        rule_meta.append({'rule': r, 'sections': secs, 'anchors': anchors})
        for a in anchors:
            if a and a not in all_anchors:
                all_anchors.append(a)

    priority = {
        "排污单位基本情况": 0,
        "生产工艺": 1,
        "主要产品及产能": 2,
        "主要原辅材料及燃料": 3,
        "产排污节点": 4,
        "污染物及污染治理设施": 5,
        "排放口": 6,
        "执行标准": 7,
        "环境管理": 8,
        "监测": 9,
        "台账": 10,
    }

    def _section_priority(section_key: str) -> int:
        if not section_key:
            return 50
        parts = [p.strip() for p in str(section_key).split("｜") if p.strip()]
        if not parts:
            return 50
        return min(priority.get(p, 50) for p in parts)

    def _section_score(section_key: str, content: str) -> int:
        if not content:
            return -999

        parts = [p.strip() for p in str(section_key).split("｜") if p.strip()]
        related_anchors = []
        for rm in rule_meta:
            if rm['sections'] and any(sec in parts for sec in rm['sections']):
                for a in rm['anchors']:
                    if a and a not in related_anchors:
                        related_anchors.append(a)
        if not related_anchors:
            related_anchors = all_anchors

        score = 0
        for a in related_anchors[:20]:
            if not a:
                continue
            if a in content:
                score += 12

        if _has_structured(content):
            score += 10

        try:
            if _is_toc_like_content(content):
                score -= 20
        except Exception:
            pass

        score -= _section_priority(section_key)

        return score

    organized_contexts = organized_contexts or {}
    items = list(organized_contexts.items())

    def _best_key_for_section(sec_name: str):
        best = None
        best_score = -10**9
        for k, v in items:
            parts = [p.strip() for p in str(k).split("｜") if p.strip()]
            if sec_name in parts:
                sc = _section_score(k, v)
                if sc > best_score:
                    best = (k, v, sc)
                    best_score = sc
        return best

    forced_keys = []
    seen_k = set()
    for rm in rule_meta:
        for sec in rm['sections']:
            if not sec:
                continue
            cand = _best_key_for_section(sec)
            if cand and cand[0] not in seen_k:
                forced_keys.append(cand)
                seen_k.add(cand[0])

    scored = []
    for k, v in items:
        sc = _section_score(k, v)
        scored.append((k, v, sc))
    scored.sort(key=lambda x: (-x[2], _section_priority(x[0]), x[0]))

    rule_details = []
    field_names = []
    for i, rule in enumerate(extraction_rules, 1):
        field_name = rule.get('name', f'字段{i}')
        field_names.append(f"{i}.{field_name}")
        prompt_text = rule.get('prompt', '')
        sections = rule.get('sections', [])

        detail = f"{i}. 【{field_name}】"
        if prompt_text:
            detail += f" - 需要提取: {prompt_text}"
        if sections:
            detail += f" (目标章节: {', '.join(sections)})"
        rule_details.append(detail)

    rule_text = '\n'.join(rule_details)
    field_names_str = ','.join(field_names)

    fixed_overhead = 2200 + len(rule_text) + len(field_names_str) + len(str(filename or ''))
    context_budget = max(MIN_CONTEXT_BUDGET, PROMPT_CHAR_BUDGET - fixed_overhead)
    if context_budget > 24000:
        context_budget = 24000

    selected = []
    total_ctx = 0

    def _append_section(k: str, v: str, sc: int, force: bool = False):
        nonlocal total_ctx
        if k in {x[0] for x in selected}:
            return
        per_max = MAX_SECTION_CHARS_STRUCT if _has_structured(v) else MAX_SECTION_CHARS_TEXT
        v2 = v if len(v) <= per_max else smart_truncate(v, per_max)
        block = f"【{k}】：\n{v2}"
        add_len = len(block) + 10
        if total_ctx + add_len <= context_budget or force:
            if total_ctx + add_len > context_budget and force:
                remain = max(500, context_budget - total_ctx - (len(f"【{k}】：\n") + 10))
                v3 = v2 if len(v2) <= remain else smart_truncate(v2, remain)
                block = f"【{k}】：\n{v3}"
                add_len = len(block) + 10
            selected.append((k, block, sc))
            total_ctx += add_len

    for k, v, sc in forced_keys:
        _append_section(k, v, sc, force=True)

    for k, v, sc in scored:
        if total_ctx >= context_budget:
            break
        _append_section(k, v, sc, force=False)

    content_preview = "\n\n---\n\n".join([b for _, b, _ in selected])

    try:
        print(f"[PROMPT_BUDGET] fixed_overhead={fixed_overhead} context_budget={context_budget} selected_sections={len(selected)} ctx_chars={len(content_preview)} total_est={fixed_overhead + len(content_preview)}")
        if len(content_preview) < 2000:
            print(f"[PROMPT_BUDGET] ⚠️ 上下文过短，可能命中不足；建议检查章节定位/锚点关键词")
    except Exception:
        pass

    file_type = "Markdown文档" if is_markdown else "JSON文档"
    user_content = f"""
我需要从以下{file_type}内容中提取指定字段的信息。

需要提取的字段（每个字段都必须有值，找不到就写\"未找到\"）：
{field_names_str}

详细规则：
{rule_text}

文档内容（已按章节组织，且已按字段相关性筛选/截断）：
{content_preview}

重要要求：
1. 只从提供的原文中提取信息，不要编造
2. 每个字段都必须输出，如果找不到相关信息，请写\"未找到\"
3. 输出必须是标准的JSON数组格式
4. 不要输出任何额外的文字说明，只输出JSON
5. 尽量提取完整信息，不要遗漏关键内容

请严格按照以下JSON格式输出：
[
  {{
    \"字段1\": \"值1\", 
    \"字段2\": \"值2\", 
    ...
  }}
]

现在请提取并输出完整JSON："""

    return user_content


def fix_mojibake(text: str) -> str:
    """修复 UTF-8 被错误当成 latin1 解码后的乱码"""
    try:
        return text.encode("latin1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def strip_eos(text: str) -> str:
    """清理模型输出中的 EOS 特殊标记"""
    if not text:
        return text

    eos_tokens = [
        "<|end_of_sentence|>",
        "</s>",
        "<eos>"
    ]

    for eos in eos_tokens:
        text = text.replace(eos, "")

    return text.strip()


def generate_stream(
        prompt: str,
        max_new_tokens: int = 1500,
        timeout: int = 240
) -> str:
    """
    流式调用API
    """
    api_url = "http://10.100.240.4:1025/generate_stream"
    request_data = {
        "model": "deepseek-v3:671b-cst",
        "inputs": prompt,
        "parameters": {
            "max_new_tokens": max_new_tokens,
            "temperature": 0.1,
            "do_sample": True,
            "top_p": 0.9,
            "top_k": 50,
            "repetition_penalty": 1.05,
            "num_return_sequences": 1
        }
    }

    try:
        response = requests.post(
            api_url,
            json=request_data,
            stream=True,
            timeout=timeout,
            verify=False,
            headers={
                "Accept": "text/event-stream",
                "Content-Type": "application/json"
            }
        )
        response.raise_for_status()
    except Exception as e:
        print(f"❌ API请求失败: {e}")
        return None

    buffer = ""
    token_chunks = []
    final_text_from_generated = None
    finished = False

    for chunk in response.iter_content(chunk_size=None):
        if not chunk:
            continue

        buffer += chunk.decode("utf-8", errors="ignore")

        while "\n\n" in buffer:
            event, buffer = buffer.split("\n\n", 1)

            for line in event.splitlines():
                if not line.startswith("data:"):
                    continue

                data_str = line[len("data:"):].strip()

                if not data_str or data_str == "[DONE]":
                    finished = True
                    continue

                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    buffer = data_str + "\n\n" + buffer
                    break

                if data.get("generated_text"):
                    final_text_from_generated = data["generated_text"]
                    finished = True
                    break

                token = data.get("token")
                if not token:
                    continue

                text = token.get("text", "")
                special = token.get("special")

                text = fix_mojibake(text)

                if not special:
                    token_chunks.append(text)
                else:
                    finished = True

            if final_text_from_generated:
                break

        if final_text_from_generated:
            break

    if final_text_from_generated:
        return strip_eos(fix_mojibake(final_text_from_generated))

    final_text = "".join(token_chunks)
    final_text = strip_eos(final_text)

    if not finished:
        final_text = "⚠️ 没有检测到完整结果\n\n" + final_text

    return final_text


def deepseek_generate_stream(
        prompt: str,
        max_new_tokens: int = 1500,
        timeout: int = 240
) -> str:
    """
    流式调用API
    """
    api_key = "sk-ad2be9fb1aa44429b8895806809ab2a5"
    client = OpenAI(
        api_key=api_key,
        base_url="https://api.deepseek.com")
    response = client.chat.completions.create(
        model="deepseek-chat",
        messages=[
            {"role": "system", "content": "你是一个从判别文档信息是否违规的专业助手"},
            {"role": "user", "content": prompt},
        ],
        stream=False
    )
    final_text = response.choices[0].message.content
    print("***************************")
    print("deepseek判别结果")
    print(response.choices[0].message.content)
    return final_text


def deepseek_v3_guangzhou(
        prompt: str,
        max_new_tokens: int = 1500,
        timeout: int = 240
) -> str:
    url = "http://59.210.232.66:10941/v1/chat/completions"
    headers = {
        "Authorization": "Bearer 094a2862-5f9b-4bbb-aba3-549f04abf177",
        "Content-Type": "application/json",
        "Host": "10.209.252.203:9000",
    }
    payload = {
        "model": "deepseek_v3",
        "messages": [
            {
                "role": "user",
                "content": prompt
            }
        ]
    }
    try:
        response = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=60
        )
        response.raise_for_status()
    except Exception as e:
        print(f"❌ API请求失败: {e}")
        return None
    result = response.json()
    print(result)
    final_text = result["choices"][0]["message"]["content"]
    return final_text


def smart_locate_in_markdown(document_str, extraction_rule):
    """智能定位：在Markdown文档中找到相关信息的位置"""
    field_name = extraction_rule.get('name', '')
    prompt_text = extraction_rule.get('prompt', '')
    target_sections = extraction_rule.get('sections', [])

    print(f"\n=== 智能定位字段: '{field_name}' ===")
    if prompt_text:
        print(f"  提示词: {prompt_text[:100]}... (仅用于大模型查询，不用于搜索)")
    if target_sections:
        print(f"  目标章节: {target_sections}")

    # 存储所有找到的内容
    all_found_content = []
    found_sections_in_title = []
    found_sections_in_content = []

    if target_sections and isinstance(target_sections, list):
        print(f"  优先在文档标题中搜索所有目标章节 ({len(target_sections)}个章节)")

        for section_name in target_sections:
            if not section_name or not isinstance(section_name, str):
                continue

            print(f"  🔍 搜索章节标题: '{section_name}'")

            section_patterns = [
                rf'^#+\s*{re.escape(section_name)}.*?\n(.*?)(?=\n#+\s|\Z)',
                rf'^##+\s*{re.escape(section_name)}.*?\n(.*?)(?=\n#+\s|\Z)',
                rf'^{re.escape(section_name)}[：:].*?\n(.*?)(?=\n#+\s|\n\S+[：:]|\Z)',
                rf'\n{re.escape(section_name)}[：:].*?\n(.*?)(?=\n#+\s|\n\S+[：:]|\Z)',
                rf'^#+\s*.*{re.escape(section_name)}.*?\n(.*?)(?=\n#+\s|\Z)',
                rf'##+\s*.*{re.escape(section_name)}.*?\n(.*?)(?=\n#+\s|\Z)',
            ]

            found_section = False
            for pattern in section_patterns:
                section_match = re.search(pattern, document_str, re.MULTILINE | re.DOTALL)
                if section_match:
                    section_content = section_match.group(0)
                    found_sections_in_title.append(section_name)
                    print(f"  ✅ 在文档标题中找到章节 '{section_name}'，长度: {len(section_content)}")

                    if len(section_content) > MAX_CONTEXT_LENGTH:
                        truncated_content = smart_truncate(section_content, MAX_CONTEXT_LENGTH)
                        all_found_content.append(f"【{section_name}】章节内容（截断）:\n{truncated_content}")
                    else:
                        all_found_content.append(f"【{section_name}】章节内容:\n{section_content}")
                    found_section = True
                    break

            if not found_section:
                print(f"  ⚠️ 在文档标题中未找到章节 '{section_name}'")

        print(f"  在文档标题中找到 {len(found_sections_in_title)}/{len(target_sections)} 个章节: {found_sections_in_title}")

        if found_sections_in_title:
            missing_sections = [s for s in target_sections if s not in found_sections_in_title]
            if missing_sections:
                print(f"  以下章节在标题中未找到，尝试在全文中搜索: {missing_sections}")

                for section_name in missing_sections:
                    print(f"  🔍 在全文中搜索章节关键词: '{section_name}'")
                    sections = find_context_around_text(document_str, section_name, context_size=400)

                    if sections:
                        all_content = [s['content'] for s in sections[:MAX_LOCATED_SEGMENTS]]
                        if all_content:
                            found_sections_in_content.append(section_name)
                            combined_content = "\n\n...\n\n".join(all_content)
                            if len(combined_content) > MAX_CONTEXT_LENGTH:
                                combined_content = smart_truncate(combined_content, MAX_CONTEXT_LENGTH)
                            all_found_content.append(f"【{section_name}】相关内容:\n{combined_content}")
                            print(f"  ✅ 在全文中找到章节 '{section_name}' 的相关内容")
                        else:
                            print(f"  ⚠️ 在全文中也未找到章节 '{section_name}' 的相关内容")
                    else:
                        print(f"  ⚠️ 在全文中也未找到章节 '{section_name}' 的相关内容")
        else:
            print(f"  所有章节在标题中都未找到，尝试在全文中搜索所有章节关键词")
            for section_name in target_sections:
                print(f"  🔍 在全文中搜索章节关键词: '{section_name}'")
                sections = find_context_around_text(document_str, section_name, context_size=400)

                if sections:
                    all_content = [s['content'] for s in sections[:MAX_LOCATED_SEGMENTS]]
                    if all_content:
                        found_sections_in_content.append(section_name)
                        combined_content = "\n\n...\n\n".join(all_content)
                        if len(combined_content) > MAX_CONTEXT_LENGTH:
                            combined_content = smart_truncate(combined_content, MAX_CONTEXT_LENGTH)
                        all_found_content.append(f"【{section_name}】相关内容:\n{combined_content}")
                        print(f"  ✅ 在全文中找到章节 '{section_name}' 的相关内容")
                    else:
                        print(f"  ⚠️ 在全文中也未找到章节 '{section_name}' 的相关内容")
                else:
                    print(f"  ⚠️ 在全文中也未找到章节 '{section_name}' 的相关内容")

        total_found = len(found_sections_in_title) + len(found_sections_in_content)
        print(f"  总计找到 {total_found}/{len(target_sections)} 个章节的内容")

    if not all_found_content and field_name:
        print(f"  ⚠️ 未找到任何目标章节内容，使用字段名在全文中搜索: '{field_name}'")

        chinese_keywords = re.findall(r'[\u4e00-\u9fa5]{2,}', field_name)
        if chinese_keywords:
            print(f"  从字段名中提取中文关键词: {chinese_keywords}")
            for keyword in chinese_keywords[:3]:
                sections = find_context_around_text(document_str, keyword, context_size=400)
                if sections:
                    all_content = [s['content'] for s in sections[:MAX_LOCATED_SEGMENTS]]
                    combined_content = "\n\n...\n\n".join(all_content)
                    if len(combined_content) > MAX_CONTEXT_LENGTH:
                        combined_content = smart_truncate(combined_content, MAX_CONTEXT_LENGTH)
                    all_found_content.append(f"【{keyword}】相关内容:\n{combined_content}")
                    print(f"  ✅ 通过中文关键词 '{keyword}' 找到相关内容")
                    break

        if not all_found_content:
            print(f"  使用完整字段名搜索: '{field_name}'")
            sections = find_context_around_text(document_str, field_name, context_size=400)

            if sections:
                all_content = [s['content'] for s in sections[:MAX_LOCATED_SEGMENTS]]
                combined_content = "\n\n...\n\n".join(all_content)
                if len(combined_content) > MAX_CONTEXT_LENGTH:
                    combined_content = smart_truncate(combined_content, MAX_CONTEXT_LENGTH)
                all_found_content.append(f"【{field_name}】相关内容:\n{combined_content}")
                print(f"  ✅ 通过字段名找到相关内容")

    if not all_found_content:
        print(f"  ⚠️ 未找到任何相关内容，使用文档开头部分作为备选")
        if len(document_str) > MAX_CONTEXT_LENGTH:
            truncated_content = smart_truncate(document_str, MAX_CONTEXT_LENGTH)
            all_found_content.append(f"文档开头部分:\n{truncated_content}")
        else:
            all_found_content.append(f"文档开头部分:\n{document_str}")

    located_content = ""
    if all_found_content:
        located_content = "\n\n---\n\n".join(all_found_content)
        prefer_long = ('"tables"' in located_content) or ('<table' in located_content) or ('"images"' in located_content) or ('"figures"' in located_content)
        max_total = (MAX_CONTEXT_LENGTH_TABLE if prefer_long else MAX_CONTEXT_LENGTH * 3)
        if len(located_content) > max_total:
            located_content = smart_truncate(located_content, max_total)

    if located_content:
        print(f"✅ 定位成功，共找到 {len(all_found_content)} 段内容，总长度: {len(located_content)} 字符")
    else:
        print(f"❌ 未定位到相关内容")

    return located_content


def extract_content_from_markdown(markdown_content, extraction_rules, folder_name, filename):
    """从Markdown内容中提取信息 - 优化版，使用统一文本块组织"""
    try:
        print(f"\n{'=' * 60}")
        print(f"开始内容提取: {filename}")
        print(f"提取规则: {len(extraction_rules)} 条")

        expected_fields = []
        for rule in extraction_rules:
            field_name = rule.get('name', '')
            if field_name:
                expected_fields.append(field_name)

        print(f"预期字段: {expected_fields}")
        print(f"Markdown内容长度: {len(markdown_content)} 字符")

        print(f"\n=== 统一组织文本块（避免重复）===")
        # 注意：Markdown版本没有page_idx信息，所以page_idx_map全部设为-1
        page_idx_map = {field: PAGE_IDX_UNKNOWN for field in expected_fields}
        
        # 这里简化处理，实际上Markdown也需要类似JSON的章节定位逻辑
        # 但为了简化，我们直接使用整个文档
        organized_contexts = {filename: markdown_content}
        
        total_context_chars = len(markdown_content)
        print(f"去重后总文本长度: {total_context_chars} 字符")
        
        estimated_original_length = total_context_chars * 1.5
        print(f"原方法估计长度: {estimated_original_length:.0f} 字符")
        print(f"Token节省比例: {(estimated_original_length - total_context_chars) / estimated_original_length * 100:.1f}%")

        user_content = build_extraction_prompt_from_organized_context(
            organized_contexts, extraction_rules, filename, is_markdown=True
        )

        print(f"\n=== 调用大模型进行内容提取 ===")
        result_text = deepseek_generate_stream(user_content)

        if not result_text:
            print(f"❌ API调用失败，返回'未找到'结果")
            not_found_result = {}
            for field in expected_fields:
                not_found_result[field] = "未找到"
            return [not_found_result], page_idx_map

        print(f"✅ API响应成功，长度: {len(result_text)} 字符")
        print(f"响应预览: {result_text[:300]}...")

        extracted_data = []

        try:
            json_match = re.search(r'\[\s*\{.*\}\s*\]', result_text, re.DOTALL)

            if json_match:
                json_str = json_match.group(0)
                print(f"找到JSON字符串，长度: {len(json_str)}")

                json_str = json_str.replace('\n', ' ').replace('\r', '')

                for field in expected_fields:
                    if f'"{field}": ""' in json_str:
                        json_str = json_str.replace(f'"{field}": ""', f'"{field}": "未找到"')

                parsed_data = json.loads(json_str)

                if isinstance(parsed_data, list) and parsed_data:
                    extracted_data = parsed_data
                    print(f"✅ 成功解析JSON，找到 {len(parsed_data[0])} 个字段")
                else:
                    raise ValueError("解析的JSON不是有效格式")
            else:
                api_extracted = extract_api_response_fields(result_text, expected_fields)
                if api_extracted:
                    for field in expected_fields:
                        if field not in api_extracted:
                            api_extracted[field] = "未找到"
                    extracted_data = [api_extracted]
                    print(f"⚠️ 未找到完整JSON，但提取并补全了 {len(api_extracted)} 个字段")
                else:
                    raise ValueError("无法从响应中提取任何字段")

        except Exception as e:
            print(f"❌ 解析响应失败: {e}")
            not_found_result = {}
            for field in expected_fields:
                not_found_result[field] = "未找到"
            extracted_data = [not_found_result]

        final_result = {}
        for field in expected_fields:
            if extracted_data and isinstance(extracted_data[0], dict):
                value = extracted_data[0].get(field, "未找到")
                if not value or (isinstance(value, str) and value.strip() == ""):
                    value = "未找到"
                final_result[field] = value
            else:
                final_result[field] = "未找到"

        extracted_data = [final_result]

        print(f"\n=== 最终提取结果 ===")
        extracted_count = 0
        not_found_count = 0
        for field, value in final_result.items():
            if value and value != "未找到":
                print(f"  ✅ {field}: {value[:100]}")
                extracted_count += 1
            else:
                print(f"  ⭕ {field}: 未找到")
                not_found_count += 1

        return extracted_data, page_idx_map

    except Exception as e:
        print(f"❌ 内容提取过程中出错: {e}")
        import traceback
        print(f"详细错误:\n{traceback.format_exc()}")

        error_data = {}
        error_page_idx = {}
        for rule in extraction_rules:
            field_name = rule.get('name', '')
            if field_name:
                error_data[field_name] = "未找到"
                error_page_idx[field_name] = PAGE_IDX_UNKNOWN
        return [error_data], error_page_idx


def process_markdown_with_custom_rules(md_dir, output_dir, extraction_rules):
    """使用自定义规则处理Markdown文件并提取内容"""
    try:
        os.makedirs(output_dir, exist_ok=True)

        rule_identifier = generate_rule_identifier(extraction_rules)
        print(f"\n{'=' * 60}")
        print(f"开始批量处理 - Markdown智能提取（优化版）")
        print(f"规则标识符: {rule_identifier}")
        print(f"输入目录: {md_dir}")
        print(f"输出目录: {output_dir}")
        print(f"提取规则: {len(extraction_rules)} 条")
        print(f"提取模式: 统一文本块优化提取（带页面索引）")
        print(f"{'=' * 60}")

        markdown_files = []
        for root, dirs, files in os.walk(md_dir):
            for filename in files:
                if filename.lower().endswith(".md"):
                    markdown_files.append((root, filename))

        total_files = len(markdown_files)
        print(f"总共发现 {total_files} 个Markdown文件待处理")

        if total_files == 0:
            print("❌ 没有找到Markdown文件")
            return False

        processed_files = 0
        failed_files = 0
        total_extracted_fields = 0
        total_expected_fields = 0
        total_not_found_fields = 0

        for root, filename in markdown_files:
            file_path = os.path.join(root, filename)

            try:
                print(f"\n{'=' * 40}")
                print(f"处理文件 {processed_files + failed_files + 1}/{total_files}: {filename}")

                result = extract_single_markdown_file(file_path, extraction_rules, output_dir)

                if result["success"]:
                    processed_files += 1

                    if result.get("extracted_data"):
                        extracted_data = result["extracted_data"]
                        if isinstance(extracted_data, list) and extracted_data and isinstance(extracted_data[0], dict):
                            extracted_count = 0
                            for field in extraction_rules:
                                field_name = field.get('name', '')
                                if field_name and field_name in extracted_data[0]:
                                    value = extracted_data[0][field_name]
                                    if value and value != "未找到":
                                        extracted_count += 1
                            not_found_count = len(extraction_rules) - extracted_count
                            total_extracted_fields += extracted_count
                            total_not_found_fields += not_found_count
                            total_expected_fields += len(extraction_rules)
                            print(f"✅ 成功提取 {extracted_count}/{len(extraction_rules)} 个字段，未找到 {not_found_count} 个字段")
                else:
                    failed_files += 1
                    print(f"❌ 处理失败: {result.get('error', '未知错误')}")

                time.sleep(1)

            except Exception as e:
                print(f"❌ 处理文件 {filename} 时出错: {e}")
                failed_files += 1

        overall_success_rate = total_extracted_fields / total_expected_fields * 100 if total_expected_fields > 0 else 0
        overall_not_found_rate = total_not_found_fields / total_expected_fields * 100 if total_expected_fields > 0 else 0

        print(f"\n{'=' * 60}")
        print("🎉 Markdown内容提取完成！（优化版）")
        print(f"{'=' * 60}")
        print(f"规则标识: {rule_identifier}")
        print(f"文件统计:")
        print(f"  - 总计: {total_files} 个文件")
        print(f"  - 成功处理: {processed_files} 个文件")
        print(f"  - 处理失败: {failed_files} 个文件")
        print(f"字段统计:")
        print(f"  - 期望提取: {total_expected_fields} 个字段")
        print(f"  - 成功提取: {total_extracted_fields} 个字段")
        print(f"  - 未找到: {total_not_found_fields} 个字段")
        print(f"  - 总体成功率: {overall_success_rate:.1f}%")
        print(f"  - 未找到率: {overall_not_found_rate:.1f}%")
        print(f"提取模式: 统一文本块优化提取（带页面索引）")
        print(f"输出目录: {output_dir}")
        print(f"{'=' * 60}")

        return processed_files > 0

    except Exception as e:
        print(f"处理过程中发生错误: {e}")
        import traceback
        print(f"详细错误:\n{traceback.format_exc()}")
        return False


def process_file_with_rules(file_path, extraction_rules, output_dir, task_id=None):
    """根据文件类型选择相应的处理函数"""
    file_ext = os.path.splitext(file_path)[1].lower()

    if file_ext == '.md':
        return extract_single_markdown_file(file_path, extraction_rules, output_dir)
    elif file_ext == '.json':
        # 如果没有 task_id，使用默认路径
        result_collection_path = Path(output_dir).parent / "result_collection_default.json"
        return extract_single_json_file(file_path, extraction_rules, output_dir, result_collection_path, task_id)
    else:
        print(f"❌ 不支持的文件类型: {file_ext}")
        return {
            "success": False,
            "file_path": file_path,
            "error": f"不支持的文件类型: {file_ext}"
        }


def extract_single_markdown_file(markdown_file_path, extraction_rules, output_dir):
    """处理单个Markdown文件 - 生成包含元信息的提取结果"""
    try:
        print(f"\n{'=' * 60}")
        print(f"处理文件: {markdown_file_path}")

        folder_name = os.path.basename(os.path.dirname(markdown_file_path))
        filename = os.path.basename(markdown_file_path)

        rule_identifier = generate_rule_identifier(extraction_rules)
        print(f"规则标识符: {rule_identifier}")
        print(f"提取规则: {len(extraction_rules)} 条")

        # 读取Markdown内容
        print(f"读取Markdown文件...")
        with open(markdown_file_path, "r", encoding="utf-8") as f:
            markdown_content = f.read()

        # 使用优化版的提取函数
        extracted_data, page_idx_map = extract_content_from_markdown(
            markdown_content,
            extraction_rules,
            folder_name,
            filename
        )

        # 创建包含元信息的完整结果
        full_result = create_extraction_result_with_metadata(
            extracted_data, extraction_rules, folder_name, filename,
            rule_identifier, markdown_content=markdown_content,
            file_path=markdown_file_path, page_idx_map=page_idx_map
        )

        # 保存结果
        base_filename = os.path.splitext(filename)[0]
        os.makedirs(output_dir, exist_ok=True)

        # 保存JSON结果（包含元信息）
        json_output_filename = f"{base_filename}_{rule_identifier}_extracted.json"
        json_output_path = os.path.join(output_dir, json_output_filename)

        print(f"保存JSON结果: {json_output_filename}")

        with open(json_output_path, "w", encoding="utf-8") as f:
            json.dump(full_result, f, ensure_ascii=False, indent=2)

        # 生成并保存Markdown结果
        md_output_filename = f"{base_filename}_{rule_identifier}_extracted.md"
        md_output_path = os.path.join(output_dir, md_output_filename)

        # 使用提取数据生成Markdown
        markdown_content_result = json_to_markdown(
            extracted_data,
            extraction_rules,
            folder_name,
            filename,
            rule_identifier,
            page_idx_map=page_idx_map
        )

        print(f"保存Markdown结果: {md_output_filename}")
        with open(md_output_path, "w", encoding="utf-8") as f:
            f.write(markdown_content_result)

        print(f"✅ 文件处理完成")
        print(f"生成文件: {json_output_filename}, {md_output_filename}")
        print(f"{'=' * 60}\n")

        return {
            "success": True,
            "file_path": markdown_file_path,
            "json_output_path": json_output_path,
            "md_output_path": md_output_path,
            "extracted_data": extracted_data,
            "page_idx_map": page_idx_map,
            "full_result": full_result,
            "markdown_content": markdown_content_result,
            "rule_identifier": rule_identifier
        }

    except Exception as e:
        print(f"❌ 处理文件时出错: {e}")
        import traceback
        print(f"详细错误:\n{traceback.format_exc()}")
        return {
            "success": False,
            "file_path": markdown_file_path,
            "error": str(e)
        }


def process_directory_with_rules(input_dir, output_dir, extraction_rules, file_types=['.md', '.json']):
    """处理目录中的文件（支持多种文件类型）"""
    try:
        os.makedirs(output_dir, exist_ok=True)

        rule_identifier = generate_rule_identifier(extraction_rules)
        print(f"\n{'=' * 60}")
        print(f"开始批量处理 - 智能提取（优化版）")
        print(f"规则标识符: {rule_identifier}")
        print(f"输入目录: {input_dir}")
        print(f"输出目录: {output_dir}")
        print(f"提取规则: {len(extraction_rules)} 条")
        print(f"支持的文件类型: {file_types}")
        print(f"提取模式: 统一文本块优化提取（带外部页面索引）")
        print(f"{'=' * 60}")

        supported_files = []
        for root, dirs, files in os.walk(input_dir):
            for filename in files:
                file_ext = os.path.splitext(filename)[1].lower()
                if file_ext in file_types:
                    supported_files.append((root, filename))

        total_files = len(supported_files)
        print(f"总共发现 {total_files} 个支持的文件待处理")

        if total_files == 0:
            print("❌ 没有找到支持的文件")
            return False

        processed_files = 0
        failed_files = 0
        total_extracted_fields = 0
        total_expected_fields = 0
        total_not_found_fields = 0

        for root, filename in supported_files:
            file_path = os.path.join(root, filename)

            try:
                print(f"\n{'=' * 40}")
                print(f"处理文件 {processed_files + failed_files + 1}/{total_files}: {filename}")

                result = process_file_with_rules(file_path, extraction_rules, output_dir)

                if result["success"]:
                    processed_files += 1

                    if result.get("extracted_data"):
                        extracted_data = result["extracted_data"]
                        if isinstance(extracted_data, list) and extracted_data and isinstance(extracted_data[0], dict):
                            extracted_count = 0
                            for field in extraction_rules:
                                field_name = field.get('name', '')
                                if field_name and field_name in extracted_data[0]:
                                    value = extracted_data[0][field_name]
                                    if value and value != "未找到":
                                        extracted_count += 1
                            not_found_count = len(extraction_rules) - extracted_count
                            total_extracted_fields += extracted_count
                            total_not_found_fields += not_found_count
                            total_expected_fields += len(extraction_rules)
                            print(f"✅ 成功提取 {extracted_count}/{len(extraction_rules)} 个字段，未找到 {not_found_count} 个字段")
                else:
                    failed_files += 1
                    print(f"❌ 处理失败: {result.get('error', '未知错误')}")

                time.sleep(1)

            except Exception as e:
                print(f"❌ 处理文件 {filename} 时出错: {e}")
                failed_files += 1

        overall_success_rate = total_extracted_fields / total_expected_fields * 100 if total_expected_fields > 0 else 0
        overall_not_found_rate = total_not_found_fields / total_expected_fields * 100 if total_expected_fields > 0 else 0

        print(f"\n{'=' * 60}")
        print(f"🎉 内容提取完成！（优化版）")
        print(f"{'=' * 60}")
        print(f"规则标识: {rule_identifier}")
        print(f"文件统计:")
        print(f"  - 总计: {total_files} 个文件")
        print(f"  - 成功处理: {processed_files} 个文件")
        print(f"  - 处理失败: {failed_files} 个文件")
        print(f"字段统计:")
        print(f"  - 期望提取: {total_expected_fields} 个字段")
        print(f"  - 成功提取: {total_extracted_fields} 个字段")
        print(f"  - 未找到: {total_not_found_fields} 个字段")
        print(f"  - 总体成功率: {overall_success_rate:.1f}%")
        print(f"  - 未找到率: {overall_not_found_rate:.1f}%")
        print(f"提取模式: 统一文本块优化提取（带外部页面索引）")
        print(f"输出目录: {output_dir}")
        print(f"{'=' * 60}")

        return processed_files > 0

    except Exception as e:
        print(f"处理过程中发生错误: {e}")
        import traceback
        print(f"详细错误:\n{traceback.format_exc()}")
        return False


def custom_extraction(md_dir, output_dir, extraction_rules):
    """兼容旧接口 - 主要用于Markdown提取"""
    return process_markdown_with_custom_rules(md_dir, output_dir, extraction_rules)


def extract_api_response_fields(response_text, expected_fields):
    """
    从API响应中提取字段信息
    """
    if not response_text:
        return {}

    extracted = {}

    for field in expected_fields:
        patterns = [
            rf'"{re.escape(field)}"\s*:\s*"([^"]+)"',
            rf'{field}.*?[:：]\s*([^\n]+)',
            rf'{field}.*?[:：]\s*"([^"]+)"',
            rf'{field}.*?[:：]\s*([^"]+)',
            rf'{field}.*?[:：]\s*(.+)',
        ]

        for pattern in patterns:
            match = re.search(pattern, response_text, re.IGNORECASE | re.DOTALL)
            if match:
                value = match.group(1).strip()
                value = re.sub(r'^["\']+|["\']+$', '', value)
                value = re.sub(r'^\s*[-:：]\s*', '', value)
                if value and value not in ['""', "''", '未找到', 'None', 'null']:
                    extracted[field] = value
                    print(f"  从响应中提取到 '{field}': {value[:100]}")
                    break

    return extracted


def _canonicalize_located_content(text: str) -> str:
    """对定位到的章节内容做规范化，用于跨章节去重"""
    if not text:
        return ""
    t = str(text)
    t = re.sub(r'^\s*【[^】]{1,50}】[^\n]*\n', '', t, flags=re.M)
    t = t.replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r'\n{3,}', '\n\n', t)
    t = re.sub(r'[ \t]{2,}', ' ', t)
    return t.strip()


def _fingerprint_located_content(text: str) -> str:
    """对规范化后的内容生成稳定指纹"""
    canon = _canonicalize_located_content(text)
    canon = canon[:8000]
    return hashlib.md5(canon.encode('utf-8', errors='ignore')).hexdigest()


def smart_locate_in_json_v4(json_text, extraction_rule):
    """修复4：同 section 多候选时，按锚点+表格优先排序；对 TOC 块降权；必要时追加“锚点全局搜索”。"""
    import json

    field_name = extraction_rule.get('name', '')
    prompt_text = extraction_rule.get('prompt', '')
    target_sections = extraction_rule.get('sections', []) or []

    print(f"\n=== 智能定位JSON字段: '{field_name}' ===")
    if target_sections:
        print(f"  目标章节: {target_sections}")

    if isinstance(json_text, str):
        try:
            json_obj = json.loads(json_text)
        except Exception:
            json_obj = {"content": json_text}
    else:
        json_obj = json_text

    title_exclude_keywords = extraction_rule.get('title_exclude_keywords', []) if isinstance(extraction_rule, dict) else [] 
    if not isinstance(title_exclude_keywords, list): 
        title_exclude_keywords = []  
    print(f"  [DEBUG] title_exclude_keywords={title_exclude_keywords}")

    anchor_global = _extract_anchor_keywords(prompt_text, field_name, section_name="", max_kws=12)

    all_found_content = []
    found_sections = []
    page_idx_values = []

    if target_sections:
        title_matches = find_sections_by_title_in_json(json_obj, target_sections, title_exclude_keywords)
        if title_matches:
            for match in title_matches:
                section_name = match['section_name']
                content = match['content']
                page_idx = get_page_idx_from_object(content)
                found_sections.append(section_name)
                page_idx_values.append({
                    'section': section_name,
                    'page_idx': page_idx
                })
                filtered_content = filter_json_content(content)
                all_found_content.append(f"【{section_name}】JSON内容（title: {match.get('title','')}）:\n{dump_filtered_json_for_llm(filtered_content)}")

    missing_sections = [s for s in target_sections if s not in found_sections] if target_sections else target_sections
    if missing_sections:
        for section_name in missing_sections:
            anchor_kws = _extract_anchor_keywords(prompt_text, field_name, section_name, max_kws=12) or anchor_global
            blocks = find_blocks_containing_text_in_json(json_obj, section_name, max_blocks=3, anchor_kws=anchor_kws)
            if blocks:
                found_sections.append(section_name)
                block_texts = []
                for b in blocks:
                    page_idx = get_page_idx_from_object(b)
                    page_idx_values.append({
                        'section': section_name,
                        'page_idx': page_idx
                    })
                    filtered_json = filter_block_by_keyword(b, anchor_kws or section_name)
                    block_texts.append(dump_filtered_json_for_llm(filtered_json))
                combined_content = "\n\n---\n\n".join(block_texts)
                all_found_content.append(f"【{section_name}】命中块（全文搜索）:\n{combined_content}")
                print(f"  ✅ 在全JSON中找到章节 '{section_name}' 的命中块，共 {len(blocks)} 个")

    combined_so_far = "\n\n".join(all_found_content)
    if anchor_global and combined_so_far and not any(ak in combined_so_far for ak in anchor_global[:6]):
        fallback_kw = anchor_global[0]
        fb_blocks = find_blocks_containing_text_in_json(json_obj, keyword=fallback_kw, max_blocks=2, anchor_kws=anchor_global)
        if fb_blocks:
            fb_texts = []
            for b in fb_blocks:
                page_idx = get_page_idx_from_object(b)
                page_idx_values.append({
                    'section': '锚点兜底',
                    'page_idx': page_idx
                })
                filtered_json = filter_block_by_keyword(b, anchor_global)
                fb_texts.append(dump_filtered_json_for_llm(filtered_json))
            all_found_content.append(f"【锚点兜底】命中块（全文搜索：{fallback_kw}）:\n" + "\n\n---\n\n".join(fb_texts))
            print(f"  ✅ 锚点兜底：找到 {len(fb_blocks)} 个候选块（keyword={fallback_kw}）")

    combined = "\n\n\n".join(all_found_content).strip()
    if not combined:
        print("  ❌ 未定位到任何相关内容")
        return "", [], []

    if ('"tables"' in combined or "<table" in combined.lower()):
        max_len = MAX_CONTEXT_LENGTH_TABLE
    else:
        max_len = MAX_CONTEXT_LENGTH

    if len(combined) > max_len:
        combined = smart_truncate(combined, max_len)

    print(f"✅ 定位成功，共找到 {len(all_found_content)} 段内容，{len(found_sections)}/{len(target_sections) if target_sections else len(found_sections)} 个章节，总长度: {len(combined)} 字符")
    return combined, found_sections, page_idx_values


def dump_filtered_json_for_llm(filtered_json):
    """将过滤后的JSON转换为字符串，供LLM使用"""
    prefer_long = False
    if isinstance(filtered_json, dict):
        prefer_long = bool(filtered_json.get('tables') or filtered_json.get('images') or filtered_json.get('figures'))
    max_len = MAX_CONTEXT_LENGTH_TABLE if prefer_long else MAX_CONTEXT_LENGTH
    s = json.dumps(filtered_json, ensure_ascii=False, indent=2)
    if len(s) > max_len:
        s = smart_truncate(s, max_len)
    return s


if __name__ == "__main__":
    print("内容提取模块 - 智能定位+大模型提取")
    print("支持的文件类型: Markdown (.md), JSON (.json)")
    print("优化策略: 先收集所有章节，统一去重定位，避免重复")
    print("Token使用优化: 预计减少30-50%的Token消耗")
    print("页面索引支持: page_idx 信息会从定位阶段一直传递到最终结果")
