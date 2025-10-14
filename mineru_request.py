import re
from pathlib import Path
import requests
import io,os
import logging
import base64
from io import BytesIO
from filelock import FileLock

# from utils.config import get_base_config

"""
功能是将 PDF 或图片类文件解析成 Markdown 文本（含图片），并上传处理后的图片，再生成最终的 Markdown 文件。
"""
MINERU_URL = "http://10.100.3.110:9087/file_parse"
TOKEN = "app-md1yrhoCIcNSYVqZCLK9oAWA"
# UPLOAD_IMAGE_URL = "http://10.100.3.110:8086/v1/files/upload"
UPLOAD_IMAGE_URL = "http://127.0.0.1:8000"

def parse_file_to_md(file_path, start_page: int = 0, end_page: int = 99999, mineru_url: str = None):
    lock_path = "/tmp/parse_file_to_md.lock"
    with FileLock(lock_path, timeout=180):
        # 设置请求头
        headers = {
            # "Authorization": f"Bearer {Globals.get_jc_token()}",
            'accept': 'application/json',
        }
        # MIME type mapping
        MIME_TYPES = {
            '.pdf': 'application/pdf',
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            # '.tiff': 'image/tiff',
            # '.tif': 'image/tiff'
        }
        data = {
            'return_middle_json': 'false',
            'return_model_output': 'false',
            'return_md': 'true',
            'return_images': 'true',
            'end_page_id': end_page,
            'parse_method': 'auto',
            'start_page_id': start_page,
            'lang_list': 'ch',
            'output_dir': './output',
            'server_url': '',
            'return_content_list': 'false',
            'backend': 'pipeline',
            'table_enable': 'true',
            'formula_enable': 'true'
        }
        logging.info(f'解析文件列表：{file_path}')
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")
        file_suffix = path.suffix.lower()
        mime_type = MIME_TYPES.get(file_suffix, 'application/octet-stream')
        # url = get_base_config('mineru', {}).get('url')
        # if mineru_url is not None:
        url = MINERU_URL
        logging.info(f'mineru,url：{url}')
        try:
            with open(file_path, 'rb') as f:
                # 使用三元组格式：(filename, fileobj, content_type)
                files = {'files': (path.name, f, mime_type)}
                response = requests.post(url, files=files, headers=headers, data=data)
            if response.status_code == 200 and response.json().get('results'):
                logging.info("文档解析成功！")
                return response.json().get('results')
            else:
                logging.error(f"解析文档失败: {response.text}")
                return None
        except Exception as e:
            logging.error(f"解析文档失败: {str(e)}")
            return f"解析文档失败: {str(e)}"
    
def process_md_images(md_content, doc_id):
    """
    解析 Markdown 文件，找到并替换所有图片链接
    
    :param md_content: 输入的 Markdown 
    :param output_file_path: 输出的 Markdown 文件路径，如果为 None 则覆盖原文件
    :param doc_id: 文档ID
    :return: 修改后的 Markdown 内容
    """
    
    # 定义 Markdown 图片的正则表达式
    # 匹配 ![alt text](url) 和 <img> 标签
    img_pattern = re.compile(
        r'!\[.*?\]\((.*?)\)|<\s*img\s+.*?src\s*=\s*["\'](.*?)["\'].*?>',
        re.IGNORECASE
    )
    
    # 找到所有匹配的图片链接
    matches = img_pattern.finditer(md_content)
    
    # 收集所有原始链接
    original_links = []
    for match in matches:
        # 第一种形式：![alt](url) 的链接在 group(1)
        # 第二种形式：<img src="url"> 的链接在 group(2)
        link = match.group(1) if match.group(1) is not None else match.group(2)
        original_links.append((match.start(), match.end(), link))
    
    # 从后向前替换，避免影响位置索引
    modified_content = md_content
    for start, end, original_link in reversed(original_links):
        # 获取图片的替代文本或整个标签
        img_context = md_content[start:end]
        
        # 替换链接
        filename = Path(original_link).name
        # new_link = f"{get_base_config('knowledge', {}).get('url')}/api/v1/documents/{doc_id}/images/{filename}"
        # new_link = f"http://10.100.3.110:8086/api/v1/documents/{doc_id}/images/{filename}"
        new_link = f"{UPLOAD_IMAGE_URL}/documents/images/{doc_id}/{filename}"
        # 替换内容
        if img_context.startswith('!['):
            # Markdown 图片语法 ![alt](url)
            alt_text = img_context[2:img_context.find(']')]
            replacement = f'![{alt_text}]({new_link})'
        else:
            # HTML <img> 标签
            replacement = img_context.replace(original_link, new_link)
        
        modified_content = modified_content[:start] + replacement + modified_content[end:]

    return modified_content

def upload_base64_image(images_dict, doc_id):
    """
    将 Base64 Data URL 图片转换为二进制并上传
    
    :param images_dict: 图片字典
    :param doc_id: 文档ID
    """
    files = []
    # 遍历所有图片
    for image_name, base64_data_url in images_dict.items():
        logging.info(f"Processing image: {image_name}")
        # 1. 从 Data URL 中提取 Base64 数据部分
        # 格式: "data:image/jpeg;base64,<actual_base64_data>"
        mime_type, base64_str = base64_data_url.split(",", 1)  # 分割头部和实际数据
        # 2. 解码 Base64 为二进制
        binary_data = base64.b64decode(base64_str)
        # 提取 "image/jpeg"
        mime_type = mime_type.split(":")[1].split(";")[0]
        # 3. 创建文件对象（模拟 open(file_path, 'rb')）
        file_like_object = BytesIO(binary_data)
        # 4. 构建 multipart/form-data 请求
        files.append(('images', (image_name, file_like_object, mime_type)))

    # 5. 发送请求
    # url = f"{get_base_config('knowledge', {}).get('url')}/api/v1/documents/{doc_id}/image"
    url = f"http://10.100.3.110:8086/api/v1/documents/{doc_id}/image"
    # url = UPLOAD_IMAGE_URL
    headers = {
        "Authorization": f"Bearer {TOKEN}",
    }
    try:
        response = requests.post(url, files=files, headers=headers)
        if response and response.json().get('code') == 0:
            logging.info("图片上传成功！")
            print("服务器响应:", response.json())
        else:
            logging.error(f"上传失败: {response.text}")
    except Exception as e:
        logging.error(f"上传过程中发生错误: {str(e)}")
    finally:
        # 确保关闭所有打开的文件句柄
        for _, file_tuple in files:
            if isinstance(file_tuple[1], io.IOBase):
                file_tuple[1].close()

def save_base64_images_locally(images_dict, output_dir="data/images"):
    """
    将 Base64 Data URL 图片保存到本地文件夹

    :param images_dict: 图片字典 {"image_name.jpg": "data:image/jpeg;base64,xxxx"}
    :param output_dir: 本地保存目录
    """
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        logging.info(f"创建目录: {output_dir}")

    for image_name, base64_data_url in images_dict.items():
        try:
            logging.info(f"保存图片: {image_name}")
            # 分割头部和 Base64 数据
            header, base64_str = base64_data_url.split(",", 1)
            binary_data = base64.b64decode(base64_str)
            # 保存到本地文件
            file_path = os.path.join(output_dir, image_name)
            with open(file_path, "wb") as f:
                f.write(binary_data)
            logging.info(f"图片保存成功: {file_path}")
        except Exception as e:
            logging.error(f"图片 {image_name} 保存失败: {e}")

def parse_file(file_path, output_file, doc_id,image_path, start_page: int = 0, end_page: int = 99999, mineru_url: str = None):
    """
    解析文件并返回解析结果

    :param file_path: 文件路径
    :param doc_id: 文档ID
    :return: 保存的文件路径
    """
    try:
        # 解析文件
        result = parse_file_to_md(file_path, start_page, end_page, mineru_url)
        if result is None:
            print("解析文档失败")
            return None
        first_result = next(iter(result.values()))

        md_content = first_result.get('md_content', '')
        images = first_result.get('images', {})
        # 使用keys()方法获取所有键
        keys = images.keys()

        # 将键的视图对象转换为列表
        keys_list = list(keys)

        # 打印键列表
        print(keys_list)
        # 获取文件名
        path = Path(file_path)

        res_md_content = md_content
        print(images and len(images) > 0)
        if images and len(images) > 0:
            # 处理md文件
            # res_md_content = process_md_images(md_content, images, file_name=path.stem, doc_id=doc_id)
            res_md_content = process_md_images(md_content, doc_id=doc_id)
            # 上传图片
            # upload_base64_image(images, doc_id)
            # 保存图片
            save_base64_images_locally(images,image_path)

        # 确定输出路径
        # output_file_path = os.path.join(get_base_config('knowledge', {}).get('doc_temp_path'), 'output', path.stem + '.md')
        # 写入文件
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(res_md_content)
        
        return True
    except Exception as e:
        logging.error(f"解析文件失败: {str(e)}")
        return str(e)
    
# if __name__ == '__main__':
#     file = "D:\\PostgraduateStudy\\env\\text_extractor-main\\test\\福贡县消防救援站建设项目_初步调查_报告_地块信息提取.pdf"
#     output_path = "D:\\PostgraduateStudy\\env\\text_extractor-main\\data\\output\\pdf2md\\1.md"
#     doc_id = "test222"
#     parse_file_to_md(file,output_path,doc_id)

