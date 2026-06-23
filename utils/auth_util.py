import hashlib
import uuid
from datetime import datetime, timedelta
import jwt
import os
from functools import wraps
from flask import request, jsonify,g
import secrets
import base64
import requests
from blueprint_config import Config
from typing import List, Dict, Optional

class AuthUtils:
    # JWT配置
    SECRET_KEY = os.environ.get('SECRET_KEY', 'ahsdadajhdkadsahd31#as4a4da')
    JWT_ALGORITHM = 'HS256'
    JWT_EXPIRY_HOURS = 72

    @staticmethod
    def hash_frontend_password(password: str) -> str:
        """
        模拟前端SHA256加密
        注意：这个方法应该与前端的加密方式完全一致
        """
        # 转换为小写，确保与前端一致
        return hashlib.sha256(password.encode('utf-8')).hexdigest().lower()

    @staticmethod
    def generate_salt() -> str:
        """生成更短的盐值"""
        # 8 字节随机数，base64 编码后约 11 字符
        salt = base64.b64encode(secrets.token_bytes(8)).decode('utf-8')
        return salt.replace('=', '').replace('+', '-').replace('/', '_')[:7]

    @staticmethod
    def create_access_token(user_id: int, username: str) -> str:
        """
        创建JWT访问令牌
        """
        payload = {
            'user_id': user_id,
            'username': username,
            'exp': datetime.utcnow() + timedelta(hours=AuthUtils.JWT_EXPIRY_HOURS),
            'iat': datetime.utcnow(),
            'type': 'access'
        }

        token = jwt.encode(
            payload,
            AuthUtils.SECRET_KEY,
            algorithm=AuthUtils.JWT_ALGORITHM
        )
        return token

    @staticmethod
    def verify_token(token: str):
        """
        验证JWT令牌

        Returns:
            dict: 如果验证成功返回payload，否则返回None
        """
        try:
            payload = jwt.decode(
                token,
                AuthUtils.SECRET_KEY,
                algorithms=[AuthUtils.JWT_ALGORITHM]
            )
            return payload
        except jwt.ExpiredSignatureError:
            print("Token已过期")
            return None
        except jwt.InvalidTokenError:
            print("无效的Token")
            return None

    @staticmethod
    def token_required(f):
        from functools import wraps
        from flask import request, jsonify, g

        @wraps(f)
        def decorated(*args, **kwargs):
            WHITELIST_PATHS = [
                '/api/task/info',
            ]
            LEADERS=['汪海洋','魏斌','毛剑英','李蔚','陈煜欣','黄明祥','陆楠','赵苗苗','范雪环','高枫','张博','轩长征']
            DEVELOPERS= ['雷震','潘紫玲','周泽南','王祖翰','刘婉平']
            token = None
            # 优先从 Authorization header 获取
            auth_header = request.headers.get("Authorization", "")
            if auth_header:
                parts = auth_header.split()
                if len(parts) == 2 and parts[0].lower() == "bearer":
                    token = parts[1]
            # 回退到 query / cookie（兼容 EventSource）
            if not token:
                token = request.args.get("token") or request.cookies.get("token")
            if not token:
                return jsonify({"msg": "token is required"}), 401



            # 向统一鉴权服务验证token
            try:
                # 从环境变量或配置获取鉴权服务地址
                print(token)
                auth_service_url = 'http://10.100.226.96:8238/oauth2'

                # 调用用户信息接口
                response = requests.get(
                    f"{auth_service_url}/userinfo",  # 根据实际接口路径调整
                    headers={
                        'Authorization': f'Bearer {token}',
                        'Content-Type': 'application/json'
                    },
                    timeout=5  # 设置超时时间
                )

                # 检查响应状态
                print(response.ok)
                if not response.ok:
                    error_msg = f"Token validation failed: {response.status_code}"
                    try:
                        error_data = response.json()
                        if 'error_description' in error_data:
                            error_msg = error_data['error_description']
                        elif 'error' in error_data:
                            error_msg = error_data['error']
                    except:
                        pass
                    return jsonify({"msg": error_msg}), 401
                # 获取用户信息


                # 检查当前请求路径是否在白名单中
                current_path = request.path
                skip_permission_check = False
                if current_path in WHITELIST_PATHS:
                    # 白名单路径，直接执行原函数，不进行权限验证
                    skip_permission_check = True
                # ========== 白名单检查结束 ==========

                # 原有的 token 验证逻辑...
                token = None
                auth_header = request.headers.get("Authorization", "")
                # ... 其余代码保持不变
                user_info = response.json()
                print(user_info)
                if not skip_permission_check:
                    # if(not AuthUtils.verify_app_permission(user_info['roles'],Config.APPLICATION_ID)):
                    #     return jsonify({"msg": "该用户没有权限"}), 403
                    if user_info['name'] not in LEADERS and user_info['name'] not in DEVELOPERS:
                        return jsonify({"msg": "该用户没有权限"}), 403
                user = {
                    "user_id": user_info['username'],
                    "username":user_info['name'],
                    "group_name":user_info['sub'] or "未知"
                    # "group_name": "生态环境部信息中心"
                }
                if not user["user_id"]:
                    return jsonify({"msg": "invalid or expired token"}), 401
                # 将用户信息放到 flask.g，兼容老代码也尝试挂到 request
                try:
                    g.current_user = user
                    setattr(request, "current_user", user)
                except Exception:
                    pass
            except requests.exceptions.Timeout:
                return jsonify({"msg": "Auth service timeout"}), 503
            except requests.exceptions.ConnectionError:
                return jsonify({"msg": "Auth service unavailable"}), 503
            except Exception as e:
                return jsonify({"msg": f"Token validation error: {str(e)}"}), 500

            # return f(*args, **kwargs)
            return f(*args, **kwargs)

        return decorated

    @staticmethod
    def verify_app_permission(roles: List[Dict[str, str]], application_id: str) -> bool:
        """
        验证 "roles"数组是否包含application_id

        Returns:
            true/false
        """
        for role in roles:
            if role['applicationId'] == application_id:
                return True
        return False