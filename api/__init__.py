from .task_api import task_bp
from .upload_api import upload_bp
from .download_api import download_bp
from .decompress_api import decompress_bp
from .parse_api import parse_bp
from .chunk_api import chunk_bp
from .extract_api import extract_bp
from .judge_api import judge_bp
from .result_api import result_bp
from .static_file_api import file_bp
from .convert_api import convert_bp
from .processStatus_api import processStatus_bp  # 12.24修改[总日志追踪]
from .airesult_api import airesult_bp # 0305新增
from .statistic_api import statistic_bp
from .auth_api import auth_bp
from flask import Blueprint, jsonify
import traceback
from werkzeug.exceptions import HTTPException
def register_blueprints(app):
    app.register_blueprint(task_bp, url_prefix="/api/task")
    app.register_blueprint(upload_bp, url_prefix="/api/upload")
    app.register_blueprint(download_bp, url_prefix="/api/download")
    app.register_blueprint(decompress_bp, url_prefix="/api/decompress")
    app.register_blueprint(parse_bp, url_prefix="/api/parse")
    app.register_blueprint(chunk_bp, url_prefix="/api/chunk")
    app.register_blueprint(extract_bp, url_prefix="/api/extract")
    app.register_blueprint(judge_bp, url_prefix="/api/judge")
    app.register_blueprint(result_bp, url_prefix="/api/result")
    app.register_blueprint(file_bp, url_prefix="/api/file")
    app.register_blueprint(convert_bp, url_prefix="/api/convert")
    app.register_blueprint(processStatus_bp, url_prefix="/api/processStatus")   #12.24新增[总日志追踪]
    app.register_blueprint(auth_bp,url_prefix="/api/auth")
    app.register_blueprint(airesult_bp, url_prefix="/api/airesult")  # 0305新增
    app.register_blueprint(statistic_bp, url_prefix="/api/statistic")# 0331新增
    register_blueprint_error_handlers(app)


def register_blueprint_error_handlers(app):
    """为应用注册错误处理器"""

    # 您提供的代码
    @app.errorhandler(Exception)
    def handle_exception(e):
        # 记录错误
        app.logger.error(f"全局异常: {type(e).__name__}: {e}")

        return jsonify({
           "error": "系统错误"
       }), 500
        # 开发环境显示详细错误
        # if app.debug:
        #     return jsonify({
        #         "error": {
        #             "type": type(e).__name__,
        #             "message": str(e),
        #             "traceback": traceback.format_exc().split('\n')
        #         }
        #     }), 500
        # else:
        #     return jsonify({
        #         "error": "系统错误"
        #     }), 500

    @app.errorhandler(400)
    def bad_request(e):
        return jsonify({
            "success": False,
            "error": {
                "code": 400,
                "message": "请求参数错误"
            }
        }), 400

    @app.errorhandler(401)
    def unauthorized(e):
        return jsonify({
            "success": False,
            "error": {
                "code": 401,
                "message": "未授权访问"
            }
        }), 401

    @app.errorhandler(403)
    def forbidden(e):
        return jsonify({
            "success": False,
            "error": {
                "code": 403,
                "message": "禁止访问"
            }
        }), 403

    @app.errorhandler(404)
    def not_found(e):
        return jsonify({
            "success": False,
            "error": {
                "code": 404,
                "message": "资源不存在"
            }
        }), 404

    @app.errorhandler(405)
    def method_not_allowed(e):
        return jsonify({
            "success": False,
            "error": {
                "code": 405,
                "message": "请求方法不允许"
            }
        }), 405

    @app.errorhandler(429)
    def too_many_requests(e):
        return jsonify({
            "success": False,
            "error": {
                "code": 429,
                "message": "请求过于频繁"
            }
        }), 429

    @app.errorhandler(500)
    def system_error(e):
        return jsonify({
            "success": False,
            "error": {
                "code": 500,
                "message": "系统错误"
            }
        }), 500