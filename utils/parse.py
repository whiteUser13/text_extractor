# -*- coding: utf-8 -*-
"""
加固版 parse.py（批量 → 标准化分块 JSON + 分块式 Excel + 原名图片拷贝）
- 输入：mineru_outputs/<报告ID>/ 目录；其中包含 <报告ID>_content_list.json（或其它 .json）与图片
- 输出：parse_outputs/<报告ID>_分块结果/ 下生成：
    - <报告ID>_parsed.json  （标准化分块 JSON，字段见 Standardized Section Schema）
    - <报告ID>.xlsx         （与 JSON 同结构"逐块成表"；每块内：正文→表格(MD)→图片(带原图名)）
    - images/               （按原目录层级复制所有图片，保持**原文件名**与相对路径）
- 总结：生成 parse_summary.csv 与 parse_debug.log（错误详情）

本版新增：
- ✅ 更严格的**标题识别**与**目录（含点线引导 TOC）识别**；
- ✅ 中文枚举（"（1）/1）/一、/（一）/①/② …"）除非 text_level==1，否则**一律当正文**；
- ✅ 句首为**日期/年份**（如"2020年7月…"、"7月…"）一律当正文；
- ✅ 自动**合并误判小节**：若某节标题是枚举或 TOC 行且正文为空/很短，则并回上一节；
- ✅ Excel/JSON 同步：与上一版一致；图片保留原名与相对路径；表格以 Markdown 嵌入。
- ✅ 兜底：凡"3 层及以上的纯数字编号标题"（如 2.2.2.2 标题）一律强制当标题；
- ✅ 兄弟编号优先：当前在 2.2.2.1 时，遇到 2.2.2.x 也当标题。
- ✅ **跨页拆分**：同一 section 内容跨越不同页面时，自动拆分为多个子块，每个子块有独立的 page_idx
- ✅ **跨页拆分**：同一 section 内容跨越不同页面时，自动拆分为多个子块，每个子块有独立的 page_idx

依赖：
    pip install openpyxl pillow   # pillow 可选但推荐
Python：3.8+
"""

import os
import re
import sys
import json
import csv
import glob
import shutil
import traceback
from datetime import datetime
from typing import Any, Dict, List, Tuple, Optional
from html.parser import HTMLParser
from openpyxl.styles import Alignment
from openpyxl import Workbook
from openpyxl.drawing.image import Image as XLImage

# ========= 可按需修改 =========
ROOT_DIR_DEFAULT = "data/mineru_outputs"  # MinerU 输出根目录（每个子目录=一份报告）
OUTPUT_DIR_DEFAULT = "data/parse_outputs"  # 导出根目录
LOG_FILE = "../parse_debug.log"  # 调试日志
IMAGE_MAX_WIDTH = 130  # Excel 预览图最大宽
IMAGE_MAX_HEIGHT = 90  # Excel 预览图最大高
IMAGE_ROW_STEP = 24  # 每张图纵向间隔"行数"
SHEET_TITLE_ILLEGAL = r'[:\\/?*\[\]]'  # Excel sheet 名非法字符
MAX_SHEETS = 300  # 安全上限，防止极端多块导致 Excel 打不开
INCLUDE_CODE_IN_TITLE = True  # 标题前拼接编号（仅当编号为数字层级，如 2 或 2.1.3）
TITLE_CODE_JOINER = ""  # 编号与标题之间的连接符（中文常用无空格）

# ========= 依赖（openpyxl 必须；Pillow 推荐）=========
try:
    from openpyxl import Workbook
    from openpyxl.utils import get_column_letter
    from openpyxl.drawing.image import Image as XLImage
    from openpyxl.styles import Alignment

    _OPENPYXL_OK = True
except Exception:
    _OPENPYXL_OK = False
    # print("❌ 缺少 openpyxl，请先安装：pip install openpyxl", file=sys.stderr)  # 12.27改：仅在需要导出分块xlsx时才提示

try:
    from PIL import Image as PILImage  # 仅用于更精确读取原始尺寸

    _PIL_OK = True
except Exception:
    _PIL_OK = False


# ================= 基础工具 =================

# 新增：
def _write_multiline(ws, row: int, col: int, text: str) -> None:
    """将多行文本写入单元格，支持自动换行和垂直对齐"""
    cell = ws.cell(row=row, column=col, value=text)
    cell.alignment = Alignment(wrap_text=True, vertical='top')


def log_debug(tag: str, msg: str) -> None:
    """写入控制台与文件"""
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] [{tag}] {msg}"
    try:
        print(line)
    except Exception:
        pass
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def ensure_dir(path: str) -> None:
    try:
        os.makedirs(path, exist_ok=True)
    except Exception as e:
        log_debug("MKDIR_FAIL", f"{path}: {e}")


def read_json_safely(path: str) -> Any:
    """稳健读取 JSON，处理 BOM/空白"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read().strip("\ufeff \n\r\t")
        return json.loads(text)
    except Exception as e:
        raise RuntimeError(f"JSON 读取失败: {path} ({e})")


def find_content_json(folder_path: str, report_id: str) -> str:
    """
    优先 <report_id>_content_list.json；
    否则 *_content_list.json；
    否则任意 .json（取第一个）
    """
    cand = os.path.join(folder_path, f"{report_id}_content_list.json")
    if os.path.exists(cand):
        return cand
    matches = sorted(glob.glob(os.path.join(folder_path, "*_content_list.json")))
    if matches:
        return matches[0]
    matches = sorted(glob.glob(os.path.join(folder_path, "*.json")))
    return matches[0] if matches else ""


def collect_images_recursive(folder_path: str) -> List[str]:
    """递归收集目录下所有常见图片"""
    exts = ("*.png", "*.jpg", "*.jpeg", "*.bmp", "*.gif", "*.tif", "*.tiff", "*.webp")
    imgs: List[str] = []
    for pat in exts:
        imgs.extend(glob.glob(os.path.join(folder_path, "**", pat), recursive=True))
    return sorted(set(imgs))


# =============== Excel 安全辅助 ===============

def safe_sheet_title(title: str, taken: set) -> str:
    """去非法字符、压缩空白、<=31 字符、自动去重"""
    base = re.sub(SHEET_TITLE_ILLEGAL, " ", (title or "Sheet").strip())
    base = re.sub(r"\s+", " ", base) or "Sheet"
    base = base[:31]
    name = base
    idx = 1
    while name in taken or not name.strip():
        suffix = f"_{idx}"
        name = base[: (31 - len(suffix))] + suffix
        idx += 1
    taken.add(name)
    return name


def auto_fit_columns(ws) -> None:
    """根据内容粗略自适应列宽"""
    max_len: Dict[int, int] = {}
    for r in ws.iter_rows(values_only=True):
        for i, v in enumerate(r, start=1):
            if v is None:
                continue
            s = str(v)
            if len(s) > 80000:  # 极端长文本计入时限幅
                s = s[:80000]
            max_len[i] = max(max_len.get(i, 0), min(len(s), 80))
    for idx, ln in max_len.items():
        ws.column_dimensions[get_column_letter(idx)].width = min(max(ln * 1.1, 16), 120)


def safe_add_image(ws, img_path: str, cell="A1",
                   max_width=IMAGE_MAX_WIDTH, max_height=IMAGE_MAX_HEIGHT) -> bool:
    """存在才插；过大则缩放；异常不中断"""
    try:
        if not img_path or not os.path.exists(img_path):
            return False

        orig_w = orig_h = None
        if _PIL_OK:
            try:
                with PILImage.open(img_path) as im:
                    orig_w, orig_h = im.size
            except Exception:
                pass

        img = XLImage(img_path)
        w = getattr(img, "width", None) or orig_w
        h = getattr(img, "height", None) or orig_h
        if w and h:
            scale = min(1.0, max_width / float(w), max_height / float(h))
            if scale < 1.0:
                img.width = int(w * scale)
                img.height = int(h * scale)

        ws.add_image(img, cell)
        return True
    except Exception as e:
        log_debug("IMG_FAIL", f"{img_path}: {e}")
        return False


# =============== HTML 表格 → Markdown ===============

class _TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_table = False
        self.in_tr = False
        self.in_th = False
        self.in_td = False
        self.rows: List[List[str]] = []
        self.cur_row: List[str] = []
        self.header_flags: List[bool] = []
        self._buf = []

    def handle_starttag(self, tag, attrs):
        tg = tag.lower()
        if tg == 'table':
            self.in_table = True
        elif tg == 'tr' and self.in_table:
            self.in_tr = True
            self.cur_row = []
        elif tg == 'th' and self.in_tr:
            self.in_th = True
            self._buf = []
        elif tg == 'td' and self.in_tr:
            self.in_td = True
            self._buf = []

    def handle_data(self, data):
        if self.in_th or self.in_td:
            self._buf.append(data)

    def handle_endtag(self, tag):
        tg = tag.lower()
        if tg == 'th' and self.in_tr:
            txt = re.sub(r"\s+", " ", ''.join(self._buf).strip())
            self.cur_row.append(txt)
            self.header_flags.append(True)
            self.in_th = False
        elif tg == 'td' and self.in_tr:
            txt = re.sub(r"\s+", " ", ''.join(self._buf).strip())
            self.cur_row.append(txt)
            self.header_flags.append(False)
            self.in_td = False
        elif tg == 'tr' and self.in_tr:
            if self.cur_row:
                self.rows.append(self.cur_row)
            self.cur_row = []
            self.in_tr = False
        elif tg == 'table':
            self.in_table = False


def html_table_to_markdown(html: str) -> str:
    """优化表格的 Markdown 转换，避免表格标题误判"""
    if not html:
        return ""
    p = _TableParser()
    try:
        p.feed(html)
    except Exception:
        txt = re.sub(r"<[^>]+>", " ", html)
        return txt.strip()

    rows = p.rows
    if not rows:
        return ""
    n = max(len(r) for r in rows)
    norm = [r + [""] * (n - len(r)) for r in rows]

    has_header = any(p.header_flags[:len(rows[0])]) or all(not c.isdigit() for c in ''.join(rows[0]))

    def pipe(cells):
        return "| " + " | ".join(cells) + " |"

    md = []
    if has_header:
        md.append(pipe(norm[0]))
        md.append(pipe(['---'] * n))
        body = norm[1:]
    else:
        md.append(pipe([f"col{i + 1}" for i in range(n)]))
        md.append(pipe(['---'] * n))
        body = norm
    for r in body:
        md.append(pipe(r))
    return "\n".join(md)


# 11.24新增
def clean_caption(text: str) -> str:
    """清理图名中的 LaTeX / 多余空白 / 零宽字符等，让图名和文件名更干净"""
    if not text:
        return ""
    # 去掉 $...$ 形式的公式
    text = re.sub(r"\$.*?\$", "", text)
    # 去掉 \xxx{...} 这种 LaTeX 命令，只保留花括号里的内容
    text = re.sub(r"\\[a-zA-Z]+\{(.*?)\}", r"\1", text)
    # 常见特殊写法修正
    text = text.replace("^{\\#}", "#").replace("\\#", "#")
    # 合并空白
    text = re.sub(r"[\s]+", " ", text)
    # 去掉零宽字符
    text = re.sub(r"[\u200b-\u200f\u202a-\u202e]", "", text)
    # 删除结尾空括号（中英文）
    text = re.sub(r"（\s*）$", "", text)
    text = re.sub(r"\(\s*\)$", "", text)
    return text.strip()


# =============== 标题识别工具 ===============

# —— 工程/公用常见关键词（用于抑制"1、给水系统"等被当标题）
UTIL_KEYWORDS = (
    "给排水", "给水", "排水", "供电", "蒸汽", "蒸汽供应", "除盐水", "循环水",
    "储运", "制冷站", "压缩空气", "公用工程", "公辅工程", "储运工程",
)


def normalize_heading_text(s: str) -> str:
    """把 '2 ． 1' / '2 . 1' 规范成 '2.1'（去掉点位两侧的空格，中文点转英文点）"""
    if not s:
        return s
    s2 = s.replace("．", ".")
    out = []
    i, L = 0, len(s2)
    while i < L:
        ch = s2[i]
        if ch == " ":
            prev_c = s2[i - 1] if i - 1 >= 0 else ""
            next_c = s2[i + 1] if i + 1 < L else ""
            if (prev_c.isdigit() and next_c == ".") or (prev_c == "." and next_c.isdigit()):
                i += 1
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def looks_like_dunhao_enum(s: str) -> bool:
    """是否是形如 '1、……' 的顿号枚举。"""
    if not s:
        return False
    u = s.lstrip()
    j = 0
    while j < len(u) and u[j].isdigit():
        j += 1
    return j > 0 and j < len(u) and u[j] == "、"


def is_pseudo_heading(text: str) -> bool:
    """像 '表2-7……' / '图1-1……' / '综上所述……' / '……报告》'(无配对《) 等不应当作标题"""
    if not text:
        return False
    u = (text or "").strip()
    if not u:
        return False
    lu = u.lstrip()
    if lu.startswith(("表", "图")):
        for ch in lu[1:6]:
            if ch.isdigit():
                return True
    if lu.startswith("综上所述"):
        return True
    if lu.endswith("》") and ("《" not in lu):
        return True
    # 新增：排除以右括号开头的文本
    if lu.startswith("）"):
        return True
    # 新增：排除书名号内容
    if "《" in lu and "》" in lu:
        return True
    return False


def should_suppress_lv1_heading(current_code: Optional[str], cand_code: Optional[str], cand_title: str) -> bool:
    """若当前已在多级章节内（如 2.2.1.4），抑制把"1、给水系统/供电/蒸汽供应"等识别成新的一级标题"""
    if not cand_code or not cand_code.isdigit():
        return False
    cur = str(current_code or "")
    if "." in cur:  # 已在深层章节里
        return True
    for kw in UTIL_KEYWORDS:
        if kw in (cand_title or ""):
            return True
    return False


# —— 目录标题（显式"目录"）
_DIR_TITLE_RE = re.compile(r"^\s*(目\s*录|目\s*錄|目录|目錄|contents|table\s*of\s*contents)\s*(（续）|\(续\)|续)?\s*$",
                           re.I)
# —— 目录"点线引导"行（例： 总论........ 15 / 3.2 编制依据 …… 48）
_TOC_LEADER_RE = re.compile(r"^\s*[^\n]{1,80}[\.·．\s]{3,}\s*\d{1,4}\s*$")
# —— 中文数字
_CJK_NUM = "一二三四五六七八九十百千"
# —— 中文/阿拉伯枚举（非标题）：（一）/ 一、/ （1）/ 1） / ①②…
_ENUM_RE = re.compile(rf"^\s*(?:[（(]?[{_CJK_NUM}]+[）)\.．、]|[（(]?\d+[）)．\.]|[①②③④⑤⑥⑦⑧⑨⑩]+)\s*\S+")
# —— 结构编号（接受 1.2 / 2.3.4 或 1）/ 2、）
_HEADING_NUMERIC_RE = re.compile(r"^\s*(\d+(?:\.\d+)+)\s*[、\.．\)]?\s*(.+)$")  # 改为+确保至少有一个点
_HEADING_LV1_DIGIT_RE = re.compile(r"^\s*(\d+)[、\.．\)]\s*(.+)$")

# —— 3层及以上的数字编号标题兜底（如 2.2.2.2 生产设备）
_HARD_NUMERIC_3PLUS_RE = re.compile(r'^\s*(\d+(?:\.\d+){2,})\s*(?:[、\.．\)])?\s*([^\s].{0,80}?)\s*$')

# 0122修改: 支持中文章节标题（"一、" / "（一）" / "第一章" 等），并通过“自动模式”降低对纯数字编号文档的干扰

# —— 中文章节标题识别
_CJK_CHAPTER_RE = re.compile(rf"^\s*第\s*([{_CJK_NUM}]+)\s*章\s*(.*)$")
_CJK_SECTION_RE = re.compile(rf"^\s*第\s*([{_CJK_NUM}]+)\s*节\s*(.*)$")
_CJK_LV1_RE = re.compile(rf"^\s*([{_CJK_NUM}]+)\s*[、．\.]\s*(.+)$")  # 一、标题 / 二.标题
_CJK_LV2_RE = re.compile(rf"^\s*[（(]([{_CJK_NUM}]+)[）)]\s*(.+)$")  # （一）标题 / (二)标题


def cjk_to_int(cjk: str) -> Optional[int]:
    """将常见中文数字（最多到千级）转为 int。无法解析返回 None。"""
    if not cjk:
        return None
    cjk = (cjk or '').strip()
    num_map = {ch: i for i, ch in enumerate("零一二三四五六七八九", start=0)}
    unit_map = {"十": 10, "百": 100, "千": 1000}
    total = 0
    current = 0
    seen = False
    for ch in cjk:
        if ch in num_map:
            current = num_map[ch]
            seen = True
        elif ch in unit_map:
            seen = True
            unit = unit_map[ch]
            # “十”省略“一” → 10
            if current == 0:
                current = 1
            total += current * unit
            current = 0
        else:
            # 超出支持字符
            return None
    if seen:
        total += current
        return total if total > 0 else None
    return None


def _title_plausible_as_heading(title: str) -> bool:
    """进一步压制把正文枚举误判为标题。"""
    t = (title or '').strip()
    if not t:
        return False
    # 太长的通常不是标题（可按需调整阈值）
    if len(t) > 60:
        return False
    # 以句号/分号/逗号结尾更像正文
    if re.search(r"[。；;，,]$", t):
        return False
    # 句中包含明显的句读，且很长，更像正文
    if len(t) > 35 and ("。" in t or "；" in t or ";" in t):
        return False
    return True


def detect_heading_mode(items: List[Dict[str, Any]], max_lines: int = 800) -> str:
    """自动判定文档的主标题体系：numeric / cjk / mixed。
    目的：在纯数字编号文档中尽量不启用中文枚举标题识别，避免误判。
    """
    numeric_cnt = 0
    cjk_cnt = 0
    nlines = 0
    for it in items:
        if it.get("type") != "text":
            continue
        txt = (it.get("text") or "")
        if not txt:
            continue
        for raw in txt.splitlines():
            line = raw.strip()
            if not line:
                continue
            nlines += 1
            if nlines > max_lines:
                break
            line_norm = normalize_heading_text(line)
            # 数字编号（必须含点）
            if re.match(r"^\s*\d+(?:\.\d+)+\s*\S+", line_norm):
                numeric_cnt += 1
            # 中文章节风格
            if _CJK_CHAPTER_RE.match(line_norm) or _CJK_SECTION_RE.match(line_norm) or _CJK_LV1_RE.match(
                    line_norm) or _CJK_LV2_RE.match(line_norm):
                cjk_cnt += 1
        if nlines > max_lines:
            break

    # 阈值与比例：可按数据集分布微调
    if numeric_cnt >= max(5, cjk_cnt * 2):
        return "numeric"
    if cjk_cnt >= max(5, numeric_cnt * 2):
        return "cjk"
    return "mixed"


def _parse_cjk_heading(text_norm: str,
                       text_level: Optional[int],
                       heading_mode: str,
                       cjk_state: Optional[Dict[str, Optional[int]]] = None
                       ) -> Tuple[Optional[str], str, bool]:
    """解析中文章节标题。返回 (code, title, is_heading)。"""
    if heading_mode == "numeric":
        return None, text_norm, False

    # mixed 模式更严格：必须是 text_level==1 才允许中文标题
    if heading_mode == "mixed":
        if text_level is not None and int(text_level) != 1:
            return None, text_norm, False
    else:
        # cjk 模式：仍建议优先利用 text_level 抑制正文枚举误判
        if text_level is not None and int(text_level) > 1:
            return None, text_norm, False

    st = cjk_state if isinstance(cjk_state, dict) else {}
    st.setdefault("lv1", None)
    st.setdefault("lv2", None)

    t = (text_norm or "").strip()
    if not t:
        return None, "", False

    mixed_no_level = (heading_mode == "mixed" and text_level is None)

    # 第一章/第一节
    m = _CJK_CHAPTER_RE.match(t)
    if m:
        n = cjk_to_int(m.group(1))
        title = (m.group(2) or "").strip()
        if n is not None and _title_plausible_as_heading(title or "章节") and (
                not mixed_no_level or len((title or "章节")) <= 30):
            st["lv1"] = n
            st["lv2"] = None
            return f"CN{n}", (title or f"第{m.group(1)}章"), True

    m = _CJK_SECTION_RE.match(t)
    if m:
        n = cjk_to_int(m.group(1))
        title = (m.group(2) or "").strip()
        if n is not None and _title_plausible_as_heading(title or "小节") and (
                not mixed_no_level or len((title or "小节")) <= 30):
            # 若没有章号，退化为一级；有章号则作为二级
            if st.get("lv1") is None:
                st["lv1"] = n
                st["lv2"] = None
                return f"CN{n}", (title or f"第{m.group(1)}节"), True
            else:
                st["lv2"] = n
                return f"CN{st['lv1']}.{n}", (title or f"第{m.group(1)}节"), True

    # 一、标题（通常一级）
    m = _CJK_LV1_RE.match(t)
    if m:
        n = cjk_to_int(m.group(1))
        title = (m.group(2) or "").strip()
        if n is not None and _title_plausible_as_heading(title) and (not mixed_no_level or len(title) <= 30):
            st["lv1"] = n
            st["lv2"] = None
            return f"CN{n}", title, True

    # （一）标题（通常二级）
    if mixed_no_level:
        return None, text_norm, False
    m = _CJK_LV2_RE.match(t)
    if m:
        n = cjk_to_int(m.group(1))
        title = (m.group(2) or "").strip()
        if n is not None and _title_plausible_as_heading(title):
            if st.get("lv1") is None:
                # 无一级上下文时，降级为一级
                st["lv1"] = n
                st["lv2"] = None
                return f"CN{n}", title, True
            st["lv2"] = n
            return f"CN{st['lv1']}.{n}", title, True

    return None, text_norm, False


def split_heading_from_tail_in_caption(caption: str) -> Optional[Tuple[str, str, int, int]]:
    """若 caption 尾部粘了一个'3层及以上的数字编号标题'（如 '... 一览表  2.5.1.2 废气'），
    则返回 (code, title, start_idx, end_idx)；否则返回 None。
    我们会用 start_idx/end_idx 从 caption 里剔除这段标题。"""
    if not caption:
        return None
    cap = caption.strip()
    # 检查 caption 的尾部是否包含有效的数字编号标题
    m = re.search(r'(\d+(?:\.\d+){2,})\s*(?:[、\.．\)])?\s*([^\s].{0,80}?)\s*$', cap)
    if not m:
        return None
    code = m.group(1)
    title = (m.group(2) or "").strip()
    if is_pseudo_heading(title) or looks_like_dunhao_enum(title):
        return None
    return code, title, m.start(1), m.end(2)


def _norm_whitespace(s: str) -> str:
    s = s.replace("\r", "")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\s+\n", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def looks_like_date_prefix(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if re.match(r'^\d{2,4}\s*年', t):
        return True
    if re.match(r'^\d{4}[./-]\d{1,2}', t):
        return True
    if re.match(r'^\d{1,2}\s*月', t):
        return True
    return False


def is_directory_title(title: str) -> bool:
    return bool(title) and _DIR_TITLE_RE.match(title) is not None


def is_toc_leader_line(text: str) -> bool:
    return bool(text) and _TOC_LEADER_RE.match(text.strip()) is not None


def is_chinese_enumerator(text: str) -> bool:
    t = (text or '').strip()
    # 兼容 LaTeX 枚举：$\textcircled{1}$ 或 \textcircled{1}
    if re.match(r'^\s*(?:\$\s*\\textcircled\{\d+\}\s*\$|\\textcircled\{\d+\})', t):
        return True
    return bool(_ENUM_RE.match(t))


def extract_dotted_numeric_code(text: str) -> Tuple[Optional[str], str]:
    t = (text or '').strip()
    m = _HEADING_NUMERIC_RE.match(t)
    if m:
        return m.group(1), m.group(2).strip()
    m = _HEADING_LV1_DIGIT_RE.match(t)
    if m:
        num = int(m.group(1))
        if 1900 <= num <= 2099:  # 过滤年份
            return None, t
        return m.group(1), m.group(2).strip()
    return None, t


def is_valid_heading(text: str) -> bool:
    """检查文本是否为有效的标题，接受更多格式的标题"""
    t = text.strip()

    # 数字编号标题：1.1, 1.1.1 等（必须包含点）
    if re.match(r"^\d+(\.\d+)+\s+.*$", t):
        return True

    return False


def is_enumeration(text: str) -> bool:
    """检查是否为枚举格式，认为是正文"""
    t = text.strip()

    # 中文枚举：一、, （一）等
    if re.match(rf"^[{_CJK_NUM}]+[、．\.].*$", t):
        return True

    # 带括号的枚举：（1）, （一）等
    if re.match(r"^[（(][一二三四五六七八九十\d]+[）)].*$", t):
        return True

    # 数字顿号枚举：1、, 2、等
    if re.match(r"^\d+[、．\.].*$", t):
        return True

    # 圆圈数字：①, ②等
    if re.match(r"^[①②③④⑤⑥⑦⑧⑨⑩].*$", t):
        return True

    # 数字加右括号：1), 2) 等
    if re.match(r"^\d+\)\s*.*$", t):
        return True

    return False


def parse_heading_strict(
        text: str,
        text_level: Optional[int] = None,
        in_toc: bool = False,
        heading_mode: str = "auto",
        cjk_state: Optional[Dict[str, Optional[int]]] = None,
) -> Tuple[Optional[str], str, bool]:
    """严格标题识别（默认优先数字编号），并在必要时支持中文章节标题。
    返回 (section_code, title, is_heading)。
    - heading_mode: auto / numeric / cjk / mixed
      auto 会根据文档内容自动选择（建议由 build_standardized_sections 预先计算后传入）
    """
    t = (text or "").strip()
    if not t:
        return None, "", False

    t = normalize_heading_text(t)

    # 目录中只识别数字编号（避免目录里的“一、”等被误判）
    if in_toc:
        m = _HEADING_NUMERIC_RE.match(t)
        if m:
            code = m.group(1)
            title = (m.group(2) or "").strip()
            if not is_pseudo_heading(title) and not looks_like_dunhao_enum(title):
                return code, title, True
        return None, t, False

    # 目录引导点线、日期前缀、伪标题直接排除
    if is_toc_leader_line(t) or looks_like_date_prefix(t) or is_pseudo_heading(t):
        return None, t, False

    # 1) 数字编号：强优先（保持原有行为）
    m = _HARD_NUMERIC_3PLUS_RE.match(t)
    if m:
        code = m.group(1)
        title = (m.group(2) or "").strip()
        if not is_pseudo_heading(title) and not looks_like_dunhao_enum(title):
            return code, title, True

    m = _HEADING_NUMERIC_RE.match(t)
    if m:
        code = m.group(1)
        title = (m.group(2) or "").strip()
        if not is_pseudo_heading(title) and not looks_like_dunhao_enum(title):
            return code, title, True

    # 2) 一级数字（如 1. 总则 / 1、总则）：谨慎保留，避免误把正文枚举当标题
    m = _HEADING_LV1_DIGIT_RE.match(t)
    if m:
        code = m.group(1)
        title = (m.group(2) or "").strip()
        if not should_suppress_lv1_heading(None, code, title):
            if not is_pseudo_heading(title) and not looks_like_dunhao_enum(t):
                return code, title, True

    # 3) 中文章节标题（按模式启用）
    mode = heading_mode or "auto"
    if mode == "auto":
        # 兜底：若未提前判定，按文本自身做一次轻量判定（对性能影响可忽略）
        # 这里不访问 items，因此只能根据当前行做判断：默认 mixed（更保守）
        mode = "mixed"

    code, title, ok = _parse_cjk_heading(t, text_level=text_level, heading_mode=mode, cjk_state=cjk_state)
    if ok and code:
        return code, title, True

    return None, t, False

    # 检查是否是目录行
    if is_toc_leader_line(t):
        return None, t, False

    # 检查是否是日期前缀
    if looks_like_date_prefix(t):
        return None, t, False

    # 规范化文本
    t_norm = normalize_heading_text(t)

    # 如果在目录中，统一使用"TOC"作为编号
    if in_toc:
        # 检查是否是多级数字编号标题
        m = re.match(r"^\s*(\d+(?:\.\d+)+)\s*(?:[、\.．\)]?\s*)(.+)$", t_norm)
        if m:
            title = m.group(2).strip()
            if title and len(title) > 1 and not is_pseudo_heading(title):
                return "TOC", title, True
        return None, t, False

    # 只识别多级数字编号标题：1.1, 2.3.4, 1.2.3.4 等
    m = re.match(r"^\s*(\d+(?:\.\d+)+)\s*(?:[、\.．\)]?\s*)(.+)$", t_norm)
    if m:
        code = m.group(1)
        title = m.group(2).strip()
        if title and len(title) > 1 and not is_pseudo_heading(title):
            return code, title, True

    # 严格的3层以上数字标题兜底
    m = re.match(r'^\s*(\d+(?:\.\d+){2,})\s*(?:[、\.．\)])?\s*([^\s].{0,80}?)\s*$', t_norm)
    if m:
        code = m.group(1)
        title = m.group(2).strip()
        if title and len(title) > 1 and not is_pseudo_heading(title):
            return code, title, True

    return None, t, False


def is_enumeration(text: str) -> bool:
    """检查是否为枚举格式，认为是正文"""
    t = text.strip()

    # 带括号的数字或中文枚举：（1）、（一）、（5）等
    if re.match(r"^\s*[（(][一二三四五六七八九十\d]+[）)]\s*.*$", t):
        return True

    # 中文枚举：一、, 二、等
    cjk_nums = "一二三四五六七八九十"
    if re.match(rf"^\s*[{cjk_nums}]+[、．\.]\s*.*$", t):
        return True

    # 数字顿号枚举：1、, 2、等
    if re.match(r"^\s*\d+[、．\.]\s*.*$", t):
        return True

    # 圆圈数字：①, ②等
    if re.match(r"^[①②③④⑤⑥⑦⑧⑨⑩].*$", t):
        return True

    # 数字加右括号：1), 2) 等
    if re.match(r"^\s*\d+\)\s*.*$", t):
        return True

    # LaTeX 枚举：$\textcircled{1}$ 等
    if re.match(r'^\s*(?:\$\s*\\textcircled\{\d+\}\s*\$|\\textcircled\{\d+\})', t):
        return True

    # 一级数字标题：1. 总论（这种应该被识别为标题，不是枚举）
    # 这里我们特别排除这种情况
    if re.match(r"^\s*\d+\s*[\.．]\s*.*$", t):
        return False

    return False


# =============== 标准化分块 JSON 生成（支持跨页拆分）===============

# =============== 标准化分块 JSON 生成（支持跨页拆分）===============

def build_standardized_sections(items: List[Dict[str, Any]], report_id: str, base_dir: str) -> List[Dict[str, Any]]:
    """
    构建标准化分块，支持跨页拆分：
    - 同一 section 的内容如果跨越多个页面，会拆分成多个子块
    - 每个子块有独立的 page_idx
    - 表格和图片会归属到对应页面的子块中
    """
    """
    构建标准化分块，支持跨页拆分：
    - 同一 section 的内容如果跨越多个页面，会拆分成多个子块
    - 每个子块有独立的 page_idx
    - 表格和图片会归属到对应页面的子块中
    """
    sections: List[Dict[str, Any]] = []
    current_section: Optional[Dict[str, Any]] = None
    in_toc = False  # 标记是否在目录部分

    # === 按页记录：最近一次出现"图/表编号引用"的 section，用于图片归属纠偏 ===
    # === 按页记录：最近一次出现"图/表编号引用"的 section，用于图片归属纠偏 ===
    last_figure_section_for_page: Dict[int, Dict[str, Any]] = {}
    last_text_section_for_page: Dict[int, Dict[str, Any]] = {}
    FIG_REF_RE = re.compile(r"(图|表)\s*\d+(?:\.\d+)*\s*[-－—]\s*\d+", re.UNICODE)

    # 0122修改: 自动判定标题体系，避免中文枚举标题识别干扰纯数字编号文档
    heading_mode = detect_heading_mode(items)
    cjk_state: Dict[str, Optional[int]] = {"lv1": None, "lv2": None}

    # 用于暂存当前 section 按页面分组的内容
    # 格式: {page_idx: {"content": str, "tables": list, "images": list}}
    page_groups: Dict[int, Dict[str, Any]] = {}

    def flush_current_section():
        """将当前 section 的 page_groups 转换为多个子块并添加到 sections"""
        nonlocal page_groups, current_section

        if not current_section or not page_groups:
            return

        base_code = current_section.get("section_code", "0")
        base_title = current_section.get("title", "概述")

        # 按页码排序
        for page in sorted(page_groups.keys()):
            group = page_groups[page]
            content = group.get("content", "").strip()
            tables = group.get("tables", [])
            images = group.get("images", [])

            # 跳过空块（但如果有表格或图片则保留）
            if not content and not tables and not images:
                continue

            # 创建子块
            sub_section = {
                "report_id": report_id,
                "section_code": base_code,
                "title": base_title,
                "content": content,
                "page_idx": page,
                "tables": tables,
                "images": images
            }
            sections.append(sub_section)

        # 清空 page_groups
        page_groups = {}

    # 创建默认的概述节
    current_section = {
        "report_id": report_id,
        "section_code": "0",
        "title": "概述",
        "content": "",
        "page_idx": None,
        "tables": [],
        "images": []
    }
    page_groups = {}
    page_groups = {}

    for it in items:
        typ = it.get("type")
        page = it.get("page_idx")
        # 确保 page 是有效整数
        if page is None or not isinstance(page, int):
            page = 0

        if typ == "text":
            txt = it.get("text", "")
            if not txt or not txt.strip():
                continue

            text_level = it.get("text_level")
            page_idx = it.get("page_idx")

            # 按行处理文本
            lines = [l.strip() for l in txt.splitlines() if l.strip()]

            for line in lines:
                if not line:
                    continue

                # 检查是否是目录标题
                if is_directory_title(line):
                    # 先 flush 当前 section
                    flush_current_section()
                    # 先 flush 当前 section
                    flush_current_section()
                    in_toc = True
                    # 创建目录节
                    current_section = {
                        "report_id": report_id,
                        "section_code": "TOC",
                        "title": "目录",
                        "content": "",
                        "page_idx": page,
                        "content": "",
                        "page_idx": page,
                        "tables": [],
                        "images": []
                    }
                    page_groups = {}
                    if page not in page_groups:
                        page_groups[page] = {"content": "", "tables": [], "images": []}
                    page_groups[page]["content"] += line + "\n"
                    page_groups = {}
                    if page not in page_groups:
                        page_groups[page] = {"content": "", "tables": [], "images": []}
                    page_groups[page]["content"] += line + "\n"
                    continue

                # 跳过目录和点线引导行
                if is_toc_leader_line(line):
                    if in_toc and current_section:
                        if page not in page_groups:
                            page_groups[page] = {"content": "", "tables": [], "images": []}
                        page_groups[page]["content"] += line + "\n"
                    if in_toc and current_section:
                        if page not in page_groups:
                            page_groups[page] = {"content": "", "tables": [], "images": []}
                        page_groups[page]["content"] += line + "\n"
                    continue

                line_norm = normalize_heading_text(line)

                # 严格的标题识别
                code, title, is_heading = parse_heading_strict(
                    line_norm, text_level=text_level, in_toc=in_toc,
                    heading_mode=heading_mode, cjk_state=cjk_state
                )
                code, title, is_heading = parse_heading_strict(
                    line_norm, text_level=text_level, in_toc=in_toc,
                    heading_mode=heading_mode, cjk_state=cjk_state
                )

                # 如果是有效的标题，创建新节
                if is_heading and code and title:
                    # 先 flush 当前 section
                    flush_current_section()

                    # 创建新节
                    current_section = {
                        "report_id": report_id,
                        "section_code": code,
                        "title": title,
                        "content": "",
                        "page_idx": page,
                        "content": "",
                        "page_idx": page,
                        "tables": [],
                        "images": []
                    }
                    page_groups = {}

                    # 如果创建了新节，说明已经离开目录部分
                    if in_toc and code != "TOC":
                        in_toc = False

                    # 标题行本身也作为内容的一部分
                    if page not in page_groups:
                        page_groups[page] = {"content": "", "tables": [], "images": []}
                    page_groups[page]["content"] += line + "\n"
                else:
                    # 正文内容，按页面分组存储
                    # 正文内容，按页面分组存储
                    if current_section:
                        if page not in page_groups:
                            page_groups[page] = {"content": "", "tables": [], "images": []}
                        page_groups[page]["content"] += line + "\n"
                        if page not in page_groups:
                            page_groups[page] = {"content": "", "tables": [], "images": []}
                        page_groups[page]["content"] += line + "\n"
                    else:
                        # 如果没有当前节，使用概述节
                        if page not in page_groups:
                            page_groups[page] = {"content": "", "tables": [], "images": []}
                        page_groups[page]["content"] += line + "\n"
                        if page not in page_groups:
                            page_groups[page] = {"content": "", "tables": [], "images": []}
                        page_groups[page]["content"] += line + "\n"

            # 记录该页最后一次出现文本时所在的节（作为 image 归属的次级兜底）
            if page_idx is not None and isinstance(page_idx, int) and current_section is not None:
                last_text_section_for_page[page_idx] = current_section

        elif typ == "table":
            # 处理表格：归属到当前页码的 group
            # 处理表格：归属到当前页码的 group
            if current_section:
                if page not in page_groups:
                    page_groups[page] = {"content": "", "tables": [], "images": []}
                page_groups[page]["tables"].append(it)
                if page not in page_groups:
                    page_groups[page] = {"content": "", "tables": [], "images": []}
                page_groups[page]["tables"].append(it)
            else:
                if page not in page_groups:
                    page_groups[page] = {"content": "", "tables": [], "images": []}
                page_groups[page]["tables"].append(it)
                if page not in page_groups:
                    page_groups[page] = {"content": "", "tables": [], "images": []}
                page_groups[page]["tables"].append(it)

        elif typ == "image":
            # 处理图片：优先挂到"同页最近出现图/表编号引用"的节，避免跨小节误挂
            # 处理图片：优先挂到"同页最近出现图/表编号引用"的节，避免跨小节误挂
            page_idx = it.get("page_idx")
            target_section = None
            if page_idx is not None and isinstance(page_idx, int):
                target_section = last_figure_section_for_page.get(page_idx) or last_text_section_for_page.get(page_idx)

            if target_section is None:
                target_section = current_section or sections[0] if sections else None
                target_section = current_section or sections[0] if sections else None

            if target_section:
                # 写入锚点元数据，便于后续 Excel 精准定位/调试
                it["anchor_section_code"] = target_section.get("section_code", "")
                it["anchor_title"] = target_section.get("title", "")

                if page not in page_groups:
                    page_groups[page] = {"content": "", "tables": [], "images": []}
                page_groups[page]["images"].append(it)
            else:
                # 兜底：放到概述节
                if 0 not in page_groups:
                    page_groups[0] = {"content": "", "tables": [], "images": []}
                page_groups[0]["images"].append(it)

    # 处理最后一个 section
    flush_current_section()

    # 清理空内容的节（除了概述节和目录节）
    filtered_sections = []
    for section in sections:
        content = section.get("content", "").strip()
        tables = section.get("tables", [])
        images = section.get("images", [])

        # 保留有内容、表格或图片的节，或者概述节、目录节
        if content or tables or images or section.get("title") in ["概述", "目录"]:
            # 清理内容中的多余空行
            if section.get("content"):
                section["content"] = re.sub(r'\n{3,}', '\n\n', section["content"].strip())
            filtered_sections.append(section)

    return filtered_sections


def _same_family(prev: Optional[str], new: Optional[str]) -> bool:
    """同族编号判断：2.2.2.1 与 2.2.2.x 属同族"""
    if not prev or not new:
        return False
    if not re.match(r'^\d+(?:\.\d+)*$', prev) or not re.match(r'^\d+(?:\.\d+)*$', new):
        return False
    p = [int(x) for x in prev.split('.')]
    n = [int(x) for x in new.split('.')]
    return len(p) == len(n) and p[:-1] == n[:-1]


def merge_false_headings(sections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """二次清洗：将误判的小节并回上一节；丢弃 TOC 行小节。"""
    merged: List[Dict[str, Any]] = []
    for s in sections:
        title = (s.get('title') or '').strip()
        content = (s.get('content') or '').strip()
        # TOC 样式：标题含 3+ 个点/点线引导，且正文为空
        if (is_toc_leader_line(title) or re.search(r"[\.·．]{3,}\s*\d{1,4}$", title)) and not content and not s.get(
                'tables') and not s.get('images'):
            continue
        # 纯枚举"标题"，正文为空或很短（可能是换行导致）→ 合并进上一节
        if is_chinese_enumerator(title) and (len(content) <= 40) and (merged):
            prev = merged[-1]
            glue = (title + ("\n" + content if content else ""))
            prev['content'] = _norm_whitespace(
                (prev.get('content') or '') + ("\n" if prev.get('content') else "") + glue)
            prev['tables'].extend(s.get('tables') or [])
            prev['images'].extend(s.get('images') or [])
            continue
        merged.append(s)
    return merged


# =============== 解析与导出核心 ===============

def parse_content_list(json_path: str) -> Dict[str, Any]:
    folder_path = os.path.dirname(os.path.abspath(json_path))
    report_id = os.path.basename(folder_path)

    data = read_json_safely(json_path)

    # MinerU content_list 风格：列表里的每个元素为 {type, text/img/table..., page_idx, ...}
    source_items = data if isinstance(data, list) else []
    std_sections = build_standardized_sections(source_items, report_id, folder_path)

    # 二次清洗：合并误判的小节
    std_sections = merge_false_headings(std_sections)

    images_all = collect_images_recursive(folder_path)

    return {
        "report_id": report_id,
        "std_sections": std_sections,
        "images_all": images_all,
        "json_path": os.path.abspath(json_path),
        "source_dir": folder_path,
    }


# (11.25修改)
def export_report_files(parsed: Dict[str, Any], output_root: str,
                        export_xlsx: bool = False) -> None:  # 12.27改：支持关闭分块xlsx导出
    """
    导出：
      - <report_id>_parsed.json（标准化分块 JSON）
      - <report_id>.xlsx（逐块成表：正文→表格(MD)→图片(含原图名/原文标题)）
      - images/（按原文图片标题重命名后的图片）
    """
    # if not _OPENPYXL_OK:
    #     raise RuntimeError("openpyxl 未安装，无法导出 xlsx")  # 12.27改：不再强制导出分块xlsx
    if export_xlsx and (not _OPENPYXL_OK):  # 12.27改：仅当需要导出分块xlsx时才检查依赖
        raise RuntimeError("openpyxl 未安装，无法导出 xlsx")

    rid = parsed.get("report_id") or "report"
    sections: List[Dict[str, Any]] = parsed.get("std_sections") or []
    source_dir: str = parsed.get("source_dir") or ""

    out_dir = os.path.join(output_root, f"{rid}_分块结果")
    ensure_dir(out_dir)
    out_xlsx = os.path.join(out_dir, f"{rid}.xlsx")
    out_json_parsed = os.path.join(out_dir, f"{rid}_parsed.json")
    out_img_dir = os.path.join(out_dir, "images")
    ensure_dir(out_img_dir)

    # ---- A) 写标准化分块 JSON ----
    try:
        with open(out_json_parsed, "w", encoding="utf-8") as f:
            json.dump(sections, f, ensure_ascii=False, indent=2)
    except Exception as e:
        raise RuntimeError(f"parsed_json_write_error: {e}")

    # ---- B) 为每个原始图片路径挑一个"最佳标题"（用来改文件名 + Excel 图名）----
    img_best_caption: Dict[str, str] = {}
    for sec in sections:
        for idx, img in enumerate(sec.get("images") or [], start=1):
            raw_path = img.get("img_path") or ""
            if not raw_path:
                continue

            if os.path.isabs(raw_path):
                src_abs = raw_path
            else:
                src_abs = os.path.abspath(os.path.join(source_dir, raw_path))

            if not os.path.exists(src_abs):
                continue

            # 优先级：image_caption[0] > caption > text > 文件名
            caption = ""
            # 先用 JSON 里的 image_caption，兼容老字段 img_caption
            cap_list = img.get("image_caption") or img.get("img_caption") or []
            if cap_list:
                caption = (cap_list[0] or "").strip()
            if not caption:
                caption = (img.get("caption") or "").strip()
            if not caption:
                caption = (img.get("text") or "").strip()
            if not caption:
                caption = os.path.splitext(os.path.basename(src_abs))[0]

            # 去掉换行、多余空格，并用 clean_caption 进一步清理图名
            caption = re.sub(r"[\r\n]+", " ", caption).strip()
            caption = clean_caption(caption)
            if not caption:
                caption = "image"

            # 只记录第一次出现的标题，避免后面覆盖
            img_best_caption.setdefault(src_abs, caption)

    # ---- C) 拷贝图片到 images/，并用"图1-1 XXX"重命名 ----
    src_to_rel: Dict[str, str] = {}

    def _safe_filename_from_caption(cap: str, src_abs: str) -> str:
        base_caption = cap or "image"
        # 去掉 Windows 不允许的字符
        base = re.sub(r'[\\/:*?"<>|]', "_", base_caption)
        base = re.sub(r"\s+", " ", base).strip().strip(".")
        if not base:
            base = "image"
        # 控制文件名长度，避免过长
        if len(base) > 80:
            base = base[:80]
        ext = os.path.splitext(src_abs)[1] or ".png"
        name = f"{base}{ext}"
        dest = os.path.join(out_img_dir, name)
        i = 1
        # 避免重名覆盖：图名相同自动加后缀 _1, _2, ...
        while os.path.exists(dest):
            name = f"{base}_{i}{ext}"
            dest = os.path.join(out_img_dir, name)
            i += 1
        return os.path.basename(dest)

    for src_abs, cap in img_best_caption.items():
        try:
            file_name = _safe_filename_from_caption(cap, src_abs)
            dest_path = os.path.join(out_img_dir, file_name)
            shutil.copy2(src_abs, dest_path)
            # 记录：这个原始路径 → images/ 下的新文件名
            src_to_rel[src_abs] = file_name
        except Exception as e:
            log_debug("IMG_COPY_FAIL", f"{rid}: {src_abs} -> {e}")

    # ---- D) 生成分块式 Excel ----
    if not export_xlsx:  # 12.27改：停止生成分块后的Excel表格
        return

    wb = Workbook()
    taken: set = set()

    # 删除默认 sheet，改用自定义名称
    try:
        default_ws = wb.active
        wb.remove(default_ws)
    except Exception:
        pass

    main_ws = wb.create_sheet(title=safe_sheet_title("Report Content", taken))
    taken.add(main_ws.title)

    headers = ["section_code", "title", "page_idx", "content", "images (with captions)", "tables (Markdown)"]
    main_ws.append(headers)

    wrap_center = Alignment(wrap_text=True, vertical="center", horizontal="center")
    wrap_left = Alignment(wrap_text=True, vertical="top", horizontal="left")

    widths = [15, 40, 10, 80, 70, 120]
    for idx, w in enumerate(widths, start=1):
        main_ws.column_dimensions[get_column_letter(idx)].width = w

    row_idx = 2
    for sec in sections:
        code = str(sec.get("section_code") or "")
        title = (sec.get("title") or "").strip()
        page_idx = sec.get("page_idx") or ""
        content = (sec.get("content") or "").strip()

        # ==== 先准备好当前小节的所有表格（仍然合在一个单元格里） ====
        tables = sec.get("tables") or []
        table_md_list: List[str] = []
        if tables:
            for t_idx, t in enumerate(tables, start=1):
                # 表名优先 table_caption，其次 caption
                caption = ""
                cap_list = t.get("table_caption") or []
                if cap_list:
                    caption = clean_caption((cap_list[0] or "").strip())
                if not caption:
                    caption = clean_caption((t.get("caption") or "").strip())
                if not caption:
                    caption = f"表格 {t_idx}"

                html_raw = t.get("html") or t.get("table_body") or ""
                md = html_table_to_markdown(html_raw)

                pages = t.get("pages") or []
                if not pages and t.get("page_idx") is not None:
                    pages = [t.get("page_idx")]

                log_debug(
                    "TABLE_DEBUG",
                    f"Table {t_idx}: Caption='{caption}', HTML length={len(html_raw)}, MD length={len(md)}",
                )

                if pages:
                    table_md_list.append(f"{caption} (页码: {pages})\n{md}")
                else:
                    table_md_list.append(f"{caption}\n{md}")

        table_text = "\n\n".join(table_md_list) if table_md_list else "[无表格]"

        # ==== 再准备好当前小节的所有图片（每张图一条记录） ====
        imgs = sec.get("images") or []
        img_entries: List[Tuple[Optional[str], str]] = []

        for img_idx, img in enumerate(imgs, start=1):
            raw_path = img.get("img_path") or ""
            if raw_path:
                if os.path.isabs(raw_path):
                    src_abs = raw_path
                else:
                    src_abs = os.path.abspath(os.path.join(source_dir, raw_path))
            else:
                src_abs = ""

            # 图名优先：image_caption[0] > img_caption[0] > caption > text > 回退到 B 步里的 img_best_caption
            cap_list = img.get("image_caption") or img.get("img_caption") or []
            if cap_list:
                caption = (cap_list[0] or "").strip()
            else:
                caption = (img.get("caption") or img.get("text") or f"图片 {img_idx}") or ""
            caption = re.sub(r"[\r\n]+", " ", caption).strip()
            caption = clean_caption(caption)
            if not caption:
                caption = img_best_caption.get(src_abs, f"图片 {img_idx}")

            # Excel 插图：优先用刚才复制并改名后的文件
            if src_abs and src_abs in src_to_rel:
                candidate = os.path.join(out_img_dir, src_to_rel[src_abs])
            else:
                candidate = src_abs  # 兜底：用原始路径

            if candidate and os.path.exists(candidate):
                img_entries.append((candidate, caption))
            else:
                img_entries.append((None, f"[未找到图片] {caption}"))

        # ==== 布局：一个 section 可能占多行 ====
        if not img_entries:
            # 没有图片的情况：只占一行
            main_ws.cell(row=row_idx, column=1, value=code)
            main_ws.cell(row=row_idx, column=2, value=title)
            main_ws.cell(row=row_idx, column=3, value=page_idx)
            c_cell = main_ws.cell(row=row_idx, column=4, value=content)
            c_cell.alignment = wrap_left

            img_cell = main_ws.cell(row=row_idx, column=5, value="[无图片]")
            img_cell.alignment = wrap_center

            t_cell = main_ws.cell(row=row_idx, column=6, value=table_text)
            t_cell.alignment = wrap_left

            main_ws.row_dimensions[row_idx].height = 100
            row_idx += 1
        else:
            # 有图片：每张图片一行
            for local_idx, (candidate, caption) in enumerate(img_entries):
                r = row_idx + local_idx
                is_first = (local_idx == 0)

                if is_first:
                    # 第 1 行写本节的基础信息 + 表格
                    main_ws.cell(row=r, column=1, value=code)
                    main_ws.cell(row=r, column=2, value=title)
                    main_ws.cell(row=r, column=3, value=page_idx)
                    c_cell = main_ws.cell(row=r, column=4, value=content)
                    c_cell.alignment = wrap_left

                    t_cell = main_ws.cell(row=r, column=6, value=table_text)
                    t_cell.alignment = wrap_left
                else:
                    # 之后的行，只写图片，其他列留空（也可以按需写"同上"）
                    pass

                # 第 5 列：当前这一张图片的图名 + 缩略图
                img_cell = main_ws.cell(row=r, column=5, value=caption)
                img_cell.alignment = wrap_center

                if candidate:
                    anchor_cell = f"{get_column_letter(5)}{r}"
                    safe_add_image(
                        main_ws,
                        candidate,
                        cell=anchor_cell,
                        max_width=260,
                        max_height=160,
                    )

                main_ws.row_dimensions[r].height = 100

            # 本节占用 len(img_entries) 行
            row_idx += len(img_entries)

    # ---- E) INDEX & INFO 页，保持原有结构 ----
    idx_ws = wb.create_sheet(title=safe_sheet_title("INDEX", taken))
    taken.add(idx_ws.title)
    idx_ws.append(["#", "section_code", "title", "page_idx", "images", "tables"])
    for i, s in enumerate(sections, start=1):
        idx_ws.append([
            i,
            s.get("section_code"),
            s.get("title"),
            s.get("page_idx"),
            len(s.get("images") or []),
            len(s.get("tables") or []),
        ])
    auto_fit_columns(idx_ws)

    info = wb.create_sheet(title=safe_sheet_title("INFO", taken))
    taken.add(info.title)
    info.append(["report_id", rid])
    info.append(["export_time", f"{datetime.now():%Y-%m-%d %H:%M:%S}"])
    info.append(["sections", len(sections)])
    # 使用真正导出的图片数量
    info.append(["images_total", len(src_to_rel)])
    info.append(["json_parsed", os.path.basename(out_json_parsed)])
    auto_fit_columns(info)

    wb.save(out_xlsx)


# ================= 主流程（逐份隔离） =================

def main() -> None:
    print("🚀 开始解析 MinerU 输出 ...")
    EXPECT_CHUNK_XLSX = False  # 12.27改：停止生成分块后的Excel表格
    if not os.path.isdir(ROOT_DIR_DEFAULT):
        print(f"❌ 未找到目录：{ROOT_DIR_DEFAULT}")
        return

    summary: List[tuple] = []  # (report_id, status, reason)
    total = 0
    success = 0

    for folder in sorted(os.listdir(ROOT_DIR_DEFAULT)):
        folder_path = os.path.join(ROOT_DIR_DEFAULT, folder)
        if not os.path.isdir(folder_path):
            continue

        total += 1
        rid = folder
        try:
            json_path = find_content_json(folder_path, rid)
            if not json_path:
                summary.append((rid, "FAILED", "missing_content_list_json"))
                log_debug("MISS_JSON", folder_path)
                continue

            try:
                parsed = parse_content_list(json_path)
            except Exception as e:
                summary.append((rid, "FAILED", f"parse_error: {e}"))
                log_debug("PARSE_FAIL", f"{json_path}\n{traceback.format_exc()}")
                continue

            try:
                # export_report_files(parsed, OUTPUT_DIR_DEFAULT)  # 12.27改：原逻辑（总是导出分块xlsx）
                export_report_files(parsed, OUTPUT_DIR_DEFAULT, export_xlsx=EXPECT_CHUNK_XLSX)  # 12.27改
            except Exception as e:
                summary.append((rid, "FAILED", f"export_error: {e}"))
                log_debug("EXPORT_FAIL", f"{rid}\n{traceback.format_exc()}")
                continue

            out_dir = os.path.join(OUTPUT_DIR_DEFAULT, f"{rid}_分块结果")
            xlsx_path = os.path.join(out_dir, f"{rid}.xlsx")
            json_parsed = os.path.join(out_dir, f"{rid}_parsed.json")
            images_dir = os.path.join(out_dir, "images")

            x_ok = os.path.exists(xlsx_path)  # 12.27改：分块xlsx不再生成
            jp_ok = os.path.exists(json_parsed)
            i_ok = os.path.isdir(images_dir)

            if jp_ok and i_ok and ((not EXPECT_CHUNK_XLSX) or x_ok):  # 12.27改：默认不再以xlsx作为成功条件
                summary.append((rid, "SUCCESS", ""))
                success += 1
            else:
                reasons = []
                if EXPECT_CHUNK_XLSX and (not x_ok): reasons.append("xlsx_missing")  # 12.27改
                if not jp_ok: reasons.append("parsed_json_missing")
                if not i_ok: reasons.append("images_missing")
                reason = "+".join(reasons) or "verify_failed"
                summary.append((rid, "FAILED", reason))
                log_debug("VERIFY_FAIL", f"{rid}: {reason}")

        except Exception as e:
            summary.append((rid, "FAILED", f"unexpected_error: {e}"))
            log_debug("UNEXPECTED", f"{rid}\n{traceback.format_exc()}")

    failed = total - success

    try:
        with open("parse_summary.csv", "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["report_id", "status", "reason"])
            for rid, status, reason in summary:
                w.writerow([rid, status, (reason or "").replace("\n", " ").replace(",", "，")])
        print("📄 已生成明细：parse_summary.csv")
    except Exception as e:
        log_debug("CSV_FAIL", str(e))

    print(f"📊 汇总：共 {total} 份，成功 {success}，失败 {failed}")
    print("✅ 批处理完成（标准化分块 JSON、分块式 Excel、原名图片已生成；查看 parse_summary.csv 与 parse_debug.log）")


if __name__ == "__main__":
    main()