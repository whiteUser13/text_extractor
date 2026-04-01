from datetime import datetime
def process_discrimination(extraction_data, judgment_rules, source_file):
    """
    处理判别逻辑（需要你根据实际情况实现）
    """
    # 这里需要实现具体的判别逻辑
    # 返回判别结果
    result = {
        "source_file": source_file,
        "discrimination_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "rules_applied": judgment_rules,
        "extraction_data": extraction_data,  # 保留原始提取数据
        "discrimination_result": {
            "is_valid": True,  # 示例：默认有效
            "score": 0.95,  # 示例：分数
            "comments": "示例判别结果",  # 示例：评语
            "recommendations": []  # 示例：建议
        }
    }
    return result

def convert_discrimination_result_to_markdown(discrimination_result, base_name):
    """
    将判别结果转换为Markdown格式
    """
    md_content = f"# 内容判别报告: {base_name}\n\n"
    md_content += f"**判别时间**: {discrimination_result.get('discrimination_time', '未知')}\n\n"
    md_content += f"**源文件**: {discrimination_result.get('source_file', '未知')}\n\n"

    # 添加判别规则信息
    md_content += "## 使用的判别规则\n\n"
    rules = discrimination_result.get('rules_applied', [])
    for i, rule in enumerate(rules, 1):
        md_content += f"{i}. {rule.get('name', '未命名规则')}: {rule.get('description', '无描述')}\n"

    md_content += "\n## 判别结果\n\n"
    disc_result = discrimination_result.get('discrimination_result', {})

    md_content += f"**有效性**: {'有效' if disc_result.get('is_valid', False) else '无效'}\n\n"
    md_content += f"**分数**: {disc_result.get('score', 0)}\n\n"
    md_content += f"**评语**: {disc_result.get('comments', '无评语')}\n\n"

    recommendations = disc_result.get('recommendations', [])
    if recommendations:
        md_content += "## 建议\n\n"
        for i, rec in enumerate(recommendations, 1):
            md_content += f"{i}. {rec}\n"

    return md_content