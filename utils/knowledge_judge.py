# utils/knowledge_judge.py
# utils/knowledge_enhanced_validator.py
import json
import os
from pathlib import Path
from utils.ai_validator import AIValidator
from utils.knowledge_util import query_knowledge_for_task, load_task_rules, _get_pk as knowledge_util_get_pk
import re


class KnowledgeEnhancedValidator:
    """知识库增强验证器 - 统一处理带知识库和不带知识库的判别逻辑"""
    
    def __init__(self, task_id, task_root_path,use_mapping_table=False):
        self.task_id = task_id
        self.task_root_path = task_root_path
        self.ai_validator = AIValidator()
        self.task_json_path = Path(self.task_root_path) / "tasks.json"
        self.use_mapping_table = use_mapping_table
    
    def process_validation_results(self, processed_results, output_dir, judgment_rules,result_collection_file_path):
        """
        处理验证后的结果 - 统一处理知识库和非知识库规则
        为每个文件单独构建规则描述，确保每个文件只使用其对应的知识库内容
        """
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
        
        # 分离知识库规则和普通规则
        knowledge_rules = []
        normal_rules = []
        
        for rule in judgment_rules:
            kb = rule.get('knowledgeBase', '').strip()
            sf = rule.get('sourceFields', [])
            if kb and kb.lower() != 'none' and sf and len(sf) > 0:  # Excludes rules with "none" as knowledgeBase# 确保 knowledgeBase 不为空且 sourceFields 不为空列表
                knowledge_rules.append(rule)
            else:
                normal_rules.append(rule)
        
        # 如果有知识库规则，先执行知识库查询（会按文件生成各自的 {pk}_knowledge.json）
        if knowledge_rules:
            self._run_knowledge_query(knowledge_rules)
        
        # 为确保每个文件只使用对应的知识库内容：逐文件构建规则描述并分别调用 AIValidator
        total_success = 0
        total_fail = 0
        merged_results = {}
        
        for folder_name, info in processed_results.items():
            single_processed = {folder_name: info}
            # 为当前文件构建合并后的规则描述（方法会读取对应的 {pk}_knowledge.json）
            combined_rules_for_file = self._combine_rules_with_knowledge(
                normal_rules, 
                knowledge_rules, 
                single_processed,
                folder_name
            )
            
            s, f, res = self.ai_validator.process_validation_results(
                single_processed,
                output_dir,
                combined_rules_for_file,
                result_collection_file_path
            )
            
            total_success += s
            total_fail += f
            if isinstance(res, dict):
                # 合并返回的验证结果字典
                for k, v in res.items():
                    merged_results[k] = v
        
        return total_success, total_fail, merged_results
    
    def _run_knowledge_query(self, knowledge_rules):
        """
        执行知识库查询
        """
        try:
            # 创建临时任务配置，只包含知识库规则
            temp_task_config = self._create_temp_task_config_with_knowledge_rules(knowledge_rules)
            
            # 执行知识库查询
            query_knowledge_for_task(
                self.task_id,
                temp_task_config,
                use_mapping_table=self.use_mapping_table
            )
            
        except Exception as e:
            print(f"知识库查询执行失败: {e}")
            import traceback
            print(f"详细错误: {traceback.format_exc()}")
    
    def _create_temp_task_config_with_knowledge_rules(self, knowledge_rules):
        """
        创建临时任务配置，只包含知识库规则
        """
        # 读取原任务配置
        if self.task_json_path.exists():
            with open(self.task_json_path, 'r', encoding='utf-8') as f:
                all_tasks = json.load(f)
        else:
            # 如果原文件不存在，创建基本结构
            all_tasks = []
        
        # 查找或创建目标任务
        target_task = None
        for task in all_tasks:
            if task.get('id') == self.task_id:
                target_task = task
                break
        
        if target_task is None:
            target_task = {
                'id': self.task_id,
                'judgmentRules': knowledge_rules
            }
            all_tasks.append(target_task)
        else:
            target_task['judgmentRules'] = knowledge_rules
        
        # 保存临时配置
        temp_task_file = self.task_json_path.parent / f"temp_{self.task_id}_knowledge.json"
        with open(temp_task_file, 'w', encoding='utf-8') as f:
            json.dump(all_tasks, f, ensure_ascii=False, indent=2)
        
        return str(temp_task_file)
    
    def _load_knowledge_for_file(self, file_name, knowledge_rules):
        """
        读取单个文件的 {pk}_knowledge.json，并返回按行的纯知识内容（只包含 content 字段）
        返回字符串，行与行之间用换行分隔。
        """
        try:
            # 使用与 knowledge_util 中相同的 _get_pk 函数，确保一致性
            pk = knowledge_util_get_pk(file_name)
            extraction_dir = Path(self.task_root_path) / "extraction_results"
            knowledge_file = extraction_dir / f"{pk}_knowledge.json"
            if not knowledge_file.exists():
                print(f"知识库文件不存在: {knowledge_file}")
                
                # 尝试更智能的文件匹配
                # 1. 尝试查找以相同前缀开头的文件
                possible_files = list(extraction_dir.glob(f"{pk}*_knowledge.json"))
                if possible_files:
                    knowledge_file = possible_files[0]
                    print(f"找到匹配文件: {knowledge_file}")
                else:
                    # 2. 如果找不到精确匹配，尝试反向查找 - 从知识库文件名推断原始文件名
                    all_knowledge_files = list(extraction_dir.glob("*_knowledge.json"))
                    
                    # 尝试从所有知识库文件中找到最可能匹配的
                    best_match = None
                    best_score = 0
                    for kf in all_knowledge_files:
                        # 提取知识库文件的名称部分
                        kb_stem = kf.stem.replace("_knowledge", "")
                        
                        # 计算相似度得分
                        common_prefix_len = 0
                        for a, b in zip(file_name, kb_stem):
                            if a == b:
                                common_prefix_len += 1
                            else:
                                break
                        
                        if common_prefix_len > best_score:
                            best_score = common_prefix_len
                            best_match = kf
                    
                    if best_match:
                        knowledge_file = best_match
                        # print(f"找到最佳匹配文件: {knowledge_file}")
                    else:
                        print(f"无法找到对应的知识库文件")
                        return ""
            
            items = []
            with open(knowledge_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            # 收集该文件下所有 knowledge.content
            for q in data.get("knowledge_queries", []):
                for k in q.get("knowledge", []):
                    c = k.get("content", "").strip()
                    if c:
                        items.append(c)
            
            return "\n\n".join(items)
        except Exception as e:
            print(f"读取知识库文件失败 {file_name}: {e}")
            return ""
    

    def _combine_rules_with_knowledge(self, normal_rules, knowledge_rules, processed_results, file_name):
        """ 
        为单个文件构建最终 prompt（包含知识库部分和普通规则部分），将知识库内容与对应规则关联
        """
        # 读取该文件对应的知识库纯内容（只取 content 字段）
        knowledge_text = self._load_knowledge_for_file(file_name, knowledge_rules) if knowledge_rules else ""

        # 获取知识库查询结果，以便与规则关联
        knowledge_items = self._load_knowledge_items(file_name, knowledge_rules) if knowledge_rules else {}

        prompt_parts = []

        # 构建知识库规则部分
        if knowledge_rules:
            kb_sections = []
            for r in knowledge_rules:
                name = (r.get("name") or "").strip()
                desc = (r.get("description") or "").strip()
                knowledge_base = r.get("knowledgeBase", "")
                
                if name:
                    rule_part = f"【{name}】: {desc}"
                    
                    # 查找与此规则相关的知识库内容
                    related_knowledge = knowledge_items.get(knowledge_base, [])
                    if related_knowledge:
                        knowledge_content = "\n".join(related_knowledge)
                        rule_part += f"\n\n参考知识库内容：\n{knowledge_content}"
                    
                    kb_sections.append(rule_part)
            
            if kb_sections:
                prompt_parts.append("需要结合知识库进行判别：\n\n" + "\n\n".join(kb_sections))

        # 普通规则部分
        normal_lines = []
        for r in (normal_rules or []):
            name = (r.get("name") or "").strip()
            desc = (r.get("description") or "").strip()
            if name:
                normal_lines.append(f"【{name}】: {desc}")
        
        if normal_lines:
            normal_section = "需要提取或判别的字段：\n\n" + "\n".join(normal_lines)
            prompt_parts.append(normal_section)

        final_prompt = "\n\n".join(prompt_parts).strip()
        # print(f"\n===== 为文件 {file_name} 生成的 Prompt（前800字符） =====")
        # print(final_prompt[:800])
        # print("=" * 50 + "\n")
        return final_prompt

    def _load_knowledge_items(self, file_name, knowledge_rules):
        """
        读取单个文件的 {pk}_knowledge.json，并返回按 knowledgeBase 分类的知识内容
        返回字典，key是knowledgeBase名称，value是相关知识内容列表
        """
        try:
            # 使用与 knowledge_util 中相同的 _get_pk 函数，确保一致性
            pk = self._get_pk(file_name)
            extraction_dir = Path(self.task_root_path) / "extraction_results"
            knowledge_file = extraction_dir / f"{pk}_knowledge.json"
            if not knowledge_file.exists():
                print(f"知识库文件不存在: {knowledge_file}")
                return {}

            knowledge_items = {}
            with open(knowledge_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            # 按 knowledgeBase 对知识内容进行分类
            for query in data.get("knowledge_queries", []):
                kb_name = query.get("knowledgeBase", "default")
                knowledge_list = []
                
                for k in query.get("knowledge", []):
                    c = k.get("content", "").strip()
                    if c:
                        knowledge_list.append(c)
                
                if knowledge_list:
                    if kb_name not in knowledge_items:
                        knowledge_items[kb_name] = []
                    knowledge_items[kb_name].extend(knowledge_list)
            
            return knowledge_items
        except Exception as e:
            print(f"读取知识库文件失败 {file_name}: {e}")
            return {}
    def _get_pk(self, name):
        """从名称中提取主键 - 使用与 knowledge_util 相同的逻辑"""
        return knowledge_util_get_pk(name)

    def save_validation_markdown(self, validation_results, output_dir, report_name, rules_description):
        """
        保存验证结果为Markdown文件
        """
        return self.ai_validator.save_validation_markdown(
            validation_results, 
            output_dir, 
            report_name, 
            rules_description
        )