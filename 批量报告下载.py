<<<<<<< HEAD
import os
import pandas as pd
import requests
import re
import time
import zipfile
import tarfile
import shutil
import rarfile
import json
from datetime import datetime

# 添加全局变量或类变量来跟踪错误信息
error_log = []

def download_reports():
    # 文件路径设置
    excel_path = r"D:\信息中心\大模型\部里工作\土壤司\建设用地\地块开发建设情况信息提取\调查报告及信息提取20240716\2025上半年台账.xlsx"
    save_dir = r"D:\信息中心\大模型\部里工作\土壤司\建设用地\地块开发建设情况信息提取\报告自动下载\4000个报告批量下载\2500-3000"

    # 创建错误日志目录
    error_log_dir = os.path.join(save_dir, "error_logs")
    if not os.path.exists(error_log_dir):
        os.makedirs(error_log_dir)

    # 创建保存目录（如果不存在）
    if not os.path.exists(save_dir):
        os.makedirs(save_dir)

    # 读取Excel文件
    try:
        df = pd.read_excel(excel_path)
        # 假设地块编码在名为'地块编码'的列中，如果不是请修改列名
        plot_codes = df['地块编码'].tolist()[2802:3000]  # 取第101到200个
        print(f"成功读取 {len(plot_codes)} 个地块编码")
    except Exception as e:
        print(f"读取Excel文件时出错: {e}")
        return

    # 设置请求头，模拟浏览器行为
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.8,zh-TW;q=0.7,zh-HK;q=0.5,en-US;q=0.3,en;q=0.2',
        'Accept-Encoding': 'gzip, deflate',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
    }

    # 使用会话保持
    session = requests.Session()

    download_count = 0
    base_url = "http://10.251.103.56/admin/suspected/projectinfo/downloadBg.do?bmOrMc="

    for code in plot_codes:
        if download_count >= 300:
            break

        # 构建下载URL
        download_url = base_url + str(code)

        max_retries = 3
        retry_count = 0
        success = False

        while retry_count < max_retries and not success:
            retry_count += 1
            if retry_count > 1:
                print(f"第 {retry_count} 次重试下载地块编码: {code}")

            try:
                print(f"\n正在尝试下载地块编码: {code}")

                # 发送请求
                response = session.get(download_url, headers=headers, stream=True, timeout=30)

                # 检查请求是否成功
                if response.status_code == 200:
                    # 从Content-Disposition头中获取文件名
                    content_disposition = response.headers.get('Content-Disposition', '')
                    print(f"Content-Disposition: {content_disposition}")

                    filename = None

                    if content_disposition:
                        # 处理utf-8编码的文件名
                        if "utf-8''" in content_disposition:
                            # 提取utf-8编码的文件名部分
                            utf8_part = content_disposition.split("utf-8''")[-1]
                            # 移除可能的多余部分
                            filename = utf8_part.split(';')[0].strip()
                            # URL解码
                            filename = requests.utils.unquote(filename)
                        else:
                            # 常规的文件名提取
                            filename_match = re.findall(r'filename="?(.+?)"?;', content_disposition)
                            if filename_match:
                                filename = filename_match[0].strip('"')

                    # 如果从header中获取到文件名，使用 地块编码_文件名 的格式
                    if filename:
                        # 清理文件名中的非法字符
                        filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
                        # 确保文件名以地块编码开头
                        if not filename.startswith(str(code)):
                            final_filename = f"{code}_{filename}"
                        else:
                            final_filename = filename
                    else:
                        # 如果没有获取到文件名，使用地块编码作为文件名
                        content_type = response.headers.get('Content-Type', '')
                        if 'pdf' in content_type.lower():
                            extension = '.pdf'
                        elif 'word' in content_type.lower():
                            extension = '.doc'
                        elif 'excel' in content_type.lower() or 'sheet' in content_type.lower():
                            extension = '.xls'
                        else:
                            extension = '.bin'
                        final_filename = f"{code}{extension}"

                    file_path = os.path.join(save_dir, final_filename)

                    # 保存文件
                    total_size = 0
                    with open(file_path, 'wb') as f:
                        for chunk in response.iter_content(chunk_size=8192):
                            if chunk:
                                f.write(chunk)
                                total_size += len(chunk)

                    # 检查文件大小
                    file_size = os.path.getsize(file_path)
                    if file_size > 0:
                        print(f"✓ 成功下载: {final_filename} (大小: {file_size} 字节)")
                        success = True

                        # 只有压缩文件才需要处理，单个文件直接保留
                        if is_compressed_file(file_path):
                            print(f"检测到压缩文件，开始处理...")
                            processed_file_path = process_compressed_file(file_path, code, save_dir)
                            if processed_file_path and processed_file_path != file_path:
                                print(f"✓ 压缩文件处理完成: {os.path.basename(processed_file_path)}")
                                download_count += 1
                            elif processed_file_path == file_path:
                                print(f"✓ 压缩文件已下载但未找到合适文件，保留原始文件")
                                download_count += 1
                            else:
                                print(f"✗ 压缩文件处理失败")
                                # 记录错误信息
                                log_error(code, f"压缩文件处理失败: {final_filename}", "process_error")
                        else:
                            # 单个文件（如PDF）直接保留，不进行处理
                            print(f"✓ 单个文件下载完成，直接保留")
                            download_count += 1
                    else:
                        print(f"✗ 下载的文件为空: {final_filename}")
                        if os.path.exists(file_path):
                            os.remove(file_path)  # 删除空文件
                        # 记录错误信息
                        log_error(code, "下载的文件为空", "empty_file")

                else:
                    error_msg = f"下载失败: 状态码 {response.status_code}"
                    print(f"✗ {error_msg}")
                    # 记录错误信息
                    log_error(code, error_msg, "http_error")

            except Exception as e:
                error_msg = f"下载出错: {e}"
                print(f"✗ {error_msg}")
                # 记录错误信息
                log_error(code, error_msg, "exception")

            # 如果不是最后一次尝试，添加延迟
            if not success and retry_count < max_retries:
                retry_delay = 2 * retry_count  # 指数退避策略
                print(f"等待 {retry_delay} 秒后重试...")
                time.sleep(retry_delay)

        # 添加短暂延迟
        time.sleep(1)

    # 下载完成后保存错误日志
    save_error_log(error_log_dir)
    print(f"\n下载完成，共成功下载了 {download_count} 份报告")


def log_error(plot_code, error_message, error_type):
    """记录错误信息"""
    error_entry = {
        "plot_code": plot_code,
        "error_message": error_message,
        "error_type": error_type,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    error_log.append(error_entry)
    print(f"错误已记录: {error_message}")

# 添加错误日志保存函数
def save_error_log(error_log_dir):
    """保存错误日志到文件"""
    if error_log:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        error_file = os.path.join(error_log_dir, f"error_log_{timestamp}.json")

        try:
            with open(error_file, 'w', encoding='utf-8') as f:
                json.dump(error_log, f, ensure_ascii=False, indent=2)
            print(f"✓ 错误日志已保存到: {error_file}")

            # 同时保存为 CSV 格式便于查看
            csv_file = os.path.join(error_log_dir, f"error_log_{timestamp}.csv")
            error_df = pd.DataFrame(error_log)
            error_df.to_csv(csv_file, index=False, encoding='utf-8-sig')
            print(f"✓ 错误日志CSV已保存到: {csv_file}")

        except Exception as e:
            print(f"保存错误日志时出错: {e}")
    else:
        print("✓ 没有错误需要记录")

def process_compressed_file(file_path, plot_code, save_dir):
    """
    处理压缩文件：解压，筛选报告，重命名
    如果找到合适文件，只保留该文件；否则保留整个解压文件夹
    """
    extract_dir = os.path.join(save_dir, f"extracted_{plot_code}")

    try:
        print(f"开始处理压缩文件: {os.path.basename(file_path)}")

        file_size = os.path.getsize(file_path)
        if file_size < 100:
            error_msg = f"文件可能下载不完整，大小: {file_size} 字节"
            print(error_msg)
            log_error(plot_code, error_msg, "file_too_small")
            return file_path

        # 创建解压目录
        if os.path.exists(extract_dir):
            shutil.rmtree(extract_dir)
        os.makedirs(extract_dir)

        # 解压文件
        if extract_file(file_path, extract_dir):
            print(f"解压成功，开始筛选文件")

            # 获取解压后的所有文件
            all_files = []
            for root, dirs, files in os.walk(extract_dir):
                for file in files:
                    file_path_full = os.path.join(root, file)
                    all_files.append(file_path_full)

            print(f"解压后共找到 {len(all_files)} 个文件:")
            for file in all_files:
                print(f"  - {os.path.basename(file)}")

            # 筛选文件
            target_file = filter_and_select_file(extract_dir)

            if target_file:
                original_filename = os.path.basename(target_file)
                print(f"找到目标文件: {original_filename}")

                # 清理文件名中的非法字符和数字前缀
                clean_filename = clean_file_name(original_filename)

                # 移动目标文件到主目录
                new_filename = f"{plot_code}_{clean_filename}"
                new_filepath = os.path.join(save_dir, new_filename)

                # 如果目标文件已存在，先删除
                if os.path.exists(new_filepath):
                    os.remove(new_filepath)

                shutil.move(target_file, new_filepath)
                print(f"✓ 已移动目标文件到主目录: {new_filename}")

                # 找到合适文件时，删除原始压缩文件和解压目录
                os.remove(file_path)
                shutil.rmtree(extract_dir)
                print(f"✓ 已删除原始压缩文件和解压目录")

                return new_filepath
            else:
                # 没有找到符合条件的文件，保留原始压缩文件和解压目录
                error_msg = "未找到符合条件的文件"
                print(error_msg)
                log_error(plot_code, error_msg, "no_suitable_file")
                return file_path  # 返回原始文件路径
        else:
            # 解压失败，保留原始文件
            error_msg = "解压失败"
            print(error_msg)
            log_error(plot_code, error_msg, "extraction_failed")
            if os.path.exists(extract_dir):
                shutil.rmtree(extract_dir)
            return file_path

    except Exception as e:
        error_msg = f"处理压缩文件时出错: {e}"
        print(error_msg)
        log_error(plot_code, error_msg, "process_exception")
        # 出错时尝试清理并保留原始文件
        if os.path.exists(extract_dir):
            shutil.rmtree(extract_dir)
        return file_path

def clean_file_name(filename):
    """
    清理文件名：移除数字前缀、点号和空格，保留中文和正常字符
    """
    # 移除数字前缀和点号（如 "1."、"2." 等）
    cleaned = re.sub(r'^\d+\.', '', filename)

    # 移除文件名开头的空格和特殊字符
    cleaned = cleaned.lstrip(' .-_')

    # 清理文件名中的非法字符（Windows文件名不允许的字符）
    cleaned = re.sub(r'[<>:"/\\|?*]', '_', cleaned)

    # 确保文件名不以点结尾
    cleaned = cleaned.rstrip('.')

    return cleaned

def is_compressed_file(file_path):
    """检查文件是否为支持的压缩格式"""
    compressed_extensions = ['.zip', '.rar', '.7z', '.tar', '.gz', '.bz2']
    return any(file_path.lower().endswith(ext) for ext in compressed_extensions)

def extract_file(file_path, extract_dir):
    """解压文件到指定目录"""
    try:
        if file_path.lower().endswith('.zip'):
            with zipfile.ZipFile(file_path, 'r') as zip_ref:
                # 处理中文文件名编码问题
                for file_info in zip_ref.infolist():
                    try:
                        # 尝试使用UTF-8解码文件名
                        file_info.filename = file_info.filename.encode('cp437').decode('gbk')
                    except:
                        try:
                            # 如果GBK失败，尝试UTF-8
                            file_info.filename = file_info.filename.encode('cp437').decode('utf-8')
                        except:
                            # 如果都失败，保留原始文件名
                            pass
                    zip_ref.extract(file_info, extract_dir)
            return True
        # 修改 extract_file 函数中的 RAR 解压部分
        elif file_path.lower().endswith('.rar'):
            # 添加 RAR 文件解压支持 - 使用更简单直接的方法
            try:
                # 检查文件大小
                file_size = os.path.getsize(file_path)
                if file_size < 100:
                    print(f"RAR 文件可能不完整，大小: {file_size} 字节")
                    return False
                print(f"开始解压 RAR 文件，大小: {file_size} 字节")
                # 使用更简单的方法解压，避免复杂的文件名处理
                with rarfile.RarFile(file_path, 'r') as rar_ref:
                    # 直接使用 extractall，让 rarfile 处理文件名编码
                    rar_ref.extractall(extract_dir)
                    # 检查是否成功解压出文件
                    extracted_files = []
                    for root, dirs, files in os.walk(extract_dir):
                        for file in files:
                            extracted_files.append(os.path.join(root, file))
                    if extracted_files:
                        print(f"成功解压 {len(extracted_files)} 个文件")
                        # 打印前几个文件名以便调试
                        for i, f in enumerate(extracted_files[:3]):
                            print(f"  - {os.path.basename(f)}")
                        if len(extracted_files) > 3:
                            print(f"  - ... 还有 {len(extracted_files) - 3} 个文件")
                        return True
                    else:
                        print("解压成功但未找到文件")
                        return False
            except rarfile.BadRarFile as e:
                print(f"RAR 文件损坏: {e}")
                return False
            except rarfile.NotRarFile as e:
                print(f"不是有效的 RAR 文件: {e}")
                return False
            except rarfile.RarCannotExec as e:
                print(f"无法执行 RAR 解压命令: {e}")
                return False
            except rarfile.RarFatalError as e:
                print(f"RAR 致命错误: {e}")
                return False
            except rarfile.RarWarning as e:
                print(f"RAR 警告: {e}")
                # 即使有警告，也可能解压成功
                try:
                    # 检查解压目录是否有文件
                    if any(os.listdir(extract_dir)):
                        print("尽管有警告，但文件已解压")
                        return True
                    else:
                        return False
                except:
                    return False
            except Exception as e:
                print(f"解压 RAR 文件时未知错误: {e}")
                return False
        elif file_path.lower().endswith('.tar') or file_path.lower().endswith('.tar.gz') or file_path.lower().endswith(
                '.tar.bz2'):
            with tarfile.open(file_path, 'r') as tar_ref:
                tar_ref.extractall(extract_dir)
            return True
        else:
            print(f"不支持的压缩格式: {file_path}")
            return False
    except Exception as e:
        print(f"解压文件时出错: {e}")
        return False

# 另外，我们可以添加一个备用方案：使用系统命令解压
def extract_with_system_command(file_path, extract_dir):
    """使用系统命令解压 RAR 文件（备用方案）"""
    try:
        import subprocess
        import sys
        if sys.platform == 'win32':
            # Windows 系统使用 WinRAR 或 unrar
            try:
                # 尝试使用 WinRAR
                result = subprocess.run([
                    'C:\\Program Files\\WinRAR\\WinRAR.exe',
                    'x', '-y', file_path, extract_dir
                ], capture_output=True, text=True, timeout=60)

                if result.returncode == 0:
                    print("使用 WinRAR 解压成功")
                    return True
            except:
                try:
                    # 尝试使用 unrar
                    result = subprocess.run([
                        'unrar', 'x', '-y', file_path, extract_dir
                    ], capture_output=True, text=True, timeout=60)

                    if result.returncode == 0:
                        print("使用 unrar 解压成功")
                        return True
                except:
                    pass
        else:
            # Linux/Mac 系统
            try:
                result = subprocess.run([
                    'unrar', 'x', '-y', file_path, extract_dir
                ], capture_output=True, text=True, timeout=60)

                if result.returncode == 0:
                    print("使用 unrar 解压成功")
                    return True
            except:
                pass
        return False
    except Exception as e:
        print(f"系统命令解压失败: {e}")
        return False
def filter_and_select_file(directory):
    """
    筛选文件：按照文件名文字匹配进行筛选，从所有文件类型中查找
    """
    all_files = []

    # 收集所有文件
    for root, dirs, files in os.walk(directory):
        for file in files:
            file_path = os.path.join(root, file)
            all_files.append(file_path)

    print(f"找到 {len(all_files)} 个文件:")
    for file_path in all_files:
        print(f"  - {os.path.basename(file_path)}")

    if not all_files:
        print("未找到任何文件")
        return None

    # 第一步：查找所有文档文件（PDF、Word、Excel等）
    document_files = [f for f in all_files if f.lower().endswith(('.pdf', '.doc', '.docx', '.xls', '.xlsx'))]
    if not document_files:
        print("未找到任何文档文件")
        return None

    print(f"找到 {len(document_files)} 个文档文件")

    # 第二步：查找包含"调查"的文件（放宽匹配条件）
    survey_files = []
    for file_path in document_files:
        filename = os.path.basename(file_path)
        if "调查" in filename:
            survey_files.append(file_path)

    if survey_files:
        print(f"找到 {len(survey_files)} 个包含'调查'的文件")

        # 排除包含排除词汇的文件
        valid_survey_files = []
        for file_path in survey_files:
            filename = os.path.basename(file_path)
            if not contains_excluded_words(filename):
                valid_survey_files.append(file_path)

        if valid_survey_files:
            print(f"找到 {len(valid_survey_files)} 个有效的调查报告")
            # 选择文件大小最大的
            valid_survey_files.sort(key=lambda x: os.path.getsize(x), reverse=True)
            selected_file = valid_survey_files[0]
            print(f"选择调查报告: {os.path.basename(selected_file)}")
            return selected_file
        else:
            # 所有调查报告都包含排除词汇，选择文件大小最大的
            print("所有调查报告都包含排除词汇，选择文件大小最大的")
            survey_files.sort(key=lambda x: os.path.getsize(x), reverse=True)
            selected_file = survey_files[0]
            print(f"选择: {os.path.basename(selected_file)}")
            return selected_file

    # 第三步：查找包含"风险"的文件（放宽匹配条件）
    risk_files = []
    for file_path in document_files:
        filename = os.path.basename(file_path)
        if "风险" in filename:
            risk_files.append(file_path)

    if risk_files:
        print(f"找到 {len(risk_files)} 个包含'风险'的文件")

        # 排除包含排除词汇的文件
        valid_risk_files = []
        for file_path in risk_files:
            filename = os.path.basename(file_path)
            if not contains_excluded_words(filename):
                valid_risk_files.append(file_path)

        if valid_risk_files:
            print(f"找到 {len(valid_risk_files)} 个有效的风险评估报告")
            # 选择文件大小最大的
            valid_risk_files.sort(key=lambda x: os.path.getsize(x), reverse=True)
            selected_file = valid_risk_files[0]
            print(f"选择风险评估报告: {os.path.basename(selected_file)}")
            return selected_file
        else:
            # 所有风险评估报告都包含排除词汇，选择文件大小最大的
            print("所有风险评估报告都包含排除词汇，选择文件大小最大的")
            risk_files.sort(key=lambda x: os.path.getsize(x), reverse=True)
            selected_file = risk_files[0]
            print(f"选择: {os.path.basename(selected_file)}")
            return selected_file

    # 第四步：如果都没有，选择最大的文档文件
    print("未找到调查或风险评估相关文件，选择最大的文档文件")
    document_files.sort(key=lambda x: os.path.getsize(x), reverse=True)
    selected_file = document_files[0]
    print(f"选择最大的文档文件: {os.path.basename(selected_file)}")
    return selected_file


def contains_excluded_words(filename):
    """检查是否包含需要排除的词汇"""
    excluded_words = ['简本', '简版', '附件', '申请表', '信息表', '评审', '目录', '摘要', '附图', '附件','专家','意见','质控','函']
    return any(word in filename for word in excluded_words)


if __name__ == "__main__":
=======
import os
import pandas as pd
import requests
import re
import time
import zipfile
import tarfile
import shutil
import rarfile
import json
from datetime import datetime

# 添加全局变量或类变量来跟踪错误信息
error_log = []

def download_reports():
    # 文件路径设置
    excel_path = r"D:\信息中心\大模型\部里工作\土壤司\建设用地\地块开发建设情况信息提取\调查报告及信息提取20240716\2025上半年台账.xlsx"
    save_dir = r"D:\信息中心\大模型\部里工作\土壤司\建设用地\地块开发建设情况信息提取\报告自动下载\4000个报告批量下载\2500-3000"

    # 创建错误日志目录
    error_log_dir = os.path.join(save_dir, "error_logs")
    if not os.path.exists(error_log_dir):
        os.makedirs(error_log_dir)

    # 创建保存目录（如果不存在）
    if not os.path.exists(save_dir):
        os.makedirs(save_dir)

    # 读取Excel文件
    try:
        df = pd.read_excel(excel_path)
        # 假设地块编码在名为'地块编码'的列中，如果不是请修改列名
        plot_codes = df['地块编码'].tolist()[2802:3000]  # 取第101到200个
        print(f"成功读取 {len(plot_codes)} 个地块编码")
    except Exception as e:
        print(f"读取Excel文件时出错: {e}")
        return

    # 设置请求头，模拟浏览器行为
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.8,zh-TW;q=0.7,zh-HK;q=0.5,en-US;q=0.3,en;q=0.2',
        'Accept-Encoding': 'gzip, deflate',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
    }

    # 使用会话保持
    session = requests.Session()

    download_count = 0
    base_url = "http://10.251.103.56/admin/suspected/projectinfo/downloadBg.do?bmOrMc="

    for code in plot_codes:
        if download_count >= 300:
            break

        # 构建下载URL
        download_url = base_url + str(code)

        max_retries = 3
        retry_count = 0
        success = False

        while retry_count < max_retries and not success:
            retry_count += 1
            if retry_count > 1:
                print(f"第 {retry_count} 次重试下载地块编码: {code}")

            try:
                print(f"\n正在尝试下载地块编码: {code}")

                # 发送请求
                response = session.get(download_url, headers=headers, stream=True, timeout=30)

                # 检查请求是否成功
                if response.status_code == 200:
                    # 从Content-Disposition头中获取文件名
                    content_disposition = response.headers.get('Content-Disposition', '')
                    print(f"Content-Disposition: {content_disposition}")

                    filename = None

                    if content_disposition:
                        # 处理utf-8编码的文件名
                        if "utf-8''" in content_disposition:
                            # 提取utf-8编码的文件名部分
                            utf8_part = content_disposition.split("utf-8''")[-1]
                            # 移除可能的多余部分
                            filename = utf8_part.split(';')[0].strip()
                            # URL解码
                            filename = requests.utils.unquote(filename)
                        else:
                            # 常规的文件名提取
                            filename_match = re.findall(r'filename="?(.+?)"?;', content_disposition)
                            if filename_match:
                                filename = filename_match[0].strip('"')

                    # 如果从header中获取到文件名，使用 地块编码_文件名 的格式
                    if filename:
                        # 清理文件名中的非法字符
                        filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
                        # 确保文件名以地块编码开头
                        if not filename.startswith(str(code)):
                            final_filename = f"{code}_{filename}"
                        else:
                            final_filename = filename
                    else:
                        # 如果没有获取到文件名，使用地块编码作为文件名
                        content_type = response.headers.get('Content-Type', '')
                        if 'pdf' in content_type.lower():
                            extension = '.pdf'
                        elif 'word' in content_type.lower():
                            extension = '.doc'
                        elif 'excel' in content_type.lower() or 'sheet' in content_type.lower():
                            extension = '.xls'
                        else:
                            extension = '.bin'
                        final_filename = f"{code}{extension}"

                    file_path = os.path.join(save_dir, final_filename)

                    # 保存文件
                    total_size = 0
                    with open(file_path, 'wb') as f:
                        for chunk in response.iter_content(chunk_size=8192):
                            if chunk:
                                f.write(chunk)
                                total_size += len(chunk)

                    # 检查文件大小
                    file_size = os.path.getsize(file_path)
                    if file_size > 0:
                        print(f"✓ 成功下载: {final_filename} (大小: {file_size} 字节)")
                        success = True

                        # 只有压缩文件才需要处理，单个文件直接保留
                        if is_compressed_file(file_path):
                            print(f"检测到压缩文件，开始处理...")
                            processed_file_path = process_compressed_file(file_path, code, save_dir)
                            if processed_file_path and processed_file_path != file_path:
                                print(f"✓ 压缩文件处理完成: {os.path.basename(processed_file_path)}")
                                download_count += 1
                            elif processed_file_path == file_path:
                                print(f"✓ 压缩文件已下载但未找到合适文件，保留原始文件")
                                download_count += 1
                            else:
                                print(f"✗ 压缩文件处理失败")
                                # 记录错误信息
                                log_error(code, f"压缩文件处理失败: {final_filename}", "process_error")
                        else:
                            # 单个文件（如PDF）直接保留，不进行处理
                            print(f"✓ 单个文件下载完成，直接保留")
                            download_count += 1
                    else:
                        print(f"✗ 下载的文件为空: {final_filename}")
                        if os.path.exists(file_path):
                            os.remove(file_path)  # 删除空文件
                        # 记录错误信息
                        log_error(code, "下载的文件为空", "empty_file")

                else:
                    error_msg = f"下载失败: 状态码 {response.status_code}"
                    print(f"✗ {error_msg}")
                    # 记录错误信息
                    log_error(code, error_msg, "http_error")

            except Exception as e:
                error_msg = f"下载出错: {e}"
                print(f"✗ {error_msg}")
                # 记录错误信息
                log_error(code, error_msg, "exception")

            # 如果不是最后一次尝试，添加延迟
            if not success and retry_count < max_retries:
                retry_delay = 2 * retry_count  # 指数退避策略
                print(f"等待 {retry_delay} 秒后重试...")
                time.sleep(retry_delay)

        # 添加短暂延迟
        time.sleep(1)

    # 下载完成后保存错误日志
    save_error_log(error_log_dir)
    print(f"\n下载完成，共成功下载了 {download_count} 份报告")


def log_error(plot_code, error_message, error_type):
    """记录错误信息"""
    error_entry = {
        "plot_code": plot_code,
        "error_message": error_message,
        "error_type": error_type,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    error_log.append(error_entry)
    print(f"错误已记录: {error_message}")

# 添加错误日志保存函数
def save_error_log(error_log_dir):
    """保存错误日志到文件"""
    if error_log:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        error_file = os.path.join(error_log_dir, f"error_log_{timestamp}.json")

        try:
            with open(error_file, 'w', encoding='utf-8') as f:
                json.dump(error_log, f, ensure_ascii=False, indent=2)
            print(f"✓ 错误日志已保存到: {error_file}")

            # 同时保存为 CSV 格式便于查看
            csv_file = os.path.join(error_log_dir, f"error_log_{timestamp}.csv")
            error_df = pd.DataFrame(error_log)
            error_df.to_csv(csv_file, index=False, encoding='utf-8-sig')
            print(f"✓ 错误日志CSV已保存到: {csv_file}")

        except Exception as e:
            print(f"保存错误日志时出错: {e}")
    else:
        print("✓ 没有错误需要记录")

def process_compressed_file(file_path, plot_code, save_dir):
    """
    处理压缩文件：解压，筛选报告，重命名
    如果找到合适文件，只保留该文件；否则保留整个解压文件夹
    """
    extract_dir = os.path.join(save_dir, f"extracted_{plot_code}")

    try:
        print(f"开始处理压缩文件: {os.path.basename(file_path)}")

        file_size = os.path.getsize(file_path)
        if file_size < 100:
            error_msg = f"文件可能下载不完整，大小: {file_size} 字节"
            print(error_msg)
            log_error(plot_code, error_msg, "file_too_small")
            return file_path

        # 创建解压目录
        if os.path.exists(extract_dir):
            shutil.rmtree(extract_dir)
        os.makedirs(extract_dir)

        # 解压文件
        if extract_file(file_path, extract_dir):
            print(f"解压成功，开始筛选文件")

            # 获取解压后的所有文件
            all_files = []
            for root, dirs, files in os.walk(extract_dir):
                for file in files:
                    file_path_full = os.path.join(root, file)
                    all_files.append(file_path_full)

            print(f"解压后共找到 {len(all_files)} 个文件:")
            for file in all_files:
                print(f"  - {os.path.basename(file)}")

            # 筛选文件
            target_file = filter_and_select_file(extract_dir)

            if target_file:
                original_filename = os.path.basename(target_file)
                print(f"找到目标文件: {original_filename}")

                # 清理文件名中的非法字符和数字前缀
                clean_filename = clean_file_name(original_filename)

                # 移动目标文件到主目录
                new_filename = f"{plot_code}_{clean_filename}"
                new_filepath = os.path.join(save_dir, new_filename)

                # 如果目标文件已存在，先删除
                if os.path.exists(new_filepath):
                    os.remove(new_filepath)

                shutil.move(target_file, new_filepath)
                print(f"✓ 已移动目标文件到主目录: {new_filename}")

                # 找到合适文件时，删除原始压缩文件和解压目录
                os.remove(file_path)
                shutil.rmtree(extract_dir)
                print(f"✓ 已删除原始压缩文件和解压目录")

                return new_filepath
            else:
                # 没有找到符合条件的文件，保留原始压缩文件和解压目录
                error_msg = "未找到符合条件的文件"
                print(error_msg)
                log_error(plot_code, error_msg, "no_suitable_file")
                return file_path  # 返回原始文件路径
        else:
            # 解压失败，保留原始文件
            error_msg = "解压失败"
            print(error_msg)
            log_error(plot_code, error_msg, "extraction_failed")
            if os.path.exists(extract_dir):
                shutil.rmtree(extract_dir)
            return file_path

    except Exception as e:
        error_msg = f"处理压缩文件时出错: {e}"
        print(error_msg)
        log_error(plot_code, error_msg, "process_exception")
        # 出错时尝试清理并保留原始文件
        if os.path.exists(extract_dir):
            shutil.rmtree(extract_dir)
        return file_path

def clean_file_name(filename):
    """
    清理文件名：移除数字前缀、点号和空格，保留中文和正常字符
    """
    # 移除数字前缀和点号（如 "1."、"2." 等）
    cleaned = re.sub(r'^\d+\.', '', filename)

    # 移除文件名开头的空格和特殊字符
    cleaned = cleaned.lstrip(' .-_')

    # 清理文件名中的非法字符（Windows文件名不允许的字符）
    cleaned = re.sub(r'[<>:"/\\|?*]', '_', cleaned)

    # 确保文件名不以点结尾
    cleaned = cleaned.rstrip('.')

    return cleaned

def is_compressed_file(file_path):
    """检查文件是否为支持的压缩格式"""
    compressed_extensions = ['.zip', '.rar', '.7z', '.tar', '.gz', '.bz2']
    return any(file_path.lower().endswith(ext) for ext in compressed_extensions)

def extract_file(file_path, extract_dir):
    """解压文件到指定目录"""
    try:
        if file_path.lower().endswith('.zip'):
            with zipfile.ZipFile(file_path, 'r') as zip_ref:
                # 处理中文文件名编码问题
                for file_info in zip_ref.infolist():
                    try:
                        # 尝试使用UTF-8解码文件名
                        file_info.filename = file_info.filename.encode('cp437').decode('gbk')
                    except:
                        try:
                            # 如果GBK失败，尝试UTF-8
                            file_info.filename = file_info.filename.encode('cp437').decode('utf-8')
                        except:
                            # 如果都失败，保留原始文件名
                            pass
                    zip_ref.extract(file_info, extract_dir)
            return True
        # 修改 extract_file 函数中的 RAR 解压部分
        elif file_path.lower().endswith('.rar'):
            # 添加 RAR 文件解压支持 - 使用更简单直接的方法
            try:
                # 检查文件大小
                file_size = os.path.getsize(file_path)
                if file_size < 100:
                    print(f"RAR 文件可能不完整，大小: {file_size} 字节")
                    return False
                print(f"开始解压 RAR 文件，大小: {file_size} 字节")
                # 使用更简单的方法解压，避免复杂的文件名处理
                with rarfile.RarFile(file_path, 'r') as rar_ref:
                    # 直接使用 extractall，让 rarfile 处理文件名编码
                    rar_ref.extractall(extract_dir)
                    # 检查是否成功解压出文件
                    extracted_files = []
                    for root, dirs, files in os.walk(extract_dir):
                        for file in files:
                            extracted_files.append(os.path.join(root, file))
                    if extracted_files:
                        print(f"成功解压 {len(extracted_files)} 个文件")
                        # 打印前几个文件名以便调试
                        for i, f in enumerate(extracted_files[:3]):
                            print(f"  - {os.path.basename(f)}")
                        if len(extracted_files) > 3:
                            print(f"  - ... 还有 {len(extracted_files) - 3} 个文件")
                        return True
                    else:
                        print("解压成功但未找到文件")
                        return False
            except rarfile.BadRarFile as e:
                print(f"RAR 文件损坏: {e}")
                return False
            except rarfile.NotRarFile as e:
                print(f"不是有效的 RAR 文件: {e}")
                return False
            except rarfile.RarCannotExec as e:
                print(f"无法执行 RAR 解压命令: {e}")
                return False
            except rarfile.RarFatalError as e:
                print(f"RAR 致命错误: {e}")
                return False
            except rarfile.RarWarning as e:
                print(f"RAR 警告: {e}")
                # 即使有警告，也可能解压成功
                try:
                    # 检查解压目录是否有文件
                    if any(os.listdir(extract_dir)):
                        print("尽管有警告，但文件已解压")
                        return True
                    else:
                        return False
                except:
                    return False
            except Exception as e:
                print(f"解压 RAR 文件时未知错误: {e}")
                return False
        elif file_path.lower().endswith('.tar') or file_path.lower().endswith('.tar.gz') or file_path.lower().endswith(
                '.tar.bz2'):
            with tarfile.open(file_path, 'r') as tar_ref:
                tar_ref.extractall(extract_dir)
            return True
        else:
            print(f"不支持的压缩格式: {file_path}")
            return False
    except Exception as e:
        print(f"解压文件时出错: {e}")
        return False

# 另外，我们可以添加一个备用方案：使用系统命令解压
def extract_with_system_command(file_path, extract_dir):
    """使用系统命令解压 RAR 文件（备用方案）"""
    try:
        import subprocess
        import sys
        if sys.platform == 'win32':
            # Windows 系统使用 WinRAR 或 unrar
            try:
                # 尝试使用 WinRAR
                result = subprocess.run([
                    'C:\\Program Files\\WinRAR\\WinRAR.exe',
                    'x', '-y', file_path, extract_dir
                ], capture_output=True, text=True, timeout=60)

                if result.returncode == 0:
                    print("使用 WinRAR 解压成功")
                    return True
            except:
                try:
                    # 尝试使用 unrar
                    result = subprocess.run([
                        'unrar', 'x', '-y', file_path, extract_dir
                    ], capture_output=True, text=True, timeout=60)

                    if result.returncode == 0:
                        print("使用 unrar 解压成功")
                        return True
                except:
                    pass
        else:
            # Linux/Mac 系统
            try:
                result = subprocess.run([
                    'unrar', 'x', '-y', file_path, extract_dir
                ], capture_output=True, text=True, timeout=60)

                if result.returncode == 0:
                    print("使用 unrar 解压成功")
                    return True
            except:
                pass
        return False
    except Exception as e:
        print(f"系统命令解压失败: {e}")
        return False
def filter_and_select_file(directory):
    """
    筛选文件：按照文件名文字匹配进行筛选，从所有文件类型中查找
    """
    all_files = []

    # 收集所有文件
    for root, dirs, files in os.walk(directory):
        for file in files:
            file_path = os.path.join(root, file)
            all_files.append(file_path)

    print(f"找到 {len(all_files)} 个文件:")
    for file_path in all_files:
        print(f"  - {os.path.basename(file_path)}")

    if not all_files:
        print("未找到任何文件")
        return None

    # 第一步：查找所有文档文件（PDF、Word、Excel等）
    document_files = [f for f in all_files if f.lower().endswith(('.pdf', '.doc', '.docx', '.xls', '.xlsx'))]
    if not document_files:
        print("未找到任何文档文件")
        return None

    print(f"找到 {len(document_files)} 个文档文件")

    # 第二步：查找包含"调查"的文件（放宽匹配条件）
    survey_files = []
    for file_path in document_files:
        filename = os.path.basename(file_path)
        if "调查" in filename:
            survey_files.append(file_path)

    if survey_files:
        print(f"找到 {len(survey_files)} 个包含'调查'的文件")

        # 排除包含排除词汇的文件
        valid_survey_files = []
        for file_path in survey_files:
            filename = os.path.basename(file_path)
            if not contains_excluded_words(filename):
                valid_survey_files.append(file_path)

        if valid_survey_files:
            print(f"找到 {len(valid_survey_files)} 个有效的调查报告")
            # 选择文件大小最大的
            valid_survey_files.sort(key=lambda x: os.path.getsize(x), reverse=True)
            selected_file = valid_survey_files[0]
            print(f"选择调查报告: {os.path.basename(selected_file)}")
            return selected_file
        else:
            # 所有调查报告都包含排除词汇，选择文件大小最大的
            print("所有调查报告都包含排除词汇，选择文件大小最大的")
            survey_files.sort(key=lambda x: os.path.getsize(x), reverse=True)
            selected_file = survey_files[0]
            print(f"选择: {os.path.basename(selected_file)}")
            return selected_file

    # 第三步：查找包含"风险"的文件（放宽匹配条件）
    risk_files = []
    for file_path in document_files:
        filename = os.path.basename(file_path)
        if "风险" in filename:
            risk_files.append(file_path)

    if risk_files:
        print(f"找到 {len(risk_files)} 个包含'风险'的文件")

        # 排除包含排除词汇的文件
        valid_risk_files = []
        for file_path in risk_files:
            filename = os.path.basename(file_path)
            if not contains_excluded_words(filename):
                valid_risk_files.append(file_path)

        if valid_risk_files:
            print(f"找到 {len(valid_risk_files)} 个有效的风险评估报告")
            # 选择文件大小最大的
            valid_risk_files.sort(key=lambda x: os.path.getsize(x), reverse=True)
            selected_file = valid_risk_files[0]
            print(f"选择风险评估报告: {os.path.basename(selected_file)}")
            return selected_file
        else:
            # 所有风险评估报告都包含排除词汇，选择文件大小最大的
            print("所有风险评估报告都包含排除词汇，选择文件大小最大的")
            risk_files.sort(key=lambda x: os.path.getsize(x), reverse=True)
            selected_file = risk_files[0]
            print(f"选择: {os.path.basename(selected_file)}")
            return selected_file

    # 第四步：如果都没有，选择最大的文档文件
    print("未找到调查或风险评估相关文件，选择最大的文档文件")
    document_files.sort(key=lambda x: os.path.getsize(x), reverse=True)
    selected_file = document_files[0]
    print(f"选择最大的文档文件: {os.path.basename(selected_file)}")
    return selected_file


def contains_excluded_words(filename):
    """检查是否包含需要排除的词汇"""
    excluded_words = ['简本', '简版', '附件', '申请表', '信息表', '评审', '目录', '摘要', '附图', '附件','专家','意见','质控','函']
    return any(word in filename for word in excluded_words)


if __name__ == "__main__":
>>>>>>> 4073bba (feat:将pdf直接提取，mineru_request,大模型处理.py改为接口)
    download_reports()