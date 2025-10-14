import os
import pandas as pd
from openpyxl import load_workbook, Workbook
from openpyxl.utils import get_column_letter
from openpyxl.drawing.image import Image
import glob
from PIL import Image as PILImage
import tempfile
import shutil


def is_temp_excel_file(filename):
    """检查是否为Excel临时文件"""
    return filename.startswith('~$')


def merge_excel_files_with_images_fixed(input_folder, output_file):
    """
    合并Excel文件并复制图片到新文件
    """
    # 获取所有Excel文件，排除临时文件
    excel_files = []
    for file_path in glob.glob(os.path.join(input_folder, "*.xlsx")) + glob.glob(os.path.join(input_folder, "*.xls")):
        filename = os.path.basename(file_path)
        if not is_temp_excel_file(filename):
            excel_files.append(file_path)

    if not excel_files:
        print(f"在目录 {input_folder} 中未找到有效的Excel文件")
        return

    print(f"找到 {len(excel_files)} 个有效的Excel文件")

    # 创建新的工作簿
    wb = Workbook()
    ws = wb.active
    ws.title = "合并数据"

    # 图片列索引（F=6, H=8, J=10, L=12）
    image_columns = [6, 8, 10, 12]

    current_row = 1  # 从第1行开始（表头）

    # 创建主临时目录（在整个函数执行期间存在）
    main_temp_dir = tempfile.mkdtemp()
    print(f"创建临时目录: {main_temp_dir}")

    try:
        for i, file_path in enumerate(excel_files, 1):
            file_name = os.path.basename(file_path)
            print(f"[{i:3d}/{len(excel_files)}] 处理: {file_name}")

            try:
                # 读取源文件的工作簿
                source_wb = load_workbook(file_path)
                source_ws = source_wb.active

                # 读取数据
                df = pd.read_excel(file_path)

                # 如果是第一个文件，写入表头
                if current_row == 1:
                    for col_idx, column_name in enumerate(df.columns, 1):
                        ws.cell(row=current_row, column=col_idx, value=column_name)
                    current_row += 1

                # 写入数据行
                for row_idx, row_data in enumerate(df.values, current_row):
                    for col_idx, value in enumerate(row_data, 1):
                        ws.cell(row=row_idx, column=col_idx, value=value)

                # 复制图片 - 修改临时文件路径处理
                for image in source_ws._images:
                    try:
                        # 获取图片位置信息
                        if hasattr(image.anchor, '_from'):
                            src_col = image.anchor._from.col + 1  # 转换为1-based索引
                            src_row = image.anchor._from.row + 1  # 转换为1-based索引

                            # 计算目标位置（考虑已经写入的行数）
                            target_col = src_col
                            target_row = current_row - len(df) - 1 + src_row  # 调整行偏移

                            # 使用主临时目录，避免路径问题
                            temp_img_path = os.path.join(main_temp_dir, f"temp_img_{i}_{src_col}_{src_row}.png")

                            # 确保目录存在
                            os.makedirs(os.path.dirname(temp_img_path), exist_ok=True)

                            with open(temp_img_path, 'wb') as f:
                                f.write(image._data())

                            # 插入图片到目标位置
                            img = Image(temp_img_path)
                            img.anchor = f"{get_column_letter(target_col)}{target_row}"
                            ws.add_image(img)

                            print(f"  已复制图片到位置: {get_column_letter(target_col)}{target_row}")

                    except Exception as img_error:
                        print(f"  复制图片时出错: {img_error}")
                        continue

                source_wb.close()
                current_row += len(df)  # 更新当前行位置

            except Exception as e:
                print(f"  处理文件 {file_name} 时出错: {e}")
                continue

        # 设置图片列的宽度和行高以适应图片
        for col_idx in image_columns:
            col_letter = get_column_letter(col_idx)
            ws.column_dimensions[col_letter].width = 30

        # 设置所有行的默认高度
        for row in range(1, current_row):
            ws.row_dimensions[row].height = 120

        # 保存合并后的文件
        try:
            # 确保输出目录存在
            output_dir = os.path.dirname(output_file)
            if output_dir and not os.path.exists(output_dir):
                os.makedirs(output_dir)

            wb.save(output_file)
            print(f"\n✅ 合并完成！")
            print(f"📊 总行数: {current_row - 1}")
            print(f"💾 输出文件: {output_file}")
            print("✅ 图片已成功复制到合并文件中")

        except Exception as e:
            print(f"保存文件时出错: {e}")

    finally:
        # 清理临时目录
        try:
            shutil.rmtree(main_temp_dir)
            print(f"已清理临时目录")
        except:
            print("清理临时目录时出错")


# 以下代码保持不变...
def merge_excel_files_simple(input_folder, output_file):
    """
    简单合并Excel文件（仅数据，不处理图片）
    """
    # 获取所有Excel文件，排除临时文件
    excel_files = []
    for file_path in glob.glob(os.path.join(input_folder, "*.xlsx")) + glob.glob(os.path.join(input_folder, "*.xls")):
        filename = os.path.basename(file_path)
        if not is_temp_excel_file(filename):
            excel_files.append(file_path)

    if not excel_files:
        print(f"在目录 {input_folder} 中未找到有效的Excel文件")
        return

    print(f"找到 {len(excel_files)} 个有效的Excel文件")

    all_dfs = []

    for i, file_path in enumerate(excel_files, 1):
        file_name = os.path.basename(file_path)
        print(f"[{i:3d}/{len(excel_files)}] 处理: {file_name}")

        try:
            df = pd.read_excel(file_path)
            all_dfs.append(df)
        except Exception as e:
            print(f"  错误: {e}")
            continue

    if not all_dfs:
        print("没有成功读取任何文件")
        return

    # 合并所有数据
    merged_df = pd.concat(all_dfs, ignore_index=True)

    # 保存合并后的文件
    try:
        # 确保输出目录存在
        output_dir = os.path.dirname(output_file)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir)

        merged_df.to_excel(output_file, index=False)
        print(f"\n✅ 简单合并完成！")
        print(f"📊 成功合并: {len(all_dfs)} 个文件")
        print(f"📈 总行数: {len(merged_df)}")
        print(f"💾 输出文件: {output_file}")
        print("⚠️  注意：不包含图片")

    except Exception as e:
        print(f"保存文件时出错: {e}")


if __name__ == "__main__":
    # 输入目录
    input_folder = r"D:\信息中心\大模型\部里工作\土壤司\建设用地\地块开发建设情况信息提取\新增加报告\报告重新下载\excel"

    # 输出文件路径
    output_file = r"D:\信息中心\大模型\部里工作\土壤司\建设用地\地块开发建设情况信息提取\新增加报告\报告重新下载\新报告审核结果-excel合并.xlsx"

    # 检查目录是否存在
    if not os.path.exists(input_folder):
        print(f"输入目录不存在: {input_folder}")
    else:
        print("请选择合并方式:")
        print("1. 完整合并（包含数据和图片）")
        print("2. 简单合并（仅数据，不包含图片）")

        choice = input("请输入选择 (1 或 2): ").strip()

        if choice == "1":
            merge_excel_files_with_images_fixed(input_folder, output_file)
        elif choice == "2":
            merge_excel_files_simple(input_folder, output_file)
        else:
            print("无效选择，使用完整合并方式")
            merge_excel_files_with_images_fixed(input_folder, output_file)