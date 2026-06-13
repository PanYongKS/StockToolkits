# StockToolkits

一个用于 A 股数据处理的小工具仓库。当前包含：

1. 从巨潮资讯网批量下载财报 PDF。
2. 通过配置文件控制下载财报种类、公司名单、下载目录等参数。

## 功能说明

- 数据来源：巨潮资讯网（cninfo.com.cn）公告接口。
- 支持报告类型：年报、半年报、一季报、三季报。
- 支持公司配置：公司名称 + 可选股票代码。
- 支持目录配置：目标下载路径可配置。
- 下载文件名保留原始名称：使用巨潮返回的 PDF 原始文件名。
- 按公司分目录保存：每家公司保存到独立子目录。
- 自动生成下载索引：每个公司目录下生成 `download_index.csv`，记录标题、日期、链接等信息。
- 自动跳过已下载文件：可重复执行，便于增量下载。
- 公司级严格过滤：下载前按 `secCode/secName` 二次校验，避免混入其他公司公告。

## 快速开始

1. 安装依赖：

```bash
pip install -r requirements.txt
```

2. 复制示例配置并修改：

```bash
cp config.example.json config.json
```

3. 执行下载：

```bash
python3 cninfo_report_downloader.py --config config.json
```

## 配置文件说明

示例见 [config.example.json](config.example.json)。

- output_dir：下载目录。
- date_range：公告日期区间，格式为 `YYYY-MM-DD~YYYY-MM-DD`。如不设置（缺失/空字符串）或设置为 `all` / `*`，表示下载所有年份。
- report_types：报告类型列表，可选值：
	- annual
	- semiannual
	- q1
	- q3
	- all（下载全部类型）
- companies：公司列表，支持两种写法：
	- 仅名称：`"平安银行"`
	- 名称+代码：`{"name": "平安银行", "code": "000001"}`
- columns：交易所范围，通常保持默认 `["szse", "sse"]`。
- page_size：每页抓取条数，建议不超过 30（过大可能导致巨潮接口翻页重复）。
- max_pages：每个公司每种报告最多翻页数。
- max_per_company_per_type：每个公司每种报告最多下载数量。
- latest_only：是否只下载每家公司每种报告的最新一份。`true` 时每种报告最多下载 1 份。
- request_timeout_seconds：请求超时时间（秒）。
- interval_seconds：每次下载后等待秒数，降低请求频率。
- exclude_title_keywords：标题包含这些关键词时跳过（默认含“摘要”）。

下载目录结构示例：

```text
downloads/reports/
  000001_平安银行/
    2025年年度报告.pdf
		download_index.csv
  600519_贵州茅台/
    2024年年度报告.pdf
		download_index.csv
```

`download_index.csv` 字段包括：下载时间、公司代码、公司名称、报告类型、公告日期、公告标题、公告链接、文件名、文件路径等。

## 免责声明

- 本工具仅用于学习与研究，请遵守目标网站服务条款及相关法律法规。
- 下载请求频率不宜过高，建议保留间隔配置，避免对目标站点造成压力。
