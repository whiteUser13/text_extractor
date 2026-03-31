# 转换格式：DOCX -> PDF

from flask import Blueprint, jsonify
from utils.task_folder_utils import task_folder_utils
from utils.docx_converter import batch_convert_docx_to_pdf
import traceback
from filelock import FileLock
import tempfile
import os
from utils.auth_util import AuthUtils
from utils.task_verify import task_permission_required

convert_bp = Blueprint("convert", __name__)

@convert_bp.post("/task/<task_id>/convert")
@AuthUtils.token_required
@task_permission_required()
def convert_task(task_id: str):
    """
    convert 阶段接口：
    - 前端在 extract-and-classify-stream (SSE) 结束后调用
    - 同步执行，确保所有 DOCX 转 PDF 完成
    - 成功返回 200，失败返回 500
    """
    try:
        # 获取任务目录
        task_dirs = task_folder_utils.get_task_dirs(task_id)

        # 执行强制转换逻辑
        # 修改：接收返回的失败列表，不再抛出 RuntimeError
        # 1224修改[总日志追踪]：传入任务ID用于状态追踪
        failed_files = batch_convert_docx_to_pdf(task_dirs, task_id=task_id)

        # (可选) 可以在这里生成一个标记文件，表示该任务已准备好进行 MinerU 解析

        # 即使有失败文件，也返回 200，因为流程并未中断
        return jsonify({
            "success": True,
            "code": 0,
            "message": "PDF转换完成！",
            "failed_files": failed_files or []  # 将失败列表返回给前端
        }), 200

    # except RuntimeError as re:
    #     # 捕获我们自己抛出的转换失败异常
    #     return jsonify({
    #         "success": False,
    #         "code": 1,
    #         "message": "部分文件转换失败",
    #         "error": str(re)
    #     }), 500
        
    except Exception as e:
        # 捕获其他未知异常
        print(traceback.format_exc())
        return jsonify({
            "success": False,
            "code": 1,
            "message": "文档转换阶段发生系统错误",
            "error": str(e)
        }), 500
