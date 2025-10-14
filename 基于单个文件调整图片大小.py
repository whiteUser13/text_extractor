import os
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from openpyxl.drawing.image import Image
from PIL import Image as PILImage
import tempfile
import shutil


def adjust_images_to_cell_size(excel_file_path):
    """
    调整Excel文件中图片的大小，使其刚好适应单元格的大小范围
    """
    try:
        # 加载工作簿
        wb = load_workbook(excel_file_path)
        ws = wb.active

        # 图片列索引（F=6, H=8, J=10, L=12）
        image_columns = [6, 8, 10, 12]

        print(f"开始处理文件: {excel_file_path}")

        # 创建临时目录
        temp_dir = tempfile.mkdtemp()
        print(f"创建临时目录: {temp_dir}")

        try:
            # 获取所有图片
            images = ws._images.copy()  # 复制图片列表，避免在迭代时修改

            # 先移除所有图片
            for img in images:
                ws._images.remove(img)

            # 重新插入调整大小后的图片
            for img_idx, original_img in enumerate(images):
                try:
                    # 获取图片位置信息
                    if hasattr(original_img.anchor, '_from'):
                        col_idx = original_img.anchor._from.col + 1  # 转换为1-based索引
                        row_idx = original_img.anchor._from.row + 1  # 转换为1-based索引

                        # 只处理指定的图片列
                        if col_idx not in image_columns:
                            continue

                        col_letter = get_column_letter(col_idx)

                        # 获取单元格尺寸
                        col_width = ws.column_dimensions[col_letter].width
                        row_height = ws.row_dimensions[row_idx].height

                        # 将列宽和行高转换为像素（近似值）
                        # Excel列宽：1单位 ≈ 7像素
                        # Excel行高：1点 ≈ 1.33像素
                        cell_width_px = int(col_width * 7)
                        cell_height_px = int(row_height * 1.33) if row_height else 100  # 默认高度

                        print(f"处理图片位置: {col_letter}{row_idx}, 单元格尺寸: {cell_width_px}x{cell_height_px}像素")

                        # 保存原始图片到临时文件
                        temp_img_path = os.path.join(temp_dir, f"temp_img_{col_idx}_{row_idx}.png")
                        with open(temp_img_path, 'wb') as f:
                            f.write(original_img._data())

                        # 使用PIL处理图片大小
                        pil_img = PILImage.open(temp_img_path)
                        original_width, original_height = pil_img.size

                        # 计算缩放比例
                        width_ratio = cell_width_px / original_width
                        height_ratio = cell_height_px / original_height
                        scale_ratio = min(width_ratio, height_ratio, 1.0)  # 不超过原始大小

                        # 只有当图片大于单元格时才调整大小，保持图片清晰度
                        if scale_ratio < 1.0:
                            # 图片大于单元格，需要缩小图片
                            new_width = int(original_width * scale_ratio)
                            new_height = int(original_height * scale_ratio)

                            # 使用高质量重采样算法调整图片大小
                            resized_img = pil_img.resize((new_width, new_height), PILImage.Resampling.LANCZOS)

                            # 保存调整后的图片
                            resized_img_path = os.path.join(temp_dir, f"resized_img_{col_idx}_{row_idx}.png")
                            resized_img.save(resized_img_path)
                            pil_img.close()

                            # 使用调整后的图片
                            img_to_insert = resized_img_path
                            print(
                                f"  图片已缩小以适应单元格: {original_width}x{original_height} → {new_width}x{new_height}")
                        else:
                            # 图片小于或等于单元格大小，直接使用原始图片以保持清晰度
                            img_to_insert = temp_img_path
                            if scale_ratio == 1.0:
                                print(f"  图片大小与单元格匹配: {original_width}x{original_height}")
                            else:
                                print(f"  图片小于单元格，保持原始大小以确保清晰度: {original_width}x{original_height}")

                        # 插入调整后的图片
                        new_img = Image(img_to_insert)
                        new_img.anchor = f"{col_letter}{row_idx}"
                        ws.add_image(new_img)


                except Exception as img_error:
                    print(f"  处理图片时出错: {img_error}")
                    continue

            # 保存修改后的文件
            output_file = excel_file_path.replace('.xlsx', '_调整后.xlsx')
            wb.save(output_file)
            print(f"\n✅ 处理完成！")
            print(f"💾 输出文件: {output_file}")
            print(
                f"📊 共处理了 {len([img for img in images if hasattr(img.anchor, '_from') and (img.anchor._from.col + 1) in image_columns])} 张图片")

        finally:
            # 清理临时目录
            shutil.rmtree(temp_dir)
            print(f"已清理临时目录")

    except Exception as e:
        print(f"处理文件时出错: {e}")


def adjust_images_simple(excel_file_path):
    """
    简单调整图片大小（备用方案）
    """
    try:
        wb = load_workbook(excel_file_path)
        ws = wb.active

        # 图片列索引
        image_columns = [6, 8, 10, 12]

        print("使用简单调整模式...")

        # 设置统一的单元格大小以适应图片
        for col_idx in image_columns:
            col_letter = get_column_letter(col_idx)
            ws.column_dimensions[col_letter].width = 30  # 设置合适的列宽

        # 设置行高
        for row in range(1, ws.max_row + 1):
            ws.row_dimensions[row].height = 120  # 设置合适的行高

        output_file = excel_file_path.replace('.xlsx', '_简单调整.xlsx')
        wb.save(output_file)
        print(f"✅ 简单调整完成！")
        print(f"💾 输出文件: {output_file}")

    except Exception as e:
        print(f"简单调整时出错: {e}")


if __name__ == "__main__":
    # Excel文件路径
    excel_file_path = r"D:\信息中心\大模型\部里工作\土壤司\建设用地\地块开发建设情况信息提取\新增加报告\报告重新下载\新报告审核结果-excel合并.xlsx"

    # 检查文件是否存在
    if not os.path.exists(excel_file_path):
        print(f"文件不存在: {excel_file_path}")
    else:
        print("请选择处理方式:")
        print("1. 精确调整（根据单元格大小调整图片）")
        print("2. 简单调整（统一设置单元格大小）")

        choice = input("请输入选择 (1 或 2): ").strip()

        if choice == "1":
            adjust_images_to_cell_size(excel_file_path)
        elif choice == "2":
            adjust_images_sim·ple(excel_file_path)
        else:
            print("无效选择，使用精确调整方式")
            adjust_images_to_cell_size(excel_file_path)