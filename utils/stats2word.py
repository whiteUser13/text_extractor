import os
import uuid
import matplotlib.pyplot as plt
from docx import Document
from docx.shared import Inches
from matplotlib import rcParams


# ========= 解决中文显示问题 =========
rcParams['font.sans-serif'] = ['SimHei']  # 黑体（Windows常用）
rcParams['axes.unicode_minus'] = False    # 解决负号显示问题


def export_stats_to_word(data_list, save_path):
    """
    将统计数据导出为Word文档（柱状图+饼图，支持中文）
    """

    # ========= 如果文件存在，直接覆盖 =========
    if os.path.exists(save_path):
        os.remove(save_path)

    doc = Document()

    for idx, item in enumerate(data_list, start=1):
        stats_name = item.get("stats_name", "")
        stats_prompt = item.get("stats_prompt", "")
        stats_result = item.get("stats_result", [])

        # ========= 标题（加粗 + 编号） =========
        p = doc.add_paragraph()
        run = p.add_run(f"{idx}. {stats_name}")
        run.bold = True

        # ========= 正文 =========
        doc.add_paragraph(stats_prompt)

        # ========= 数据解析 =========
        labels = [x["name"] for x in stats_result]
        values = [x["value"] for x in stats_result]

        # ========= 临时图片 =========
        tmp_id = str(uuid.uuid4())
        bar_path = f"{tmp_id}_bar.png"
        pie_path = f"{tmp_id}_pie.png"

        # ========= 柱状图 =========
        plt.figure(figsize=(6, 4))
        plt.bar(labels, values)
        plt.xticks(rotation=30, ha="right")
        plt.title(f"{stats_name} - 柱状图")
        plt.tight_layout()
        plt.savefig(bar_path, dpi=300)
        plt.close()

        # ========= 饼图 =========
        plt.figure(figsize=(6, 4))
        plt.pie(values, labels=labels, autopct="%1.1f%%")
        plt.title(f"{stats_name} - 饼图")
        plt.tight_layout()
        plt.savefig(pie_path, dpi=300)
        plt.close()

        # ========= 插入 Word =========
        doc.add_paragraph("柱状图：")
        doc.add_picture(bar_path, width=Inches(5.5))

        doc.add_paragraph("饼图：")
        doc.add_picture(pie_path, width=Inches(5.5))

        doc.add_paragraph("")

        # ========= 删除临时文件 =========
        os.remove(bar_path)
        os.remove(pie_path)

    # ========= 保存 =========
    doc.save(save_path)