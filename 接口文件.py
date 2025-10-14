# 流程调用接口：
# curl -X POST 'http://10.100.3.110:8086/v1/chat-messages' \
# --header 'Authorization: Bearer {app-md1yrhoCIcNSYVqZCLK9oAWA}' \
# --header 'Content-Type: application/json' \
# --data-raw '{
#     "inputs": {},
#     "query": "start",
#     "response_mode": "streaming",
#     "conversation_id": "",
#     "user": "abc-123",
#     "files": [
#       {
#         "type": "document",
#         "transfer_method": "local_file",
#         "upload_file_id": ""
#       }
#     ]
# }'
#
# 上传文件接口：
# curl -X POST 'http://10.100.3.110:8086/v1/files/upload' \
# --header 'Authorization: Bearer {app-md1yrhoCIcNSYVqZCLK9oAWA}' \
# --form 'file=@localfile;type=document/[PDF|DOCX] \
# --form 'user=abc-123'