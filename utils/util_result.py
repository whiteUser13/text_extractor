import json,os
import re
from utils import task_folder_utils
from flask import jsonify
from pathlib import Path
from datetime import datetime
from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, OneCellAnchor
from openpyxl.drawing.xdr import XDRPositiveSize2D
# Excel相关导入
try:
    from openpyxl import Workbook
    from openpyxl.utils import get_column_letter
    from openpyxl.styles import Font, Alignment, PatternFill
    from openpyxl.drawing.image import Image as XLImage
    OPENPYXL_AVAILABLE = True
except ImportError:
    OPENPYXL_AVAILABLE = False
    print("警告: openpyxl 未安装，Excel功能将不可用")

# ---------------- Image embedding helpers ----------------
_IMG_INDEX_CACHE = {}
_PARSED_JSON_CACHE = {}
_IMAGES_DIR_CACHE = {}

_COLS_WITH_IMAGES = set()  # 记录插入过图片的列，避免后续自动列宽覆盖

def _sanitize_filename(name: str) -> str:
    if name is None:
        return ""
    s = str(name).strip()
    s = s.strip().strip('"').strip("'")
    s = re.sub(r'[\\/:*?"<>|]+', "_", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = s.rstrip(". ").strip()
    return s

def _extract_captions(item: dict):
    caps = []
    if not isinstance(item, dict):
        return caps
    for k in ("image_caption", "caption", "captions", "title"):
        v = item.get(k)
        if isinstance(v, list):
            caps.extend([str(x).strip() for x in v if str(x).strip()])
        elif isinstance(v, str) and v.strip():
            caps.append(v.strip())
    out, seen = [], set()
    for c in caps:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out

def _load_parsed_image_index(parsed_json_path: str):
    if not parsed_json_path:
        return {}
    
    parsed_json_path = str(parsed_json_path)
    if parsed_json_path in _IMG_INDEX_CACHE:
        return _IMG_INDEX_CACHE[parsed_json_path]

    try:
        with open(parsed_json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        print(f"[图片索引] 成功加载parsed.json")
    except Exception as e:
        print(f"[图片索引] 加载失败: {e}")
        _IMG_INDEX_CACHE[parsed_json_path] = {}
        return {}

    images = []
    if isinstance(data, dict):
        images = data.get("images") or data.get("image_list") or []
    if not isinstance(images, list):
        images = []

    idx = {}
    for it in images:
        if not isinstance(it, dict):
            continue
        img_path = it.get("img_path") or it.get("path")
        if not img_path:
            continue
        caps = _extract_captions(it)
        if caps:
            idx[str(img_path)] = caps

    _IMG_INDEX_CACHE[parsed_json_path] = idx
    print(f"[图片索引] 加载了 {len(idx)} 个图片索引")
    return idx

def _find_report_parsed_dir(task_base_dir: str, report_name: str):
    """定位某个报告对应的 parsed 目录与 images 目录（更稳）

    旧实现会因为 report_name 里包含通用词（如 parsed/未匹配/已剪切）而误命中别的报告。
    新策略：
    1) 先按目录名精确匹配：parsed/<report_base> 或 parsed/<report_name>
    2) 再按“目录名以 report_base 开头”匹配（解决目录名被截断/清洗的情况）
    3) 最后才做全局 rglob 兜底，但会用打分选择最像的结果
    """
    if not task_base_dir or not report_name:
        return None, None

    base = Path(task_base_dir)
    parsed_root = base / "parsed"

    # cache key needs task path to avoid cross-task contamination
    cache_key = f"{str(base.resolve())}::{report_name}"
    if cache_key in _PARSED_JSON_CACHE and cache_key in _IMAGES_DIR_CACHE:
        return _PARSED_JSON_CACHE[cache_key], _IMAGES_DIR_CACHE[cache_key]

    report_name = str(report_name).strip()
    report_base = report_name.replace("_parsed", "").strip()

    def pick_content_list(d: Path) -> str | None:
        if not d or not d.exists():
            return None
        # Prefer <report_base>_content_list.json
        p1 = d / f"{report_base}_content_list.json"
        if p1.exists():
            return str(p1)
        p2 = d / f"{report_name}_content_list.json"
        if p2.exists():
            return str(p2)
        # common names
        legacy = d / "content_list.json"
        if legacy.exists():
            return str(legacy)
        # any *_content_list.json
        cands = list(d.glob("*_content_list.json"))
        if cands:
            # choose the one with longest common prefix with report_base
            def score(p: Path) -> int:
                stem = p.stem.lower()
                key = report_base.lower()
                n = 0
                for a, b in zip(stem, key):
                    if a == b:
                        n += 1
                    else:
                        break
                return n
            cands.sort(key=score, reverse=True)
            return str(cands[0])
        return None

    def has_images(d: Path) -> str | None:
        if not d or not d.exists():
            return None
        p = d / "images"
        if p.is_dir():
            return str(p)
        return None

    # 1) exact dirs
    exact_dirs = []
    if parsed_root.is_dir():
        exact_dirs.extend([parsed_root / report_base, parsed_root / report_name])
    for d in exact_dirs:
        if d.is_dir():
            images_dir = has_images(d)
            parsed_json = pick_content_list(d)  # keep var name for compatibility
            if images_dir:
                _PARSED_JSON_CACHE[cache_key] = parsed_json
                _IMAGES_DIR_CACHE[cache_key] = images_dir
                print(f"[图片查找] 命中目录: {d.name}")
                print(f"[图片查找] 结果: parsed={parsed_json}, images={images_dir}")
                return parsed_json, images_dir

    # 2) prefix dirs
    if parsed_root.is_dir():
        prefix_dirs = [d for d in parsed_root.iterdir() if d.is_dir() and d.name.startswith(report_base)]
        if prefix_dirs:
            # choose shortest extra tail (closest match)
            prefix_dirs.sort(key=lambda d: len(d.name))
            d = prefix_dirs[0]
            images_dir = has_images(d)
            parsed_json = pick_content_list(d)
            if images_dir:
                _PARSED_JSON_CACHE[cache_key] = parsed_json
                _IMAGES_DIR_CACHE[cache_key] = images_dir
                print(f"[图片查找] 前缀命中目录: {d.name}")
                print(f"[图片查找] 结果: parsed={parsed_json}, images={images_dir}")
                return parsed_json, images_dir

    # 3) global fallback with scoring
    search_dirs = [parsed_root] if parsed_root.is_dir() else [base]
    best = (0, None, None)  # score, parsed_json, images_dir

    def lcp(a: str, b: str) -> int:
        n = 0
        for x, y in zip(a, b):
            if x == y:
                n += 1
            else:
                break
        return n

    report_key = report_base.lower()
    for search_dir in search_dirs:
        print(f"[图片查找] 兜底搜索目录: {search_dir}")
        for d in search_dir.iterdir():
            if not d.is_dir():
                continue
            images_dir = has_images(d)
            if not images_dir:
                continue
            parsed_json = pick_content_list(d)
            # score by dir name prefix + presence of content list
            s = lcp(d.name.lower(), report_key)
            if parsed_json:
                s += 5
            if s > best[0]:
                best = (s, parsed_json, images_dir)

    parsed_json, images_dir = best[1], best[2]
    if parsed_json:
        _PARSED_JSON_CACHE[cache_key] = parsed_json
    if images_dir:
        _IMAGES_DIR_CACHE[cache_key] = images_dir

    print(f"[图片查找] 结果: parsed={parsed_json}, images={images_dir}")
    return parsed_json, images_dir



def _extract_img_tokens(value):
    if value is None:
        return []
    
    if isinstance(value, (list, tuple)):
        s = ";".join(map(str, value))
    else:
        s = str(value)
    
    s = s.strip()
    
    # 提取图片路径
    patterns = [
        r"(images[/\\][^\s'\"<>]+?\.(?:png|jpg|jpeg|webp|bmp|gif))",
        r"([^\s'\"<>]+?\.(?:png|jpg|jpeg|webp|bmp|gif))"
    ]
    
    for pattern in patterns:
        matches = re.findall(pattern, s, flags=re.I)
        if matches:
            return [x.replace("\\", "/") for x in matches]
    
    return []

def _resolve_image_path_for_value(value, task_base_dir: str, report_name: str) -> str | None:
    """解析图片路径到真实文件 - 只返回实际匹配的文件"""
    print(f"\n开始解析图片路径")
    print(f"单元格值: {value}")
    print(f"报告名称: {report_name}")
    
    if not value or not isinstance(value, str):
        print("值无效")
        return None
    
    # 查找 images 目录：先按目录名精确拼接（更快），不行再用 _find_report_parsed_dir 兜底搜索
    report_base = report_name.replace("_parsed", "").strip()
    candidate = Path(task_base_dir) / "parsed" / report_base / "images"
    images_dir = str(candidate) if candidate.is_dir() else None

    if not images_dir:
        _, images_dir = _find_report_parsed_dir(task_base_dir, report_name)

    print("***************")
    print(images_dir)
    if not images_dir:
        print("未找到images目录")
        return None
    
    images_dir_path = Path(images_dir)
    print(f"images目录: {images_dir_path}")
    
    # 获取images目录中的所有图片文件
    all_images = []
    for ext in ['.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif', '.JPG', '.JPEG', '.PNG']:
        files = list(images_dir_path.glob(f"*{ext}"))
        all_images.extend(files)
    
    if not all_images:
        print(f"× images目录为空，没有图片文件")
        return None
    
    print(f"找到 {len(all_images)} 个图片文件")
    
    # 显示找到的文件
    for i, img_file in enumerate(all_images[:10]):  # 只显示前10个
        print(f"  {i+1}. {img_file.name}")
    if len(all_images) > 10:
        print(f"  ... 还有 {len(all_images)-10} 个文件")
    
    # 方法1：尝试直接匹配提取的图片token
    tokens = _extract_img_tokens(value)
    if tokens:
        token = tokens[0]
        token_filename = Path(token).name
        print(f"尝试匹配文件: {token_filename}")
        
        # 精确匹配（不区分大小写）
        for img_file in all_images:
            if img_file.name.lower() == token_filename.lower():
                print(f"√ 精确匹配到文件: {img_file}")
                return str(img_file.resolve())
        
        # 部分匹配（文件名包含token的部分）
        if '_' in token_filename or '.' in token_filename:
            # 提取可能的哈希部分（文件名去掉扩展名）
            name_without_ext = token_filename.split('.')[0]
            
            # 尝试不同的分割方式
            possible_parts = []
            if '_' in name_without_ext:
                possible_parts.extend(name_without_ext.split('_'))
            else:
                possible_parts.append(name_without_ext)
            
            # 尝试匹配每个部分
            for part in possible_parts:
                if len(part) > 6:  # 可能是哈希值或其他标识
                    for img_file in all_images:
                        if part.lower() in img_file.name.lower():
                            print(f"√ 部分匹配到文件: {img_file} (包含 {part})")
                            return str(img_file.resolve())
    
    # 方法2：根据单元格内容猜测
    value_lower = value.lower()
    
    # 常见的图片关键词
    image_keywords = [
        "地块", "项目", "范围", "示意图", "位置", "平面", "布局",
        "现状", "规划", "设计", "施工", "建设", "工程", "地图",
        "总平", "红线", "边界", "现场", "照片", "效果图", "测点",
        "调查", "监测", "采样", "钻孔", "剖面", "柱状图"
    ]
    
    for keyword in image_keywords:
        if keyword in value_lower:
            print(f"发现关键词 '{keyword}'，尝试查找")
            matching_files = []
            for img_file in all_images:
                if keyword in img_file.name:
                    matching_files.append(img_file)
            
            if matching_files:
                print(f"√ 通过关键词找到 {len(matching_files)} 个文件，使用第一个: {matching_files[0]}")
                return str(matching_files[0].resolve())
            else:
                print(f"× 没有找到包含关键词 '{keyword}' 的文件")
    
    # 方法3：查找报告相关的文件
    report_parts = report_name.split('_')
    for part in report_parts:
        if len(part) > 2:  # 避免太短的词
            print(f"尝试匹配报告名部分: {part}")
            matching_files = []
            for img_file in all_images:
                if part in img_file.name:
                    matching_files.append(img_file)
            
            if matching_files:
                print(f"√ 通过报告名部分找到 {len(matching_files)} 个文件，使用第一个: {matching_files[0]}")
                return str(matching_files[0].resolve())
    
    # 方法4：如果所有方法都失败，检查parsed.json中的映射
    parsed_json_path, _ = _find_report_parsed_dir(task_base_dir, report_name)
    if parsed_json_path:
        print(f"尝试从parsed.json获取映射...")
        idx = _load_parsed_image_index(parsed_json_path)
        
        if tokens and idx:
            token = tokens[0]
            if token in idx:
                caps = idx[token]
                print(f"在索引中找到caption: {caps}")
                
                for cap in caps:
                    if not cap:
                        continue
                    
                    stem = _sanitize_filename(cap)
                    if not stem:
                        continue
                    
                    print(f"尝试匹配caption: {stem}")
                    
                    # 精确匹配
                    for ext in ['.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif']:
                        exact_file = images_dir_path / f"{stem}{ext}"
                        if exact_file.exists():
                            print(f"√ 通过caption精确匹配到文件: {exact_file}")
                            return str(exact_file.resolve())
                    
                    # 模糊匹配
                    for ext in ['.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif']:
                        pattern = f"*{stem}*{ext}"
                        matches = list(images_dir_path.glob(pattern))
                        if matches:
                            print(f"√ 通过caption模糊匹配到文件: {matches[0]}")
                            return str(matches[0].resolve())
    
    print(f"× 没有找到匹配的图片文件")
    return None

def _col_width_to_px(w: float) -> int:
    # openpyxl列宽到像素的近似换算：px ≈ width*7 + 5
    try:
        return int(float(w) * 7 + 5)
    except Exception:
        return 300


def _row_height_to_px(h: float) -> int:
    # openpyxl行高单位是 points：px ≈ pt * 96/72 = pt * 4/3
    try:
        return int(float(h) * 4 / 3)
    except Exception:
        return 120


def _emu(px: int) -> int:
    # 1px ≈ 9525 EMU
    return int(px * 9525)


def _resolve_image_paths_for_value(value, task_base_dir: str, report_name: str) -> list[str]:
    """解析 value 中的所有图片token，返回真实存在的图片文件路径列表（可能多张）"""
    print(f"\n开始解析图片路径(多张)")
    print(f"单元格值: {value}")
    print(f"报告名称: {report_name}")

    if not value:
        return []

    s = value if isinstance(value, str) else str(value)
    tokens = _extract_img_tokens(s)
    if not tokens:
        return []

    # 定位 images 目录
    report_base = str(report_name).replace("_parsed", "").strip()
    candidate = Path(task_base_dir) / "parsed" / report_base / "images"
    images_dir = str(candidate) if candidate.is_dir() else None
    if not images_dir:
        _, images_dir = _find_report_parsed_dir(task_base_dir, report_name)

    if not images_dir:
        print("未找到images目录")
        return []

    images_dir_path = Path(images_dir)

    # 建立文件名索引（lowercase -> real path）
    file_map = {}
    try:
        for p in images_dir_path.iterdir():
            if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}:
                file_map[p.name.lower()] = str(p.resolve())
    except Exception:
        pass

    resolved = []
    for t in tokens:
        fname = Path(t).name.lower()
        real = file_map.get(fname)
        if real and os.path.exists(real):
            resolved.append(real)
        else:
            print(f"× token未匹配到文件: {t}")

    # 去重保序
    out, seen = [], set()
    for p in resolved:
        if p not in seen:
            seen.add(p)
            out.append(p)

    print(f"√ 成功解析到 {len(out)} 张图片")
    return out


def _maybe_embed_image(
    ws,
    row_idx: int,
    col_idx: int,
    value,
    task_base_dir: str,
    report_name: str,
    max_images: int | None = None,
    padding_px: int = 6,
) -> bool:
    """Embed one or more images into a single cell (max two per row)."""

    if not OPENPYXL_AVAILABLE:
        return False

    # Fast check to avoid filesystem work for non-image values
    s = value if isinstance(value, str) else str(value)
    if not _extract_img_tokens(s):
        return False

    img_files = _resolve_image_paths_for_value(value, task_base_dir, report_name)
    if not img_files:
        return False

    if max_images is not None:
        img_files = img_files[:max_images]

    cell = ws.cell(row=row_idx, column=col_idx)
    col_letter = get_column_letter(col_idx)

    # Current column width / row height
    cur_col_w = ws.column_dimensions[col_letter].width or 20
    cur_row_h = ws.row_dimensions[row_idx].height or 18

    cell_w_px = _col_width_to_px(cur_col_w)
    cell_h_px = _row_height_to_px(cur_row_h)

    # Grid layout: 1 per row if only one, otherwise 2 per row
    img_count = len(img_files)
    if img_count == 1:
        grid_cols, grid_rows = 1, 1
    else:
        grid_cols = 2
        grid_rows = (img_count + 1) // 2

    # Grow row height to fit all rows of images
    min_slot_h = 170
    desired_cell_h_px = grid_rows * min_slot_h + padding_px * (grid_rows + 1)
    if cell_h_px < desired_cell_h_px:
        ws.row_dimensions[row_idx].height = max(cur_row_h, desired_cell_h_px * 72 / 96)  # px->pt
        cell_h_px = desired_cell_h_px

    slot_w = max(1, (cell_w_px - padding_px * (grid_cols + 1)) // grid_cols)
    slot_h = max(1, (cell_h_px - padding_px * (grid_rows + 1)) // grid_rows)

    # If the column is too narrow, widen it a bit
    if grid_cols > 1 and slot_w < 120:
        ws.column_dimensions[col_letter].width = max(ws.column_dimensions[col_letter].width or 0, 45)
        cell_w_px = _col_width_to_px(ws.column_dimensions[col_letter].width)
        slot_w = max(1, (cell_w_px - padding_px * (grid_cols + 1)) // grid_cols)

    # Clear text to avoid showing path strings
    cell.value = None

    ok_any = False
    for i, img_path in enumerate(img_files):
        try:
            if not os.path.exists(img_path):
                continue

            img = XLImage(img_path)

            ow = getattr(img, 'width', 0) or 1
            oh = getattr(img, 'height', 0) or 1
            scale = min(slot_w / ow, slot_h / oh, 1.0)
            img.width = int(ow * scale)
            img.height = int(oh * scale)

            # Grid position
            r = i // grid_cols
            c = i % grid_cols

            # Slot top-left
            x0 = padding_px + c * (slot_w + padding_px)
            y0 = padding_px + r * (slot_h + padding_px)

            # Center within slot
            x_off = x0 + max(0, (slot_w - img.width) // 2)
            y_off = y0 + max(0, (slot_h - img.height) // 2)

            _set_onecell_anchor(img, row_idx, col_idx, x_off, y_off)
            ws.add_image(img)
            ok_any = True
        except Exception as e:
            print(f"Image embed failed: {e}")

    if ok_any:
        _COLS_WITH_IMAGES.add(col_idx)
        return True
    return False

def _set_onecell_anchor(img, row_idx, col_idx, x_off_px, y_off_px):
    # openpyxl 的 row/col 是从 0 开始计数
    marker = AnchorMarker(
        col=col_idx - 1,
        colOff=_emu(x_off_px),
        row=row_idx - 1,
        rowOff=_emu(y_off_px),
    )
    size = XDRPositiveSize2D(_emu(img.width), _emu(img.height))
    img.anchor = OneCellAnchor(_from=marker, ext=size)
# 其他函数保持不变...
def find_nested_value(data, target_key):
    # target_key为header，data为提取的json字典
    """在嵌套结构中查找目标键的值"""
    if data is None:
        return ""

    if isinstance(data, dict):
        if target_key in data:
            value = data[target_key]
            return format_cell_value(value)

        for key, value in data.items():
            if target_key in key:
                return format_cell_value(value)

        for key, value in data.items():
            if isinstance(value, (dict, list)):
                result = find_nested_value(value, target_key)
                if result not in ["", None]:
                    return result

    elif isinstance(data, list):
        if data and all(isinstance(item, str) for item in data):
            for item in data:
                if target_key in item:
                    return item

        for item in data:
            if isinstance(item, (dict, list)):
                result = find_nested_value(item, target_key)
                if result not in ["", None]:
                    return result

    return ""

def format_cell_value(value):
    """格式化单元格值"""
    if value is None:
        return ""
    elif isinstance(value, bool):
        return "是" if value else "否"
    elif isinstance(value, (list, dict)):
        try:
            if isinstance(value, list):
                return "; ".join(str(item) for item in value)
            else:
                return json.dumps(value, ensure_ascii=False, indent=2)
        except:
            return str(value)
    else:
        return str(value)

def _generate_excel_from_folder(data):
    """从文件夹数据生成Excel报告 - 支持任务目录格式"""
    folder_name = data.get("folder_name")
    task_id = data.get("task_id")
    task_name = data.get("task_name")
    headers = data.get("headers", [])
    header_mode = data.get("header_mode", "all")
    custom_output_path = data.get("excel_output_path")
    no_subfolder = data.get("no_subfolder", False)

    print(f"\n开始生成Excel报告")
    print(f"任务ID: {task_id}")
    print(f"文件夹: {folder_name}")
    print(f"任务: {task_name}")

    if not folder_name or not task_id:
        return jsonify({"error": "缺少必要参数 folder_name 或 task_id"}), 400

    if not headers:
        return jsonify({"error": "缺少表头配置"}), 400

    # 配置路径
    if custom_output_path:
        if no_subfolder:
            excel_output_dir = Path(custom_output_path)
        else:
            excel_output_dir = Path(custom_output_path) / folder_name
        os.makedirs(excel_output_dir, exist_ok=True)
    else:
        task_dirs = task_folder_utils.get_task_dirs(task_id)
        excel_output_dir = task_dirs["results"] / folder_name
        os.makedirs(excel_output_dir, exist_ok=True)

    # 从任务目录读取数据
    task_dirs = task_folder_utils.get_task_dirs(task_id)
    all_data = _load_task_data_from_filesystem(task_id, folder_name, headers)

    if len(all_data) == 0:
        return jsonify({"error": "没有加载到任何数据"}), 400

    # 生成Excel文件名
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    excel_filename = f"{task_name}_报告_{timestamp}.xlsx"
    excel_path = excel_output_dir / excel_filename

    try:
        # 创建工作簿
        wb = Workbook()
        ws = wb.active
        ws.title = task_name[:31]

        # 写入表头
        for col_idx, header in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col_idx, value=header)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.alignment = Alignment(horizontal='center', vertical='center')
            cell.fill = PatternFill(start_color="366092", end_color="366092", fill_type="solid")

        # 写入数据到Excel
        row_index = 2
        for data_row in all_data:
            for col_idx, header in enumerate(headers, 1):
                value = data_row.get(header, "")
                cell = ws.cell(row=row_index, column=col_idx, value=value)
                # 尝试嵌入图片
                _maybe_embed_image(ws, row_index, col_idx, value, str(task_dirs['base']), data_row.get('报告名称',''))
            row_index += 1

        # 调整列宽
        for col_idx, header in enumerate(headers, 1):
            if col_idx in _COLS_WITH_IMAGES:
                continue  # 图片列不要被覆盖列宽
            max_len = len(str(header))
            for row in range(2, row_index):
                cell_value = ws.cell(row=row, column=col_idx).value
                if cell_value:
                    max_len = max(max_len, len(str(cell_value)))

            width = min(max_len + 2, 50)
            ws.column_dimensions[get_column_letter(col_idx)].width = width

        # 保存Excel文件
        wb.save(excel_path)
        file_size = os.path.getsize(excel_path)

    except Exception as e:
        print(f"Excel文件写入失败: {e}")
        return jsonify({"error": f"Excel文件写入失败: {str(e)}"}), 500

    # 构建下载URL
    if 'task_id' in data:
        try:
            relative_path = excel_path.relative_to(task_dirs["results"])
            download_url = f"/task-results/{task_id}/{relative_path}"
        except:
            download_url = f"/task-results/{task_id}/{folder_name}/{excel_filename}"
    elif custom_output_path and no_subfolder:
        download_url = f"/documents/excel_result/{folder_name}/{excel_filename}"
    elif custom_output_path:
        clean_folder_name = folder_name.strip('/')
        download_url = f"/documents/excel_result/{clean_folder_name}/{excel_filename}"

    return jsonify({
        "message": f"Excel报告生成成功",
        "success": True,
        "task_id": task_id,
        "folder_name": folder_name,
        "task_name": task_name,
        "header_mode": header_mode,
        "excel_filename": excel_filename,
        "download_url": download_url,
        "file_path": str(excel_path),
        "headers_count": len(headers),
        "data_count": len(all_data),
        "generation_mode": "folder_data",
        "status": "completed",
        "file_size": file_size,
        "download_instructions": "如果无法自动下载，请手动访问下载URL"
    }), 200

def _load_task_data_from_filesystem(task_id, folder_name, headers):
    """从任务目录加载实际的JSON数据内容"""
    all_data = []
    base_fields = {"报告名称", "地块编号"}

    # 获取任务目录
    task_dirs = task_folder_utils.get_task_dirs(task_id)

    # 从提取结果文件夹读取实际的提取数据
    extraction_dir = task_dirs["extraction_results"]
    extraction_data_loaded = False

    if extraction_dir.exists():
        json_files = list(extraction_dir.rglob("*_extracted.json"))

        specific_folder = extraction_dir / folder_name
        if specific_folder.exists():
            json_files.extend(list(specific_folder.rglob("*_extracted.json")))

        json_files = list(set(json_files))

        for json_file in json_files:
            try:
                with open(json_file, 'r', encoding='utf-8') as f:
                    json_data = json.load(f)

                # 创建数据行
                data_row = {}

                # 提取报告名称
                report_name = json_file.stem
                if '_extracted' in report_name:
                    report_name = report_name.replace('_extracted', '')

                data_row["报告名称"] = report_name
                data_row["地块编号"] = folder_name

                # 填充提取规则字段
                for header in headers:
                    if header in base_fields:
                        continue

                    value = find_nested_value(json_data, header)
                    if value:
                        data_row[header] = value

                all_data.append(data_row)
                extraction_data_loaded = True

            except Exception as e:
                print(f"读取提取文件失败 {json_file}: {str(e)}")

    # 从判别结果文件夹读取实际的判别数据
    discrimination_dir = task_dirs["discrimination_results"]
    discrimination_data_loaded = False

    if discrimination_dir.exists():
        json_files = list(discrimination_dir.rglob("*_discrimination.json"))

        specific_folder = discrimination_dir / folder_name
        if specific_folder.exists():
            json_files.extend(list(specific_folder.rglob("*_discrimination.json")))

        json_files = list(set(json_files))

        for json_file in json_files:
            try:
                with open(json_file, 'r', encoding='utf-8') as f:
                    json_data = json.load(f)

                base_filename = json_file.stem
                if '_discrimination' in base_filename:
                    base_filename = base_filename.replace('_discrimination', '')

                # 查找对应的数据行
                existing_row = None
                for data_row in all_data:
                    if (data_row.get("报告名称") == base_filename or
                            data_row.get("报告名称", "").startswith(base_filename) or
                            base_filename.startswith(data_row.get("报告名称", ""))):
                        existing_row = data_row
                        break

                if not existing_row:
                    data_row = {}
                    data_row["报告名称"] = base_filename
                    data_row["地块编号"] = folder_name
                    existing_row = data_row
                    all_data.append(existing_row)

                # 填充判别规则字段
                for header in headers:
                    if header in base_fields:
                        continue

                    if header in existing_row and existing_row[header]:
                        continue

                    value = find_nested_value(json_data, header)
                    if value:
                        existing_row[header] = value
                        discrimination_data_loaded = True

            except Exception as e:
                print(f"读取判别文件失败 {json_file}: {str(e)}")

    return all_data