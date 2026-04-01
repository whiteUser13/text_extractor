# utils/pdf_text_fast.py
from __future__ import annotations

import json
import os
from io import BytesIO
from pathlib import Path
from typing import List, Dict, Tuple
def smart_sample_pdf_text_length(pdf_path: str, threshold: int = 200) -> tuple[bool, dict]:
    """
    多点抽样判断是否为扫描件
    返回：
      (is_text_pdf, debug_info)
    """
    import fitz

    doc = fitz.open(pdf_path)
    page_count = doc.page_count

    # 页数太少，直接顺序检查
    if page_count <= 3:
        pages = list(range(page_count))
    else:
        pages = [
            0,                      # 第一页
            page_count // 2,        # 中间页
            page_count - 1          # 最后一页
        ]

    page_text_lens = {}

    for i in pages:
        try:
            txt = (doc.load_page(i).get_text("text") or "").strip()
            page_text_lens[i] = len(txt)
        except Exception:
            page_text_lens[i] = 0

    doc.close()

    # 只要有一页文本超过阈值，就认为是“文本型 PDF”
    is_text_pdf = any(v >= threshold for v in page_text_lens.values())

    return is_text_pdf, {
        "page_count": page_count,
        "sample_pages": pages,
        "text_lens": page_text_lens,
        "threshold": threshold,
    }


def extract_pdf_to_md_and_content_list(
    pdf_path: str,
    md_out: str,
    content_list_out: str,
    start_page: int = 0,
    end_page: int = 99999,
) -> Tuple[bool, Dict]:
    """快速提取 PDF 纯文本，输出 md + content_list.json（供后续分块复用）。"""
    import fitz  # PyMuPDF

    doc = fitz.open(pdf_path)
    page_count = doc.page_count
    sp = max(0, start_page)
    ep = min(page_count - 1, end_page)

    items: List[Dict] = []
    md_parts: List[str] = []

    for page_idx in range(sp, ep + 1):
        page = doc.load_page(page_idx)
        txt = (page.get_text("text") or "").strip()
        if not txt:
            continue

        md_parts.append(f"\n\n---\n\n## Page {page_idx + 1}\n\n{txt}\n")

        items.append(
            {
                "type": "text",
                "text": txt + "\n",
                "page_idx": page_idx,
                "text_level": 1,
            }
        )

    doc.close()

    Path(md_out).parent.mkdir(parents=True, exist_ok=True)
    Path(content_list_out).parent.mkdir(parents=True, exist_ok=True)

    with open(md_out, "w", encoding="utf-8") as f:
        f.write("".join(md_parts).strip() + "\n")

    with open(content_list_out, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

    return True, {"pages": page_count, "items": len(items)}



def extract_pdf_to_md_and_content_list_with_images(
    pdf_path: str,
    md_out: str,
    content_list_out: str,
    images_dir: str,
    start_page: int = 0,
    end_page: int = 99999,
) -> Tuple[bool, Dict]:
    """快速提取 PDF 的文本 + 图片，输出 md + content_list.json，并把图片落盘到 images_dir。

    与旧版相比：在 fast 抽图阶段加入“页眉页脚/装饰小图”过滤，减少无关小图进入后续链路（分块/大模型/Excel）。
    过滤策略（默认偏保守，尽量不误杀）：
      - 仅对“小图”候选生效（面积/边长阈值可配）
      - 且满足“位于页眉/页脚区域 + 跨页重复(指纹出现页数>=N)”才剔除
      - 若某 PDF 无法可靠取得图片位置(rect)，则退化为“小图 + 跨页重复”剔除（可关闭）

    性能/内存：第一遍只做特征计算（尺寸/指纹/位置），不在内存中缓存图片 bytes；第二遍仅对保留图片再提取并落盘。
    """
    import fitz  # PyMuPDF
    from PIL import Image

    # ---------- 可配置阈值（环境变量优先） ----------
    SMALL_MAX_SIDE = int(os.getenv("FAST_IMG_FILTER_SMALL_MAX_SIDE", "120"))
    SMALL_MAX_AREA = int(os.getenv("FAST_IMG_FILTER_SMALL_MAX_AREA", "20000"))

    HEADER_TOP_RATIO = float(os.getenv("FAST_IMG_FILTER_HEADER_TOP_RATIO", "0.12"))
    FOOTER_BOT_RATIO = float(os.getenv("FAST_IMG_FILTER_FOOTER_BOT_RATIO", "0.12"))

    REPEAT_MIN_PAGES = int(os.getenv("FAST_IMG_FILTER_REPEAT_MIN_PAGES", "3"))

    ALLOW_FALLBACK_NO_RECT = os.getenv("FAST_IMG_FILTER_FALLBACK_NO_RECT", "1") not in ("0", "false", "False")

    def _dhash(img: Image.Image, hash_size: int = 8) -> int:
        im = img.convert("L").resize((hash_size + 1, hash_size))
        pixels = list(im.getdata())
        h = 0
        for row in range(hash_size):
            row_start = row * (hash_size + 1)
            for col in range(hash_size):
                left = pixels[row_start + col]
                right = pixels[row_start + col + 1]
                h = (h << 1) | (1 if left > right else 0)
        return h

    def _safe_img_meta(img_bytes: bytes):
        try:
            im = Image.open(BytesIO(img_bytes))
            w, h = im.size
            dh = _dhash(im)
            return w, h, w * h, dh
        except Exception:
            return None

    def _rect_is_header_footer(rect, page_h: float) -> bool:
        try:
            y0 = float(rect.y0)
            y1 = float(rect.y1)
        except Exception:
            return False
        if page_h <= 0:
            return False
        return (y0 <= HEADER_TOP_RATIO * page_h) or (y1 >= (1.0 - FOOTER_BOT_RATIO) * page_h)

    # ---------- 第一遍：抽取文本 + 采集图片特征（不落盘，不缓存 bytes） ----------
    doc = fitz.open(pdf_path)
    page_count = doc.page_count
    sp = max(0, start_page)
    ep = min(page_count - 1, end_page)

    images_dir_p = Path(images_dir)
    images_dir_p.mkdir(parents=True, exist_ok=True)

    page_entries: List[Dict] = []   # {"page_idx": int, "text": str|None, "images": [img_entry,...]}
    all_imgs: List[Dict] = []

    for page_idx in range(sp, ep + 1):
        page = doc.load_page(page_idx)

        txt = (page.get_text("text") or "").strip()
        entry = {"page_idx": page_idx, "text": txt if txt else None, "images": []}

        try:
            img_list = page.get_images(full=True) or []
        except Exception:
            img_list = []

        if img_list:
            seen_xref = set()
            order_in_page = 0
            for img in img_list:
                try:
                    xref = img[0]
                except Exception:
                    continue
                if xref in seen_xref:
                    continue
                seen_xref.add(xref)

                try:
                    info = doc.extract_image(xref)
                    img_bytes = info.get("image")
                    ext = (info.get("ext") or "png").lower()
                    if not img_bytes:
                        continue
                except Exception:
                    continue

                meta = _safe_img_meta(img_bytes)
                if not meta:
                    continue
                w, h, area, dh = meta
                max_side = max(w, h)
                is_small = (area <= SMALL_MAX_AREA) or (max_side <= SMALL_MAX_SIDE)

                rects = []
                try:
                    rects = page.get_image_rects(xref) or []
                except Exception:
                    rects = []
                page_h = float(page.rect.height) if hasattr(page, "rect") else 0.0
                has_rect = len(rects) > 0 and page_h > 0
                is_hf = False
                if has_rect:
                    is_hf = any(_rect_is_header_footer(r, page_h) for r in rects)

                order_in_page += 1
                img_entry = {
                    "page_idx": page_idx,
                    "order": order_in_page,    # 保持页内顺序
                    "xref": int(xref),
                    "ext": ext,
                    "w": int(w),
                    "h": int(h),
                    "area": int(area),
                    "dhash": int(dh),
                    "is_small": bool(is_small),
                    "has_rect": bool(has_rect),
                    "is_hf": bool(is_hf),
                }
                entry["images"].append(img_entry)
                all_imgs.append(img_entry)

        page_entries.append(entry)

    doc.close()

    # ---------- 第二遍：统计重复（跨页）并决定过滤 ----------
    dhash_page_set: Dict[int, set] = {}
    for im in all_imgs:
        if not im.get("is_small"):
            continue
        dh = im["dhash"]
        dhash_page_set.setdefault(dh, set()).add(im["page_idx"])
    dhash_pages = {dh: len(pages) for dh, pages in dhash_page_set.items()}

    def _should_drop(im: Dict) -> Tuple[bool, str]:
        if not im.get("is_small"):
            return False, "keep:not_small"
        repeat_pages = dhash_pages.get(im["dhash"], 1)
        if repeat_pages < REPEAT_MIN_PAGES:
            return False, f"keep:repeat_pages<{REPEAT_MIN_PAGES}"

        if im.get("has_rect"):
            if im.get("is_hf"):
                return True, f"drop:small+hf+repeat_pages>={REPEAT_MIN_PAGES}"
            return False, "keep:has_rect_not_hf"

        if ALLOW_FALLBACK_NO_RECT:
            return True, f"drop:small+repeat_pages>={REPEAT_MIN_PAGES}+no_rect_fallback"
        return False, "keep:no_rect_fallback_disabled"

    audit = {
        "thresholds": {
            "SMALL_MAX_SIDE": SMALL_MAX_SIDE,
            "SMALL_MAX_AREA": SMALL_MAX_AREA,
            "HEADER_TOP_RATIO": HEADER_TOP_RATIO,
            "FOOTER_BOT_RATIO": FOOTER_BOT_RATIO,
            "REPEAT_MIN_PAGES": REPEAT_MIN_PAGES,
            "ALLOW_FALLBACK_NO_RECT": ALLOW_FALLBACK_NO_RECT,
        },
        "dhash_repeat_pages_top": [],
        "dropped": [],
        "kept_small": [],
    }
    top_rep = sorted(dhash_pages.items(), key=lambda x: x[1], reverse=True)[:20]
    audit["dhash_repeat_pages_top"] = [{"dhash": int(dh), "repeat_pages": int(n)} for dh, n in top_rep]

    for im in all_imgs:
        drop, reason = _should_drop(im)
        im["_drop"] = bool(drop)
        im["_reason"] = reason
        rec = {
            "page_idx": im["page_idx"],
            "order": im["order"],
            "w": im["w"], "h": im["h"], "area": im["area"],
            "ext": im["ext"],
            "dhash": im["dhash"],
            "has_rect": im["has_rect"],
            "is_hf": im["is_hf"],
            "reason": reason,
        }
        if drop:
            audit["dropped"].append(rec)
        else:
            if im.get("is_small"):
                audit["kept_small"].append(rec)

    # ---------- 第三遍：重新打开 PDF，仅对“保留图片”落盘，并输出 md + content_list ----------
    # ---------- 第三遍：重新打开 PDF，按位置插入图片并落盘 ----------
    doc = fitz.open(pdf_path)

    items: List[Dict] = []
    md_parts: List[str] = []

    # 为了快速通过 xref 查找图片是否被保留，构建映射表
    kept_img_map = {
        (im["page_idx"], im["xref"]): im 
        for im in all_imgs if not im.get("_drop")
    }

    for entry in page_entries:
        page_idx = entry["page_idx"]
        page = doc.load_page(page_idx)
        md_parts.append(f"\n\n---\n\n## Page {page_idx + 1}\n")

        # ========== 核心修改：提取页面元素并按位置排序 ==========
        # 1. 提取所有文本块，带位置信息
        text_blocks = []
        for block in page.get_text("blocks"):
            # block 结构：(x0, y0, x1, y1, text, block_no, block_type)
            x0, y0, x1, y1, text, _, block_type = block
            if block_type == 0 and text.strip():  # 0 = 文本块
                text_blocks.append({
                    "type": "text",
                    "y0": y0,  # 文本块顶部 y 坐标，用于排序
                    "content": text.strip()
                })
        
        # 2. 提取所有未被过滤的图片块，带位置信息
        img_blocks = []
        for im in entry.get("images", []):
            if im.get("_drop"):
                continue
            # 获取图片在页面上的矩形位置
            rects = page.get_image_rects(im["xref"])
            if not rects:
                continue
            rect = rects[0]  # 取第一个矩形（大部分图片只有一个）
            img_blocks.append({
                "type": "image",
                "y0": rect.y0,  # 图片顶部 y 坐标，用于排序
                "im_info": im
            })
        
        # 3. 合并文本块和图片块，按 y0 升序排序（保证从上到下的顺序）
        all_blocks = text_blocks + img_blocks
        all_blocks.sort(key=lambda x: x["y0"])

        # ========== 遍历排序后的块，生成 MD ==========
        img_no_in_page = 0  # 页内图片序号，重新计数
        for block in all_blocks:
            if block["type"] == "text":
                # 文本块：直接追加到 MD
                md_parts.append(block["content"] + "\n\n")
                items.append({
                    "type": "text",
                    "text": block["content"] + "\n",
                    "page_idx": page_idx,
                    "text_level": 1
                })
            else:
                # 图片块：落盘并插入 MD 链接
                img_no_in_page += 1
                im = block["im_info"]
                xref = im["xref"]
                ext = im.get("ext", "png").lower()

                try:
                    info = doc.extract_image(xref)
                    img_bytes = info.get("image")
                    if not img_bytes:
                        continue
                    ext = (info.get("ext") or ext).lower()
                except Exception:
                    continue

                # 生成不重复的文件名
                filename = f"p{page_idx+1}_img{img_no_in_page}.{ext}"
                out_fp = images_dir_p / filename
                k = 2
                while out_fp.exists():
                    out_fp = images_dir_p / f"p{page_idx+1}_img{img_no_in_page}_{k}.{ext}"
                    k += 1
                # 落盘图片
                out_fp.write_bytes(img_bytes)

                # 插入 MD 图片链接
                rel_path = f"./images/{out_fp.name}"
                md_parts.append(f"\n![]({rel_path})\n\n")
                items.append({
                    "type": "image",
                    "img_path": rel_path,
                    "page_idx": page_idx
                })

    doc.close()

    # 后续的 MD 和 JSON 写入逻辑和原代码一致
    Path(md_out).parent.mkdir(parents=True, exist_ok=True)
    Path(content_list_out).parent.mkdir(parents=True, exist_ok=True)

    with open(md_out, "w", encoding="utf-8") as f:
        f.write("".join(md_parts).strip() + "\n")

    with open(content_list_out, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

    # 审计日志逻辑不变
    try:
        audit_path = Path(content_list_out).with_suffix("").as_posix() + "_image_filter_report.json"
        with open(audit_path, "w", encoding="utf-8") as f:
            json.dump(audit, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

    return True, {"pages": page_count, "items": len(items), "images_dir": str(images_dir_p), "image_filter_report": True}