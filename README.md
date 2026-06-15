# 📄 PDF2MD_base_PaddleOCR

把 PDF（尤其是大本扫描书）解析为 **Markdown** 的工具，底层调用 [PaddleOCR-VL](https://www.paddleocr.ai) 云端 API。提供 **网页版**（浏览器上传、填写 API Key、实时查看进度、下载结果）与 **命令行版** 两种用法。

针对大文件做了专门优化：**自动分卷上传 + 失败重试 + 断点续传 + 结果合并**，实测可稳定处理 500MB+ / 500 页的扫描教材。

---

## ✨ 特性

- 🌐 **网页界面**：拖拽上传 PDF、填写 API Key、实时进度条与日志、一键下载
- 🧩 **大文件分卷**：自动按 N 页/卷切分，规避单文件过大导致上传中断
- 🔁 **自动重试**：上传失败、服务端 500 均自动重试（可配置次数）
- ▶️ **断点续传**：CLI 版记录已完成分卷，中断后重跑自动跳过
- 🖼️ **图片处理**：解析结果中的图片自动下载，合并版 Markdown 的图片路径已校正为相对路径，可正常显示
- 💻 **双模式**：`app.py`（Web）/ `cli.py`（CLI），共用同一引擎 `ocr_engine.py`

---

## 🚀 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

> 需要 Python 3.9+。依赖：Flask、requests、PyMuPDF。

### 2. 获取 PaddleOCR API Key

1. 登录 [AI Studio](https://aistudio.baidu.com/)
2. 进入 [PaddleOCR 应用](https://paddleocr.aistudio-app.com/)，创建/查看你的应用
3. 复制 **Access Token**（即 API Key），形如 `20fe0f98...`

### 3a. 网页版

```bash
python app.py
```

浏览器打开 **http://127.0.0.1:5000** ：

1. 选择 / 拖入 PDF
2. 粘贴 API Key
3. （可选）填写书名、每卷页数
4. 点击 **开始解析**，实时查看进度与日志
5. 完成后点击 **下载 Markdown**

### 3b. 命令行版

```bash
python cli.py "C:\path\to\book.pdf" --api-key YOUR_TOKEN --book 书名 --pages 30
```

结果输出到 `outputs/书名/书名.md`，每个分卷的逐页结果在 `outputs/书名/chunk_XXXX/`。

---

## 🏗️ 工作原理

```
PDF ─► [1. 分卷] ─► chunk_0000.pdf, chunk_0030.pdf, ...
                        │
                        ▼ (逐卷，带重试)
                   [2. 上传到 PaddleOCR API]
                        │
                        ▼ (轮询 jobId)
                   [3. 下载 JSONL 结果]
                        │
                        ▼
                   [4. 保存逐页 Markdown + 图片]
                        │
                        ▼ (全部卷完成)
                   [5. 合并为单个 书名.md]
```

- **分卷大小**：默认 30 页/卷。扫描件约 1.5MB/页时单卷 ~50MB，能稳定上传。可在网页/CLI 调整。
- **进度回调**：引擎通过 `on_progress` 事件向外推送阶段（拆分/上传/处理/保存/合并），Web 端写入内存供前端轮询，CLI 端直接打印。

---

## 📁 项目结构

```
PDF2MD_base_PaddleOCR/
├── app.py                 # Flask Web 服务（上传/进度/下载接口）
├── ocr_engine.py          # 核心引擎：分卷、上传、轮询、合并（带进度回调）
├── cli.py                 # 命令行入口
├── requirements.txt
├── templates/
│   └── index.html         # 上传 + 进度界面
├── static/
│   ├── app.js             # 前端：上传 + 轮询进度
│   └── style.css
├── uploads/               # 运行时：上传的 PDF（已 gitignore）
└── outputs/               # 运行时：解析结果（已 gitignore）
```

---

## 🔌 Web API

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET`  | `/` | 首页 |
| `POST` | `/upload` | `multipart/form-data`：`file`、`api_key`、`book_name?`、`pages_per_chunk?` → 返回 `{job_id}` |
| `GET`  | `/progress/<job_id>` | 返回任务状态、进度、最近 200 行日志 |
| `GET`  | `/download/<job_id>` | 下载合并后的 Markdown |
| `POST` | `/cancel/<job_id>` | 取消运行中的任务 |

---

## ⚙️ 部署建议

本地开发用 Flask 内置服务器即可。**生产/多人使用**建议：

- 用 `waitress`（Windows）或 `gunicorn`（Linux）替代内置服务器：
  ```bash
  pip install waitress
  waitress-serve --port=5000 app:app
  ```
- 任务状态目前存内存，重启即丢失；如需持久化可接入 SQLite / Redis。
- 大文件上传注意反向代理（Nginx）的 `client_max_body_size` 与超时设置。

---

## ⚠️ 说明

- API Key 仅用于请求 PaddleOCR，Web 版不落盘存储；但仍请勿在公共环境暴露服务端口。
- 扫描件 OCR 难免少量字符误识（如药名、剂量、化学式），关键内容请与原书核对。
- 解析速度受上行带宽与服务端排队影响，500 页约需 30–60 分钟。

---

## 📜 License

MIT
