import re
from datetime import datetime
def generate_rule_identifier(extraction_rules):
    """
    根据提取规则生成规则标识符
    """
    if not extraction_rules:
        return "default"
    # 如果有规则名称，使用规则名称生成标识
    rule_names = []
    for rule in extraction_rules:
        if rule.get('name'):
            # 清理规则名称，只保留字母数字和中文字符
            clean_name = re.sub(r'[^\w\u4e00-\u9fa5]', '_', rule['name'])
            rule_names.append(clean_name)

    if rule_names:
        # 如果多个规则，取前两个规则名称组合
        if len(rule_names) == 1:
            identifier = rule_names[0]
        else:
            identifier = f"{rule_names[0]}_{rule_names[1]}"

        # 限制标识长度
        if len(identifier) > 30:
            identifier = identifier[:30]
        return identifier

    # 如果没有规则名称，使用规则ID或生成哈希
    rule_ids = []
    for rule in extraction_rules:
        if rule.get('id'):
            rule_ids.append(rule['id'][:8])  # 取ID前8位

    if rule_ids:
        return "_".join(rule_ids)

    # 最后使用规则数量
    return f"rules_{len(extraction_rules)}"

def custom_extraction_from_data(json_data, extraction_rules, source_file):
    """
    自定义内容提取函数（需要你根据实际情况实现）
    """
    # 这里需要实现具体的提取逻辑
    # 返回提取结果
    result = {
        "source_file": source_file,
        "extraction_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "rules_applied": extraction_rules,
        "extracted_data": json_data  # 示例：直接返回原始数据
    }
    return result

def convert_extraction_result_to_markdown(extraction_result, base_name):
    """
    将提取结果转换为Markdown格式
    """
    md_content = f"# 内容提取报告: {base_name}\n\n"
    md_content += f"**提取时间**: {extraction_result.get('extraction_time', '未知')}\n\n"
    md_content += f"**源文件**: {extraction_result.get('source_file', '未知')}\n\n"

    # 添加提取规则信息
    md_content += "## 使用的提取规则\n\n"
    rules = extraction_result.get('rules_applied', [])
    for i, rule in enumerate(rules, 1):
        md_content += f"{i}. {rule.get('name', '未命名规则')}: {rule.get('description', '无描述')}\n"

    md_content += "\n## 提取结果\n\n"
    extracted_data = extraction_result.get('extracted_data', {})

    # 尝试将JSON数据转换为可读格式
    try:
        import json
        md_content += "```json\n"
        md_content += json.dumps(extracted_data, ensure_ascii=False, indent=2)
        md_content += "\n```\n"
    except:
        md_content += str(extracted_data)

    return md_content
