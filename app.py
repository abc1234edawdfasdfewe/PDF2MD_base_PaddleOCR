# -*- coding: utf-8 -*-
"""PDF2MD_base_PaddleOCR —— 网页版接口（批量 + 多 Key 并发）。

本地运行：
    pip install -r requirements.txt
    python app.py
然后浏览器访问  http://127.0.0.1:5000

接口：
    GET  /                       首页
    POST /upload                 批量上传 PDF + 多个 API Key，返回 job_id
    GET  /progress/<job_id>      批量 + 逐文件进度（前端轮询）
    GET  /download/<job_id>/<f>  下载单个 Markdown
    GET  /download_all/<job_id>  下载全部（zip）
    POST /cancel/<job_id>        取消整个任务
"""
import io
import os
import re
import time
import uuid
import zipfile
import threading

from flask import (Flask, request, render_template, jsonify,
                   send_file, abort)

from batch import run_batch

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024 * 1024  # 2GB

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
OUTPUT_DIR = os.path.join(BASE_DIR, "outputs")
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

JOBS = {}
JOBS_LOCK = threading.Lock()

_UNSAFE = re.compile(r'[\\/:*?"<>|]+')


def safe_name(name):
    """把输出名里的不安全字符替换掉。"""
    return _UNSAFE.sub("_", name).strip().strip(".") or "output"


def fmt_time(s):
    s = int(s)
    return f"{s // 60}分{s % 60}秒" if s >= 60 else f"{s}秒"


def format_log(e):
    p = e.get("phase")
    if p == "split":
        return f"拆分页面 {e.get('text', '')}"
    if p == "split_done":
        return f"拆分完成：{e.get('total_chunks')} 卷 / {e.get('total_pages')} 页"
    if p == "chunk_start":
        return f"[卷 {e.get('index')}/{e.get('total')}] 页 {e.get('start')}-{e.get('end')}"
    if p == "upload":
        return f"上传中（第 {e.get('attempt')} 次，{e.get('size_mb')}MB）"
    if p == "upload_retry":
        return f"上传重试（{e.get('attempt')}）：{e.get('reason')}"
    if p == "process":
        ex, tot = e.get("extracted"), e.get("total")
        return f"OCR {ex}/{tot} 页" if ex is not None and tot is not None else "OCR 处理中..."
    if p == "job_retry":
        return f"任务重试（{e.get('attempt')}）：{e.get('reason')}"
    if p == "chunk_saved":
        return f"已保存 {e.get('pages')} 页"
    if p == "chunk_done":
        return f"[卷 {e.get('index')}/{e.get('total')}] 完成"
    if p == "combine":
        return "合并 Markdown..."
    if p == "done":
        return f"完成：{e.get('total_pages')} 页"
    if p == "error":
        return f"错误：{e.get('message')}"
    return str(e)


def make_on_event(job_id):
    """构造引擎事件回调：把事件路由到 job 内对应文件，并更新进度。"""
    def on_event(task_id, event):
        line = format_log(event)
        phase = event.get("phase")
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if not job:
                return
            f = next((x for x in job["files"] if x["id"] == task_id), None)
            if not f:
                return
            f["log"].append(line)
            f["last_line"] = line
            if phase == "split_done":
                f["total_pages"] = event.get("total_pages")
                f["total_chunks"] = event.get("total_chunks")
            elif phase == "chunk_start":
                f["current_chunk"] = event.get("index")
                f["chunk_extracted"] = 0
                f["chunk_total"] = 0
            elif phase == "process":
                if event.get("extracted") is not None:
                    f["chunk_extracted"] = event.get("extracted")
                if event.get("total") is not None:
                    f["chunk_total"] = event.get("total")
            elif phase == "chunk_done":
                f["chunks_done"] = event.get("index")
            elif phase == "done":
                f["status"] = "done"
                f["output_file"] = event.get("output_file")
                f["total_pages"] = event.get("total_pages")
                job["done_count"] += 1
            elif phase == "error":
                f["status"] = "failed"
                f["error"] = event.get("message")
                job["fail_count"] += 1
            # 计算该文件百分比
            f["pct"] = compute_pct(f)
    return on_event


def compute_pct(f):
    if f["status"] == "done":
        return 100
    if f["total_chunks"]:
        done = f["chunks_done"]
        if f["chunk_total"]:
            done += f["chunk_extracted"] / f["chunk_total"]
        return round(min(99, done / f["total_chunks"] * 100))
    return 0


def run_job_thread(job_id, tasks, api_keys):
    on_event = make_on_event(job_id)

    def on_start(task_id, engine):
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if job:
                job["engines"][task_id] = engine
                # 标记为 running（首次拿到引擎时）
                f = next((x for x in job["files"] if x["id"] == task_id), None)
                if f and f["status"] == "pending":
                    f["status"] = "running"

    def cancel_check():
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            return bool(job and job["status"] == "cancelled")

    try:
        run_batch(tasks, api_keys, on_event=on_event,
                  on_start=on_start, cancel_check=cancel_check)
    finally:
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if job:
                # 任务结束（含取消）：未被标记的 pending/running 文件视为取消
                for f in job["files"]:
                    if f["status"] in ("pending", "running"):
                        f["status"] = "cancelled"
                if job["status"] == "running":
                    job["status"] = "done" if job["fail_count"] == 0 else (
                        "failed" if job["done_count"] == 0 else "partial"
                    )


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/upload", methods=["POST"])
def upload():
    # ---- 解析 API Keys（每行一个）----
    raw_keys = request.form.get("api_keys", "").splitlines()
    api_keys = [k.strip() for k in raw_keys if k.strip()]
    if not api_keys:
        return jsonify({"error": "请至少填写一个 API Key"}), 400

    # ---- 收集 PDF 文件 ----
    files = [f for f in request.files.getlist("file")
             if f and (f.filename or "").lower().endswith(".pdf")]
    if not files:
        return jsonify({"error": "请至少选择一个 PDF 文件"}), 400

    # ---- 命名方式 ----
    naming = request.form.get("naming", "filename")
    custom = (request.form.get("custom_name") or "").strip()
    try:
        pages_per_chunk = max(5, int(request.form.get("pages_per_chunk") or 30))
    except ValueError:
        pages_per_chunk = 30

    job_id = uuid.uuid4().hex[:12]
    job_upload_dir = os.path.join(UPLOAD_DIR, job_id)
    job_output_dir = os.path.join(OUTPUT_DIR, job_id)
    os.makedirs(job_upload_dir, exist_ok=True)
    os.makedirs(job_output_dir, exist_ok=True)

    # ---- 构建任务列表 ----
    tasks = []
    file_entries = []
    for i, f in enumerate(files):
        fid = f"f{i}"
        base = os.path.splitext(f.filename)[0]
        if naming == "custom" and custom:
            name = custom if len(files) == 1 else f"{custom}_{i + 1}"
        else:
            name = base
        name = safe_name(name)

        src = os.path.join(job_upload_dir, f"{fid}.pdf")
        f.save(src)

        out_dir = os.path.join(job_output_dir, fid)
        os.makedirs(out_dir, exist_ok=True)

        task = {
            "id": fid,
            "pdf_path": src,
            "book_name": name,
            "output_dir": out_dir,
            "pages_per_chunk": pages_per_chunk,
        }
        tasks.append(task)
        file_entries.append({
            "id": fid, "name": name, "filename": f.filename,
            "status": "pending", "pct": 0, "total_pages": None,
            "total_chunks": None, "current_chunk": 0, "chunks_done": 0,
            "chunk_extracted": 0, "chunk_total": 0,
            "output_file": None, "error": None,
            "last_line": "排队中…", "log": [],
        })

    concurrency = min(len(files), len(api_keys))
    with JOBS_LOCK:
        JOBS[job_id] = {
            "status": "running",
            "concurrency": concurrency,
            "total": len(files),
            "done_count": 0,
            "fail_count": 0,
            "start_time": time.time(),
            "files": file_entries,
            "engines": {},
        }

    t = threading.Thread(
        target=run_job_thread,
        args=(job_id, tasks, api_keys),
        daemon=True,
    )
    t.start()

    return jsonify({
        "job_id": job_id,
        "concurrency": concurrency,
        "total": len(files),
        "files": [{"id": f["id"], "name": f["name"]} for f in file_entries],
    })


@app.route("/progress/<job_id>")
def progress(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return jsonify({"error": "任务不存在或已过期"}), 404
        elapsed = time.time() - job["start_time"]
        files_out = [{
            "id": f["id"], "name": f["name"], "status": f["status"],
            "pct": f["pct"], "total_pages": f["total_pages"],
            "total_chunks": f["total_chunks"],
            "chunks_done": f["chunks_done"], "current_chunk": f["current_chunk"],
            "output_file": f["output_file"], "error": f["error"],
            "last_line": f["last_line"],
        } for f in job["files"]]
        return jsonify({
            "status": job["status"],
            "concurrency": job["concurrency"],
            "total": job["total"],
            "done_count": job["done_count"],
            "fail_count": job["fail_count"],
            "elapsed": round(elapsed, 1),
            "elapsed_text": fmt_time(elapsed),
            "files": files_out,
        })


@app.route("/download/<job_id>/<file_id>")
def download_one(job_id, file_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        entry = None
        if job:
            entry = next((x for x in job["files"] if x["id"] == file_id), None)
        path = entry["output_file"] if entry and entry["output_file"] else None
        name = entry["name"] if entry else "output"
    if not path or not os.path.exists(path):
        abort(404)
    return send_file(path, as_attachment=True, download_name=f"{name}.md")


@app.route("/download_all/<job_id>")
def download_all(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            abort(404)
        entries = [(f["name"], f["output_file"]) for f in job["files"]
                   if f["output_file"] and os.path.exists(f["output_file"])]
    if not entries:
        abort(404)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        used = set()
        for name, path in entries:
            arc = f"{name}.md"
            # 避免重名
            n, k = arc, 1
            while n in used:
                n = f"{name}_{k}.md"
                k += 1
            used.add(n)
            zf.write(path, n)
    buf.seek(0)
    return send_file(buf, as_attachment=True,
                     download_name=f"{job_id}_markdown.zip",
                     mimetype="application/zip")


@app.route("/cancel/<job_id>", methods=["POST"])
def cancel(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return jsonify({"error": "任务不存在"}), 404
        if job["status"] != "running":
            return jsonify({"status": job["status"]})
        job["status"] = "cancelled"
        # 中止已运行的引擎（在下一个检查点退出）；排队中的由 cancel_check 跳过
        for engine in job["engines"].values():
            try:
                engine.cancel()
            except Exception:
                pass
    return jsonify({"status": "cancelled"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
