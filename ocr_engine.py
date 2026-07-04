# -*- coding: utf-8 -*-
"""核心 OCR 引擎：调用 PaddleOCR-VL API 将 PDF 解析为 Markdown。

特性：
- 大 PDF 自动分卷上传，规避单文件过大导致的连接中断
- 上传失败 / 服务端处理失败 自动重试
- 进度回调（on_progress），便于 CLI 与 Web 端实时展示
- 最终合并为单个 Markdown，图片路径自动校正为相对路径
"""
import fitz          # PyMuPDF
import json
import os
import requests
import threading
import time

JOB_URL = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
DEFAULT_MODEL = "PaddleOCR-VL-1.6"

# OCR 可选参数：关闭方向分类/去畸变/图表识别，加快纯文档解析
OPTIONAL_PAYLOAD = {
    "useDocOrientationClassify": False,
    "useDocUnwarping": False,
    "useChartRecognition": False,
}

IMG_EXTS = ("jpg", "jpeg", "png", "gif", "bmp", "webp")


class OCREngine:
    """解析单个 PDF 的引擎。

    在调用方线程（通常是后台线程）中调用 ``run()`` 即可完成全流程。
    通过 ``on_progress`` 回调向外推送事件（dict），用于 UI 实时更新。
    """

    def __init__(self, api_key, pdf_path, book_name, output_dir,
                 pages_per_chunk=30, model=DEFAULT_MODEL, on_progress=None,
                 max_upload_retries=3, max_job_retries=3, poll_interval=8):
        self.api_key = api_key
        self.pdf_path = pdf_path
        self.book_name = book_name or os.path.splitext(os.path.basename(pdf_path))[0]
        self.output_dir = output_dir
        self.pages_per_chunk = pages_per_chunk
        self.model = model or DEFAULT_MODEL  # 防御 None/空值导致 model 字段丢失
        self.on_progress = on_progress or (lambda event: None)
        self.max_upload_retries = max_upload_retries
        self.max_job_retries = max_job_retries
        self.poll_interval = poll_interval

        self.chunk_dir = os.path.join(output_dir, "chunks")
        self.combined_md = os.path.join(output_dir, f"{self.book_name}.md")
        self._lock = threading.Lock()
        self._cancelled = False

    # ---------- 进度与取消 ----------
    def _emit(self, **event):
        self.on_progress(event)

    def cancel(self):
        with self._lock:
            self._cancelled = True

    def _check_cancel(self):
        with self._lock:
            if self._cancelled:
                raise RuntimeError("用户已取消任务")

    # ---------- 1. 拆分 PDF ----------
    def split_pdf(self):
        os.makedirs(self.chunk_dir, exist_ok=True)
        doc = fitz.open(self.pdf_path)
        total = doc.page_count
        chunks = []
        for start in range(0, total, self.pages_per_chunk):
            self._check_cancel()
            end = min(start + self.pages_per_chunk, total)
            chunk_path = os.path.join(self.chunk_dir, f"chunk_{start:04d}_{end:04d}.pdf")
            if not os.path.exists(chunk_path):
                self._emit(phase="split", text=f"{start}-{end - 1}")
                new = fitz.open()
                new.insert_pdf(doc, from_page=start, to_page=end - 1)
                new.save(chunk_path, garbage=4, deflate=True)
                new.close()
            chunks.append((start, end, chunk_path))
        doc.close()
        self._emit(phase="split_done", total_pages=total, total_chunks=len(chunks))
        return chunks

    # ---------- 2. 上传单卷（带重试） ----------
    def _submit_job(self, chunk_path):
        headers = {"Authorization": f"bearer {self.api_key}"}
        data = {"model": self.model, "optionalPayload": json.dumps(OPTIONAL_PAYLOAD)}
        last_err = None
        for attempt in range(1, self.max_upload_retries + 1):
            self._check_cancel()
            try:
                size_mb = os.path.getsize(chunk_path) / 1024 / 1024
                self._emit(phase="upload", attempt=attempt, size_mb=round(size_mb, 1))
                with open(chunk_path, "rb") as f:
                    files = {"file": f}
                    resp = requests.post(JOB_URL, headers=headers, data=data,
                                         files=files, timeout=600)
                if resp.status_code == 200:
                    return resp.json()["data"]["jobId"]
                last_err = f"HTTP {resp.status_code}: {resp.text[:200]}"
            except Exception as e:
                last_err = str(e)
            self._emit(phase="upload_retry", attempt=attempt, reason=last_err)
            time.sleep(5 * attempt)
        raise RuntimeError(f"上传失败（{self.max_upload_retries} 次）: {last_err}")

    # ---------- 3. 轮询任务结果 ----------
    def _poll_job(self, job_id):
        headers = {"Authorization": f"bearer {self.api_key}"}
        while True:
            self._check_cancel()
            r = requests.get(f"{JOB_URL}/{job_id}", headers=headers, timeout=60)
            if r.status_code != 200:
                raise RuntimeError(f"轮询失败 HTTP {r.status_code}")
            d = r.json()["data"]
            state = d.get("state")
            if state == "running":
                try:
                    p = d["extractProgress"]
                    self._emit(phase="process",
                               extracted=p.get("extractedPages"),
                               total=p.get("totalPages"))
                except KeyError:
                    self._emit(phase="process")
            elif state == "done":
                return d["resultUrl"]["jsonUrl"]
            elif state == "failed":
                raise RuntimeError(f"服务端处理失败: {d.get('errorMsg')}")
            # pending -> 继续等待
            time.sleep(self.poll_interval)

    # ---------- 4. 下载并保存单卷结果 ----------
    def _save_chunk(self, jsonl_url, start):
        chunk_folder = os.path.join(self.output_dir, f"chunk_{start:04d}")
        os.makedirs(chunk_folder, exist_ok=True)
        r = requests.get(jsonl_url, timeout=120)
        r.raise_for_status()
        lines = [l for l in r.text.strip().split("\n") if l.strip()]
        local_page = 0
        for line in lines:
            result = json.loads(line)["result"]
            for res in result.get("layoutParsingResults", []):
                md_text = res["markdown"]["text"]
                md_file = os.path.join(chunk_folder, f"doc_{local_page}.md")
                with open(md_file, "w", encoding="utf-8") as f:
                    f.write(md_text)
                for img_path, img_url in res["markdown"].get("images", {}).items():
                    full = os.path.join(chunk_folder, img_path)
                    os.makedirs(os.path.dirname(full), exist_ok=True)
                    try:
                        b = requests.get(img_url, timeout=120).content
                        with open(full, "wb") as f:
                            f.write(b)
                    except Exception:
                        pass
                local_page += 1
        self._emit(phase="chunk_saved", pages=local_page)
        return local_page

    # ---------- 5. 合并全书 Markdown ----------
    def _combine(self, chunks):
        self._emit(phase="combine")
        all_pages = []
        for (start, _end, _cp) in chunks:
            folder = os.path.join(self.output_dir, f"chunk_{start:04d}")
            if not os.path.isdir(folder):
                continue
            for fn in sorted(os.listdir(folder)):
                if fn.startswith("doc_") and fn.endswith(".md"):
                    local_idx = int(fn[len("doc_"):-len(".md")])
                    md_text = open(os.path.join(folder, fn), encoding="utf-8").read()
                    all_pages.append((start, local_idx, md_text))
        all_pages.sort(key=lambda x: (x[0], x[1]))

        with open(self.combined_md, "w", encoding="utf-8") as out:
            out.write(f"# {self.book_name}\n\n")
            for gpage, (start, local_idx, md_text) in enumerate(all_pages):
                folder_name = f"chunk_{start:04d}"
                chunk_folder = os.path.join(self.output_dir, folder_name)
                rewritten = md_text
                # 把图片相对路径加上分卷前缀，使合并文件可正确显示图片
                for root, _dirs, files in os.walk(chunk_folder):
                    for fn in files:
                        ext = fn.lower().rsplit(".", 1)[-1] if "." in fn else ""
                        if ext in IMG_EXTS:
                            rel = os.path.relpath(os.path.join(root, fn), chunk_folder).replace("\\", "/")
                            if rel in rewritten:
                                rewritten = rewritten.replace(rel, f"{folder_name}/{rel}")
                out.write(f"\n<!-- Page {gpage} ({folder_name} doc_{local_idx}) -->\n\n")
                out.write(rewritten)
                out.write("\n\n---\n")
        self._emit(phase="done", total_pages=len(all_pages), output_file=self.combined_md)
        return self.combined_md

    # ---------- 主流程 ----------
    def run(self):
        try:
            os.makedirs(self.output_dir, exist_ok=True)
            chunks = self.split_pdf()
            total_chunks = len(chunks)
            for i, (start, end, chunk_path) in enumerate(chunks, 1):
                self._check_cancel()
                self._emit(phase="chunk_start", index=i, total=total_chunks,
                           start=start, end=end - 1)
                jsonl_url = None
                for job_attempt in range(1, self.max_job_retries + 1):
                    try:
                        job_id = self._submit_job(chunk_path)
                        jsonl_url = self._poll_job(job_id)
                        break
                    except Exception as e:
                        if job_attempt == self.max_job_retries:
                            raise
                        self._emit(phase="job_retry", attempt=job_attempt, reason=str(e))
                        time.sleep(15)
                self._save_chunk(jsonl_url, start)
                self._emit(phase="chunk_done", index=i, total=total_chunks)
            self._combine(chunks)
        except Exception as e:
            self._emit(phase="error", message=str(e))
            raise
