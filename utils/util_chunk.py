from utils import task_folder_utils, mineru_request
import shutil
from pathlib import Path



# 列出指定任务已经解析的文件,用于分块，去除已经分块的文件
def list_parsed_for_task(task_id):
    """列出指定任务 parsed 目录下的解析结果（仅返回“有效解析输出”的文件）"""
    task_dirs = task_folder_utils.get_task_dirs(task_id)
    parsed_dir = task_dirs["parsed"]

    if not parsed_dir.exists():
        return []

    # 已分块的文件不再出现在待分块列表中
    chunked_files = list_chunk_results_for_task(task_id)
    chunked_names = {f["area_code"] for f in chunked_files}

    def _nonempty(p: Path) -> bool:
        try:
            return p.exists() and p.is_file() and p.stat().st_size > 0
        except Exception:
            return False

    items = []
    for folder in sorted(parsed_dir.iterdir()):
        if not folder.is_dir():
            continue

        rid = folder.name
        if rid in chunked_names:
            continue

        images_dir = folder / "images"
        images_has_files = images_dir.exists() and any(images_dir.iterdir())

        md_file = folder / f"{rid}.md"
        md_ok = _nonempty(md_file)

        # content_list 文件名可能不固定：优先 <rid>_content_list.json，其次 content_list.json，再兜底任意 json
        content_list = _find_content_list_file(folder, rid)
        json_ok = _nonempty(content_list) if content_list else False
        if not json_ok:
            # 兜底：任意非空 json 都算（避免命名差异/中途产物）
            for fp in folder.iterdir():
                if fp.is_file() and fp.suffix.lower() == ".json" and _nonempty(fp):
                    json_ok = True
                    break

        pdf_file = folder / f"{rid}.pdf"
        pdf_ok = _nonempty(pdf_file)

        # ✅ 关键：不再把“只有 pdf（常见于 DOCX->PDF 中途停止）”视为解析成功
        if not (md_ok or json_ok or images_has_files):
            continue

        files = []
        if images_dir.exists():
            files.append("images")
        if md_ok:
            files.append(md_file.name)
        if pdf_ok:
            files.append(pdf_file.name)
        # 尽量返回更有用的 json 文件名
        if content_list and content_list.exists():
            files.append(content_list.name)
        else:
            for fp in sorted(folder.iterdir()):
                if fp.is_file() and fp.suffix.lower()==".json":
                    files.append(fp.name)

        items.append({"area_code": rid, "name": rid, "files": files})

    return items
    # return list_chunk_results_for_task(task_id)
    # return list_chunk_results_for_task(items, "chunked", task_id)
def list_chunk_results_for_task(task_id):
    """列出指定任务 chunked 目录下的分块结果"""
    task_dirs = task_folder_utils.get_task_dirs(task_id)
    chunked_dir = task_dirs["chunked"]

    if not chunked_dir.exists():
        return []

    items = []
    for folder in sorted(chunked_dir.iterdir()):
        if not folder.is_dir():
            continue
        if not folder.name.endswith("_分块结果"):
            continue
        rid = folder.name.replace("_分块结果", "")
        files = []
        if (folder / "images").exists():
            files.append("images")
        jf = folder / f"{rid}_parsed.json"
        if jf.exists():
            files.append(jf.name)
        xlsx = folder / f"{rid}.xlsx"
        if xlsx.exists():
            files.append(xlsx.name)
        if files:
            items.append({"area_code": rid, "name": f"{rid}_分块结果", "files": files})
    return items

def _find_content_list_file(report_dir: Path, rid: str) -> Path | None:
    """
    在 report_dir(parsed文件夹) 中查找 content_list 文件，兼容两种命名：
    1) <rid>_content_list.json（推荐）
    2) content_list.json（某些 MinerU 产物）
    """
    preferred = report_dir / f"{rid}_content_list.json"
    legacy = report_dir / "content_list.json"
    if preferred.exists():
        return preferred
    if legacy.exists():
        return legacy
    return None



#json转换为markdown
import json
import re
from pathlib import Path
from bs4 import BeautifulSoup
from typing import List, Dict


# ========= 工具函数 =========

def section_level(section_code: str) -> int:
    """根据 section_code 计算 Markdown 标题级别"""
    if not section_code:
        return 1
    return section_code.count(".") + 1


def clean_text(text: str) -> str:
    """清洗正文文本"""
    if not text:
        return ""
    text = re.sub(r"\.{2,}", "", text)  # 去掉多余 ..
    return text.strip()


def html_table_to_markdown(html: str) -> str:
    """HTML table → Markdown table"""
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table")
    if not table:
        return ""

    rows = []
    for tr in table.find_all("tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
        if cells:
            rows.append(cells)

    if not rows:
        return ""

    header = rows[0]
    body = rows[1:]

    md = []
    md.append("| " + " | ".join(header) + " |")
    md.append("| " + " | ".join(["---"] * len(header)) + " |")

    for row in body:
        md.append("| " + " | ".join(row) + " |")

    return "\n".join(md)


# ========= 主转换逻辑 =========

def json_report_to_markdown(
    json_path: str,
    output_md_path: str,
):
    data: List[Dict] = json.loads(
        Path(json_path).read_text(encoding="utf-8")
    )

    md_lines = []

    # 报告标题（取第一个 report_id）
    report_id = data[0].get("report_id", "报告")
    md_lines.append(f"# {report_id}")
    md_lines.append("")

    for block in data:
        section_code = block.get("section_code", "")
        title = clean_text(block.get("title", ""))
        content = clean_text(block.get("content", ""))

        # ===== 标题 =====
        if title:
            level = section_level(section_code)
            heading = f"{'#' * level} {section_code} {title}".strip()
            md_lines.append(heading)
            md_lines.append("")

        # ===== 正文 =====
        if content:
            md_lines.append(content)
            md_lines.append("")

        # ===== 表格 =====
        for table in block.get("tables", []):
            caption = " ".join(table.get("table_caption", []))
            html = table.get("table_body", "")
            table_md = html_table_to_markdown(html)

            if caption:
                md_lines.append(f"**{caption}**")
                md_lines.append("")

            if table_md:
                md_lines.append(table_md)
                md_lines.append("")
            else:
                # 兜底：图片表格
                img = table.get("img_path")
                if img:
                    md_lines.append(f"![表格]({img})")
                    md_lines.append("")

        # ===== 图片 =====
        for img in block.get("images", []):
            img_path = img.get("img_path")
            caption = " ".join(img.get("image_caption", []))
            alt = caption if caption else "图片"
            if img_path:
                md_lines.append(f"![{alt}]({img_path})")
                md_lines.append("")

    Path(output_md_path).write_text(
        "\n".join(md_lines),
        encoding="utf-8"
    )

