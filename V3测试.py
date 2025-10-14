import requests


def DeepSeek_V3_671B(inputs):
    # 定义请求的URL
    url = "http://10.100.240.4:1025/generate"

    # 定义请求头
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json"
    }

    # 定义请求体数据
    data = {
        "inputs": inputs,
        "parameters": {
            "details": True,
            "do_sample": True,
            "max_new_tokens": 10000,
            "repetition_penalty": 1.03,
            "return_full_text": False,
            "seed": None,
            "temperature": 0.1,
            "top_k": 10,
            "top_p": 0.95,
            "truncate": None,
            "typical_p": 0.5,
            "watermark": False
        }
    }

    # 定义SSL证书相关文件路径
    # cert = ("client.pem", "client.key.pem")
    # ca_cert = "ca.pem"

    try:
        # 发送POST请求
        response = requests.post(
            url,
            headers=headers,
            json=data,
            # cert=cert,
            # verify=ca_cert
        )



        # 检查响应状态码
        if response.status_code == 200:
            # 解析返回的JSON数据
            result = response.json()
            # 返回generated_text的内容
            return result.get("generated_text", "")

        else:
            # 如果状态码不是200，打印错误信息
            print(f"Error: Received status code {response.status_code}")
            print(f"Response: {response.text}")
            return None
    except Exception as e:
        # 捕获异常并打印错误信息
        print(f"An error occurred: {e}")
        return None


# 示例调用
if __name__ == "__main__":
    question = "天空为什么是蓝色的?"
    answer = DeepSeek_V3_671B(question)
    print("answer：", answer)