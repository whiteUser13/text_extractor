# text_extractor
一个基于 Python 的工具，用于从 Word (.doc/.docx) 和 PDF 文件中提取关键内容
## 📦 安装依赖
```bash
pip install -r requirements.txt
```
## 📂 项目文件结构说明
```text
text_extractor/
├── data/
│   ├── uploads/                	# 上传文档目录
│   │	└── upload_file_map.json	# 上传文件映射词典文件
│   ├── downloads/              	# 下载文件目录
│	│	└── download_zip_map.json	# 下载压缩包映射词典文件 
│   └── output/                 	# 解析输出文件目录
│   │   ├── filter_pdf/            	# 使用关键词筛选的pdf目录
│	│	│	├── area_code			# 以地块编码命名的文件夹，同downloads中解压出来的文件夹同名
│   │   │	└── folder_map.json     # 文件夹信息文件
│   │   ├── pdf2md/             	# 将筛选后的pdf转换为md的目录
│	│	│	├── area_code			# 以地块编码命名的文件夹，同downloads中解压出来的文件夹同名
│   │   │	└── folder_map.json     # 文件夹信息文件
│   │   └── results/            	# 将md文档交由ai判定的excel结果表目录,静态资源文件夹，包括子文件夹
│	│		├── area_code			# 以地块编码命名的文件夹，同downloads中解压出来的文件夹同名
│   │    	└── folder_map.json     # 文件夹信息文件
│	└── images						# 转换为markdown格式时生成的图片文件夹
│		└── area_code               # 以地块编码命名的文件夹，同downloads中解压出来的文件夹同名
├── requirements.txt          # 依赖列表
├── README.md                 # 项目说明
└── app.py                    # 启动脚本
```

### 1.流程简介

前端上传地块编码表格文件，前端解析出地块编码
==>后端根据编码下载文件到服务器（data/downloads文件夹，在data/downloads/download_file_map.json记录每个压缩包的信息) 
==>解压文件到downloads文件夹，解压成功就在output/cut_pdf,pdf2md,results创建与压缩包名称相同的文件夹，同时在foler_map.json记录文件信息，对状态status进行初始化
==>根据关键词筛选压缩包里的文件
==>将筛选的文件通过pdf信息直接提取.py进行提取，最后保存到output/cut_pdf/地块编码,
==>将提取的pdf通过mineru——request.py转换为md格式，保存到pdf2md文件夹下面。
==>最后使用提示词将md文件发给大模型处理，生成excel表格，保存到output/resuts/地块编码，需要在results/地块编码文件夹下创建results_file_map.json记录结果。
### 2.上传与下载文件映射文件说明

##### (1)data/downloads/download_file_map.json

用于记录downloads文件夹下的压缩包是否下载解压

```json
{
  "123456789101": {
    "area_code": "123456789101",
    "file_name": "123456789101.zip",
    "origin_name": "北京市大兴区北京建筑大学地块一.zip",
    "folder_name": "北京市大兴区北京建筑大学地块一",
    "size": 321.34,
    "type": "zip",
    "download_time": "2025-10-09 19:56:04",
    "status": "decompressed"
  },
  "1595326646": {
    "area_code": "1595326646",
    "file_name": "1595326646.zip",
    "origin_name": "北京市大兴区北京建筑大学地块二.zip",
    "folder_name": "北京市大兴区北京建筑大学地块二",
    "size": 362.34,
    "type": "zip",
    "download_time": "2025-10-09 19:23:04",
    "status": "decompressed"
  }
}
```

| 字段          | 含义                                                         |
| ------------- | ------------------------------------------------------------ |
| area_code     | 地块编码                                                     |
| file_name     | 压缩包文件名                                                 |
| origin_name   | 源文件名，123456789101.zip为重新命名后的名称                 |
| folder_name   | 文件解压后的文件名                                           |
| size          | 文件大小，kb                                                 |
| type          | 压缩包类型                                                   |
| download_time | 下载时间                                                     |
| status        | 状态，包括undownload未下载 \| downloading下载中 \| downloaded已下载 \| undecompress未解压 \| decompressing解压中 \| decompressed已解压 |

#### Notes:

下载压缩包文件时，是根据对应的地块编码下载的，在下载时需要将压缩包的名称更改为地块编码，同时更改status未downloading,如果下载失败就改为undownload，下载成果改为downloaded，解压失败改为undecompress，已解压改为decompressed，解压时改为decompressing

##### (2)data/output/filter_pdf/folder_map.json

用于记录文件夹下的pdf文件是提取

```json
{
    "123456789101": {
        "area_code": "123456789101",
        "folder_name": "123456789101",
        "origin_folder_name": "北京市大兴区北京建筑大学地块一",
        "update_time": "2025-10-12 21:18:05",
        "status": "extracted"
    },
    "1595326646": {
        "area_code": "1595326646",
        "foleder_name": "1595326646",
        "origin_folder_name": "北京市大兴区北京建筑大学地块二",
        "update_time": "2025-10-09 19:23:04",
        "status": "unextracted"
    }
}
```

| 字段               | 含义                                                         |
| ------------------ | ------------------------------------------------------------ |
| area_code          | 地块编码                                                     |
| folder_name        | 文件夹名称                                                   |
| origin_folder_name | 源文件名                                                     |
| update_time        | 更新时间                                                     |
| status             | 状态，包括extracted已提取 \| extracting提取中 \| unextracted未提取 |

#### Notes:

提取操作执行时，需要修改状态为extracting，提取完成改为extracted，失败改为unextracted

##### (3)data/output/pdf2md/folder_map.json

用于记录文件夹下的pdf文件是否转换为markdown文件

```json
{
    "123456789101": {
        "area_code": "123456789101",
        "folder_name": "北京市大兴区北京建筑大学地块一",
        "update_time": "2025-10-13 16:42:12",
        "status": "converted"
    }
}
```

| 字段        | 含义                                                         |
| ----------- | ------------------------------------------------------------ |
| area_code   | 地块编码                                                     |
| folder_name | 文件夹名称                                                   |
| update_time | 更新时间                                                     |
| status      | 状态，包括converted已转换 \| converting转换中 \| unconvert未转换 |

#### Notes:

转换操作执行时，需要修改状态为converting，转换完成改为converted，失败改为unconvert

##### (4)data/output/results/folder_map.json

用于记录文件夹是否判别

```json
{
    "123456789101": {
        "area_code": "123456789101",
        "folder_name": "北京市大兴区北京建筑大学地块一",
        "update_time": "2025-10-09 19:56:04",
        "status": "judged"
    }
}
```

| 字段        | 含义                                                     |
| ----------- | -------------------------------------------------------- |
| area_code   | 地块编码                                                 |
| folder_name | 文件夹名称                                               |
| update_time | 更新时间                                                 |
| status      | 状态，包括judged未判别 \| judging判别中 \| unjudge判别中 |

#### Notes:

判别操作执行时，需要修改状态为judging，判别完成改为judged，失败改为unjudge
