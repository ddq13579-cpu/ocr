# 文档智能整理系统

通用的本地文档结构化工具：上传 PDF 或图片后，Worker 从 PDF 提取文字或使用阿里云 OCR 识别图片；Google Gemini 按用户定义模板提取严格 JSON，并保存到 SQLite，可在网页修改与导出 Excel。

## 架构

- **nginx**：唯一的 Web 入口，提供 Vue 静态文件并将 `/api/` 代理至 backend。
- **backend**：FastAPI API、SQLite 初始化、文件安全保存、模板/结果管理与 Excel 导出。
- **worker**：轮询 pending 文档，执行 PyMuPDF、阿里云 OCR 和 Gemini。

关系：`templates 1--n template_fields`，`documents 1--1 ocr_results`，`documents n--1 templates`，`documents + templates 1--1 records`，`documents 1--n processing_logs`。

## 启动

```bash
cp .env.example .env
# 在 .env 中填写 GOOGLE_API_KEY、ALIBABA_CLOUD_ACCESS_KEY_ID 和 ALIBABA_CLOUD_ACCESS_KEY_SECRET
docker compose up -d --build
docker compose ps
```

访问 <http://localhost:6868>。查看处理日志：

```bash
docker compose logs -f worker
```

停止服务：`docker compose down`。持久化数据在 [data/uploads](./data/uploads)、[data/database](./data/database) 和 [data/exports](./data/exports)。

## 使用

1. 在“模板管理”创建模板并配置字段名称、JSON `field_key`、类型、是否必填和说明。
2. 返回“文件处理”，选择模板，选择文件或使用目录选择器；拖放文件时保留浏览器提供的相对路径。
3. 点击“开始处理”。PDF 只通过 PyMuPDF 提取前两页的嵌入文字；没有文字的扫描 PDF 会失败（V1 不会隐式 OCR PDF）。图片由阿里云 OCR 处理。
4. 在“处理结果”查看自动刷新的状态、受控的原文件链接与原始文字，选择结构化记录进行修改，或按当前选择的模板导出 Excel。失败的文件可在修复模板或配置后点击“重新处理”；非处理中任务可点击“删除任务”，同时删除上传原文件、OCR 文字、结构化结果和处理日志。

上传以 SHA-256 去重，重复的文件不会重新 OCR 或调用 Gemini。仅支持 PDF、JPG/JPEG、PNG、WEBP、BMP、TIF/TIFF、HEIC；其他文件会跳过。所有原始文件只经受控 API 访问，上传路径会拒绝路径穿越。

## 依赖说明

Worker 通过阿里云 OCR API 2021-07-07 的 `RecognizeGeneral` 直接发送图片二进制数据，不使用 OSS 或本地 OCR 模型。每个待处理批次先以 1 条并发（`OCR_CONCURRENCY`）完成所有 OCR，再以 20 条并发（`GEMINI_CONCURRENCY`）调用 Gemini；OCR 并发仅用于限制云 API 请求数量。HEIC 图片会在内存外的临时文件中转换为 PNG，以保持既有支持的上传格式，其余图片直接发送。Worker 每 0.25 秒轮询一次待处理任务，降低上传后的排队延迟。Gemini 默认模型为 `gemini-3.5-flash-lite`，使用 Google 官方 `google-genai` SDK 的 `response_schema` 和 `application/json` 响应类型；针对瞬时 SSL 连接中断会自动有限次退避重试。API Key 仅从环境变量读取，不应提交 `.env`。
