import base64
import os


def base64_key(filename):
    """Base64编码文件名"""
    encoded = base64.b64encode(filename.encode()).decode()
    # 替换Base64中的特殊字符
    return encoded.replace('/', '_').replace('+', '-').rstrip('=')

def decode_base64_key(key):
    """解码Base64键"""
    # 恢复Base64字符
    key = key.replace('_', '/').replace('-', '+')
    # 添加填充
    padding = 4 - len(key) % 4
    key += '=' * padding
    return base64.b64decode(key.encode()).decode()

# 使用示例
# filename = "敏感/+-文件/路径.txt"
# safe_key = base64_key(filename)  # 如: "6YCi5ZCe5paH5Lu2LnR4dA"
# print(safe_key)
# original = decode_base64_key(safe_key)  # 还原
# print(original)

def is_safe_filename(filename: str) -> bool:
    """
    检查文件名是否安全
    """
    # 检查路径遍历
    if '..' in filename or filename.startswith('/'):
        return False

    # 检查绝对路径
    if os.path.isabs(filename):
        return False

    # 检查特殊字符
    unsafe_chars = ['<', '>', ':', '"', '|', '?', '*']
    if any(char in filename for char in unsafe_chars):
        return False

    # 文件名不能为空
    if not filename.strip():
        return False

    return True


