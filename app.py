# -*- coding: utf-8 -*-
"""PDF2MD_base_PaddleOCR —— 网页版接口。

本地运行：
    pip install -r requirements.txt
    python app.py
然后浏览器访问  http://127.0.0.1:5000

接口：
    GET  /                    首页（上传 + 进度界面）
    POST /upload              上传 PDF + API Key，返回 job_id
    GET  /progress/<job_id>   查询任务进度（前端轮询）
    GET  /download/<job_id>   下载合并后的 Markdown
    POST /cancel/<job_id>     取消任务
"""
import os
import time
import uuid
import threading

from flask import (Flask, request, render_template, jsonify,
                   send_file, abort)

from ocr_engine import OCREngine

app = Flask(__name__)
# 允许大文件上传（最大 2GB）
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024 * 1024

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
OUTPUT_DIR = os.path.join(BASE_DIR, "outputs")
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

JOBS = {}            # job_id -> 状态 dict
JOBS_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# 进度回调
# ---------------------------------------------------------------------------
def update_job(job_id, event):
    """引擎事件 -> 更新 JOBS[job_id] 并追加日志行。"""
    phase = event.get("phase")
    log_line = format_log(event)
    with JOBS_LOCK:
        j = JOBS.get(job_id)
        if j is None:
            return
        j["events"].append(event)
        j["log"].append(log_line)
        if phase == "split_done":
            j["total_pages"] = event.get("total_pages")
            j["total_chunks"] = event.get("total_chunks")
        elif phase == "chunk_start":
            j["current_chunk"] = event.get("index")
            j["chunk_extracted"] = 0
            j["chunk_total"] = 0
        elif phase == "process":
            if event.get("extracted") is not None:
                j["chunk_extracted"] = event.get("extracted")
            if event.get("total") is not None:
                j["chunk_total"] = event.get("total")
        elif phase == "chunk_done":
            j["chunks_done"] = event.get("index")
        elif phase == "done":
            j["status"] = "done"
            j["output_file"] = event.get("output_file")
            j["total_pages"] = event.get("total_pages")
        elif phase == "error":
            j["status"] = "failed"
            j["error"] = event.get("message")


def format_log(e):
    p = e.get("phase")
    if p == "split":
        return f"拆分页面 {e.get('text', '')}"
    if p == "split_done":
        return f"拆分完成：共 {e.get('total_chunks')} 卷 / {e.get('total_pages')} 页"
    if p == "chunk_start":
        return f"[第 {e.get('index')}/{e.get('total')} 卷] 开始处理（页 {e.get('start')}-{e.get('end')}）"
    if p == "upload":
        return f"    上传中（第 {e.get('attempt')} 次，{e.get('size_mb')}MB）..."
    if p == "upload_retry":
        return f"    上传出错，将重试（{e.get('attempt')}）：{e.get('reason')}"
    if p == "process":
        ex, tot = e.get("extracted"), e.get("total")
        if ex is not None and tot is not None:
            return f"    OCR 处理中 {ex}/{tot} 页"
        return "    OCR 处理中..."
    if p == "job_retry":
        return f"    任务失败，将重试（{e.get('attempt')}）：{e.get('reason')}"
    if p == "chunk_saved":
        return f"    已保存 {e.get('pages')} 页"
    if p == "chunk_done":
        return f"[第 {e.get('index')}/{e.get('total')} 卷] 完成 ✓"
    if p == "combine":
        return "正在合并全书 Markdown..."
    if p == "done":
        return f"✅ 完成！共 {e.get('total_pages')} 页 -> {os.path.basename(e.get('output_file', ''))}"
    if p == "error":
        return f"❌ 错误：{e.get('message')}"
    return str(e)


# ---------------------------------------------------------------------------
# 后台执行
# ---------------------------------------------------------------------------
def run_job(job_id, api_key, pdf_path, book_name, pages_per_chunk):
    output_dir = os.path.join(OUTPUT_DIR, job_id)
    engine = OCREngine(
        api_key=api_key,
        pdf_path=pdf_path,
        book_name=book_name,
        output_dir=output_dir,
        pages_per_chunk=pages_per_chunk,
        on_progress=lambda ev: update_job(job_id, ev),
    )
    with JOBS_LOCK:
        JOBS[job_id]["engine"] = engine
    try:
        engine.run()
    except Exception:
        pass  # 错误已通过回调记录


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/upload", methods=["POST"])
def upload():
    api_key = (request.form.get("api_key") or "").strip()
    if not api_key:
        return jsonify({"error": "请填写 API Key"}), 400
    if "file" not in request.files:
        return jsonify({"error": "请选择 PDF 文件"}), 400
    f = request.files["file"]
    if not (f.filename or "").lower().endswith(".pdf"):
        return jsonify({"error": "仅支持 PDF 文件"}), 400

    book_name = (request.form.get("book_name") or "").strip() or os.path.splitext(f.filename)[0]
    try:
        pages_per_chunk = max(5, int(request.form.get("pages_per_chunk") or 30))
    except ValueError:
        pages_per_chunk = 30

    job_id = uuid.uuid4().hex[:12]
    save_dir = os.path.join(UPLOAD_DIR, job_id)
    os.makedirs(save_dir, exist_ok=True)
    # 用固定文件名避免原始文件名中的特殊字符
    pdf_path = os.path.join(save_dir, "source.pdf")
    f.save(pdf_path)

    with JOBS_LOCK:
        JOBS[job_id] = {
            "status": "running",
            "book_name": book_name,
            "filename": f.filename,
            "events": [],
            "log": [],
            "total_pages": None,
            "total_chunks": None,
            "current_chunk": 0,
            "chunks_done": 0,
            "chunk_extracted": 0,
            "chunk_total": 0,
            "output_file": None,
            "error": None,
            "start_time": time.time(),
            "engine": None,
        }

    t = threading.Thread(
        target=run_job,
        args=(job_id, api_key, pdf_path, book_name, pages_per_chunk),
        daemon=True,
    )
    t.start()
    return jsonify({"job_id": job_id, "book_name": book_name})


@app.route("/progress/<job_id>")
def progress(job_id):
    with JOBS_LOCK:
        j = JOBS.get(job_id)
        if j is None:
            return jsonify({"error": "任务不存在或已过期"}), 404
        elapsed = time.time() - j["start_time"]
        return jsonify({
            "status": j["status"],
            "book_name": j["book_name"],
            "total_pages": j["total_pages"],
            "total_chunks": j["total_chunks"],
            "current_chunk": j["current_chunk"],
            "chunks_done": j["chunks_done"],
            "chunk_extracted": j["chunk_extracted"],
            "chunk_total": j["chunk_total"],
            "error": j["error"],
            "output_file": j["output_file"],
            "elapsed": round(elapsed, 1),
            "log": j["log"][-200:],
        })


@app.route("/download/<job_id>")
def download(job_id):
    with JOBS_LOCK:
        j = JOBS.get(job_id)
        path = j["output_file"] if j else None
    if not path or not os.path.exists(path):
        abort(404)
    return send_file(path, as_attachment=True, download_name=os.path.basename(path))


@app.route("/cancel/<job_id>", methods=["POST"])
def cancel(job_id):
    with JOBS_LOCK:
        j = JOBS.get(job_id)
        if j is None:
            return jsonify({"error": "任务不存在"}), 404
        if j["status"] not in ("running",):
            return jsonify({"status": j["status"]})
        engine = j.get("engine")
        if engine:
            engine.cancel()
        j["status"] = "cancelled"
    return jsonify({"status": "cancelled"})


if __name__ == "__main__":
    # threaded=True 允许进度轮询与上传并发；生产环境建议用 waitress/gunicorn
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
