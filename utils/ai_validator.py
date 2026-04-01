import os
import json
import glob
import requests
import time
import logging
import uuid
from datetime import datetime
from pathlib import Path
import re
from openai import OpenAI
from utils.json_manager import ResultCollectionJsonFileManager


class AIValidator:
    """AI验证器 - 专门处理JSON数据验证并生成MD和JSON报告，严格按用户规则进行判别"""

    def __init__(self):
        # 新接口配置
        self.new_config = {
            'base_url': "http://10.100.240.4:1025/generate_stream",
            'headers': {
                "Accept": "application/json",
                "Content-Type": "application/json"
            }
        }
        self.setup_logging()

    def setup_logging(self):
        """设置日志"""
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )
        self.logger = logging.getLogger(__name__)

    def load_json_files(self, json_dir):
        """从JSON目录加载所有JSON文件，每个JSON文件作为一个整体处理"""
        # 查找所有JSON文件
        json_files = glob.glob(os.path.join(json_dir, "*.json"))
        processed_results = {}

        print(f"找到 {len(json_files)} 个JSON文件")

        for json_file in json_files:
            try:
                # 从文件名提取folder_name（去掉.json后缀）
                folder_name = os.path.splitext(os.path.basename(json_file))[0]

                # 读取JSON文件
                with open(json_file, 'r', encoding='utf-8') as f:
                    json_data = json.load(f)

                # 处理JSON数据：无论是单个地块还是多个地块，都作为该文件的数据
                processed_results[folder_name] = {
                    'json_data': json_data,  # 保持原始结构
                    'json_path': json_file,
                    'original_folder': folder_name,
                    'raw_text': self.extract_raw_text_from_json(json_data)  # 提取原始文本用于验证
                }

                print(
                    f"成功加载JSON文件: {folder_name}，数据条数: {len(json_data) if isinstance(json_data, list) else 1}")

            except Exception as e:
                print(f"加载JSON文件失败 {json_file}: {str(e)}")
                continue

        return processed_results

    def extract_raw_text_from_json(self, json_data):
        """从JSON数据中提取原始文本内容，用于验证"""
        raw_text = ""

        def extract_text(obj):
            nonlocal raw_text
            if isinstance(obj, dict):
                for key, value in obj.items():
                    if isinstance(value, str):
                        raw_text += f"{key}: {value}\n"
                    elif isinstance(value, (dict, list)):
                        extract_text(value)
            elif isinstance(obj, list):
                for item in obj:
                    extract_text(item)

        extract_text(json_data)
        return raw_text

    def extract_rule_items(self, rules_description):
        """从规则描述中提取具体的检查项"""
        if not rules_description:
            return []
        
        # 常见的中文分隔符
        separators = ['、', '，', ',', '；', ';', '以及', '和', '或', '还有', '包括']
        
        # 尝试提取括号内的内容
        import re
        bracket_pattern = r'[（\(](.*?)[）\)]'
        bracket_matches = re.findall(bracket_pattern, rules_description)
        
        if bracket_matches:
            # 如果有括号内容，通常括号内是具体的检查项
            items_text = bracket_matches[0]
            # 按分隔符拆分
            for sep in separators:
                if sep in items_text:
                    items = [item.strip() for item in items_text.split(sep) if item.strip()]
                    if items:
                        return items
        
        # 如果没有括号，尝试从整个描述中提取
        # 查找常见的规则模式
        patterns = [
            r'检查(.*?)是否',
            r'验证(.*?)是否',
            r'分析(.*?)是否',
            r'评估(.*?)是否',
            r'判别(.*?)是否',
            r'包括(.*?)等'
        ]
        
        for pattern in patterns:
            matches = re.findall(pattern, rules_description)
            if matches:
                items_text = matches[0]
                # 按分隔符拆分
                for sep in separators:
                    if sep in items_text:
                        items = [item.strip() for item in items_text.split(sep) if item.strip()]
                        if items:
                            return items
        
        # 如果无法提取具体项，返回整个规则作为一个检查项
        return [rules_description]

    def build_key_elements_section(self, rules_description):
        """根据规则描述动态生成关键要素检查部分"""
        if not rules_description:
            return '      "关键要素检查": {},\n'
        
        # 提取规则中的具体检查项
        rule_items = self.extract_rule_items(rules_description)
        
        # 如果无法提取具体项，使用默认格式
        if not rule_items:
            return '      "关键要素检查": {},\n'
        
        # 生成关键要素检查JSON格式
        elements_section = '      "关键要素检查": {\n'
        for i, item in enumerate(rule_items, 1):
            # 清理项目名称，移除可能的问题字符
            cleaned_item = item.replace('"', "'").replace('\n', ' ')
            elements_section += f'        "要素{i}（{cleaned_item}）": "存在/不存在 - 基于文档判断，引用具体内容"'
            if i < len(rule_items):
                elements_section += ',\n'
            else:
                elements_section += '\n'
        elements_section += '      },\n'
        
        return elements_section

    def decode_response_text(self, response_text):
        """解码API响应文本，处理可能的编码问题"""
        if not response_text:
            return ""

        # 尝试不同的编码方式
        encodings = ['utf-8', 'gbk', 'gb2312', 'latin-1', 'iso-8859-1', 'cp1252']

        # 如果是bytes，尝试解码
        if isinstance(response_text, bytes):
            for encoding in encodings:
                try:
                    decoded = response_text.decode(encoding)
                    # 检查解码后的文本是否包含乱码
                    if 'å' in decoded and 'æ' in decoded:
                        continue  # 跳过这种编码
                    return decoded
                except:
                    continue
            return response_text.decode('utf-8', errors='ignore')

        # 如果是字符串，检查是否已经是乱码
        if 'å' in response_text and 'æ' in response_text:
            # 可能是错误的utf-8编码，尝试重新编码再解码
            try:
                # 先尝试认为是latin-1编码的中文字符
                encoded = response_text.encode('latin-1', errors='ignore')
                decoded = encoded.decode('utf-8', errors='ignore')
                if decoded and len(decoded) > 10:
                    return decoded
            except:
                pass

            # 尝试不同的编码组合
            for encoding in encodings:
                try:
                    encoded = response_text.encode('latin-1', errors='ignore')
                    decoded = encoded.decode(encoding, errors='ignore')
                    # 检查是否包含中文
                    if any('\u4e00' <= char <= '\u9fff' for char in decoded[:100]):
                        return decoded
                except:
                    continue

        return response_text

    def fix_encoding_issues(self, response_text):
        """修复编码问题，特别处理常见的乱码模式"""
        if not response_text:
            return response_text

        # 常见的乱码模式映射（UTF-8被误认为是Latin-1）
        encoding_fixes = {
            'åæç»æ': '分析结果',
            'å®æ´æ§ä¸åè§æ§': '完整性与合规性',
            'å¿å¡«å­æ®µå®æ´æ§': '必填字段完整性',
            'æ»å': '总则',
            'èè´£åå·¥': '职责分工',
            'ææ¥ç±»åä¸åå®¹': '排查类型与内容',
            'æ²»çæµç¨': '治理流程',
            'éªæ¶ç¨åº': '验收程序',
            'æ¡£æ¡ç®¡çè¦æ±': '档案管理要求',
            'å¥æ©èæ ¸æºå¶': '奖惩考核机制',
            'é»è¾å³ç³»åçæ§': '逻辑关系合理性',
            'èè´£ååè¦çå¨é¢': '职责划分覆盖全面',
            'éæ£åçº§ååºåç': '隐患分级响应合理',
            'é­ç¯ç®¡çé¾æ¡å®æ´': '闭环管理链条完整',
            'å¤å«ç»è®º': '判别结论',
            'æ¹è¿å»ºè®®': '改进建议',
            'ä¸åè§': '不合规',
            'è¡¥å': '补充',
            'æç¡®': '明确',
            'å¶å®': '制定',
            'è¯¦ç»': '详细',
            'å¢å ': '增加',
            'ææ¡£': '文档',
            'æªæ¾å°': '未找到',
            'ç¸å³': '相关',
            'ä¿¡æ¯': '信息',
            'åè§': '合规'
        }

        # 替换乱码
        for wrong, correct in encoding_fixes.items():
            response_text = response_text.replace(wrong, correct)

        return response_text

    def build_validation_prompt(self, json_data, raw_text, folder_name, rules_description):
        """根据用户提供的规则描述构建验证提示"""

        # 如果规则描述为空，使用默认格式
        if not rules_description:
            rules_description = "请对文档内容进行合规性分析"

        # 动态生成关键要素检查部分
        key_elements_section = self.build_key_elements_section(rules_description)
        # print("***********rules_description***********")
        # print(rules_description)

        prompt_parts = [
            f"# 任务：按照用户指定规则进行文档验证",
            f"# 地块文件夹名称: {folder_name}",
            f"# 用户指定的判别规则: {rules_description}",
            f"# 重要提示：严格按照用户规则进行分析，所有判断必须严格基于原始文档内容",
            "",
            f"## 文档内容:",
            raw_text[:8000] if len(raw_text) > 8000 else raw_text,  # 限制长度，防止超出token限制
            "",
            "请严格基于以上文档内容，按照用户指定的规则进行判别：",
            "",
            "## 判别要求：",
            "1. 严格按照用户指定的规则进行分析，不能自行修改或扩展规则",
            "2. 必须严格基于用户规则中提到的具体要求进行检查",
            "3. 如果文档中有明确证据支持规则要求，判断为'合规'并引用具体内容",
            "4. 如果文档中缺乏规则要求的信息，判断为'不合规'并说明缺失什么",
            "5. 对于规则中提到的具体检查项，必须逐项检查并给出明确结论",
            "6. 所有输出必须使用正确的中文编码（UTF-8）",
            "",
            f"## 关键要素检查说明：",
            f"- 请根据用户规则中提到的具体检查项，逐项进行验证：",
            f"- 如果规则中提到多个检查项，请为每个检查项生成一个验证结果",
            f"- 如果规则中没有明确列出检查项，请根据规则的整体要求进行验证",
            f"- 每个检查项的结果应包含：是否存在、文档依据、是否符合规则要求",
            f"重要要求：",
            f"1. 只从提供的文本中判别，不要编造",
            f"2.每个字段都必须输出",
            f"3.不要输出任何额外的文字说明，只输出JSON",
            f"4. 输出必须是一个标准的一维数组，数组长度为1，包含一个字典"
            f"请严格按照以下JSON格式输出：",
            "[",
            "  {",
            f'    "字段1": "值1",',
            f'    "字段2": "值2",',
            f'    "字段3": "值3",',
            f'    "..."',
            "  }",
            "]",
            "现在请提取并输出完整JSON：",
        ]

        return "\n".join(prompt_parts)

    def validate_with_ai(self, json_data, folder_name, rules_description=""):
        """使用新接口验证单个地块JSON数据的合理性，严格按用户规则进行判别"""

        # 提取原始文本内容
        raw_text = self.extract_raw_text_from_json(json_data)

        if rules_description:
            system_content = f"""你是一名优秀的信息判别专家。在你开始判别时必须遵守以下核心规则：
                        1. 严格按照用户指定的规则进行分析.
                        2. 必须严格基于提供的原始文档内容进行分析，不能凭空想象或创造信息
                        3. 所有判断必须有明确的原始文档依据，必须引用文档中的具体内容
                        4. 如果文档中没有规则要求的相关信息，必须明确说明"文档中未找到规则要求的相关内容"
                        5. 不能复制任何示例数据，所有分析必须基于当前文档
                        6. 输出必须是纯JSON数组格式，不要包含任何思考过程、解释或其他文本
                        7. 对于合规性判断，必须有具体的文档证据支持
                        8. 确保所有输出都是正确的中文编码（UTF-8）
                        9. 关键要素检查的数量必须严格匹配用户规则中的具体要求数量
                        """
        else:
            system_content = """你是一名优秀的信息判别专家。在你开始判别时必须遵守以下核心规则：
                        1. 请对文档内容进行合规性分析
                        2. 必须严格基于提供的原始文档内容进行分析，不能凭空想象或创造信息
                        3. 所有判断必须有明确的原始文档依据，必须引用文档中的具体内容
                        4. 如果文档中没有相关信息，必须明确说明"文档中未找到相关信息"
                        5. 不能复制任何示例数据，所有分析必须基于当前文档
                        6. 输出必须是纯JSON数组格式，不要包含任何思考过程、解释或其他文本
                        7. 对于合规性判断，必须有具体的文档证据支持
                        8. 确保所有输出都是正确的中文编码（UTF-8）
                        """

        # 根据用户规则构建验证提示
        user_content = self.build_validation_prompt(json_data, raw_text, folder_name, rules_description)

        # 构建完整提示词
        prompt = f"{system_content}\n\n{user_content}"

        # 根据错误信息，接口需要 'inputs' 参数且类型为字符串
        # print("***********输入提示词**********")
        # print(prompt)
        result = self.deepseek_v3_guangzhou(prompt)
        # print("***********判别json***********")
        # print(result)

        try:
            # print(f"\n正在调用AI接口进行验证: {folder_name}")
            # # print(f"用户指定规则: {rules_description}")
            # print(f"原始文档长度: {len(raw_text)} 字符")
            # 发送请求到新接口
                # 尝试解析响应
            try:
                # print(
                #     f"AI接口返回JSON数据，结构: {list(result.keys()) if isinstance(result, dict) else type(result)}")
                # 根据content_extraction.py的经验，处理流式响应
                response_text = ""
                if isinstance(result, str):
                    # print(f"响应是字符串，长度: {len(result)}")
                    response_text = result
                elif isinstance(result, dict):
                    # 尝试不同的字段名
                    if 'response' in result:
                        response_text = result['response']
                    elif 'generated_text' in result:
                        response_text = result['generated_text']
                    elif 'text' in result:
                        response_text = result['text']
                    else:
                        # 如果找不到特定字段，尝试转换为字符串
                        response_text = json.dumps(result, ensure_ascii=False)
                else:
                    response_text = str(result)

                # 解码响应文本
                decoded_response = self.decode_response_text(response_text)
                # print(f"解码后响应文本长度: {len(decoded_response)}")

                # 修复可能的编码问题
                fixed_response = self.fix_encoding_issues(decoded_response)

                # 检查是否仍有乱码
                if 'å' in fixed_response or 'æ' in fixed_response:
                    print(f"警告: 响应中可能仍有乱码字符")
                    # 尝试更激进的修复
                    try:
                        if isinstance(response_text, str):
                            # 尝试重新编码解码
                            fixed_response = response_text.encode('latin-1').decode('utf-8', errors='ignore')
                    except:
                        pass

                # print(f"响应预览: {fixed_response[:500]}...")

                # 尝试从响应中提取JSON
                json_result = self.extract_json_from_response(fixed_response)

                # 添加规则信息
                if isinstance(json_result, list) and len(json_result) > 0 and isinstance(json_result[0], dict):
                    json_result[0]["user_rules"] = rules_description

                return json_result

            except json.JSONDecodeError:
                # 可能是纯文本响应
                # print(f"响应不是JSON，是纯文本，长度: {len(result.text)}")
                response_text = result.text
                decoded_response = self.decode_response_text(response_text)
                fixed_response = self.fix_encoding_issues(decoded_response)
                json_result = self.extract_json_from_response(fixed_response)

                # 添加规则信息
                if isinstance(json_result, list) and len(json_result) > 0 and isinstance(json_result[0], dict):
                    json_result[0]["user_rules"] = rules_description

                return json_result



        except requests.exceptions.Timeout:
            print("AI接口请求超时")
            return [{
                "error": "验证超时: 请求AI接口超过180秒",
                "user_rules": rules_description
            }]
        except requests.exceptions.ConnectionError:
            print("AI接口连接错误")
            return [{
                "error": "连接AI接口失败，请检查网络和URL配置",
                "user_rules": rules_description
            }]
        except Exception as e:
            print(f"新接口验证过程出错: {str(e)}")
            import traceback
            print(f"详细错误: {traceback.format_exc()}")
            return [{
                "error": f"验证失败: {str(e)}",
                "user_rules": rules_description
            }]

    def extract_json_from_response(self, response_text):
        """从响应文本中提取JSON"""
        try:
            # print(f"开始提取JSON, 响应文本长度: {len(response_text)}")

            preview_length = min(1000, len(response_text))
            # print(f"响应预览: {response_text[:preview_length]}...")

            # 首先尝试直接解析
            try:
                json_result = json.loads(response_text)
                print(f"✅ 直接JSON解析成功，类型: {type(json_result)}")
                if isinstance(json_result, dict):
                    json_result = [json_result]
                return json_result
            except json.JSONDecodeError:
                print("直接解析失败，尝试提取JSON部分")

            # 查找JSON开始和结束位置
            start_idx = response_text.find('[')
            end_idx = response_text.rfind(']') + 1

            if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                json_str = response_text[start_idx:end_idx]
                print(f"找到JSON字符串，长度: {len(json_str)}")
                try:
                    json_result = json.loads(json_str)
                    print(f"✅ JSON提取解析成功，类型: {type(json_result)}")
                    return json_result
                except json.JSONDecodeError as e:
                    print(f"JSON解析失败: {e}")

            # 如果没有找到完整的JSON，尝试查找对象格式并转换为数组
            start_idx = response_text.find('{')
            end_idx = response_text.rfind('}') + 1

            if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                json_str = response_text[start_idx:end_idx]
                print(f"找到JSON对象，长度: {len(json_str)}")
                try:
                    json_obj = json.loads(json_str)
                    json_result = [json_obj]
                    print(f"✅ 转换为数组成功")
                    return json_result
                except json.JSONDecodeError as e:
                    print(f"JSON对象解析失败: {e}")

            # 尝试从SSE格式中提取
            if 'data:' in response_text:
                print("检测到SSE格式，尝试提取...")
                lines = response_text.strip().split('\n')
                for line in lines:
                    if line.startswith('data: ') and line.strip() != 'data:':
                        data_content = line[6:].strip()
                        if data_content:
                            try:
                                data_json = json.loads(data_content)
                                if 'generated_text' in data_json:
                                    generated_text = data_json['generated_text']
                                    return self.extract_json_from_response(generated_text)
                            except:
                                continue

            # 如果没有找到完整的JSON，返回原始文本
            print(f"⚠️ 警告：未找到完整JSON格式")
            # print(f"原始响应: {response_text[:500]}...")
            return [{"error": "无法解析JSON响应", "raw_response": response_text[:500]}]

        except Exception as e:
            print(f"提取JSON时发生错误: {e}")
            import traceback
            print(f"详细错误: {traceback.format_exc()}")
            return [{"error": f"提取JSON失败: {e}",
                     "raw_response": response_text[:500] if 'response_text' in locals() else "无响应"}]

    def clean_filename(self, filename):
        """清理文件名中的非法字符"""
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            filename = filename.replace(char, '_')
        return filename

    def extract_rule_name(self, rules_description):
        """从规则描述中提取规则名称"""
        if not rules_description:
            return "自定义规则判别"

        # 清理并提取规则名称
        rule_name = rules_description.strip()

        # 如果规则描述太长，截断
        if len(rule_name) > 30:
            rule_name = rule_name[:30] + "..."

        rule_name = self.clean_filename(rule_name)

        return rule_name

    def save_validation_json(self, validation_results, output_dir, report_name, rules_description):
        """保存验证结果为JSON文件，按 报告名_规则名.json 格式命名"""
        print("report_name: ", report_name)
        clean_report_name = self.clean_filename(report_name)
        rule_name = self.extract_rule_name(rules_description)

        json_filename = f"{clean_report_name}_discrimination.json"
        json_path = os.path.join(output_dir, json_filename)

        if not isinstance(validation_results, list):
            validation_results = [validation_results]

        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(validation_results, f, ensure_ascii=False, indent=2)

        print(f"验证结果JSON文件已保存")
        return json_path

    def save_validation_markdown(self, validation_results, output_dir, report_name, rules_description):
        """保存验证结果为Markdown文件，按 报告名_规则名.md 格式命名"""
        clean_report_name = self.clean_filename(report_name)
        rule_name = self.extract_rule_name(rules_description)

        md_filename = f"{clean_report_name}.md"
        md_path = os.path.join(output_dir, md_filename)

        md_content = f"# 验证结果报告\n\n"
        md_content += f"**报告名称**: {report_name}\n\n"
        md_content += f"**用户指定判别规则**: {rules_description}\n\n"
        md_content += f"**生成时间**: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        md_content += f"**验证原则**: 严格按用户规则进行分析，所有判断基于原始文档内容\n\n"
        md_content += "---\n\n"

        # 处理validation_results，确保它是字典格式
        if not isinstance(validation_results, dict):
            print(f"警告: validation_results 不是字典格式，类型: {type(validation_results)}")
            if isinstance(validation_results, list):
                validation_results = {"default": {"default": validation_results}}
            else:
                validation_results = {"default": {"default": [validation_results]}}

        for folder_name, plot_results in validation_results.items():
            md_content += f"## 文件夹: {folder_name}\n\n"

            # 确保plot_results是字典
            if not isinstance(plot_results, dict):
                print(f"警告: plot_results 不是字典格式，类型: {type(plot_results)}")
                if isinstance(plot_results, list):
                    plot_results = {"default": plot_results}
                else:
                    plot_results = {"default": [plot_results]}

            for plot_identifier, result in plot_results.items():
                md_content += f"### 地块: {plot_identifier}\n\n"

                # 处理数组格式的结果
                if isinstance(result, list):
                    for index, item in enumerate(result, 1):
                        md_content += f"#### 验证结果 {index}\n\n"

                        # 显示规则信息
                        user_rules = item.get("user_rules", rules_description)
                        md_content += f"**验证规则**: {user_rules}\n\n"

                        if isinstance(item, dict):
                            # 规则遵循情况
                            if "规则遵循情况" in item:
                                md_content += f"**规则遵循情况**: {item['规则遵循情况']}\n\n"

                            # 分析结果
                            if "分析结果" in item:
                                md_content += "##### 分析结果（基于用户规则）\n\n"
                                analysis = item["分析结果"]

                                # 规则符合性分析
                                if "规则符合性分析" in analysis:
                                    md_content += f"- **规则符合性分析**: {analysis['规则符合性分析']}\n\n"

                                # 关键要素检查
                                if "关键要素检查" in analysis:
                                    md_content += "- **关键要素检查（基于文档）**:\n"
                                    for field, status in analysis["关键要素检查"].items():
                                        md_content += f"  - {field}: {status}\n"
                                    md_content += "\n"

                                # 逻辑一致性
                                if "逻辑一致性" in analysis:
                                    md_content += "- **逻辑一致性（基于文档）**:\n"
                                    for relation, consistent in analysis["逻辑一致性"].items():
                                        if isinstance(consistent, bool):
                                            status = "✅ 一致" if consistent else "❌ 不一致"
                                        else:
                                            status = consistent
                                        md_content += f"  - {relation}: {status}\n"
                                    md_content += "\n"

                            # 判别结论
                            if "判别结论" in item:
                                conclusion = item["判别结论"]
                                status_emoji = "✅" if conclusion == "合规" else "❌"
                                md_content += f"##### 判别结论（基于用户规则）: {status_emoji} {conclusion}\n\n"

                            # 改进建议
                            if "改进建议" in item and item["改进建议"]:
                                md_content += "##### 改进建议（基于规则要求和文档缺失）\n\n"
                                for suggestion in item["改进建议"]:
                                    md_content += f"- {suggestion}\n"
                                md_content += "\n"
                        else:
                            md_content += f"**验证结果**: {item}\n\n"

                        md_content += "---\n\n"
                elif isinstance(result, dict):
                    # 显示规则信息
                    user_rules = result.get("user_rules", rules_description)
                    md_content += f"**验证规则**: {user_rules}\n\n"

                    # 规则遵循情况
                    if "规则遵循情况" in result:
                        md_content += f"**规则遵循情况**: {result['规则遵循情况']}\n\n"

                    # 分析结果
                    if "分析结果" in result:
                        md_content += "#### 分析结果（基于用户规则）\n\n"
                        analysis = result["分析结果"]

                        # 规则符合性分析
                        if "规则符合性分析" in analysis:
                            md_content += f"- **规则符合性分析**: {analysis['规则符合性分析']}\n\n"

                        # 关键要素检查
                        if "关键要素检查" in analysis:
                            md_content += "- **关键要素检查（基于文档）**:\n"
                            for field, status in analysis["关键要素检查"].items():
                                md_content += f"  - {field}: {status}\n"
                            md_content += "\n"

                        # 逻辑一致性
                        if "逻辑一致性" in analysis:
                            md_content += "- **逻辑一致性（基于文档）**:\n"
                            for relation, consistent in analysis["逻辑一致性"].items():
                                if isinstance(consistent, bool):
                                    status = "✅ 一致" if consistent else "❌ 不一致"
                                else:
                                    status = consistent
                                md_content += f"  - {relation}: {status}\n"
                            md_content += "\n"

                    # 判别结论
                    if "判别结论" in result:
                        conclusion = result["判别结论"]
                        status_emoji = "✅" if conclusion == "合规" else "❌"
                        md_content += f"#### 判别结论（基于用户规则）: {status_emoji} {conclusion}\n\n"

                    # 改进建议
                    if "改进建议" in result and result["改进建议"]:
                        md_content += "#### 改进建议（基于规则要求和文档缺失）\n\n"
                        for suggestion in result["改进建议"]:
                            md_content += f"- {suggestion}\n"
                        md_content += "\n"
                else:
                    md_content += f"**验证结果**: {result}\n\n"

                md_content += "---\n\n"

        # 添加文档验证说明
        md_content += "## 验证说明\n\n"
        md_content += f"1. **规则驱动**: 严格按照用户指定的规则'{rules_description}'进行验证\n"
        md_content += "2. **文档依据**: 所有判断必须有明确的原始文档依据\n"
        md_content += "3. **严格性**: 如果文档中未找到规则要求的相关信息，判断为'不存在'或'未找到'\n"
        md_content += "4. **可追溯性**: 分析结果必须能够追溯到文档中的具体内容\n"
        md_content += "5. **客观性**: 避免主观臆断，所有结论基于文档事实和规则要求\n\n"

        # 保存Markdown文件
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write(md_content)

        # print(f"验证结果Markdown文件已保存: {md_path}")
        return md_path

    def process_validation_results(self, processed_results, output_dir, rules_description="",result_collection_file_path=""):
        """处理验证后的结果并生成JSON和Markdown文件"""
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)

        successful_count = 0
        failed_count = 0
        validation_results = {}

        # print(f"\n{'=' * 60}")
        # print(f"开始处理验证结果（严格按用户规则验证）")
        # print(f"输出目录: {output_dir}")
        # print(f"待处理文件数: {len(processed_results)}")
        # print(f"验证原则: 严格按用户规则，基于原始文档内容")
        # print(f"{'=' * 60}")

        for folder_name, result_info in processed_results.items():
            try:
                json_data = result_info['json_data']
                json_path = result_info['json_path']
                raw_text = result_info.get('raw_text', '')

                # print(f"\n处理文件: {folder_name}")
                # print(f"JSON路径: {json_path}")
                # print(f"原始文档长度: {len(raw_text)} 字符")
                # print(f"数据条数: {len(json_data) if isinstance(json_data, list) else 1}")

                # 对JSON文件中的每个地块进行AI验证
                plot_validation_results = {}

                # 确保json_data是列表（数组格式）
                if not isinstance(json_data, list):
                    json_data = [json_data]

                # 获取报告名称（使用第一个地块的报告名称或文件夹名称）
                report_name = None
                if json_data and isinstance(json_data[0], dict):
                    report_name = json_data[0].get("报告名称", folder_name)
                else:
                    report_name = folder_name

                for plot_idx, plot_data in enumerate(json_data):
                    # 生成地块标识
                    plot_identifier = plot_data.get("地块编号", f"{folder_name}_地块{plot_idx + 1}")

                    print(f"验证地块: {plot_identifier}")
                    # print(f"用户规则: {rules_description}")

                    # 使用新接口验证单个地块数据
                    validation_result = self.validate_with_ai(plot_data, plot_identifier, rules_description)
                    plot_validation_results[plot_identifier] = validation_result

                    print(f"地块验证完成: {plot_identifier}（严格按用户规则）")

                # 保存该文件的验证结果
                validation_results[folder_name] = plot_validation_results

                # 保存JSON格式的验证结果

                json_path = self.save_validation_json(plot_validation_results, output_dir, report_name,
                                                      rules_description)

                # 保存Markdown格式的验证结果
                md_path = self.save_validation_markdown(plot_validation_results, output_dir, report_name,
                                                        rules_description)
                print("md_path: ", md_path)
                if json_path and md_path:
                    successful_count += 1
                    print(f"✅ 成功生成验证结果: {report_name}（严格按用户规则）")
                    # print(f"  JSON文件: {json_path}")
                    # print(f"  MD文件: {md_path}")
                else:
                    failed_count += 1
                    print(f"❌ 生成验证结果失败: {report_name}")
                judge_result = {}
                if isinstance(plot_validation_results, dict):
                    for key, value in plot_validation_results.items():
                        if isinstance(value,list):
                            judge_result = value[0]
                elif isinstance(plot_validation_results, list):
                    judge_result = plot_validation_results[0]
                else:
                    judge_result = plot_validation_results
                result_collection_json_manager = ResultCollectionJsonFileManager()
                if not result_collection_file_path.exists():
                    result_collection_file_path.write_text("[]", encoding="utf-8")
                with result_collection_json_manager.file_lock:
                    collections = result_collection_json_manager.load_json_without_lock(result_collection_file_path)
                    for collection in collections:
                        if folder_name+".json" == collection['filename']:
                            collection["judge_results"] = judge_result
                            collection["update_time"] = int(time.time())
                            collection["is_judged"] = True
                            collection["is_valid"] = self.check_violation_status(judge_result)
                    # 保存任务
                    result_collection_json_manager.save_json_without_lock(result_collection_file_path, collections)

            except Exception as e:
                failed_count += 1
                print(f"❌ 处理验证结果失败 {folder_name}: {str(e)}")
                import traceback
                print(f"详细错误: {traceback.format_exc()}")
                continue

        # print(f"\n{'=' * 60}")
        # print("AI验证处理完成！（严格按用户规则验证）")
        # print(f"成功: {successful_count} 个文件")
        # print(f"失败: {failed_count} 个文件")
        # print(f"{'=' * 60}")

        return successful_count, failed_count, validation_results

    def fix_mojibake(self,text: str) -> str:
        """
        修复 UTF-8 被错误当成 latin1 解码后的乱码
        如果是正常 Unicode 中文，原样返回
        """
        try:
            return text.encode("latin1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return text

    def strip_eos(self,text: str) -> str:
        """
        清理模型输出中的 EOS 特殊标记
        """
        if not text:
            return text

        eos_tokens = [
            "<|end_of_sentence|>",
            "",
            "</s>",
            "<eos>"
        ]

        for eos in eos_tokens:
            text = text.replace(eos, "")

        return text.strip()

    def generate_stream(
            self,
            prompt: str,
            max_new_tokens: int = 1500,
            timeout: int = 240
    ) -> str:
        """
        最终·最终版：
        - 兼容 HuggingFace TGI / vLLM 的 SSE 流式输出
        - 优先使用 generated_text（最终权威结果）
        - 否则使用 token 流拼接
        - 防 JSON 半包
        - 修复乱码
        """
        api_url = "http://10.100.240.4:1025/generate_stream"
        request_data = {
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

        buffer = ""
        token_chunks = []
        final_text_from_generated = None
        finished = False

        # 用 iter_content，避免 iter_lines 导致 JSON 半包
        for chunk in response.iter_content(chunk_size=None):
            if not chunk:
                continue

            buffer += chunk.decode("utf-8", errors="ignore")

            # SSE 事件以空行分隔
            while "\n\n" in buffer:
                event, buffer = buffer.split("\n\n", 1)

                for line in event.splitlines():
                    if not line.startswith("data:"):
                        continue

                    data_str = line[len("data:"):].strip()

                    # SSE 结束标志
                    if not data_str or data_str == "[DONE]":
                        finished = True
                        continue

                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        # JSON 半包，放回 buffer 等下一次
                        buffer = data_str + "\n\n" + buffer
                        break

                    # ==============================
                    # ✅ 最高优先级：generated_text
                    # ==============================
                    if data.get("generated_text"):
                        final_text_from_generated = data["generated_text"]
                        finished = True
                        break

                    # ==============================
                    # 普通 token 流
                    # ==============================
                    token = data.get("token")
                    if not token:
                        continue

                    text = token.get("text", "")
                    special = token.get("special")

                    text = self.fix_mojibake(text)

                    if not special:
                        token_chunks.append(text)
                    else:
                        finished = True

                if final_text_from_generated:
                    break

            if final_text_from_generated:
                break

        # ==============================
        # 最终结果选择
        # ==============================
        if final_text_from_generated:
            return self.strip_eos(self.fix_mojibake(final_text_from_generated))

        final_text = "".join(token_chunks)
        final_text = self.strip_eos(final_text)
        print(final_text)

        if not finished:
            final_text = "⚠️ 没有检测到完整结果\n\n" + final_text

        return final_text

    def deepseek_chat(
            self,
            prompt: str,
            max_new_tokens: int = 1500,
            timeout: int = 240
    ) -> str:
        """
        流式调用API
        """
        api_key = "sk-61c86b0083fe4aa99b216691ac8e8cec"
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
            self,
            prompt: str,
            max_new_tokens: int = 1500,
            timeout: int = 240
    ) -> str:
        url = "http://59.210.232.66:10941/v1/chat/completions"
        headers = {
            "Authorization": "Bearer 094a2862-5f9b-4bbb-aba3-549f04abf177",
            "Content-Type": "application/json",
            # curl 里有这个 host 头，requests 里也要保持一致
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
            # 检查状态码
            response.raise_for_status()
        except Exception as e:
            print(f"❌ API请求失败: {e}")
            return None
        # 解析 JSON
        result = response.json()

        # 打印完整返回
        print(result)
        # 常见 OpenAI / DeepSeek 风格的最终文本位置
        final_text = result["choices"][0]["message"]["content"]
        return final_text

    def check_violation_status(self,data_dict):
        """
        判断字典中是否存在违规字段

        Args:
            data_dict: 待检查的字典

        Returns:
            str: "legal" - 没有违规
                 "illegal" - 存在违规
                 "unknown" - 存在error字段
        """
        # 首先检查是否存在error字段
        if "error" in data_dict or "Error" in data_dict or "ERROR" in data_dict:
            return "unknown"

        # 需要检查的违规相关字段关键词
        violation_keywords = ["违规","超标","violation", "是否违规", "违规判定"]

        # 违规判定值中的违规标识
        violation_indicators = ["违规", "是", "yes", "true", "存在违规", "违规建设"]

        # 遍历字典中的所有字段
        for key, value in data_dict.items():
            # 跳过user_rules等规则说明字段
            if key == "user_rules" or "规则" in key:
                continue

            # 检查字段名是否包含违规相关关键词
            if any(keyword in key for keyword in violation_keywords):
                # 如果值是字符串且包含违规标识
                if isinstance(value, str):
                    value_lower = value.lower()
                    # 检查是否包含违规标识
                    for indicator in violation_indicators:
                        if indicator in value:
                            # 排除"不违规"、"未违规"等否定情况
                            if "不违规" not in value and "未违规" not in value:
                                return "illegal"

        # 如果没有找到违规字段
        return "legal"

if __name__ == "__main__":
    validator = AIValidator()

    # 配置路径
    json_dir = "data/output/filter_pdf/markdown_extraction"
    output_dir = "data/output/results"

    # 加载JSON文件
    processed_results = validator.load_json_files(json_dir)

    if processed_results:
        # 示例：使用不同的用户规则进行验证
        user_rules_options = [
            "检查文档是否包含完整的安全生产责任制，包括责任分工、考核机制等内容",
            "验证环境管理体系中污染防治措施是否明确，监测监控要求是否具体",
            "判别质量管理体系中质量控制流程是否合理，质量检查程序是否完整",
            "分析项目管理制度中风险管理措施是否到位，应急预案是否完善",
            "评估文档中是否包含隐患排查制度的所有必要要素：总则、职责分工、排查内容、治理流程等"
        ]

        print("示例用户规则选项:")
        for i, rule in enumerate(user_rules_options, 1):
            print(f"  {i}. {rule}")

        # 默认使用第一个规则
        selected_rule = user_rules_options[0]
        print(f"\n使用用户规则: {selected_rule}")

        # validator.process_validation_results(
        #     processed_results,
        #     output_dir,
        #     rules_description=selected_rule
        # )
    else:
        print("没有找到可处理的JSON文件")
