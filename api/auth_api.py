from flask import Flask, request, jsonify,Blueprint
# from models.user import User
from extensions import db
from utils.auth_util import AuthUtils
from extensions import limiter

auth_bp = Blueprint("auth", __name__)

# @auth_bp.route("/login", methods=["POST"])
# @limiter.limit("5 per minute")  # 每分钟最多5次登录尝试
# def login():
#     """用户登录"""
#     try:
#         data = request.get_json()
#         username = data.get('username')
#         password = data.get('password')  # 前端已经SHA256加密的密码
#
#         if not username or not password:
#             return jsonify({'error': '用户名和密码是必需的'}), 400
#
#         # 查找用户
#         user = User.query.filter_by(username=username).first()
#         if not user:
#             return jsonify({'error': '用户名或密码错误'}), 401
#
#         # 验证密码
#         if not user.verify_password(password):
#             return jsonify({'error': '用户名或密码错误'}), 401
#
#         # 检查用户是否激活
#         if not user.is_active:
#             return jsonify({'error': '账户已被禁用'}), 403
#
#         # 创建访问令牌
#         token = AuthUtils.create_access_token(user.id, user.username)
#
#         return jsonify({
#             'message': '登录成功',
#             'token': token,
#             'user': user.to_dict()
#         })
#
#     except Exception as e:
#         print(f"登录错误: {e}")
#         return jsonify({'error': '登录失败'}), 500


# @auth_bp.route("/me", methods=["GET"])
# @AuthUtils.token_required
# def get_current_user():
#     """获取当前用户信息"""
#     try:
#         # 从token中获取用户ID
#         user_id = request.current_user.get('user_id')
#
#         # 查询用户
#         user = User.query.get(user_id)
#         if not user:
#             return jsonify({'error': '用户不存在'}), 404
#
#         return jsonify({
#             'user': user.to_dict()
#         })
#
#     except Exception as e:
#         print(f"获取用户信息错误: {e}")
#         return jsonify({'error': '获取用户信息失败'}), 500


# @auth_bp.route("/register", methods=["POST"])
# @limiter.limit("5 per minute")  # 每分钟最多5次注册尝试
# def register():
#     """用户注册"""
#     try:
#         data = request.get_json()
#         username = data.get('username')
#         password = data.get('password')  # 前端已经SHA256加密的密码
#         group_name = data.get('group_name')
#
#         # 验证输入
#         if not username or not password or not group_name:
#             return jsonify({'error': '用户名和密码以及用户组是必需的'}), 400
#
#         # 检查用户是否已存在
#         existing_user = User.query.filter_by(username=username).first()
#         if existing_user:
#             return jsonify({'error': '用户名已存在'}), 400
#
#         # 生成盐值
#         salt = AuthUtils.generate_salt()
#         print(salt)
#
#         # 为数据库存储生成最终密码哈希
#         # password_hash = AuthUtils.hash_password_for_db(password, salt)
#         # 创建用户
#         new_user = User(
#             username=username,
#             password_frontend_hashed=password,
#             group_name=group_name,
#             salt=salt
#         )
#
#         db.session.add(new_user)
#         db.session.commit()
#         return jsonify({
#             'message': '注册成功',
#             'user': new_user.to_dict()
#         }), 200
#
#     except Exception as e:
#         db.session.rollback()
#         print(f"注册错误: {e}")
#         return jsonify({'error': '注册失败'}), 500