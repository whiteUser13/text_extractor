import os
import pandas as pd
import requests
import time
from tqdm import tqdm
import urllib.parse
import re
import hashlib


def download_reports():
    # 定义路径
    csv_path = r"D:\信息中心\大模型\部里工作\土壤司\建设用地\地块开发建设情况信息提取\新增加报告\报告重新下载\下载错误日志.xlsx"
    download_dir = r"D:\信息中心\大模型\部里工作\土壤司\建设用地\地块开发建设情况信息提取\新增加报告\报告重新下载\错误报告重新下载"

    # 设置更完整的请求头
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': '*/*',  # 接受所有类型
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Accept-Encoding': 'gzip, deflate',
        'Connection': 'keep-alive',
        'Cache-Control': 'no-cache'
    }

    # 创建会话对象
    session = requests.Session()
    session.headers.update(headers)

    # 检查CSV文件是否存在
    if not os.path.exists(csv_path):
        print(f"CSV文件不存在: {csv_path}")
        return

    # 创建下载目录（如果不存在）
    os.makedirs(download_dir, exist_ok=True)

    try:
        # 读取Excel文件
        df = pd.read_excel(csv_path)

        # 检查必需的列是否存在
        if '地块编码' not in df.columns:
            print("Excel文件中没有找到'地块编码'列")
            return

        # 获取所有plot_code
        plot_codes = df['地块编码'].dropna().unique()
        total_count = len(plot_codes)

        print(f"找到 {total_count} 个唯一的plot_code")

        # 下载计数器
        success_count = 0
        fail_count = 0
        skip_count = 0

        # 创建成功下载记录文件
        success_log_path = os.path.join(download_dir, "成功下载记录.csv")
        if not os.path.exists(success_log_path):
            with open(success_log_path, 'w', encoding='utf-8-sig') as f:
                f.write("地块编码,文件名,文件大小(字节),下载时间,MD5校验值\n")

        # 逐个下载
        for plot_code in tqdm(plot_codes, desc="下载进度"):
            url = f"http://10.251.103.56/admin/suspected/projectinfo/downloadBg.do?bmOrMc={plot_code}"

            # 检查是否已经成功下载过（避免重复下载）
            if check_already_downloaded(download_dir, plot_code):
                print(f"跳过已下载: {plot_code}")
                skip_count += 1
                continue

            try:
                # 发送请求
                response = session.get(url, timeout=60, allow_redirects=True)

                # 检查请求是否成功
                if response.status_code != 200:
                    print(f"下载失败: {plot_code} (状态码: {response.status_code})")
                    log_error(download_dir, plot_code, f"HTTP状态码错误({response.status_code})", url)
                    fail_count += 1
                    continue

                # 获取文件大小
                content_length = len(response.content)

                # 放宽文件大小检查：只要不是空文件就接受
                if content_length == 0:
                    print(f"下载失败: {plot_code} (文件为空)")
                    log_error(download_dir, plot_code, "文件为空", url)
                    fail_count += 1
                    continue

                # 进一步放宽小文件检查：只要大于1k字节就接受
                if content_length < 1024:  # 1KB = 1024字节
                    print(f"下载失败: {plot_code} (文件过小: {content_length}字节)")
                    log_error(download_dir, plot_code, f"文件过小({content_length}字节)", url)
                    fail_count += 1
                    continue

                # 检查是否是明显的HTML错误页面
                if is_error_page(response.content):
                    print(f"下载失败: {plot_code} (返回错误页面)")
                    log_error(download_dir, plot_code, "返回错误页面", url)
                    fail_count += 1
                    continue

                # 获取文件名
                filename = get_filename(response, plot_code)
                save_path = os.path.join(download_dir, filename)

                # 保存文件
                with open(save_path, 'wb') as f:
                    f.write(response.content)

                # 验证文件基本完整性（放宽条件）
                if verify_file_basic(save_path):
                    # 计算文件MD5
                    file_md5 = calculate_md5(save_path)

                    # 记录成功下载
                    log_success(download_dir, plot_code, filename, content_length, file_md5)
                    success_count += 1
                    print(f"下载成功: {filename} ({content_length}字节)")
                else:
                    print(f"文件验证失败: {plot_code}")
                    # 删除可能损坏的文件
                    if os.path.exists(save_path):
                        os.path.exists(save_path) and os.remove(save_path)
                    log_error(download_dir, plot_code, "文件验证失败", url)
                    fail_count += 1

                # 添加延迟，避免请求过于频繁
                time.sleep(0.3)

            except requests.exceptions.Timeout:
                print(f"下载超时: {plot_code}")
                log_error(download_dir, plot_code, "请求超时", url)
                fail_count += 1
            except requests.exceptions.RequestException as e:
                print(f"网络错误: {plot_code} - {str(e)}")
                log_error(download_dir, plot_code, f"网络错误({str(e)})", url)
                fail_count += 1
            except Exception as e:
                print(f"处理错误: {plot_code} - {str(e)}")
                log_error(download_dir, plot_code, f"处理错误({str(e)})", url)
                fail_count += 1

        # 输出下载结果
        print(f"\n下载完成！")
        print(f"成功: {success_count}")
        print(f"失败: {fail_count}")
        print(f"跳过(已存在): {skip_count}")
        print(f"总计: {total_count}")

    except Exception as e:
        print(f"读取Excel文件时出错: {str(e)}")


def check_already_downloaded(download_dir, plot_code):
    """检查文件是否已经下载过"""
    # 检查成功下载记录
    success_log_path = os.path.join(download_dir, "成功下载记录.csv")
    if os.path.exists(success_log_path):
        try:
            with open(success_log_path, 'r', encoding='utf-8-sig') as f:
                content = f.read()
                if plot_code in content:
                    return True
        except:
            pass

    # 检查实际文件是否存在（通过文件名模式匹配）
    for filename in os.listdir(download_dir):
        if filename.startswith(plot_code) and os.path.isfile(os.path.join(download_dir, filename)):
            file_size = os.path.getsize(os.path.join(download_dir, filename))
            if file_size > 100:  # 文件大小合理
                return True

    return False


def get_filename(response, plot_code):
    """获取文件名"""
    content_disposition = response.headers.get('Content-Disposition', '')

    # 从Content-Disposition中提取文件名
    if content_disposition:
        # 尝试多种模式匹配文件名
        patterns = [
            r'filename\*?=([^;]+)',
            r'filename=["\']?([^;"\']+)',
            r'filename=([^;]+)'
        ]

        for pattern in patterns:
            match = re.search(pattern, content_disposition, re.IGNORECASE)
            if match:
                filename = match.group(1).strip()
                # 去除可能的引号
                filename = filename.strip('"\'')
                # URL解码
                filename = urllib.parse.unquote(filename)
                break
        else:
            filename = None
    else:
        filename = None

    # 如果无法从header获取文件名，根据内容生成
    if not filename:
        content_type = response.headers.get('Content-Type', '').lower()
        file_extension = get_file_extension(content_type, response.content)
        filename = f"{plot_code}{file_extension}"
    else:
        # 在文件名前加上plot_code
        name, ext = os.path.splitext(filename)
        filename = f"{plot_code}_{name}{ext}" if name else f"{plot_code}{ext}"

    # 清理文件名
    return clean_filename(filename)


def get_file_extension(content_type, content):
    """根据内容确定文件扩展名"""
    # 检查文件签名
    if len(content) < 8:
        return ".dat"  # 小文件使用通用扩展名

    # 文件签名检测
    signatures = [
        (b'%PDF', '.pdf'),
        (b'PK\x03\x04', '.zip'),
        (b'\xD0\xCF\x11\xE0', '.doc'),
        (b'Rar!', '.rar'),
        (b'7z\xBC\xAF\x27\x1C', '.7z'),
    ]

    for signature, extension in signatures:
        if content.startswith(signature):
            return extension

    # 基于Content-Type判断
    if 'pdf' in content_type:
        return '.pdf'
    elif 'zip' in content_type or 'compressed' in content_type:
        return '.zip'
    elif 'msword' in content_type:
        return '.doc'
    elif 'wordprocessingml' in content_type:
        return '.docx'
    elif 'rar' in content_type:
        return '.rar'

    return '.dat'  # 默认扩展名


def is_error_page(content):
    """检查是否是错误页面（放宽条件）"""
    if len(content) < 100:
        return False

    try:
        content_str = content[:500].decode('utf-8', errors='ignore').lower()
        # 只检查明显的错误关键词
        error_indicators = ['error', '404', 'not found', '无法找到', '错误', '失败']
        return any(indicator in content_str for indicator in error_indicators)
    except:
        return False


def clean_filename(filename):
    """清理文件名"""
    # 替换非法字符
    filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
    # 去除首尾空格和点
    filename = filename.strip().strip('.')
    # 限制长度
    if len(filename) > 150:
        name, ext = os.path.splitext(filename)
        filename = name[:150 - len(ext)] + ext
    return filename


def verify_file_basic(filepath):
    """基本文件验证（放宽条件）"""
    try:
        file_size = os.path.getsize(filepath)
        # 只要文件存在且不是空文件就认为基本有效
        return file_size > 0
    except:
        return False


def calculate_md5(filepath):
    """计算文件MD5"""
    try:
        hash_md5 = hashlib.md5()
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
    except:
        return "未知"


def log_success(download_dir, plot_code, filename, file_size, md5_value):
    """记录成功下载"""
    success_log_path = os.path.join(download_dir, "成功下载记录.csv")
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")

    with open(success_log_path, 'a', encoding='utf-8-sig') as f:
        f.write(f"{plot_code},{filename},{file_size},{timestamp},{md5_value}\n")


def log_error(download_dir, plot_code, error_reason, url):
    """记录错误日志"""
    error_log_path = os.path.join(download_dir, "下载错误日志.csv")
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")

    if not os.path.exists(error_log_path):
        with open(error_log_path, 'w', encoding='utf-8-sig') as f:
            f.write("地块编码,错误原因,下载链接,时间戳\n")

    with open(error_log_path, 'a', encoding='utf-8-sig') as f:
        f.write(f"{plot_code},{error_reason},{url},{timestamp}\n")


if __name__ == "__main__":
    download_reports()