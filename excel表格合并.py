import pandas as pd
import os
import glob


def merge_excel_files_fixed_order(input_folder, output_file):
    """
    按照指定字段顺序合并Excel文件，直接使用文件中的字段
    """

    # 定义要求的字段顺序（用于最终输出）
    output_columns = [
        "报告名称", "评审前违规建设情况", "开发证明材料文字",
        "开发证明材料图片_1", "开发证明材料图片_1_url",
        "开发证明材料图片_2", "开发证明材料图片_2_url",
        "开发证明材料图片_3", "开发证明材料图片_3_url",
        "开发证明材料图片_4", "开发证明材料图片_4_url",
        "是否2019年前完工", "完工时间证明材料文字", "最终判断结果",
        "行业类别", "地块用地历史是否涉及重点行业", "现状用地类型"
    ]

    # 获取所有Excel文件
    excel_files = glob.glob(os.path.join(input_folder, "*.xlsx")) + glob.glob(os.path.join(input_folder, "*.xls"))

    if not excel_files:
        print(f"在目录 {input_folder} 中未找到Excel文件")
        return

    print(f"找到 {len(excel_files)} 个Excel文件")

    all_dfs = []

    for i, file_path in enumerate(excel_files, 1):
        file_name = os.path.basename(file_path)
        print(f"[{i:3d}/{len(excel_files)}] 处理: {file_name}")

        try:
            # 读取Excel文件（跳过空文件检查）
            df = pd.read_excel(file_path)

            # 直接使用文件中的列，按照输出要求的顺序重新排列
            # 只保留在output_columns中定义的列，忽略其他列
            existing_columns = [col for col in output_columns if col in df.columns]
            df = df[existing_columns]

            # 添加到列表
            all_dfs.append(df)

        except Exception as e:
            print(f"  错误: {e}")
            continue

    if not all_dfs:
        print("没有成功读取任何文件")
        return

    # 合并所有数据
    merged_df = pd.concat(all_dfs, ignore_index=True)

    # 确保最终输出包含所有要求的列（按顺序）
    for col in output_columns:
        if col not in merged_df.columns:
            merged_df[col] = None  # 添加缺失的列为空值

    # 按照指定顺序排列列
    merged_df = merged_df[output_columns]

    # 保存合并后的文件
    try:
        merged_df.to_excel(output_file, index=False)
        print(f"\n✅ 合并完成！")
        print(f"📊 成功合并: {len(all_dfs)} 个文件")
        print(f"📈 总行数: {len(merged_df)}")
        print(f"💾 输出文件: {output_file}")

    except Exception as e:
        print(f"保存文件时出错: {e}")


# 使用示例
if __name__ == "__main__":
    # 输入目录
    input_folder = r"D:\信息中心\大模型\部里工作\土壤司\建设用地\地块开发建设情况信息提取\新增加报告\报告重新下载\excel"

    # 输出文件路径
    output_file = r"D:\信息中心\大模型\部里工作\土壤司\建设用地\地块开发建设情况信息提取\新增加报告\报告重新下载\新报告审核结果-excel合并.xlsx"

    # 检查目录是否存在
    if not os.path.exists(input_folder):
        print(f"输入目录不存在: {input_folder}")
    else:
        merge_excel_files_fixed_order(input_folder, output_file)