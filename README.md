# 📄 PDF2MD_base_PaddleOCR

把 PDF（尤其是大本扫描书）批量解析为 **Markdown** 的工具，底层调用 [PaddleOCR-VL](https://www.paddleocr.ai) 云端 API。提供 **网页版**（批量上传、多 API Key 自动并发、实时进度、打包下载）与 **命令行版** 两种用法。

针对大文件做了专门优化：**自动分卷上传 + 失败重试 + 多 Key 并发 + 结果合并**，实测可稳定处理 500MB+ / 500 页的扫描教材。

---

## ✨ 特性

- 🌐 **网页界面**：拖拽多选上传、实时进度、一键下载
- 📚 **批量处理**：一次上传多个 PDF，自动排队逐个/并发解析
- 🔑 **多 Key 并发**：填入多个 API Key，**并发数 = Key 数**，每个并发任务独占一个不同 Key，规避单 Key 限流；单 Key 时退化为逐个顺序处理
- 🏷️ **命名方式**：默认使用 PDF 文件名作为输出名，也可自定义（多文件自动加序号）
- 🧩 **大文件分卷**：自动按 N 页/卷切分，规避单文件过大导致上传中断
- 🔁 **自动重试**：上传失败、服务端 500 均自动重试
- 🖼️ **图片处理**：解析结果中的图片自动下载，合并版 Markdown 的图片路径已校正为相对路径
- 📦 **打包下载**：批量完成后可一键下载全部 Markdown（zip）
- 💻 **双模式**：`app.py`（Web）/ `cli.py`（CLI），共用同一引擎

---

## 🚀 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```
> Python 3.9+。依赖：Flask、requests、PyMuPDF。

### 2. 获取 PaddleOCR API Key

登录 [AI Studio](https://aistudio.baidu.com/) → 进入 [PaddleOCR 应用](https://paddleocr.aistudio-app.com/) → 复制 **Access Token**。

### 3a. 网页版

```bash
python app.py
```
浏览器打开 **http://127.0.0.1:5000**：

1. **选择/拖入**一个或多个 PDF
2. **粘贴 API Key**（每行一个；多个 Key 自动并发）
3. **命名方式**：默认用 PDF 文件名，或选「自定义名称」
4. 设置每卷页数 → **开始批量解析**
5. 查看总进度 + 逐文件进度，完成后**单文件下载**或**下载全部 (zip)**

### 3b. 命令行版

```bash
# 单文件单 Key（顺序）
python cli.py book.pdf --api-key TOKEN

# 多文件多 Key（并发数 = Key 数）
python cli.py a.pdf b.pdf c.pdf --api-key K1 --api-key K2 --api-key K3

# Key 也可逗号分隔
python cli.py a.pdf b.pdf --api-key "K1,K2"
```
结果输出到 `outputs/书名/书名.md`。

---

## ⚙️ 并发模型

```
任务队列 [pdf1, pdf2, pdf3, ...]
              │
              ▼
   ┌───────────────────────────┐
   │  Key 池  [K1] [K2] [K3]    │   并发数 = min(文件数, Key数)
   └───────────────────────────┘
        │      │      │          每个 worker 独占一个不同 Key
        ▼      ▼      ▼
     worker  worker  worker  ──► 各自跑 OCREngine（分卷/上传/轮询/合并）
```

- **单 Key**：1 个 worker，PDF **逐个顺序**处理。
- **N 个 Key**：N 个 worker 并发，每个用不同 Key；某文件完成后该 worker 立刻从队列取下一个。
- Key 池保证任意时刻并发任务使用的 Key 互不相同，避免同账号并发限流。

---

## 🏗️ 单个 PDF 的工作流程

```
PDF ─► [分卷] ─► chunk_0000.pdf, chunk_0030.pdf ...
                    │  (逐卷，带重试)
                    ▼  上传 PaddleOCR → 轮询 jobId → 下载 JSONL
              [保存逐页 Markdown + 图片]
                    │  (全部卷完成)
                    ▼
              [合并为单个 书名.md]
```

---

## 📁 项目结构

```
PDF2MD_base_PaddleOCR/
├── app.py              # Flask Web 服务（批量/多Key/进度/zip下载）
├── batch.py            # 批量并发编排（ThreadPoolExecutor + Key 池）
├── ocr_engine.py       # 单 PDF 引擎：分卷、上传、轮询、合并（带进度回调）
├── cli.py              # 命令行入口（批量 + 多 Key）
├── requirements.txt
├── templates/index.html
├── static/{app.js, style.css}
├── uploads/            # 运行时：上传的 PDF（gitignore）
└── outputs/            # 运行时：解析结果（gitignore）
```

---

## 🔌 Web API

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET`  | `/` | 首页 |
| `POST` | `/upload` | `multipart`：`file`（可多个）、`api_keys`（每行一个）、`naming`、`custom_name?`、`pages_per_chunk?` → `{job_id, concurrency, files}` |
| `GET`  | `/progress/<job_id>` | 批量 + 逐文件状态、百分比、最新日志行 |
| `GET`  | `/download/<job_id>/<file_id>` | 下载单个 Markdown |
| `GET`  | `/download_all/<job_id>` | 下载全部 Markdown（zip） |
| `POST` | `/cancel/<job_id>` | 取消整个批量任务 |

---

## ⚙️ 部署建议

- 生产环境用 `waitress`（Windows）/ `gunicorn`（Linux）替代内置服务器。
- 任务状态存内存，重启即丢失；需持久化可接 SQLite/Redis。
- 大文件上传注意反向代理的 `client_max_body_size` 与超时。

---

## ⚠️ 说明

- API Key 仅用于请求 PaddleOCR，不落盘存储。
- 扫描件 OCR 难免少量字符误识（药名、剂量、化学式），关键内容请与原书核对。
- 解析速度受上行带宽与服务端排队影响，500 页约需 30–60 分钟。

## 📜 License

MIT
