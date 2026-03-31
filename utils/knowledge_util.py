import json
import requests
import time
import csv
import io
from pathlib import Path

# ===============================
# 配置
# ===============================

DIFY_API_KEY = "app-IFqP7MF17f0k7WYVSRtNacre"
WORKFLOW_URL = "http://10.100.3.110:8086/v1/workflows/run"
MAX_FIELD_LENGTH = 2000
MAX_RETRY = 3
current_dir = Path(__file__).parent
TASKS_BASE_DIR = current_dir.parent / "data" / "tasks"  
# LAND_MAPPING_CSV = Path(r"E:\1_给雷震师姐\text_detach\utils\landuse_type.csv")
# 调用知识库的次数由 knowledge_base 字段决定，tasks.json里面有几个就调用几次
# ===============================
# 工具函数
# ===============================

def _get_pk(file_name):
    stem = Path(file_name).stem

    if stem.endswith("_parsed_extracted"):
        return stem[:-len("_parsed_extracted")]

    if stem.endswith("_extracted"):
        return stem[:-len("_extracted")]

    return stem
def query_by_mapping_table(source_content: str):
    """
    根据 source_content 中的标识符查询相应的映射表
    如果包含 {industry_category}，则查询 industry_category.csv
    如果包含 {landuse_type}，则查询 landuse_type.csv
    """
    results = []
    
    # 检查 source_content 中包含哪些标识符
    if "{industry_category}" in source_content:
        industry_results = _query_specific_mapping_table(source_content, "industry_category")
        results.extend(industry_results)
    
    if "{landuse_type}" in source_content:
        landuse_results = _query_specific_mapping_table(source_content, "landuse_type")
        results.extend(landuse_results)
    
    # 如果都没有标识符，也尝试查询默认的 landuse_type 表（兼容旧逻辑）
    if not results and ("{industry_category}" not in source_content and "{landuse_type}" not in source_content):
        default_results = _query_specific_mapping_table(source_content, "landuse_type")
        results.extend(default_results)
    
    print(f"映射表查询结果数量: {len(results)}")
    return {"data": {"outputs": {"result": results}}}

def _query_specific_mapping_table(source_content: str, table_type: str):
    """
    查询指定类型的映射表
    """
    
    # 确定映射表文件路径
    if table_type == "industry_category":
        mapping_csv = current_dir.parent / "mappingdata"/"industry_category.csv"
        # 移除标识符，只保留实际内容进行匹配
        clean_source = source_content.replace("{industry_category}", "")
    elif table_type == "landuse_type":
        mapping_csv = current_dir.parent / "mappingdata"/"landuse_type.csv"
        # 移除标识符，只保留实际内容进行匹配
        clean_source = source_content.replace("{landuse_type}", "")
    else:
        return []

    if not mapping_csv.exists():
        print(f"{table_type} 映射表文件不存在: {mapping_csv}")
        return []

    results = []
    
    try:
        # 尝试不同的编码方式读取文件
        encodings = ['utf-8', 'gbk', 'gb2312', 'gb18030']
        content = None
        
        for encoding in encodings:
            try:
                with open(mapping_csv, 'r', encoding=encoding) as f:
                    content = f.read()
                print(f"成功使用 {encoding} 编码读取 {table_type} 文件")
                break
            except UnicodeDecodeError:
                continue
        
        if content is None:
            print(f"无法使用任何编码方式读取 {table_type} 文件")
            return []

        # 尝试不同的分隔符
        delimiters = [',', '\t', ';','；']
        
        reader = None
        
        for delimiter in delimiters:
            try:
                import io
                csvfile = io.StringIO(content)
                reader = csv.DictReader(csvfile, delimiter=delimiter)
                # 尝试读取第一行验证
                first_row = next(reader)
                csvfile.seek(0)
                reader = csv.DictReader(csvfile, delimiter=delimiter)
                print(f"{table_type}: 成功使用 '{delimiter}' 作为分隔符")
                break
            except Exception:
                continue
        
        if reader is None:
            print(f"{table_type}: 无法确定合适的分隔符")
            return []
        
        # 获取列名
        fieldnames = reader.fieldnames
        # print(f"{table_type} CSV 列名: {fieldnames}")
        
        # 打印要搜索的源内容
        # print(f"{table_type} 搜索内容: {clean_source}")
        
        # 根据表类型确定列名
        if table_type == "industry_category":
            found_match = False
            # industry_category.csv 的列名
            keyword_col = next((fn for fn in fieldnames if "关键词" in fn or "keyword" in fn.lower() or "行业类别" in fn), "关键词")
            menlei_col = next((fn for fn in fieldnames if "门类" in fn or "menlei" in fn.lower()), "门类")
            menlei_name_col = next((fn for fn in fieldnames if "门类名称" in fn or "menlei_name" in fn.lower()), "门类名称")
            hangye_dalei_code_col = next((fn for fn in fieldnames if "行业大类编码" in fn or "hangye_dalei_code" in fn.lower()), "行业大类编码")
            hangye_dalei_name_col = next((fn for fn in fieldnames if "行业大类名称" in fn or "hangye_dalei_name" in fn.lower()), "行业大类名称")
            hangye_zhonglei_code_col = next((fn for fn in fieldnames if "行业中类编码" in fn or "hangye_zhonglei_code" in fn.lower()), "行业中类编码")
            hangye_zhonglei_name_col = next((fn for fn in fieldnames if "行业中类名称" in fn or "hangye_zhonglei_name" in fn.lower()), "行业中类名称")
            hangye_xiaolei_code_col = next((fn for fn in fieldnames if "行业小类编码" in fn or "hangye_xiaolei_code" in fn.lower()), "行业小类编码")
            hangye_xiaolei_name_col = next((fn for fn in fieldnames if "行业小类名称" in fn or "hangye_xiaolei_name" in fn.lower()), "行业小类名称")
            is_key_industry_col = next((fn for fn in fieldnames if "是否是重点行业" in fn or "is_key_industry" in fn.lower()), "是否是重点行业")
            
            # print(f"{table_type} 使用的列名: 关键词={keyword_col}, 门类={menlei_col}, 门类名称={menlei_name_col}, 行业大类编码={hangye_dalei_code_col}, 行业大类名称={hangye_dalei_name_col}, 行业中类编码={hangye_zhonglei_code_col}, 行业中类名称={hangye_zhonglei_name_col}, 行业小类编码={hangye_xiaolei_code_col}, 行业小类名称={hangye_xiaolei_name_col}, 是否是重点行业={is_key_industry_col}")
            import re
            # 遍历 CSV 行进行匹配
            for i, row in enumerate(reader):
                keyword = row.get(keyword_col, "") or ""
                keyword_str = str(keyword)

                # 🔥 清洗关键词
                keyword_str = keyword_str.replace('"', '').replace("'", "")
                keyword_str = keyword_str.replace("\n", "").replace("\r", "")

                # 🔥 拆分（支持所有分隔符）
                keywords = re.split(r"[；;，,]", keyword_str)

                # 🔥 清理 + 过滤短词
                keywords = [k.strip() for k in keywords if k.strip() and len(k.strip()) >= 2]

                # 🔥 清洗文本
                source_str = str(clean_source)
                source_str = source_str.replace(" ", "").replace("\n", "").replace("\r", "")
                
                match_keywords = []
                score = 0

                for kw in keywords:
                    if kw in source_str:
                        print(f"✅ 命中关键词: {kw}")   # 🔥 调试用
                        match_keywords.append(kw)
                        score += len(kw)

                if score > 0:
                    # 构造内容字符串，包含所有字段
                    content_str = (
                        f"门类: {row.get(menlei_col, '')}, "
                        f"门类名称: {row.get(menlei_name_col, '')}, "
                        f"行业大类编码: {row.get(hangye_dalei_code_col, '')}, "
                        f"行业大类名称: {row.get(hangye_dalei_name_col, '')}, "
                        f"行业中类编码: {row.get(hangye_zhonglei_code_col, '')}, "
                        f"行业中类名称: {row.get(hangye_zhonglei_name_col, '')}, "
                        f"行业小类编码: {row.get(hangye_xiaolei_code_col, '')}, "
                        f"行业小类名称: {row.get(hangye_xiaolei_name_col, '')}, "
                        # f"关键词: {keyword_str}, "
                        f"是否是重点行业: {row.get(is_key_industry_col, '')}, "
                        f"命中关键词: {','.join(match_keywords)}"
                    )

                    results.append({
                        "title": ",".join(match_keywords),
                        "metadata": {"score": score},
                        "content": content_str
                    })

                    # print(f"{table_type} 命中关键词: {match_keywords}, score={score}")
            if table_type == "industry_category" and not found_match:
                results.append({
                    "title": "未匹配到关键词",
                    "metadata": {"score": 0},
                    "content": "不是重点行业"
                })

        elif table_type == "landuse_type":
            # landuse_type.csv 的列名（您的原始逻辑）
            keyword_col = next((fn for fn in fieldnames if "关键词" in fn or "keyword" in fn.lower()), "关键词")
            level1_code_col = next((fn for fn in fieldnames if "一级代码" in fn or "level1_code" in fn.lower()), "一级代码")
            level1_name_col = next((fn for fn in fieldnames if "一级名称" in fn or "level1_name" in fn.lower()), "一级名称")
            level2_code_col = next((fn for fn in fieldnames if "二级代码" in fn or "level2_code" in fn.lower()), "二级代码")
            level2_name_col = next((fn for fn in fieldnames if "二级名称" in fn or "level2_name" in fn.lower()), "二级名称")
            yzlg_name_col = next((fn for fn in fieldnames if "是否属于一住两公" in fn or "yzlg_name" in fn.lower()), "是否属于一住两公")
            meaning_col = next((fn for fn in fieldnames if "含义" in fn or "meaning" in fn.lower()), "含义")
            
            # print(f"{table_type} 使用的列名: 关键词={keyword_col}, 一级代码={level1_code_col}, 一级名称={level1_name_col}, 二级代码={level2_code_col}, 二级名称={level2_name_col}, 是否属于一住两公={yzlg_name_col}, 含义={meaning_col}")
            
            import re
            # 定义要跳过的关键词
            skip_keywords = ["未发现", "已拆除", "无","未"]
            def split_to_sentences(text):
                # 使用中文常见标点符号分割句子
                sentences = re.split(r'[，,；;：:！!？?。\.]', text)
                # 过滤空字符串
                sentences = [s.strip() for s in sentences if s.strip()]
                return sentences
            
            # 检查句子是否包含跳过的关键词
            def should_skip_sentence(sentence):
                for skip_word in skip_keywords:
                    if skip_word in sentence:
                        return True
                return False
            # 分割句子的函数 - 使用中文标点符号
            
            # 遍历 CSV 行进行匹配
            for i, row in enumerate(reader):
                keyword = row.get(keyword_col, "") or ""
                keyword_str = str(keyword)

                # 🔥 清洗关键词
                keyword_str = keyword_str.replace('"', '').replace("'", "")
                keyword_str = keyword_str.replace("\n", "").replace("\r", "")

                # 🔥 拆分（支持所有分隔符）
                keywords = re.split(r"[；;，,]", keyword_str)

                # 🔥 清理 + 过滤短词
                keywords = [k.strip() for k in keywords if k.strip() and len(k.strip()) >= 2]

                # 🔥 清洗文本
                source_str = str(clean_source)
                source_str = source_str.replace(" ", "").replace("\n", "").replace("\r", "")
                
                # 🔥 分割成句子并过滤掉包含特定词汇的句子
                original_sentences = split_to_sentences(clean_source)
                filtered_sentences = [sent for sent in original_sentences if not should_skip_sentence(sent)]
                
                # 🔥 重新组合过滤后的文本
                filtered_text = "".join(filtered_sentences)
                filtered_text = filtered_text.replace(" ", "").replace("\n", "").replace("\r", "")
                match_keywords = []
                score = 0

                for kw in keywords:
                    if kw in filtered_text:
                        print(f"✅ landuse_type命中关键词: {kw}")   # 🔥 调试用
                        match_keywords.append(kw)
                        score += len(kw)

                if score > 0:
                    # 构造内容字符串
                    content_str = (
                        f"一级代码: {row.get(level1_code_col, '')}, "
                        f"一级名称: {row.get(level1_name_col, '')}, "
                        f"二级代码: {row.get(level2_code_col, '')}, "
                        f"二级名称: {row.get(level2_name_col, '')}, "
                        f"是否属于一住两公: {row.get(yzlg_name_col, '')}, "
                        # f"含义: {row.get(meaning_col, '')}, "
                        # f"关键词: {keyword_str}, "
                        f"命中关键词: {','.join(match_keywords)}"
                    )

                    results.append({
                        "title": ",".join(match_keywords),
                        "metadata": {"score": score},
                        "content":content_str
                    })

                    print(f"{table_type} 命中关键词: {match_keywords}, score={score}")
    
    except Exception as e:
        print(f"查询 {table_type} 映射表时发生错误: {e}")
        import traceback
        print(f"错误详情: {traceback.format_exc()}")
    
    print(f"{table_type} 最终匹配结果数量: {len(results)}")
    return results
def get_task_dirs(task_id, base_dir=TASKS_BASE_DIR):
    """返回 task 目录相关路径"""
    task_base = Path(base_dir) / task_id
    return {
        "task_root": task_base,
        "chunked": task_base / "chunked",
        "extraction_results": task_base / "extraction_results",
    }

def get_extraction_file(task_id, file_name):
    task_dirs = get_task_dirs(task_id)
    extraction_dir = task_dirs["extraction_results"]
    pk = _get_pk(file_name)
    # 尝试 _extracted.json 格式
    extraction_file = extraction_dir / f"{pk}_extracted.json"
    if not extraction_file.exists():
        # 如果 _extracted.json 不存在，尝试 _parsed_extracted.json 格式
        parsed_extraction_file = extraction_dir / f"{pk}_parsed_extracted.json"
        if parsed_extraction_file.exists():
            extraction_file = parsed_extraction_file
        else:
            raise Exception(f"提取结果文件不存在: {extraction_file} 或 {parsed_extraction_file}")
    return extraction_file

def load_extraction_fields(task_id, file_name):
    extraction_file = get_extraction_file(task_id, file_name)
    with open(extraction_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    results = data.get("extraction_results", [])
    if not results:
        raise Exception(f"{extraction_file} 中 extraction_results 为空")
    return results[0]

# ===============================
# 构建 source_content
# ===============================

def build_source_content(task_id, file_name, knowledge_base, source_fields):
    fields_data = load_extraction_fields(task_id, file_name)
    contents = []
    # 只拼接 source_fields
    for field in source_fields:
        if field in fields_data and fields_data[field]:
            value = str(fields_data[field])[:MAX_FIELD_LENGTH]
            contents.append(f"{field}：{value}")
    if not contents:
        raise Exception("未找到任何可用字段")
    # 拼接正文
    text = "\n".join(contents)
    # 在最前面加知识库标识
    if knowledge_base:
        text = f"{{{knowledge_base}}}{text}"
    return text

# ===============================
# Dify 调用
# ===============================

def call_dify_workflow(source_content):
    headers = {
        "Authorization": f"Bearer {DIFY_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "inputs": {
            "source_content": source_content
        },
        "response_mode": "blocking",
        "user": "system"
    }
    for attempt in range(MAX_RETRY):
        try:
            response = requests.post(WORKFLOW_URL, headers=headers, json=payload, timeout=60)
            if response.status_code != 200:
                raise Exception(response.text)
            return response.json()
        except Exception as e:
            print(f"Dify调用失败 {attempt+1}/{MAX_RETRY} : {e}")
            if attempt == MAX_RETRY - 1:
                raise
            time.sleep(2)

# ===============================
# task.json 中加载规则
# ===============================

def load_task_rules(task_json_path, task_id):
    """返回 task_id 中需要调用知识库的规则列表"""
    with open(task_json_path, "r", encoding="utf-8") as f:
        tasks = json.load(f)
    task = next((t for t in tasks if t.get("id") == task_id), None)
    if not task:
        raise Exception("task 不存在")
    rules = []
    for rule in task.get("judgmentRules", []):
        kb = rule.get("knowledgeBase")  # 如果没有该字段就不处理
        if kb:
            rules.append({
                "knowledge_base": kb,
                "source_fields": rule.get("sourceFields", []),
                "rule_name": rule.get("name")
            })
    return rules

# ===============================
# 运行知识库查询并保存
# ===============================

def query_knowledge_for_task(task_id, task_json_path, use_mapping_table=True):
    task_dirs = get_task_dirs(task_id)
    extraction_dir = task_dirs["extraction_results"]
    # print("task_dir:", task_dirs["task_root"])
    # print("extraction_dir:", extraction_dir)
    rules = load_task_rules(task_json_path, task_id)
    if not rules:
        print("该任务没有配置需要调用知识库的规则")
        return
    
    # =========================
    # 收集所有请求并去重
    # =========================
    query_map = {}
    for file_path in extraction_dir.glob("*extracted.json"):
        file_name = file_path.name
        for rule in rules:
            kb = rule["knowledge_base"]
            source_fields = rule["source_fields"]

            try:
                source_content = build_source_content(
                    task_id,
                    file_name,
                    kb,
                    source_fields
                )
                key = (file_name, kb, tuple(source_fields))
                if key not in query_map:
                    query_map[key] = {
                        "filename": file_name,
                        "knowledgeBase": kb,
                        "sourceFields": source_fields,
                        "judgmentRules": [rule["rule_name"]],
                        "source_content": source_content
                    }
                else:
                    if rule["rule_name"] not in query_map[key]["judgmentRules"]:
                        query_map[key]["judgmentRules"].append(rule["rule_name"])
                    
            except Exception as e:
                print(f"{file_name} 构建source_content失败: {e}")

    results_by_file = {}

    for key, info in query_map.items():
        source_content = info["source_content"]
        file_name = info["filename"]

        try:
            if use_mapping_table:
                # 使用映射表查询
                dify_raw = query_by_mapping_table(source_content)
                try:
                    dify_items = dify_raw["data"]["outputs"]["result"]
                    simplified = [
                        {
                            "title": item.get("title"),
                            "score": item.get("metadata", {}).get("score"),
                            "content": item.get("content")
                        }
                        for item in dify_items
                    ]
                except Exception:
                    simplified = []
            else:
                # 使用 Dify 查询
                dify_raw = call_dify_workflow(source_content)
                try:
                    dify_items = dify_raw["data"]["outputs"]["result"]
                    simplified = [
                        {
                            "title": item.get("title"),
                            "score": item.get("metadata", {}).get("score"),
                            "content": item.get("content")
                        }
                        for item in dify_items
                    ]
                except Exception:
                    simplified = []

            result_item = {
                "filename": file_name,
                "knowledgeBase": info["knowledgeBase"],
                "sourceFields": info["sourceFields"],
                "judgmentRules": info["judgmentRules"],
                "source_content": source_content,
                "knowledge": simplified
            }

            # 按文件归类
            if file_name not in results_by_file:
                results_by_file[file_name] = []

            results_by_file[file_name].append(result_item)

        except Exception as e:
            print("知识库调用失败:", e)

    # =========================
    # 每个文件单独保存
    # =========================

    for file_name, items in results_by_file.items():
        pk = _get_pk(file_name)
        output_file = extraction_dir / f"{pk}_parsed_knowledge.json"

        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "knowledge_queries": items
                },
                f,
                ensure_ascii=False,
                indent=2
            )

        print(f"知识库结果已保存: {output_file}")

# ===============================
# 单独调用入口
# ===============================
# if __name__ == "__main__":
#     task_id = "task-1768638400541"
#     task_json_path = r"E:\\1_给雷震师姐\\text_detach\data\tasks\tasks.json"
#     print(f"开始处理任务 {task_id}")
#     # print(f"{build_source_content(task_id, task_json_path)}")
#     query_knowledge_for_task(task_id, task_json_path)
#     print("处理完成")
