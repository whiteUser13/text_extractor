from flask import Blueprint, request, jsonify
from pathlib import Path
import json
import traceback
import time
from collections import Counter
from datetime import datetime
import hashlib
import os
import re  # 0316新增
from utils.task_folder_utils import task_folder_utils
from utils.auth_util import AuthUtils
from utils.task_verify import task_permission_required, scene_or_task_permission_required
from utils.json_manager import ResultCollectionJsonFileManager

statistic_bp = Blueprint("statistic", __name__)

result_collection_json_manager = ResultCollectionJsonFileManager()
def _parse_rule_fields_from_user_rules(user_rules_text: str) -> list:
    """
    0311修改：从判别结果里的 user_rules 文本提取“需要提取或判别的字段”清单
    兼容你的样例格式： '需要提取或判别的字段 A、B、C、...'
    """
    if not isinstance(user_rules_text, str) or not user_rules_text.strip():
        return []

    # 找到“需要提取或判别的字段”这一行
    m = re.search(r"需要提取或判别的字段\s*([^\n\r]+)", user_rules_text)
    if not m:
        return []

    raw = m.group(1).strip()
    # 你的分隔符主要是 “、” 和 “,”
    parts = re.split(r"[、,，]\s*", raw)
    fields = []
    seen = set()
    for p in parts:
        name = p.strip().strip(" \"'“”")
        if name and name not in seen:
            fields.append(name)
            seen.add(name)
    return fields


def _field_triggered(field_name: str, field_value) -> bool:
    """
    0316修改：判断某个判别字段是否“触发异常/风险”，用于违规饼图多分类计数
    修复点：像“开发活动性质是否违规判定”这种字段名包含“是否”，但本质是“判定字段”，
           必须优先走“判定类”逻辑，而不是“是否=是/否”逻辑。
    """
    if not field_name:
        return False

    v = "" if field_value is None else str(field_value).strip()

    # 1) 先处理判定/违规判定类（优先级最高）——修复“开发活动性质是否违规判定”被误判的问题
    if ("判定" in field_name) or ("违规" in field_name):
        # 值包含“违规”，且不是“不违规/无违规”
        if "违规" in v and ("不违规" not in v) and ("无违规" not in v):
            # 排除“虽然写违规但结论说不属于/未满足”等反例措辞
            neg_kw = ["不属于违规", "未满足违规", "不构成违规", "不属于", "未满足", "不构成"]
            if any(k in v for k in neg_kw):
                return False
            return True

    # 2) 是否类字段：一般“是”触发
    if "是否" in field_name:
        # 特例：是否2019年前完工 —— “否”触发
        if field_name == "是否2019年前完工":
            return v.startswith("否")
        return v.startswith("是")

    # 3) 超标类：是触发
    if "超标" in field_name:
        return v.startswith("是")

    # 4) 一致性/符合性：不一致/不符合触发
    if ("一致" in field_name) or ("符合" in field_name) or ("匹配" in field_name):
        bad_kw = ["不一致", "不符合", "不匹配", "不相符", "不一致性", "不满足"]
        return any(k in v for k in bad_kw)

    # 5) 动工时间类：识别到年份>=2019 触发（可按你业务再细化）
    if "动工时间" in field_name:
        m = re.search(r"(19\d{2}|20\d{2})", v)
        if m:
            try:
                year = int(m.group(1))
                return year >= 2019
            except Exception:
                return False
        return False

    return False


def api_ok(data=None):
    return {"code": 0, "msg": "ok", "data": data}


def api_fail(msg: str, code: int = 1):
    return {"code": code, "msg": msg}


def _safe_load_json(p: Path):
    try:
        if not p or (not p.exists()) or (not p.is_file()):
            return None
        # 使用文件锁，确保多任务读取时文件不被破坏
        with result_collection_json_manager.file_lock:
            return result_collection_json_manager.load_json_without_lock(p)
    except Exception:
        return None

def _get_task_root_from_task_dirs(task_dirs: dict):
    """
    0326修改：从 task_dirs 推断任务根目录。
    """
    try:
        if not isinstance(task_dirs, dict):
            return None
        for key in ["parsed", "extraction_results", "discrimination_results", "downloads", "pdf_cut"]:
            p = task_dirs.get(key)
            if isinstance(p, Path):
                return p.parent
            if isinstance(p, str) and p:
                return Path(p).parent
    except Exception:
        return None
    return None


def _load_result_collection(task_id: str, task_dirs: dict = None, scene_id: str = None) -> list:
    """
    0328修改：根据 sceneId 读取对应的 result_collection_{sceneid}.json 文件。
    如果未提供 scene_id，则从 tasks.json 中获取。
    如果 scene_id 无效，则返回空列表。
    """
    candidates = []
    try:
        # 如果未提供 scene_id，从 tasks.json 中获取
        if scene_id is None and task_id:
            task_record = _get_task_record_from_tasks_json(task_id)
            scene_id = str(task_record.get("sceneId") or "").strip()
        
        # 如果 scene_id 为空或无效，不创建文件，直接返回空
        if not scene_id or scene_id == "None":
            print(f"任务 {task_id} 的 sceneId 无效，返回空结果")
            return []

        filename = f"result_collection_{scene_id}.json"

        if task_dirs:
            task_root = _get_task_root_from_task_dirs(task_dirs)
            if task_root:
                candidates.append(Path(task_root) / filename)
        if task_id:
            candidates.append(Path("data/tasks") / task_id / filename)
        seen = set()
        for p in candidates:
            sp = str(p)
            if sp in seen:
                continue
            seen.add(sp)
            obj = _safe_load_json(p)
            if isinstance(obj, list):
                return _extract_construction_year_from_collection(obj)
    except Exception as e:
        print(f"Error loading result collection: {str(e)}")
        return []
    return []

def _normalize_collection_extraction_result(item: dict) -> dict:
    if not isinstance(item, dict):
        return {}
    results = item.get("extraction_results")
    if isinstance(results, list) and results and isinstance(results[0], dict):
        return results[0]
    if isinstance(results, dict):
        return results
    return {}


def _normalize_collection_judge_result(item: dict) -> dict:
    if not isinstance(item, dict):
        return {}
    jr = item.get("judge_results")
    return jr if isinstance(jr, dict) else {}


def _normalize_collection_filename(item: dict) -> str:
    """
    0327修改：线索/统计场景下，优先返回任务内部文件标识 foldername，
    保证与 file/details、pdf/path 这条链路使用的 file_name 一致。
    filename 仅作为兜底展示名。
    """
    if not isinstance(item, dict):
        return ""
    return str(
        item.get("foldername")
        or item.get("file_name")
        or item.get("filename")
        or ""
    ).strip()


def _resolve_preview_file_name(task_dirs: dict, item: dict) -> str:
    """
    0327修改：把 result_collection.json 中的 foldername / filename / file_name
    映射为“结果预览链路真正可用”的 file_name。
    只用于线索清单，不改动详情/PDF主链路。
    """
    if not isinstance(item, dict):
        return ""

    parsed_names = _list_parsed_file_names(task_dirs)
    if not parsed_names:
        return _normalize_collection_filename(item)

    candidates = [
        str(item.get("foldername") or "").strip(),
        str(item.get("file_name") or "").strip(),
        str(item.get("filename") or "").strip(),
    ]
    candidates = [x for x in candidates if x]

    # 1) 先精确匹配
    for c in candidates:
        if c in parsed_names:
            return c

    # 2) 再按归一化名字匹配
    parsed_name_map = {}
    for p in parsed_names:
        parsed_name_map[_normalize_name_for_match(p)] = p

    for c in candidates:
        norm = _normalize_name_for_match(c)
        if norm in parsed_name_map:
            return parsed_name_map[norm]

    # 3) 兜底
    return _normalize_collection_filename(item)


def _normalize_name_for_match(name: str) -> str:
    """
    0327修改：统一名称匹配规则
    - 去扩展名
    - 去常见后缀：_parsed / _分块结果 / _错误_分块结果 / _错误
    - 去首尾空格
    """
    s = str(name or "").strip()
    if not s:
        return ""

    # 去扩展名
    s = Path(s).stem

    changed = True
    while changed:
        changed = False
        for suffix in ["_parsed", "_分块结果", "_错误_分块结果", "_错误"]:
            if s.endswith(suffix):
                s = s[: -len(suffix)].strip()
                changed = True

    return s


def _normalize_collection_is_valid(item: dict) -> str:
    """
    直接返回 result_collection.json 中的 is_valid 字段值，并做常见文本归一化。
    """
    if not isinstance(item, dict):
        return "unknown"

    v = item.get("is_valid")

    if isinstance(v, bool):
        return "legal" if v else "illegal"

    if isinstance(v, (int, float)):
        # 0/1 映射（兼容老数据）
        if v == 0:
            return "legal"
        if v == 1:
            return "illegal"
        return "unknown"

    if isinstance(v, str):
        s = v.strip()
        s_lower = s.lower()

        if s_lower in ["legal", "illegal", "unknown"]:
            return s_lower

        # 常见直接文本
        # if "不违规" in s or "无违规" in s or "未违规" in s:
        #     return "legal"
        # if "违规" in s or "违法" in s or "不合规" in s:
        #     # 关键词“违规”可能包含“不违规”、排除后再判断
        #     if "不违规" in s or "无违规" in s or "未违规" in s:
        #         return "legal"
        #     return "illegal"

        # 兼容“评审前违规建设情况”字段的多种书写
        if re.search(r"评审前违规建设情况\s*[:：]\s*['\"“”‘’]?\s*否\s*['\"“”‘’]?", s):
            return "legal"
        if re.search(r"评审前违规建设情况\s*[:：]\s*['\"“”‘’]?\s*是\s*['\"“”‘’]?", s):
            return "illegal"

        # 其它直接归为 unknown，避免误把任意字符当非法
        return "unknown"

    return "unknown"


def _normalize_collection_update_time(item: dict):
    if not isinstance(item, dict):
        return ""
    v = item.get("update_time")
    if isinstance(v, (int, float)):
        try:
            ts = float(v)
            if ts > 1e12:
                ts = ts / 1000.0
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return str(v)
    if isinstance(v, str):
        return v
    return ""


def _get_extraction_fields_from_collection(collection: list, task_dirs: dict = None) -> list:
    fields = []
    seen = set()
    for item in collection or []:
        rules = item.get("extraction_rules") if isinstance(item, dict) else None
        if isinstance(rules, list):
            for f in rules:
                name = str(f).strip()
                if name and name not in seen:
                    fields.append(name)
                    seen.add(name)
    if fields:
        return fields
    return _get_extraction_fields(task_dirs) if task_dirs else []


def _get_field_extraction_rate_from_collection(collection: list, task_dirs: dict = None) -> list:
    target_fields = _get_extraction_fields_from_collection(collection, task_dirs)
    if not target_fields:
        return []
    field_total = len(collection or [])
    field_found_count = Counter()
    for item in collection or []:
        extraction_result = _normalize_collection_extraction_result(item)
        for field in target_fields:
            if field in extraction_result:
                value = extraction_result.get(field)
                if value is None:
                    continue
                if isinstance(value, str):
                    txt = value.strip()
                    if txt and txt not in ["未找到", "未提及", "未明确", "无法判断"]:
                        field_found_count[field] += 1
                else:
                    field_found_count[field] += 1
    result = []
    for field in target_fields:
        found = field_found_count.get(field, 0)
        rate = round((found / field_total) * 100, 2) if field_total > 0 else 0
        result.append({
            "field_name": field,
            "found_count": found,
            "total_files": field_total,
            "rate": rate,
        })
    return result


def _stem(name: str) -> str:
    return Path(name).stem if name else ""


def _list_parsed_file_names(task_dirs: dict):
    """
    从 parsed 目录获取所有子文件夹名称作为 file_name。
    """
    parsed_dir: Path = task_dirs["parsed"]
    if (not parsed_dir.exists()) or (not parsed_dir.is_dir()):
        return []
    return sorted([p.name for p in parsed_dir.iterdir() if p.is_dir()])




def _parse_discrimination_json(dis_obj):
    """
    判别样例结构：外层 list -> 元素 dict -> {file_name: [ {...} ]}。
    - 成功样例：arr[0] 是包含大量判别字段的 dict（含 user_rules）
    - 失败样例：arr[0] 可能包含 error/raw_response/user_rules 等
    这里返回：dis_map（file_name -> list[...]）, err=None
    """
    if not isinstance(dis_obj, list) or len(dis_obj) == 0:
        return {}, "判别结果外层不是list或为空"

    first = dis_obj[0]
    if not isinstance(first, dict):
        return {}, "判别结果list[0]不是dict"

    return first, None


def _find_discrimination_json(task_dirs: dict, file_name: str):
    """
    在 discrimination_results 目录内找最匹配该 file_name 的 json。
    - 优先：文件名包含匹配
    - 其次：读内容，看 dis_obj[0] 是否包含 file_name 这个 key
    - 再其次：仅一个文件兜底
    """
    dis_dir: Path = task_dirs["discrimination_results"]
    if not dis_dir.exists():
        return None, None

    json_files = sorted(dis_dir.rglob("*.json"))
    if not json_files:
        return None, None

    target_stem = _stem(file_name)

    # 1) 文件名包含匹配
    name_hits = [p for p in json_files if target_stem and target_stem in p.stem]
    if name_hits:
        name_hits.sort(key=lambda x: (len(x.stem), x.name))
        return name_hits[0], _safe_load_json(name_hits[0])

    # 2) 内容匹配：判断 json 中是否含 file_name 这个 key
    for p in json_files:
        obj = _safe_load_json(p)
        if not obj:
            continue
        if isinstance(obj, list) and len(obj) > 0 and isinstance(obj[0], dict) and file_name in obj[0]:
            return p, obj

    # 3) 兜底：只有一个文件
    if len(json_files) == 1:
        return json_files[0], _safe_load_json(json_files[0])

    return None, None


def _extract_judge_for_file(dis_map: dict, file_name: str):
    """
    dis_map: {file_name: [ {...}, ... ]}（成功样例就是这个结构）
    返回：
      judge_result: dict
      judge_error: str|None
    """
    if not isinstance(dis_map, dict):
        return {}, "判别结果结构不是dict"

    arr = dis_map.get(file_name)

    # 0313修改：兜底——如果 file_name 找不到，但判别 json 只有一个 key，就用那个 key
    if (not isinstance(arr, list) or len(arr) == 0) and isinstance(dis_map, dict) and len(dis_map) == 1:
        only_key = next(iter(dis_map.keys()))
        arr = dis_map.get(only_key)

    if not isinstance(arr, list) or len(arr) == 0:
        return {}, "判别结果中未找到该文件key或结果为空"

    first = arr[0]
    if not isinstance(first, dict):
        return {}, "判别结果元素不是dict"

    # 失败结构：包含 error
    if first.get("error"):
        return {}, str(first.get("error"))

    # 成功结构：直接返回 dict（包含判别字段全集）
    return first, None
    # return "illegal"
def _compute_valid_from_judge(judge_result: dict, judge_error: str):
    """
    返回 valid: "legal"/"illegal"/"unknown"。
    基于用户逻辑：查找 judge_results，如果存在“评审前违规建设情况：'否'”，则为 legal；
    如果存在“评审前违规建设情况：'是'”，则为 illegal。
    """
    if judge_error:
        return "illegal"

    if not isinstance(judge_result, dict) or not judge_result:
        return "unknown"

    blob = json.dumps(judge_result, ensure_ascii=False)

    if re.search(r"评审前违规建设情况\s*[:：]\s*['\"“”‘’]?\s*否\s*['\"“”‘’]?", blob):
        return "legal"
    if re.search(r"评审前违规建设情况\s*[:：]\s*['\"“”‘’]?\s*是\s*['\"“”‘’]?", blob):
        return "illegal"

    return "unknown"

def _extract_province_from_string(text: str) -> str:
    """
    从任意字符串中提取省份信息
    创建时间: 2024-03-16
    最后修改: 2024-03-16
    说明: 用于从任务名称或文件名中提取省份
    """
    if not text:
        return "其他地区"

    # 标准省份映射
    province_map = {
        # 直辖市
        "北京": "北京市", "上海": "上海市", "天津": "天津市", "重庆": "重庆市",
        # 省份
        "河北": "河北省", "山西": "山西省", "辽宁": "辽宁省", "吉林": "吉林省",
        "黑龙江": "黑龙江省", "江苏": "江苏省", "浙江": "浙江省", "安徽": "安徽省",
        "福建": "福建省", "江西": "江西省", "山东": "山东省", "河南": "河南省",
        "湖北": "湖北省", "湖南": "湖南省", "广东": "广东省", "海南": "海南省",
        "四川": "四川省", "贵州": "贵州省", "云南": "云南省", "陕西": "陕西省",
        "甘肃": "甘肃省", "青海": "青海省", "台湾": "台湾省",
        # 自治区
        "内蒙古": "内蒙古自治区", "广西": "广西壮族自治区", "西藏": "西藏自治区",
        "宁夏": "宁夏回族自治区", "新疆": "新疆维吾尔自治区",
        # 特别行政区
        "香港": "香港特别行政区", "澳门": "澳门特别行政区"
    }

    # 完整名称列表
    full_names = [
        "北京市", "上海市", "天津市", "重庆市",
        "河北省", "山西省", "辽宁省", "吉林省", "黑龙江省",
        "江苏省", "浙江省", "安徽省", "福建省", "江西省", "山东省",
        "河南省", "湖北省", "湖南省", "广东省", "海南省",
        "四川省", "贵州省", "云南省", "陕西省", "甘肃省", "青海省",
        "台湾省", "内蒙古自治区", "广西壮族自治区", "西藏自治区",
        "宁夏回族自治区", "新疆维吾尔自治区",
        "香港特别行政区", "澳门特别行政区"
    ]

    # 1. 先匹配完整名称
    for full_name in full_names:
        if full_name in text:
            print(f"完整名称匹配: '{full_name}' in '{text}'")
            return full_name

    # 2. 再匹配关键词
    for keyword, full_name in province_map.items():
        if keyword in text:
            print(f"关键词匹配: '{keyword}' -> '{full_name}' in '{text}'")
            return full_name

    # 3. 如果文本本身就是省份简称
    text_stripped = text.strip()
    if text_stripped in province_map:
        return province_map[text_stripped]

    print(f"未匹配到任何省份: '{text}'")
    return "其他地区"


def _get_task_name_from_tasks_json(task_id: str) -> str:
    """
    0316修改：从 tasks.json 获取任务名称（用于省份识别/地图高亮）。
    - 不再依赖固定绝对路径，改为复用 _load_all_tasks() 的多路径兜底。
    - 找不到则回退为 task_id。
    """
    try:
        task_id = (task_id or "").strip()
        if not task_id:
            return ""

        tasks = _load_all_tasks()
        if not tasks:
            return task_id

        for t in tasks:
            if str(t.get("id") or "").strip() == task_id:
                name = str(t.get("name") or "").strip()
                return name or task_id

        return task_id
    except Exception:
        return task_id

        
    except Exception as e:
        print(f"读取 tasks.json 失败: {e}")
        import traceback
        traceback.print_exc()
        return task_id


# ==================== 0316修改：任务列表读取====================

def _load_all_tasks() -> list:
    """
    0326修改：读取 tasks.json，返回任务列表。
    统一保留：
      - id
      - name
      - province
      - extractionRules
      - judgmentRules

    修复点：
      1) 正确读取 data/tasks/tasks.json
      2) 兼容 backend/data/tasks/tasks.json
    """
    candidates = []

    try:
        here = Path(__file__).resolve()
        candidates.extend([
            # 当前文件附近
            here.parent / "data" / "tasks" / "tasks.json",
            here.parent.parent / "data" / "tasks" / "tasks.json",
            here.parent.parent / "backend" / "data" / "tasks" / "tasks.json",
        ])
    except Exception:
        pass

    try:
        cwd = Path.cwd().resolve()
        for parent in [cwd] + list(cwd.parents)[:6]:
            candidates.append(parent / "data" / "tasks" / "tasks.json")
            candidates.append(parent / "backend" / "data" / "tasks" / "tasks.json")
    except Exception:
        pass

    # 兜底相对路径
    candidates.extend([
        Path("data/tasks/tasks.json"),
        Path("backend/data/tasks/tasks.json"),
    ])

    seen = set()
    for p in candidates:
        sp = str(p.resolve()) if p.exists() else str(p)
        if sp in seen:
            continue
        seen.add(sp)
        try:
            if p.exists() and p.is_file():
                with open(p, "r", encoding="utf-8") as f:
                    tasks = json.load(f)
                if isinstance(tasks, list):
                    out = []
                    for t in tasks:
                        if not isinstance(t, dict):
                            continue
                        tid = str(t.get("id") or "").strip()
                        if not tid:
                            continue
                        out.append({
                            "id": tid,
                            "name": str(t.get("name") or tid).strip() or tid,
                            "province": str(t.get("province") or "").strip(),
                            "extractionRules": t.get("extractionRules") if isinstance(t.get("extractionRules"),
                                                                                      list) else [],
                            "sceneId": str(t.get("sceneId") or "").strip(),  # ✅ 添加这一行
                            "judgmentRules": t.get("judgmentRules") if isinstance(t.get("judgmentRules"), list) else [],
                        })
                    return out
        except Exception:
            continue

    return []


def _get_task_record_from_tasks_json(task_id: str) -> dict:
    """
    0326修改：从 tasks.json 获取单个任务完整配置记录。
    """
    try:
        task_id = (task_id or "").strip()
        if not task_id:
            return {}
        for t in _load_all_tasks():
            if str(t.get("id") or "").strip() == task_id:
                return t if isinstance(t, dict) else {}
        return {}
    except Exception:
        return {}


def _get_task_province_from_tasks_json(task_id: str) -> str:
    """
    0326修改：从 tasks.json 获取任务省份。
    """
    try:
        record = _get_task_record_from_tasks_json(task_id)
        province = str(record.get("province") or "").strip()
        return province
    except Exception:
        return ""


def _get_tasks_by_scene(scene_id: str) -> list:
    """
    0328修改：根据 scene_id 返回相同场景的所有任务 ID 列表
    """
    print(f"查找场景ID: '{scene_id}'")
    print(f"所有任务的sceneId: {[str(t.get('sceneId', 'None')) for t in _load_all_tasks()]}")
    try:
        scene_id = (scene_id or "").strip()
        if not scene_id:
            return []
        
        tasks = _load_all_tasks()
        task_ids = []
        for t in tasks:
            if str(t.get("id") or "").strip():  # 确保有 id
                task_scene_id = str(t.get("sceneId") or "").strip()
                if task_scene_id == scene_id:
                    task_ids.append(str(t.get("id")).strip())
        
        return task_ids
    except Exception:
        return []


def _extract_explicit_confidence(judge_result: dict):
    """
    0326修改：从判别结果中提取显式置信度，取不到则返回 None。
    """
    if not isinstance(judge_result, dict):
        return None
    for key in ["confidence", "置信度", "可信度", "confidence_score"]:
        if key not in judge_result:
            continue
        value = judge_result.get(key)
        try:
            if isinstance(value, (int, float)):
                return int(value)
            if isinstance(value, str):
                m = re.findall(r"\d+", value)
                if m:
                    return int(m[0])
        except Exception:
            continue
    return None


# ==================== 0327修改：轻量规则置信度计算（仅修复默认85问题） ====================

def _clamp_confidence(value: int, low: int = 0, high: int = 100) -> int:
    """0327修改：限制置信度范围"""
    try:
        iv = int(value)
    except Exception:
        iv = low
    return max(low, min(high, iv))


def _is_uncertain_text(text: str) -> bool:
    """0327修改：判断文本是否存在明显不确定表达"""
    if not text:
        return True
    uncertain_keywords = [
        "未提及", "未找到", "未明确", "无法判断", "无法确定",
        "暂无法判断", "信息不足", "依据不足", "未说明", "未知"
    ]
    return any(k in text for k in uncertain_keywords)


def _score_text_confidence(text: str) -> int:
    """
    0327修改：对单段判别文本做轻量规则评分
    仅做字符串判断，不增加额外 IO，不影响加载速度
    """
    if not text:
        return 35

    score = 55
    txt = str(text).strip()

    # 明确结论加分
    strong_positive = [
        "判断结果：违规", "判断结果:违规", "判断结果：不违规", "判断结果:不违规",
        "属于", "不属于", "是", "否", "存在", "不存在", "一致", "不一致",
        "符合", "不符合", "超标", "未超标"
    ]
    if any(k in txt for k in strong_positive):
        score += 12

    # 有依据/原因分析加分
    support_keywords = ["依据", "原因分析", "分析如下", "判定依据", "结论如下", "综合判断"]
    if any(k in txt for k in support_keywords):
        score += 8

    # 文本较完整加分
    if len(txt) >= 20:
        score += 6
    if len(txt) >= 60:
        score += 4

    # 不确定表达减分
    if _is_uncertain_text(txt):
        score -= 22

    return _clamp_confidence(score)


def _get_clue_confidence(judge_result: dict, field_name: str) -> int:
    """
    0327修改：线索级置信度，按字段单独计算，避免同一文件所有线索都一样
    """
    if not isinstance(judge_result, dict):
        return 35

    # 先看该字段本身是否有显式置信度（极少见，但保留兼容）
    explicit = _extract_explicit_confidence(judge_result)
    field_text = str(judge_result.get(field_name) or "").strip()

    # 如果有显式值，仍然优先使用
    if explicit is not None:
        base = explicit
    else:
        base = _score_text_confidence(field_text)

    # 字段被识别为触发项时，小幅加权，但不大幅波动
    if field_text and not _is_uncertain_text(field_text):
        base += 5

    return _clamp_confidence(base)


# ==================== 0327修改结束 ====================

def _get_triggered_violation_fields(judge_result: dict, task_dirs: dict = None) -> list:
    """
    0326修改：优先根据 tasks.json / config 中的判别字段清单筛选触发项。
    """
    if not isinstance(judge_result, dict) or not judge_result:
        return []

    candidate_fields = []

    # 1) tasks.json -> judgmentRules[].name
    task_id = None
    if isinstance(task_dirs, dict):
        task_root = _get_task_root_from_task_dirs(task_dirs)
        task_id = os.path.basename(task_root) if task_root else None
    if task_id:
        record = _get_task_record_from_tasks_json(task_id)
        jr = record.get("judgmentRules") if isinstance(record, dict) else []
        if isinstance(jr, list):
            for rule in jr:
                if not isinstance(rule, dict):
                    continue
                if rule.get("enabled") is False:
                    continue
                name = str(rule.get("name") or "").strip()
                if name:
                    candidate_fields.append(name)

    # 2) config.json / 兼容旧逻辑
    if not candidate_fields and task_dirs:
        candidate_fields.extend(_get_violation_types_from_config(task_dirs) or [])

    # 3) 最后兜底：judge_results 的 keys
    if not candidate_fields:
        candidate_fields = [k for k in judge_result.keys() if
                            k not in ["user_rules", "user_rules_preview", "confidence", "置信度", "可信度",
                                      "confidence_score"]]

    hits = []
    seen = set()
    for f in candidate_fields:
        if f in seen:
            continue
        seen.add(f)
        if _field_triggered(f, judge_result.get(f)):
            hits.append(f)
    return hits


def _compute_task_region_stat(task_id: str, task_name: str = "", scene_id: str = None) -> dict:
    """
    0328修改：支持scene_id参数，用于场景聚合统计。
    province 暂时从 tasks.json 读取。
    """
    task_dirs = task_folder_utils.get_task_dirs(task_id)
    collection = _load_result_collection(task_id, task_dirs, scene_id)
    province = _get_task_province_from_tasks_json(task_id) or _extract_province_from_string(task_name or "") or "其他地区"
    industry_violation_count = _get_industry_violation_count_from_collection(collection, task_dirs)
    stat = {
        "province": province, 
        "count": len(collection), 
        "illegal_count": 0, 
        "legal_count": 0,
        "industry_violation_count": industry_violation_count,
        # "construction_date":construction_year
    }
    for item in collection:
        valid = _normalize_collection_is_valid(item)
        if valid == "illegal":
            stat["illegal_count"] += 1
        elif valid == "legal":
            stat["legal_count"] += 1
    return stat

    task_dirs = task_folder_utils.get_task_dirs(task_id)
    file_names = _list_parsed_file_names(task_dirs)
    stat = {"province": province, "count": len(file_names), "illegal_count": 0, "legal_count": 0}
    for fn in file_names:
        dis_path, dis_obj = _find_discrimination_json(task_dirs, fn)
        if not (dis_path and dis_obj):
            continue
        dis_map, dis_err = _parse_discrimination_json(dis_obj)
        if dis_err:
            continue
        jr, je = _extract_judge_for_file(dis_map, fn)
        if je:
            continue
        valid = _compute_valid_from_judge(jr, None)
        if valid == "illegal":
            stat["illegal_count"] += 1
        elif valid == "legal":
            stat["legal_count"] += 1
    return stat


def _get_task_config(task_dirs: dict) -> dict:
    """
    获取任务的配置文件内容
    从 tasks/task_id/config.json 读取
    创建时间: 2024-03-12
    最后修改: 2024-03-13
    """
    try:
        print(f"task_dirs 内容: {task_dirs}")

        # 尝试多种方式获取任务根目录
        task_root = None

        # 1. 直接获取 task_root
        if "task_root" in task_dirs:
            task_root = task_dirs.get("task_root")
            print(f"从 task_root 获取: {task_root}")

        # 2. 如果没有 task_root，尝试从其他路径构建
        if not task_root and "task_id" in task_dirs:
            task_id = task_dirs.get("task_id")
            # 假设任务目录在 tasks/{task_id}
            task_root = Path("tasks") / task_id
            print(f"从 task_id 构建 task_root: {task_root}")

        # 3. 如果还没有，尝试从 parsed 路径获取
        if not task_root and "parsed" in task_dirs:
            parsed_path = task_dirs.get("parsed")
            if parsed_path and isinstance(parsed_path, Path):
                # parsed 路径通常是 tasks/{task_id}/parsed
                task_root = parsed_path.parent
                print(f"从 parsed 路径获取 task_root: {task_root}")

        if not task_root:
            print("错误: 无法获取 task_root")
            return {}

        # 确保 task_root 是 Path 对象
        if not isinstance(task_root, Path):
            task_root = Path(task_root)

        config_path = task_root / "config.json"
        print(f"尝试读取配置文件: {config_path}")

        if not config_path.exists():
            print(f"配置文件不存在: {config_path}")
            return {}

        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
            print(f"成功读取任务配置: {list(config.keys())}")
            return config
    except Exception as e:
        print(f"读取任务配置失败: {e}")
        import traceback
        traceback.print_exc()
        return {}


def _get_extraction_fields(task_dirs: dict) -> list:
    """
    从任务配置中获取提取字段列表
    创建时间: 2024-03-12
    最后修改: 2024-03-13
    """
    config = _get_task_config(task_dirs)

    print("=" * 50)
    print("开始获取提取字段...")

    if not config:
        print("警告: 任务配置为空")
        # 如果没有配置文件，尝试从提取结果文件中读取已有的字段
        try:
            extraction_dir = task_dirs.get("extraction_results")
            if extraction_dir and extraction_dir.exists():
                # 读取第一个 extraction json 文件，获取其中的字段
                json_files = list(extraction_dir.glob("*.json"))
                if json_files:
                    sample = _safe_load_json(json_files[0])
                    if sample and isinstance(sample, dict):
                        results = sample.get("extraction_results", [])
                        if results and len(results) > 0 and isinstance(results[0], dict):
                            fields = list(results[0].keys())
                            print(f"从提取结果文件中获取到字段: {fields}")
                            return fields
        except Exception as e:
            print(f"尝试从提取结果获取字段失败: {e}")

        return []

    # 打印完整的配置结构，便于调试
    print(f"配置顶层keys: {list(config.keys())}")

    # 尝试从不同路径获取字段配置
    field_sources = []

    # 1. 从 extraction_rules 获取（content_extraction.py 使用的格式 - 列表形式）
    extraction_rules = config.get("extraction_rules")
    if extraction_rules and isinstance(extraction_rules, list):
        print(f"extraction_rules 是列表，长度: {len(extraction_rules)}")
        # 从每个规则中提取 name 字段
        fields = []
        for i, rule in enumerate(extraction_rules):
            if isinstance(rule, dict):
                name = rule.get("name")
                if name and isinstance(name, str) and name.strip():
                    fields.append(name.strip())
                    print(f"  规则 {i + 1}: name = '{name}'")
        if fields:
            print(f"✓ 从 extraction_rules 列表获取到 {len(fields)} 个字段: {fields}")
            field_sources.append(("extraction_rules列表", fields))

    # 2. 从 extraction_rules.fields 获取（字典格式）
    extraction_rules_dict = config.get("extraction_rules")
    if extraction_rules_dict and isinstance(extraction_rules_dict, dict):
        fields = extraction_rules_dict.get("fields")
        if fields and isinstance(fields, list) and len(fields) > 0:
            print(f"✓ 从 extraction_rules.fields 获取到字段: {fields}")
            field_sources.append(("extraction_rules.fields", fields))

    # 3. 从 rules.extraction_fields 获取
    rules = config.get("rules")
    if rules and isinstance(rules, dict):
        fields = rules.get("extraction_fields")
        if fields and isinstance(fields, list) and len(fields) > 0:
            print(f"✓ 从 rules.extraction_fields 获取到字段: {fields}")
            field_sources.append(("rules.extraction_fields", fields))

    # 4. 从 ai_config.extraction_fields 获取
    ai_config = config.get("ai_config")
    if ai_config and isinstance(ai_config, dict):
        fields = ai_config.get("extraction_fields")
        if fields and isinstance(fields, list) and len(fields) > 0:
            print(f"✓ 从 ai_config.extraction_fields 获取到字段: {fields}")
            field_sources.append(("ai_config.extraction_fields", fields))

    # 5. 从 task_config.fields 获取
    task_config = config.get("task_config")
    if task_config and isinstance(task_config, dict):
        fields = task_config.get("fields")
        if fields and isinstance(fields, list) and len(fields) > 0:
            print(f"✓ 从 task_config.fields 获取到字段: {fields}")
            field_sources.append(("task_config.fields", fields))

    # 6. 从 custom_fields 获取
    custom_fields = config.get("custom_fields")
    if custom_fields and isinstance(custom_fields, list) and len(custom_fields) > 0:
        print(f"✓ 从 custom_fields 获取到字段: {custom_fields}")
        field_sources.append(("custom_fields", custom_fields))

    # 7. 从 fields 直接获取
    fields_direct = config.get("fields")
    if fields_direct and isinstance(fields_direct, list) and len(fields_direct) > 0:
        print(f"✓ 从 fields 直接获取到字段: {fields_direct}")
        field_sources.append(("fields", fields_direct))

    # 如果有多个来源，选择第一个非空的
    if field_sources:
        selected_source, selected_fields = field_sources[0]
        print(f"选择字段来源: {selected_source}, 字段数: {len(selected_fields)}")
        return selected_fields

    # 8. 如果都没有找到，返回空列表
    print("✗ 未找到任何自定义字段，返回空列表")
    return []


def _get_violation_types_from_config(task_dirs: dict) -> list:
    """
    0316修改：违规分类维度必须来自“任务配置”，取不到就返回空，交由 user_rules 兜底
    支持两类来源：
      1) config.json.discrimination_rules.violation_types
      2) config.json.judgmentRules（前端任务配置结构）-> 用 rule.name 作为分类名
    """
    config = _get_task_config(task_dirs)
    if not isinstance(config, dict) or not config:
        return []

    # 1) discrimination_rules.violation_types
    discrimination_rules = config.get("discrimination_rules")
    if isinstance(discrimination_rules, dict):
        types = discrimination_rules.get("violation_types")
        if isinstance(types, list) and len(types) > 0:
            return [str(x).strip() for x in types if str(x).strip()]

    # 2) judgmentRules（与你 tasks.json 示例一致的结构）
    jr = config.get("judgmentRules")
    if isinstance(jr, list) and len(jr) > 0:
        out = []
        for rule in jr:
            if not isinstance(rule, dict):
                continue
            if rule.get("enabled") is False:
                continue
            name = str(rule.get("name") or "").strip()
            if name:
                out.append(name)
        if out:
            return out

    return []


def _extract_violation_type(judge_result: dict, task_dirs: dict) -> str:
    """
    0316修改：违规分类必须基于任务配置；配置缺失时，用 user_rules 解析字段列表做兜底。
    分类策略：
      A. 若任务配置提供 violation_types：按这些类别做关键词/字段匹配
      B. 否则：从 user_rules 的“需要提取或判别的字段”中提取字段列表，
              找到第一个“判定为违规”的字段作为该文件的违规类型
    """
    if not isinstance(judge_result, dict) or not judge_result:
        return "其他违规"

    # A) 配置提供的分类维度
    config_types = _get_violation_types_from_config(task_dirs)

    # 将判别结果转字符串（包含 key+value），用于弱匹配
    blob = json.dumps(judge_result, ensure_ascii=False)

    if config_types:
        # 1) 精确匹配
        for t in config_types:
            if t and t in blob:
                return t
        # 2) 去掉“违规”后缀的弱匹配（避免“其他违规=100%”）
        for t in config_types:
            base = str(t).replace("违规", "").strip()
            if base and base in blob:
                return t
        return "其他违规"

    # B) 没有配置维度 -> 从 user_rules 里抽字段列表
    ur = judge_result.get("user_rules") or judge_result.get("user_rules_preview") or ""
    rule_fields = _parse_rule_fields_from_user_rules(ur)

    # 只关心“判定类字段”（你样例里大量以“判定”结尾/包含“是否”）
    candidates = [f for f in rule_fields if ("判定" in f or "是否" in f)]

    # 找第一个判定为“违规”的字段（按配置顺序）
    for f in candidates:
        v = judge_result.get(f)
        if isinstance(v, str):
            txt = v.strip()
            if txt.startswith("违规") or ("违规" in txt and "不违规" not in txt and "无违规" not in txt):
                return f  # 直接用字段名作为类型（最贴合你“按任务配置展示”的要求）

    return "其他违规"


def _get_confidence_from_judge(judge_result: dict) -> int:
    """
    0327修改：文件级最终置信度
    优先读取显式置信度；没有时做轻量规则计算，不再默认 85
    """
    if not isinstance(judge_result, dict):
        return 35

    # 0327修改：优先读取显式置信度
    explicit = _extract_explicit_confidence(judge_result)
    if explicit is not None:
        return _clamp_confidence(explicit)

    # 0327修改：没有显式值时，基于整份 judge_result 做轻量规则计算
    score = 50

    useful_texts = []
    for k, v in judge_result.items():
        if k in ["user_rules", "confidence", "置信度", "可信度", "confidence_score"]:
            continue
        if v is None:
            continue
        txt = str(v).strip()
        if txt:
            useful_texts.append(txt)

    # 有效判别字段数量
    non_uncertain_count = 0
    for txt in useful_texts:
        if not _is_uncertain_text(txt):
            non_uncertain_count += 1

    score += min(non_uncertain_count * 4, 20)

    # 有明确最终结论时加分
    final_text = str(judge_result.get("最终违规判定（AI判断过程+结果）") or "").strip()
    if final_text:
        score += 10
        if "判断结果" in final_text:
            score += 8
        if "依据" in final_text or "原因分析" in final_text:
            score += 6
        if _is_uncertain_text(final_text):
            score -= 18

    # 统计触发字段数量，适度加分
    try:
        triggered_count = len(_get_triggered_violation_fields(judge_result))
        score += min(triggered_count * 3, 12)
    except Exception:
        pass

    return _clamp_confidence(score)
def _get_industry_violation_count_from_collection(collection: list, task_dirs: dict = None) -> int:
    """
    计算违规文件中涉及重点行业的数量
    """
    industry_violation_count = 0
    
    for item in collection:
        judge_result = _normalize_collection_judge_result(item)
        if not judge_result:
            continue
            
        # 检查是否违规
        valid_status = _normalize_collection_is_valid(item)
        if valid_status != "illegal":
            continue
            
        # 获取行业类别编码及重点行业判断字段
        industry_field = None
        for key in ["行业类别编码及重点行业判断", "【行业类别编码及重点行业判断】"]:
            if key in judge_result:
                industry_field = judge_result[key]
                break
        
        if industry_field and isinstance(industry_field, str):
            # 如果包含"涉及重点行业"则为True
            if "涉及重点行业" in industry_field:
                industry_violation_count += 1
            # 如果包含"不涉及重点行业"或"未找到规则要求的相关内容"则为False
            elif "不涉及重点行业" in industry_field or "未找到规则要求的相关内容" in industry_field:
                continue
    
    return industry_violation_count
def _extract_construction_year(judge_result: dict) -> int:
    """
    从 judge_results 中提取动工时间判定的年份
    通常格式为 "正式动工时间为2019年"，只提取年份数字
    """
    if not isinstance(judge_result, dict):
        return 0
    
    # 查找动工时间判定字段
    construction_field = None
    for key in ["动工时间判定", "【动工时间判定】"]:
        if key in judge_result:
            construction_field = judge_result[key]
            break
    
    if not construction_field or not isinstance(construction_field, str):
        return 0
    
    # 使用正则表达式提取年份（4位数字）
    import re
    year_match = re.search(r'(\d{4})', construction_field)
    if year_match:
        try:
            year = int(year_match.group(1))
            # 确保是合理的年份（1900-2100）
            if 1900 <= year <= 2100:
                return year
        except ValueError:
            pass
    
    return 0

def _extract_construction_year_from_collection(collection: list) -> list:
    """
    为 collection 中的每个项目添加 construction_date 字段
    """
    updated_collection = []
    for item in collection:
        new_item = dict(item)  # 创建副本
        
        # 提取 judge_results
        judge_result = _normalize_collection_judge_result(item)
        if judge_result:
            construction_year = _extract_construction_year(judge_result)
            new_item["construction_date"] = construction_year
        else:
            new_item["construction_date"] = 0
            
        updated_collection.append(new_item)
    
    return updated_collection
# ==================== 新增统计接口 ====================

# 5) /airesult/statistics/overview - 获取任务统计数据概览
@statistic_bp.get("/statistics/overview")
@AuthUtils.token_required
@scene_or_task_permission_required()
def airesult_statistics_overview():
    """
    0328修改：支持按 task_id（单任务统计）或 sceneId（场景聚合统计）。
    """
    try:
        task_id = (request.args.get("task_id") or "").strip()
        scene_id = (request.args.get("scene_id") or "").strip()
        
        # 如果两个都提供，优先使用 task_id
        task_ids = []
        if task_id:
            task_ids = [task_id]
        elif scene_id:
            task_ids = _get_tasks_by_scene(scene_id)
        else:
            return jsonify(api_fail("缺少task_id或scene_id")), 400
        
        if not task_ids:
            # 没有任务，返回空统计
            return jsonify(api_ok({
                "total_files": 0,
                "extracted_files": 0,
                "judged_files": 0,
                "reviewed_files": 0,
                "violation_types": [],
                "field_extraction_rate": [],
            }))
        
        # 聚合所有任务的统计数据
        total_files = 0
        extracted_files = 0
        judged_files = 0
        reviewed_files = 0
        violation_counter = Counter()
        all_collections = []
        
        for tid in task_ids:
            task_dirs = task_folder_utils.get_task_dirs(tid)
            # 修复：只有 scene_id 存在时才传递，否则不传，避免空字符串过滤数据
            if scene_id:
                collection = _load_result_collection(tid, task_dirs, scene_id)
            else:
                collection = _load_result_collection(tid, task_dirs)
            all_collections.extend(collection)
            
            total_files += len(collection)
            extracted_files += sum(1 for item in collection if bool(item.get("is_extracted")))
            judged_files += sum(1 for item in collection if bool(item.get("is_judged")))
            reviewed_files += sum(1 for item in collection if bool(item.get("is_reviewed")))
            
            for item in collection:
                judge_result = _normalize_collection_judge_result(item)
                if not judge_result:
                    continue
                for f in _get_triggered_violation_fields(judge_result, task_dirs):
                    violation_counter[f] += 1

        violation_types = [{"type": k, "count": v} for k, v in violation_counter.most_common()]
        field_extraction_rate = _get_field_extraction_rate_from_collection(all_collections, None)

        result = {
            "total_files": total_files,
            "extracted_files": extracted_files,
            "judged_files": judged_files,
            "reviewed_files": reviewed_files,
            "violation_types": violation_types,
            "field_extraction_rate": field_extraction_rate,
        }
        return jsonify(api_ok(result))
    except Exception:
        return jsonify(api_fail(traceback.format_exc())), 500


# 6) /airesult/statistics/violation-types
@statistic_bp.get("/statistics/violation-types")
@AuthUtils.token_required
@scene_or_task_permission_required()
def airesult_statistics_violation_types():
    """0328修改：支持按 task_id 或 sceneId 的违规类型统计。"""
    try:
        task_id = (request.args.get("task_id") or "").strip()
        scene_id = (request.args.get("scene_id") or "").strip()
        
        task_ids = []
        if task_id:
            task_ids = [task_id]
        elif scene_id:
            task_ids = _get_tasks_by_scene(scene_id)
        else:
            return jsonify(api_fail("缺少task_id或scene_id")), 400
        
        if not task_ids:
            return jsonify(api_ok([]))
        
        violation_counter = Counter()
        for tid in task_ids:
            task_dirs = task_folder_utils.get_task_dirs(tid)
            collection = _load_result_collection(tid, task_dirs, scene_id)
            
            for item in collection:
                judge_result = _normalize_collection_judge_result(item)
                if not judge_result:
                    continue
                for f in _get_triggered_violation_fields(judge_result, task_dirs):
                    violation_counter[f] += 1

        result = [{"type": k, "count": v} for k, v in violation_counter.most_common()]
        return jsonify(api_ok(result))
    except Exception:
        return jsonify(api_fail(traceback.format_exc())), 500

# 7) /airesult/statistics/field-rate - 获取字段提取率统计
@statistic_bp.get("/statistics/field-rate")
@AuthUtils.token_required
@scene_or_task_permission_required()
def airesult_statistics_field_rate():
    """0328修改：支持按 task_id 或 sceneId 的字段提取率统计。"""
    try:
        task_id = (request.args.get("task_id") or "").strip()
        scene_id = (request.args.get("scene_id") or "").strip()
        
        task_ids = []
        if task_id:
            task_ids = [task_id]
        elif scene_id:
            task_ids = _get_tasks_by_scene(scene_id)
        else:
            return jsonify(api_fail("缺少task_id或scene_id")), 400
        
        if not task_ids:
            return jsonify(api_ok([]))
        
        all_collections = []
        for tid in task_ids:
            task_dirs = task_folder_utils.get_task_dirs(tid)
            collection = _load_result_collection(tid, task_dirs, scene_id)
            all_collections.extend(collection)
        
        result = _get_field_extraction_rate_from_collection(all_collections, None)
        return jsonify(api_ok(result))
    except Exception:
        return jsonify(api_fail(traceback.format_exc())), 500

# 8) /airesult/statistics/clues - 获取线索清单

@statistic_bp.get("/statistics/clues")
@AuthUtils.token_required
@scene_or_task_permission_required()
def airesult_statistics_clues():
    """0328修改：支持按 task_id 或 sceneId 的线索清单统计。"""
    try:
        task_id = (request.args.get("task_id") or "").strip()
        scene_id = (request.args.get("scene_id") or "").strip()
        page = int(request.args.get("page", 1))
        page_size = int(request.args.get("page_size", 10))
        
        if not task_id and not scene_id:
            return jsonify(api_fail("缺少task_id或scene_id")), 400
        
        task_ids = []
        if task_id:
            task_ids = [task_id]
        elif scene_id:
            task_ids = _get_tasks_by_scene(scene_id)
            if not task_ids:
                return jsonify(api_fail("场景中没有找到任务")), 400
        
        # 合并所有任务的线索数据
        all_clues = []
        for tid in task_ids:
            task_dirs = task_folder_utils.get_task_dirs(tid)
            if scene_id:
                collection = _load_result_collection(tid, task_dirs, scene_id)
            else:
                collection = _load_result_collection(tid, task_dirs)
            if collection:
                for item in collection:
                    judge_result = _normalize_collection_judge_result(item)
                    if not judge_result:
                        continue
                    file_name = _resolve_preview_file_name(task_dirs, item)  # 0327修改：返回结果预览链路可识别的文件名
                    final_valid = _normalize_collection_is_valid(item)
                    final_confidence = _get_confidence_from_judge(judge_result)
                    confidence_source = "explicit" if _extract_explicit_confidence(
                        judge_result) is not None else "rule_based"
                    created_at = _normalize_collection_update_time(item)
                    for field_name in _get_triggered_violation_fields(judge_result):
                        clue_confidence = _get_clue_confidence(judge_result, field_name)  # 0327修改：线索级置信度按字段单独计算
                        id_str = f"{tid}_{file_name}_{field_name}"
                        clue_id = hashlib.md5(id_str.encode()).hexdigest()[:8]
                        all_clues.append({
                            "id": clue_id,
                            "task_id": tid,
                            "task_name": _get_task_name_from_tasks_json(tid),
                            "file_name": file_name,
                            "violation_type": field_name,
                            "field_name": field_name,
                            "confidence": clue_confidence,
                            "final_confidence": final_confidence,
                            "confidence_source": confidence_source,
                            "created_at": created_at,
                            "final_valid": final_valid,
                        })
        
        # 如果没有从result_collection获取到数据，回退到旧逻辑
        if not all_clues:
            for tid in task_ids:
                task_dirs = task_folder_utils.get_task_dirs(tid)
                file_names = _list_parsed_file_names(task_dirs)
                for fn in file_names:
                    dis_path, dis_obj = _find_discrimination_json(task_dirs, fn)
                    if not (dis_path and dis_obj):
                        continue
                    dis_map, dis_err = _parse_discrimination_json(dis_obj)
                    if dis_err:
                        continue
                    judge_result, judge_error = _extract_judge_for_file(dis_map, fn)
                    if judge_error:
                        continue
                    valid = _compute_valid_from_judge(judge_result, None)
                    if valid == "illegal":
                        violation_type = _extract_violation_type(judge_result, task_dirs)
                        confidence = _get_confidence_from_judge(judge_result)
                        field_name = "关键字段"
                        for key in ["违规字段", "violation_field", "违规内容", "具体违规"]:
                            if judge_result.get(key):
                                field_name = str(judge_result.get(key))
                                break
                        if field_name == "关键字段":
                            field_name = violation_type
                        clue_id = hashlib.md5(f"{tid}_{fn}_{field_name}".encode()).hexdigest()[:8]
                        all_clues.append({
                            "id": clue_id,
                            "task_id": tid,
                            "task_name": _get_task_name_from_tasks_json(tid),
                            "file_name": fn,
                            "violation_type": violation_type,
                            "field_name": field_name,
                            "confidence": confidence,
                            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        })
        
        all_clues.sort(key=lambda x: (-x["confidence"], x["file_name"], x["field_name"]))
        total = len(all_clues)
        start = (page - 1) * page_size
        end = start + page_size
        
        return jsonify(api_ok({
            "total": total, 
            "items": all_clues[start:end],
            "task_ids": task_ids
        }))
    except Exception:
        return jsonify(api_fail(traceback.format_exc())), 500

# 9) /airesult/statistics/region-distribution - 获取地域分布数据
# ==================== 0316修改：region-distribution 全任务汇总 + 当前任务高亮 ====================

@statistic_bp.get("/statistics/region-distribution")
@AuthUtils.token_required
@scene_or_task_permission_required()
def airesult_statistics_region_distribution():
    """
    0328修改：支持按 task_id（单任务统计）或 sceneId（场景聚合统计）。
    province 暂时从 tasks.json 读取，这只会读取一个很小的配置文件，不会造成性能瓶颈。
    """
    try:
        task_id = (request.args.get("task_id") or "").strip()
        scene_id = (request.args.get("scene_id") or "").strip()
        
        if not task_id and not scene_id:
            return jsonify(api_fail("缺少task_id或scene_id")), 400

        tasks = _load_all_tasks()
        
        # 如果提供了 scene_id，使用场景聚合逻辑
        if scene_id:
            current_scene_id = scene_id
            # 找到场景中的第一个任务作为"当前任务"
            scene_tasks = [t for t in tasks if str(t.get("sceneId") or "").strip() == scene_id]
            if not scene_tasks:
                return jsonify(api_fail("场景中没有找到任务")), 400
            
            current_task = scene_tasks[0]
            task_id = str(current_task.get("id")).strip()
            current_task_name = str(current_task.get("name") or task_id).strip()
        else:
            # 单任务模式
            current_task_name = _get_task_name_from_tasks_json(task_id)
            # 获取当前任务的 sceneId
            current_scene_id = None
            for t in tasks:
                if str(t.get("id") or "").strip() == task_id:
                    current_scene_id = str(t.get("sceneId") or "default").strip()
                    break
        
        current_province = _get_task_province_from_tasks_json(task_id) or _extract_province_from_string(
            current_task_name) or "其他地区"
        
        current_task_stat = _compute_task_region_stat(task_id, current_task_name, current_scene_id)
        current_task_stat.update({
            "is_current_task": True,
            "current_task_id": task_id,
            "current_task_name": current_task_name,
        })

        agg = {}
        for t in tasks:
            tid = t.get("id")
            tname = t.get("name") or tid
            if not tid:
                continue
            # 只统计相同场景的任务
            task_scene_id = str(t.get("sceneId") or "default").strip()
            if task_scene_id != current_scene_id:
                continue
            one = _compute_task_region_stat(tid, tname, current_scene_id)
            prov = one.get("province") or "其他地区"
            if prov not in agg:
                agg[prov] = {
                    "province": prov,
                    "count": 0,
                    "illegal_count": 0,
                    "legal_count": 0,
                    "industry_violation_count": 0,
                    "is_current_task": False,
                    "current_task_id": "",
                    "current_task_name": "",
                }
            agg[prov]["count"] += int(one.get("count", 0) or 0)
            agg[prov]["illegal_count"] += int(one.get("illegal_count", 0) or 0)
            agg[prov]["legal_count"] += int(one.get("legal_count", 0) or 0)
            agg[prov]["industry_violation_count"] += int(one.get("industry_violation_count", 0) or 0)

        if current_province not in agg:
            agg[current_province] = {
                "province": current_province,
                "count": int(current_task_stat.get("count", 0) or 0),
                "illegal_count": int(current_task_stat.get("illegal_count", 0) or 0),
                "legal_count": int(current_task_stat.get("legal_count", 0) or 0),
                "industry_violation_count": int(current_task_stat.get("industry_violation_count", 0) or 0),
                "is_current_task": True,
                "current_task_id": task_id,
                "current_task_name": current_task_name,
            }

        agg[current_province]["is_current_task"] = True
        agg[current_province]["current_task_id"] = task_id
        agg[current_province]["current_task_name"] = current_task_name

        regions = list(agg.values())
        regions.sort(key=lambda x: x.get("province") or "")

        return jsonify(api_ok({
            "current_task_id": task_id,
            "current_task_name": current_task_name,
            "current_task_province": current_province,
            "current_task_stat": current_task_stat,
            "regions": regions,
        }))
    except Exception:
        return jsonify(api_fail(traceback.format_exc())), 500


