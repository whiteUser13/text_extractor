import os
import uuid
import tempfile
import matplotlib.pyplot as plt
from docx import Document
from docx.shared import Inches
import matplotlib.font_manager as fm


# ========= 初始化字体（关键修复） =========
def init_font():
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(current_dir)

    font_path = os.path.join(project_root, "fonts", "SimHei.ttf")

    if not os.path.exists(font_path):
        raise FileNotFoundError(f"字体文件不存在: {font_path}")

    # 注册字体（解决 findfont 问题）
    fm.fontManager.addfont(font_path)

    font = fm.FontProperties(fname=font_path)

    # 仅保留负号设置
    plt.rcParams['axes.unicode_minus'] = False

    return font


def export_stats_to_word(data_list, save_path):
    """
    将统计数据导出为Word文档（柱状图+饼图，支持中文，Docker稳定）
    """

    font = init_font()

    # ========= 覆盖文件 =========
    if os.path.exists(save_path):
        os.remove(save_path)

    doc = Document()

    for idx, item in enumerate(data_list, start=1):
        stats_name = item.get("stats_name", "")
        stats_prompt = item.get("stats_prompt", "")
        stats_result = item.get("stats_result", [])

        # ========= 标题 =========
        p = doc.add_paragraph()
        run = p.add_run(f"{idx}. {stats_name}")
        run.bold = True

        # ========= 正文 =========
        doc.add_paragraph(stats_prompt)

        # ========= 数据 =========
        labels = [x["name"] for x in stats_result]
        values = [x["value"] for x in stats_result]

        # ========= 临时文件 =========
        tmp_dir = tempfile.gettempdir()
        tmp_id = str(uuid.uuid4())
        bar_path = os.path.join(tmp_dir, f"{tmp_id}_bar.png")
        pie_path = os.path.join(tmp_dir, f"{tmp_id}_pie.png")

        # ========= 柱状图（强制字体） =========
        plt.figure(figsize=(6, 4))
        plt.bar(labels, values)

        plt.xticks(rotation=30, ha="right", fontproperties=font)
        plt.yticks(fontproperties=font)
        plt.title(f"{stats_name} - 柱状图", fontproperties=font)

        plt.tight_layout()
        plt.savefig(bar_path, dpi=300)
        plt.close()

        # ========= 饼图（必须 textprops） =========
        plt.figure(figsize=(6, 4))
        plt.pie(
            values,
            labels=labels,
            autopct="%1.1f%%",
            textprops={'fontproperties': font}
        )

        plt.title(f"{stats_name} - 饼图", fontproperties=font)

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