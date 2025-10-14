import os
import json
import time
from openai import OpenAI
from openpyxl import Workbook
from openpyxl.drawing.image import Image
from openpyxl.utils import get_column_letter
from openpyxl.styles import Alignment, Font, PatternFill
import re
import logging  # 推荐用logging模块，支持输出到文件，便于追溯
from pathlib import Path  # 用于读取markdown文件内容


"""
从大量 Markdown 格式的地块报告中提取结构化信息（通过大模型处理），并自动生成带图片的 Excel 报告。
"""
# 初始化客户端
client = OpenAI(
    api_key="sk-f46b3de03068446fa683841dc42bab54",
    base_url="https://api.deepseek.com"
)

# --- 路径配置 ---
# markdown_dir = r"D:\PostgraduateStudy\env\text_extractor-main\data\output\pdf2md"
# excel_output_dir = r"D:\PostgraduateStudy\env\text_extractor-main\data\output\results"
#image_dir = r"D:\信息中心\大模型\部里工作\土壤司\建设用地\地块开发建设情况信息提取\报告自动下载\4000个报告批量下载\3000-4159\markdown\images"

# 确保输出目录存在
# os.makedirs(excel_output_dir, exist_ok=True)


def create_excel_from_json(json_data, excel_path, image_base_dir):
    """根据JSON数据创建Excel文件"""
    # 确保json_data是列表格式
    if isinstance(json_data, str):
        try:
            json_data = json.loads(json_data)
        except json.JSONDecodeError:
            print("错误：无法解析JSON字符串")
            return False

    if not isinstance(json_data, list) or len(json_data) == 0:
        print("错误：JSON数据不是列表格式或为空")
        return False

    wb = Workbook()
    ws = wb.active
    ws.title = "地块开发信息"

    # 定义表头
    headers = [
        "地块编号","报告名称","评审前违规建设情况","开发证明材料文字",
        "开发证明材料图片1图片说明","开发证明材料图片1图片",
        "开发证明材料图片2图片说明","开发证明材料图片2图片",
        "开发证明材料图片3图片说明","开发证明材料图片3图片",
        "开发证明材料图片4图片说明","开发证明材料图片4图片",
        "是否2019年前完工","完工时间证明材料文字","最终判断结果","行业类别","地块用地历史是否涉及重点行业","现状用地类型"
    ]

    # 写入表头
    for col_idx, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal='center', vertical='center')

    # 写入数据
    row_idx = 2
    for item_idx, item in enumerate(json_data, 1):
        if not isinstance(item, dict):
            continue

        # 基础信息字段
        ws.cell(row=row_idx, column=1, value=item.get("地块编号", ""))
        ws.cell(row=row_idx, column=2, value=item.get("报告名称", ""))
        ws.cell(row=row_idx, column=3, value=item.get("评审前违规建设情况", ""))
        ws.cell(row=row_idx, column=4, value=item.get("开发证明材料文字", ""))
        ws.cell(row=row_idx, column=13, value=item.get("是否2019年前完工", ""))
        ws.cell(row=row_idx, column=14, value=item.get("完工时间证明材料文字", ""))
        ws.cell(row=row_idx, column=15, value=item.get("最终判断结果", ""))
        ws.cell(row=row_idx, column=16, value=item.get("行业类别", ""))
        ws.cell(row=row_idx, column=17, value=item.get("地块用地历史是否涉及重点行业", ""))
        ws.cell(row=row_idx, column=18, value=item.get("现状用地类型", ""))


        # 处理4个固定的图片字段
        for img_group in range(1, 5):
            # 计算列位置：图片说明列和图片列
            desc_col = 4 + (img_group * 2) - 1  # 图片说明列：4,6,8,10
            img_col = desc_col + 1  # 图片列：5,7,9,11

            # 图片说明字段
            desc_key = f"开发证明材料图片_{img_group}图片说明"  # 构建图片说明字段名
            desc_value = item.get(desc_key, "")  # 获取图片说明，如果没有则为空字符串
            ws.cell(row=row_idx, column=desc_col, value=desc_value)  # 写入图片说明到单元格

            # 图片路径字段
            img_key = f"开发证明材料图片_{img_group}_图片"  # 构建图片路径字段名
            img_path = item.get(img_key, "")  # 获取图片绝对路径，如果没有则为空字符串
            print(img_path)
            if img_path:  # 如果图片路径不为空
                # 修复路径格式：只替换错误的分隔符，保持正确的路径结构
                img_path = img_path.replace('Ā', '4').replace('À', '3').replace('Ñ', '3')
                img_path = img_path.replace('\\', '/').replace('//', '/')  # 统一使用正斜杠

                if os.path.exists(img_path):  # 检查图片文件是否存在
                    try:
                        img = Image(img_path)  # 创建图片对象
                        img.width = 8 * 37.8  # 设置图片宽度（8厘米转换为点数）
                        img.height = 5 * 37.8  # 设置图片高度（5厘米转换为点数）
                        img.anchor = f"{get_column_letter(img_col)}{row_idx}"  # 设置图片锚定位置
                        ws.add_image(img)  # 将图片添加到工作表
                        print(f"图片插入成功: {os.path.basename(img_path)}")  # 打印成功信息
                    except Exception as e:
                        # 图片插入失败，在单元格中记录错误信息
                        ws.cell(row=row_idx, column=img_col, value=f"图片插入失败: {e}")
                        print(f"图片插入失败: {e}")  # 打印错误信息
                else:
                    # 图片文件不存在，将图片绝对路径保存到单元格
                    ws.cell(row=row_idx, column=img_col, value=img_path)
                    print(f"图片文件不存在，保存路径: {img_path}")  # 打印提示信息
                    print(f"检查路径是否存在: {os.path.exists(img_path)}")  # 添加调试信息
            else:
                # 图片路径为空，单元格留空
                ws.cell(row=row_idx, column=img_col, value="")
                print(f"第{img_group}组图片路径为空")  # 打印提示信息

        row_idx += 1

    # 调整格式 - 自动调整单元格大小以匹配图片大小
    # 图片大小为8厘米宽×5厘米高
    # 列宽设置：图片列需要足够宽以容纳8厘米的图片
    column_widths = [25, 20, 40, 25, 32, 32, 32, 32, 32, 32, 32, 32, 40, 50]
    for i, width in enumerate(column_widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = width

    # 设置行高以匹配图片高度(5*37.8点)
    # Excel中行高单位为像素，图片高度为5*37.8=189像素
    for row in range(2, row_idx):
        ws.row_dimensions[row].height = 189  # 与图片高度保持一致

    # 保存文件
    try:
        wb.save(excel_path)
        print(f"Excel文件保存成功: {excel_path}")
        return True
    except Exception as e:
        print(f"保存Excel文件时出错: {e}")
        return False

# 添加一个辅助函数来调试JSON结构
def debug_json_structure(json_data):
    """调试JSON数据结构，确保字段名正确"""
    if isinstance(json_data, list) and len(json_data) > 0:
        first_item = json_data[0]
        print("=== JSON数据结构分析 ===")

        # 打印所有字段
        print("所有字段:")
        for key, value in first_item.items():
            value_preview = str(value)[:50] + "..." if len(str(value)) > 50 else str(value)
            print(f"  '{key}': {value_preview}")

        # 特别检查图片相关字段
        print("\n=== 图片字段检查 ===")
        image_fields = []
        for key in first_item.keys():
            if any(x in key for x in ['图片', 'image', 'img', 'photo']):
                image_fields.append(key)

        if image_fields:
            for field in sorted(image_fields):
                value = first_item[field]
                print(f"  {field}: {str(value)[:100]}...")
        else:
            print("  未找到图片相关字段")

        return first_item.keys()
    return []

def extract_json_from_response(response_text):
    """从大模型响应中提取JSON字符串"""
    print(f"开始解析响应: {response_text[:200]}...")  # 打印前200字符

    # 清理响应文本
    cleaned_text = response_text.strip()

    # 尝试多种方式提取JSON
    json_patterns = [
        r'```json\s*([\s\S]*?)\s*```',  # 匹配 ```json ... ```
        r'```\s*([\s\S]*?)\s*```',  # 匹配 ``` ... ```
        r'(\[[\s\S]*\])',  # 匹配 [...]
        r'(\{[\s\S]*\})'  # 匹配 {...}
    ]

    for pattern in json_patterns:
        match = re.search(pattern, cleaned_text)
        if match:
            json_str = match.group(1).strip()
            try:
                result = json.loads(json_str)
                print("成功解析JSON")
                return result
            except json.JSONDecodeError as e:
                print(f"JSON解析错误: {e}")
                continue

    # 如果以上模式都不匹配，尝试直接解析整个响应
    try:
        result = json.loads(cleaned_text)
        print("直接解析成功")
        return result
    except json.JSONDecodeError as e:
        print(f"无法解析JSON: {e}")
        raise ValueError("无法从响应中提取有效的JSON数据")

def ai_judge(markdown_dir,excel_output_dir,image_dir):
    # 添加统计变量
    total_files = 0  # 总文件数
    processed_files = 0  # 已处理文件数

    # 统计总文件数
    md_files = []
    for root, dirs, files in os.walk(markdown_dir):
        for filename in files:
            if filename.endswith(".md"):
                md_files.append((root, filename))

    total_files = len(md_files)
    print(f"总共发现 {total_files} 个Markdown文件待处理")
    # 处理所有Markdown文件
    for root, dirs, files in os.walk(markdown_dir):
        for filename in files:
            if filename.endswith(".md"):
                file_path = os.path.join(root, filename)
                # 获取文件夹名（包含地块编号）
                folder_name = os.path.basename(root)
                excel_filename = f"{folder_name}.xlsx"
                excel_path = os.path.join(excel_output_dir, excel_filename)

                # 如果Excel文件已存在，跳过处理
                # if os.path.exists(excel_path):
                #     print(f"跳过已处理文件: {folder_name}/{filename}")
                #     continue

                try:
                    print(f"正在处理: {folder_name}/{filename}")

                    # 读取Markdown内容
                    with open(file_path, "r", encoding="utf-8") as f:
                        markdown_content = f.read()

                    # 获取对应的图片文件夹路径
                    # image_dir = os.path.join(root, "images")

                    # 构造提示词（这里需要替换为实际的完整提示词）
                    user_content =f"""
你是一名“单文档专属”信息提取助手，**每次仅处理“当前单次输入”的{markdown_content}文档，绝对不保留、不参考任何历史输入的文档内容（包括上一次处理的报告）**，严格遵循以下"唯一性优先"规范，输出仅对应当前文档的JSON数据，任何偏离均视为无效。

### 核心指令（新增"输入隔离"条款）
1. 输出必须是有效的List[Dict]格式JSON字符串，单行无换行、无额外空格/不可见字符；
2. 严格禁止在JSON前后包含```json\n、json、`\n````、\n等任何无关字符；
3. **输入隔离规则**：处理当前文档时，视为"首次启动"，完全清空历史文档的所有记忆（包括页码、描述、图片路径、判断结果），仅以当前{markdown_content}为唯一信息源；
5. 报告名称提取规则：从{folder_name}中切割"_地块信息提取"及后续字符
6. 缺失值用空字符串""填充，禁止用任何非当前文档信息补全。
    ### 输出格式约束（严格执行）
    - 最外层必须是方括号：[]
    - 内部必须是字典对象：{{}}
    - 所有字符串必须使用双引号，内部引号必须转义：\\"
    - 字段名必须用双引号包裹
    - 绝对不允许任何换行符\n
    - 绝对不允许任何Markdown代码块标记
    
    ### 处理规则（强制绑定"文档独有信息"）
    所有规则的执行，必须以"当前文档内独有的、不可重复的信息"为依据，具体要求如下：
    1. 开发活动性质判定（区分违规 / 非违规）：
    违规情形：① 涉及土方开挖、基坑开挖、地基建设的建设行为；②正在建设主体工程，或主体工程已封顶，或已建成主体工程正在装修，且建设用途为 "一住两公" 用地（如建设住宅楼、学校、医院等）；③ 地块内已建成且建设用途为 "一住两公" 用地（如建设住宅楼、学校、医院等）；④ 地块内规划建设内容已建成并投入使用，且建设用途为 "一住两公" 用地（如建设住宅楼、学校、医院等）
    非违规情形：① 地块内部三通一平、临时道路建设、场地平整；② 地面硬化、旧厂房拆除、作为停车场使用；③ 搭建临时工棚、施工板房、项目部、临时办公用房、施工临时用地等；④ 对原有建筑进行装修使用；⑤考古挖掘；⑥即将开发建设、暂未开工或者规划建设，但是没有实际建设，场地平整；⑦ 现状为空地、荒地、闲置等未利用情况（无论之前是否有建设内容，已拆除则不违规）
    2. 动工时间判断标准：仅 "正式开始开发建设行为"（如地基开挖、基坑施工、地基建设、正在建设主体工程、规划建设内容已建成或投入使用等）视为动工；搭建临时工棚、施工板房、考古挖掘、堆放渣土、运输建筑材料、场地清理等准备工作，不认定为动工。
    3. 历史建成情况判定： 以 2019 年 1 月 1 日（《土壤污染防治法》实施日期）为界：2019 年 1 月 1 日前已建成的用地，无论建设用途是否为 "一住两公"，均不属违规。2019 年 1 月 1 日前动工，但是在2019 年 1 月 1 日后建设的用地，且满足违规情形的，属违规。2019 年 1 月 1 日后动工，无论是否建成，只要满足违规情形的，均属违规。
    4. "一住两公" 用地性质判定（严格匹配 2023 年分类指南）
    重点识别用地是否属于 07 类（居住用地）或 08 类（公共管理与公共服务用地），需明确标注对应子分类代码及名称，具体分类如下：
    07 居住用地
    0701 城镇住宅用地（含 070101 一类、070102 二类、070103 三类城镇住宅用地）
    0702 城镇社区服务设施用地
    0703 农村宅基地（含 070301 一类、070302 二类农村宅基地）
    0704 农村社区服务设施用地
    08 公共管理与公共服务用地
    0801 机关团体用地；0802 科研用地
    0803 文化用地（含 080301 图书与展览用地、080302 文化活动用地）
    0804 教育用地（含 080401 高等教育用地、080402 中等职业教育用地、080403 中小学用地、080404 幼儿园用地、080405 其他教育用地）
    0805 体育用地（含 080501 体育场馆用地、080502 体育训练用地）
    0806 医疗卫生用地（含 080601 医院用地、080602 基层医疗卫生设施用地、080603 公共卫生用地）
    0807 社会福利用地（含 080701 老年人社会福利用地、080702 儿童社会福利用地、080703 残疾人社会福利用地、080704 其他社会福利用地）
    5.重点行业清单
    （1） 门类 B：采矿业
       - 大类 07：石油和天然气开采业
         - 中类 071：石油开采
           - 小类 0710：石油开采
       - 大类 08：黑色金属矿采选业
         - 中类 081：铁矿采选
           - 小类 0810：铁矿采选
         - 中类 082：锰矿、铬矿采选
           - 小类 0820：锰矿、铬矿采选
         - 中类 089：其他黑色金属矿采选
           - 小类 0890：其他黑色金属矿采选（钒矿）
       - 大类 09：有色金属矿采选业
         - 中类 091：常用有色金属矿采选
           - 小类 0911：铜矿采选
           - 小类 0912：铅锌矿采选
           - 小类 0913：镍钴矿采选
           - 小类 0914：锡矿采选
           - 小类 0915：锑矿采选
           - 小类 0916：铝矿采选
           - 小类 0919：其他常用有色金属采选（汞矿）
         - 中类 092：贵金属矿采选
           - 小类 0921：金矿采选
           - 小类 0922：银矿采选
         - 中类 093：稀有稀土金属矿采选
           - 小类 0931：钨钼矿采选
           - 小类 0932：稀土金属矿采选
    （2） 门类 C：制造业
       - 大类 17：纺织业
         - 中类 171：棉纺织及印染精加工
           - 小类 1713：棉印染精加工
         - 中类 172：毛纺织及染整精加工
           - 小类 1723：毛染整精加工
         - 中类 173：麻纺织及染整精加工
           - 小类 1733：麻染整精加工
         - 中类 174：丝绢纺织及印染精加工
           - 小类 1743：丝印染精加工
         - 中类 175：化纤织造及印染精加工
           - 小类 1752：化纤织物染整精加工
         - 中类 176：针织或钩针编织物及其制品制造
           - 小类 1762：针织或钩针编织物印染精加工
       - 大类 19：皮革、毛皮、羽毛及其制品和制鞋业
         - 中类 191：皮革鞣制加工
           - 小类 1910：皮革鞣制加工
         - 中类 193：毛皮鞣制及制品加工
           - 小类 1931：毛皮鞣制加工
       - 大类 22：造纸和纸制品业
         - 中类 221：纸浆制造
           - 小类 2211：木竹浆制造
           - 小类 2212：非木竹浆制造
       - 大类 25：石油加工、炼焦和核燃料加工业
         - 中类 251：精炼石油产品制造
           - 小类 2511：原料加工及石油制品制造
           - 小类 2512：人造原油制造
         - 中类 252：炼焦
           - 小类 2520：炼焦
       - 大类 26：化学原料和化学制品制造业
         - 中类 261：基础化学原料制造
           - 小类 2611：无机酸制造
           - 小类 2613：无机盐制造
           - 小类 2614：有机化学原料制造
           - 小类 2619：其他基础化学原料制造
         - 中类 263：农药制造
           - 小类 2631：化学农药制造
         - 中类 264：涂料、油墨、颜料及类似产品制造
           - 小类 2643：颜料制造
           - 小类 2644：染料制造
         - 中类 265：合成材料制造
           - 小类 2651：初级形态塑料及合成树脂制造
           - 小类 2652：合成橡胶制造
           - 小类 2653：合成纤维单 (聚合) 体制造
           - 小类 2659：其他合成材料制造
         - 中类 266：专用化学品制造
           - 小类 2661：化学试剂和助剂制造
           - 小类 2662：专项化学用品制造
           - 小类 2664：信息化学品制造
           - 小类 2669：其他专用化学产品制造
         - 中类 267：炸药、火工及焰火产品制造
           - 小类 2671：炸药及火工产品制造
       - 大类 27：医药制造业
         - 中类 271：化学药品原料药制造
           - 小类 2710：化学药品原料药制造
       - 大类 28：化学纤维制造业
         - 中类 281：纤维素纤维原料及纤维制造
           - 小类 2811：化纤浆粕制造
           - 小类 2812：人造纤维（纤维素纤维）制造
         - 中类 282：合成纤维制造
           - 小类 2822：涤纶纤维制造
           - 小类 2823：腈纶纤维制造
           - 小类 2826：氨纶纤维制造
           - 小类 2829：其他合成纤维制造
       - 大类 31：黑色金属冶炼和压延加工业
         - 中类 311：炼铁
           - 小类 3110：炼铁
         - 中类 312：炼钢
           - 小类 3120：炼钢
         - 中类 315：铁合金冶炼
           - 小类 3150：铁合金冶炼
       - 大类 32：有色金属冶炼和压延加工业
         - 中类 321：常用有色金属冶炼
           - 小类 3211：铜冶炼
           - 小类 3212：铅锌冶炼
           - 小类 3213：镍钴冶炼
           - 小类 3214：锡冶炼
           - 小类 3215：锑冶炼
           - 小类 3216：铝冶炼
           - 小类 3217：镁冶炼
           - 小类 3219：其他常用有色金属冶炼（汞冶炼）
         - 中类 322：贵金属冶炼
           - 小类 3221：金冶炼
           - 小类 3222：银冶炼
         - 中类 323：稀有稀土金属冶炼
           - 小类 3231：钨钼冶炼
           - 小类 3232：稀土金属冶炼
       - 大类 33：金属制品业
         - 中类 336：金属表面处理及热处理加工
           - 小类 3360：金属表面处理及热处理加工
       - 大类 38：电气机械和器材制造业
         - 中类 384：电池制造
           - 小类 3841：锂离子电池制造
           - 小类 3842：镍氢电池制造
           - 小类 3849：其他电池制造
    （3）门类 G：交通运输、仓储和邮政业
       - 大类 59：仓储业
         - 中类 599：其他仓储业
           - 小类 5990：其他仓储业（原油、成品油、危化品、金属矿仓储）
    （4）门类 N：水利、环境和公共设施管理业
       - 大类 77：生态保护和环境治理业
         - 中类 772：环境治理业
           - 小类 7724：危险废物治理（危废、医废处置）
       - 大类 78：公共设施管理业
         - 中类 782：环境卫生管理
           - 小类 7820：环境卫生管理（生活垃圾处置）
    6. 用地性质与规划一致性判定
    必须提取并明确 "规划建设内容""实际建设内容" 两类信息，按最新规划判断两者是否一致。
    判定逻辑：正在建设主体工程、主体工程已封顶、已建成主体工程正在装修、已建成的：① 建设内容符合上一轮规划但不符合最新规划→不违规；② 仅当 "规划为一住两公用地" 且 "实际建设为一住两公用地" 且 "两者完全一致"→才具备违规判定基础（需结合时间维度进一步判断）。
    7. 图片处理：
       - 图片范围：仅提取施工场景、工程进度、空地影像等与开发建设直接相关的有效图片；
       - 字段规则：图片描述与图片成对出现（描述在前、图片在后），共 4 组字段（即使无图片，也需保留字段并填 ""）；
       - 图片的描述是图片的图例名称
       --图片路径规范：图片绝对路径必须严格按照以下格式构建：
         1. 基础路径固定：{image_dir}
         2. 当前文件夹完整名称：{folder_name}（必须完整使用这个文件夹名称，不要截断或修改）
         3. 图片文件夹：/images/
         4. 图片文件名：从文档中的![](images/文件名.扩展名)或类似格式中提取真实的哈希文件名（如6aa0b81b044c5a7f8f091ba232825e609f050f42a48c035ddf1745e13dada582.jpg）
         5. 完整路径示例：D:/PostgraduateStudy/env/text_extractor-main/data/images/{folder_name}/874b37daf1477e4f984c6244fc0985be778e56a038d0bf27e335b3f064984bf8.jpg
     
    ### 证明材料要求
    1. 开发证明材料文字,需尽可能全的从地块的历史、现状、规划章节中描述清楚地块的开发建设历史、现状和规划建设内容,（需包含但不限于）：
    - 具体施工活动描述：土方开挖、基坑开挖、地基建设、地基开挖、基坑施工、桩基工程、结构施工等，
    - 施工时间证据：施工许可证日期、开工通知书、施工日志记录
    - 施工范围证据：施工平面图、工程范围描述、场地勘察报告
    - 施工单位信息：施工企业名称、项目经理、联系方式
    - 监理单位证据：监理报告、验收记录、质量检测报告
    - 政府部门批文：建设工程规划许可证、施工许可证、用地批准文件
    - 影像证据说明：遥感影像时间、拍摄角度、清晰度描述
    - 现场勘查记录：勘查时间、参与人员、发现的具体情况等地块现状描述
    - 地块规划建设：地块规划建设内容
    2. 完工时间证明材料文字（需包含但不限于），如果有开发建设活动，一定要有完工时间或可以判断出完工时间在2019年前后的描述：
    - 竣工备案证明：竣工验收备案表编号及日期
    - 工程验收报告：各方验收签字日期、验收结论
    - 投入使用证明：首次使用时间、入住/使用单位证明
    - 政府部门证明：规划验收合格证、消防验收合格证
    - 财务支付证据：工程尾款支付时间、结算报告日期
    - 影像证据说明：显示完工状态的遥感影像时间及特征
    - 相关单位证明：建设单位、监理单位出具的完工证明
    - 现场勘查证据：现场勘查时间及完工状态确认
    
    ### 最终判断结果规则（唯一 4 类结果，需完整描述判定依据），严格根据评审前违规建设情况、开发证明材料文字、完工时间证明材料文字、开发活动性质判定进行判断，且一定注意是对调查地块的判断，不是周边地块，如地块周边、相邻地块等周围地块的开发建设情况，跟本地块无关：
    结果 1：无违规（对应 "评审前违规建设情况" 为 "否"）
    判定条件：不存在调查评审前的违规开发建设情况（如未动工、动工但属非违规情形、现状为空地、荒地、闲置、平整等）；
    结果描述：需包含 "评审前违规建设情况：'否'；判断结果：没有违规；原因分析：[结合具体情形说明，如未发现任何开发建设行为 / 建设行为属非违规情形（如临时道路建设）/ 现状为空地等]"。
    
    结果 2：无违规（对应 "评审前违规建设情况" 为 "否"， 2019 年前完工）
    判定条件：评审前不论什么开发建设情况，只要在 2019 年 1 月 1 日前已完工，都不算违规；
    结果描述：需包含 "评审前违规建设情况：'否' - 在土壤法实施（2019 年 1 月 1 日）之前完工；完工时间证明：[引用完工证明材料文字中的关键信息，如 P15 页 3.2 节：2018 年 12 月 30 日竣工验收备案]；判断结果：没有违规；原因分析：建设行为在《土壤污染防治法》实施前已完工，根据法律适用原则不认定为违规。
    
    结果 3：违规 / 无违规（对应 "评审前违规建设情况" 为 "是/否"，2019 年前动工、2019 年后完工）
    判定条件：2019 年 1 月 1 日前动工，2019 年 1 月 1 日后完工，且满足违规情形；结果描述：需包含 "评审前违规建设情况：'是 / 否 ' - 在土壤法实施之前动工，土壤法实施之后完工；动工时间证明：[引用开发证明材料中的动工信息，如 P8 页 2.1 节：2018 年 10 月 15 日开工通知书]；完工时间证明：[引用完工证明材料中的关键信息，如 P20 页 4.3 节：2020 年 5 月 20 日竣工备案]；判定结果：违规/没有违规；原因分析：详细描述违规或没有违规的原因，违规需符合违规情形"。
    
    结果 4：违规（对应 "评审前违规建设情况" 为 "是"，2019 年后动工）
    判定条件：2019 年 1 月 1 日后正式动工，且满足“违规情形”,确实已实际建设（非仅规划未动工）"；
    结果描述：需包含 "评审前违规建设情况：' 是 ' - 在土壤法实施（2019 年 1 月 1 日）之后动工；动工时间证明：[引用开发证明材料中的关键信息，如 P10 页 2.3 节：2021 年 3 月 10 日施工许可证日期]；建设内容判断：实际建设为 [一住两公分类代码及名称，如 070102 二类城镇住宅用地]，最新规划为 [对应分类代码及名称]，两者一致，且已实际动工建设（非规划阶段）；判断结果：违规建设；原因分析：2019年后动工建设，且符合违规情形。
    
    ### 输出格式（仅格式参考，内容必须来自当前文档）
    [{{
    "地块编号":"从{folder_name}中提取_前面的数字，如报告名是：3211623990025_镇江西出入口鲇鱼套北地块，则提取编号为：3211623990025，
    "报告名称":"从{folder_name}提取并清理后的名称，需要清理{folder_name}前后的相关信息，如原始报告名称是：3211623990025_镇江西出入口鲇鱼套北地块_地块信息提取.pdf-72337a33-c5e7-4fa9-8414-d9673b53dcea，清理后的报告名称是：镇江西出入口鲇鱼套北地块",
    "评审前违规建设情况":"是 / 否",  // 严格基于当前文档内容判断
    "开发证明材料文字":"从当前文档中提取的具体证据，包括页码和详细描述，描述要尽量详细，把现状情况说清楚，一定要有现状的描述，且要符合实际，不要编造",
    "开发证明材料图片_1图片说明":"从当前文档图片中提取的描述",
    "开发证明材料图片_1_图片":"图片绝对路径",
    "开发证明材料图片_2图片说明":"",
    "开发证明材料图片_2_图片":"图片绝对路径",
    "开发证明材料图片_3图片说明":"",
    "开发证明材料图片_3_图片":"",
    "开发证明材料图片_4图片说明":"",
    "开发证明材料图片_4_图片":"",
    "是否 2019 年前完工":"是 / 否",  // 基于当前文档判断
    "完工时间证明材料文字":"从当前文档中提取的具体证据",
    "最终判断结果":"必须基于当前文档内容进行完整分析，前文档页码+独有依据的完整分析，禁止任何通用表述，包含违规/无违规结论和具体原因，原因尽可能详细说明，不能有任何编纂，完全尊重事实，要有现状是什么",
    "行业类别":"将调查地块历史开发建设情况涉及到的行业类型和《国民经济行业分类》（GB_T 4754—2017）里面的行业进行匹配，可以是行业大类、中类、小类任意一种，如果地块历史上涉及到多种生产行业，则需要将每种行业类别都写上，示例：0710石油开采”,
    "地块用地历史是否涉及重点行业":"根据’重点行业清单’,分析’行业类别’是否在重点行业内，如果在则为是，如果不在则为否，示例：是/否,3221：金冶炼"
    "现状用地类型":"根据《国土空间调查、规划、用途管制用地用海分类指南》（2023年）表2.1或表F中的内容，分析地块现状或者规划用地类型，一个地块可能原用途或规划用途会涉及到多种用地类型，例如一个地块可能部分规划为住宅，部分规划为道路，需要把多种用途都填写在表中，示例：080501	体育场馆用地，或者0805	体育用地。"
    }}]
    
    ### 关键约束（必须严格执行）
    
    1. 绝对确保 List [Dict] 结构（最外层是方括号，内部是字典）
    2. 图片字段必须连续成对出现（描述在前，绝对地址在后），共 4 组，无图片时填空字符串 ""
    3. 空值必须保留字段名并赋空字符串 ""，禁止删除字段
    4. 字符串内部包含引号时必须转义（如："评审前开发建设情况：" 是 ""）
    5. 输出必须是单行无换行的有效 JSON 字符串，禁止包含 Markdown 代码块标记（```json 等）、额外空格、换行符或不可见字符
    6.所有字段内容必须严格基于当前输入的markdown文档内容生成
    7. 禁止复制示例中的任何具体内容（如页码、时间、描述等）
    8. 禁止使用之前处理过的任何文档内容
    9. 每个文档必须独立分析，不得相互影响
    10.报告名称必须从当前文档标题中提取并清理
    11.报告编号必须从当前文档标题中提取并清理
    12.所有证据材料必须标注当前文档中的具体页码和章节
    
    ### 严格禁止：
    - 复制任何示例内容
    - 使用之前文档的内容
    - 生成文档中不存在的信息
    
    ### 输出验证（大模型返回结果后需二次校验）
    1. 开头为 [，结尾为]
    2. 无 ```json、\n 等无关字符
    3. 所有引号正确转义
    4. 字段完整无缺失
    5. 图片本地地址格式正确
    6.检查"报告名称"中的唯一ID是否与{folder_name}一致（确保每本报告名称唯一）；
    7.检查"开发证明材料文字""最终判断结果"是否包含唯一ID（无ID则重新处理）；
    8.对比当前输出与历史输出的"页码+施工许可证号/备案号"（若完全一致，判定为重复，重新处理）；
    9.检查"图片路径"中的哈希文件名是否来自当前文档的图片引用（禁止复用其他报告的图片名）。
    """
                    # 首先在调用大模型之前，构建一个全新的系统提示
                    system_content = """你是一名严格的信息提取助手。必须遵守以下核心规则：
                    1. 只基于当前输入的markdown文档内容进行分析
                    2. 禁止复制任何示例数据或之前处理过的内容
                    3. 所有输出必须完全来自当前文档
                    4. 如果文档中没有相关信息，对应字段留空
                    5. 图片路径必须基于当前文档的实际图片文件名生成
                    6.你是一个独立的信息提取工具，每次处理都是全新的会话，不记忆之前的内容"""

                    # 调用大模型API
                    response = client.chat.completions.create(
                        model="deepseek-chat",
                        messages=[
                            {"role": "system", "content": system_content},  # 添加系统提示
                            {"role": "user", "content": user_content}
                        ],
                        temperature=0.1, # 提高温度值，减少复制倾向
                        max_tokens=8000  # 设置最大输出 token 数为 10000
                    )

                    # 提取和解析JSON
                    response_text = response.choices[0].message.content
                    json_data = extract_json_from_response(response_text)

                    # 创建Excel文件，传入对应的图片文件夹路径
                    create_excel_from_json(json_data, excel_path,image_dir)

                    print(f"成功处理: {folder_name}/{filename} -> {excel_filename}")
                    processed_files += 1  # 成功处理后增加计数

                    # 添加延迟避免API限制
                    time.sleep(1)

                except Exception as e:
                    # 记录错误日志
                    error_msg = f"处理文件 {folder_name}/{filename} 时出错: {str(e)}"
                    print(error_msg)

                    # 写入错误日志文件
                    error_log_path = os.path.join(excel_output_dir, "error_log.txt")
                    with open(error_log_path, "a", encoding="utf-8") as error_log:
                        error_log.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {error_msg}\n")
                        # 记录详细的错误堆栈信息
                        import traceback
                        error_log.write(traceback.format_exc() + "\n")
                        error_log.write("-" * 50 + "\n")

                    continue

    # 添加最终总结打印
    print("=" * 50)
    print("所有文件处理完成！")
    print(f"总计: {total_files} 个报告")
    print(f"成功处理: {processed_files} 个报告")
    print(f"跳过/失败: {total_files - processed_files} 个报告")
    print("=" * 50)
    return True

def validate_image_paths(json_data, folder_name):
    """验证图片路径格式是否正确"""
    if isinstance(json_data, list) and len(json_data) > 0:
        item = json_data[0]
        for i in range(1, 5):
            img_key = f"开发证明材料图片_{i}_图片"
            img_path = item.get(img_key, "")
            if img_path:
                # 检查是否包含完整的文件夹名称
                if folder_name not in img_path:
                    print(f"错误：图片路径缺少完整文件夹名称！期望: {folder_name}，实际: {img_path}")
                    return False
                # 检查文件名是否为哈希格式（32位以上十六进制）
                filename = os.path.basename(img_path)
                if len(filename.split('.')[0]) < 32 or not all(
                        c in '0123456789abcdef' for c in filename.split('.')[0].lower()):
                    print(f"错误：图片文件名不是哈希格式！文件名: {filename}")
                    return False
    return True

# if __name__ == "__main__":
#     markdown_dir = r"D:\PostgraduateStudy\env\text_extractor-main\data\output\pdf2md\123456789101"
#     excel_output_dir = r"D:\PostgraduateStudy\env\text_extractor-main\data\output\results\123456789101"
#     image_dir = r"D:/PostgraduateStudy/env/text_extractor-main/data/images"
#     ai_judge(markdown_dir,excel_output_dir,image_dir)


