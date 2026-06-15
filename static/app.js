// PDF2MD 前端逻辑：上传 -> 轮询进度 -> 展示日志/下载
const $ = (id) => document.getElementById(id);

let pollTimer = null;

// ---- 拖拽 / 选择文件 ----
const dropzone = $("dropzone");
const fileInput = $("file");
const submitBtn = $("submit-btn");

dropzone.addEventListener("click", () => fileInput.click());

["dragover", "dragenter"].forEach((ev) =>
    dropzone.addEventListener(ev, (e) => {
        e.preventDefault();
        dropzone.classList.add("drag");
    })
);
["dragleave", "drop"].forEach((ev) =>
    dropzone.addEventListener(ev, (e) => {
        e.preventDefault();
        dropzone.classList.remove("drag");
    })
);
dropzone.addEventListener("drop", (e) => {
    if (e.dataTransfer.files.length) {
        fileInput.files = e.dataTransfer.files;
        renderFileName();
    }
});
fileInput.addEventListener("change", renderFileName);

function renderFileName() {
    const f = fileInput.files[0];
    if (f) {
        $("dropzone-text").textContent = f.name;
        $("dropzone-hint").textContent = (f.size / 1024 / 1024).toFixed(1) + " MB";
        submitBtn.disabled = false;
    } else {
        submitBtn.disabled = true;
    }
}

// ---- 提交上传 ----
$("upload-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    $("form-error").textContent = "";

    const apiKey = $("api_key").value.trim();
    if (!apiKey) {
        $("form-error").textContent = "请填写 API Key";
        return;
    }
    if (!fileInput.files.length) {
        $("form-error").textContent = "请选择 PDF 文件";
        return;
    }

    submitBtn.disabled = true;
    submitBtn.textContent = "上传中…";

    const fd = new FormData();
    fd.append("file", fileInput.files[0]);
    fd.append("api_key", apiKey);
    fd.append("book_name", $("book_name").value.trim());
    fd.append("pages_per_chunk", $("pages_per_chunk").value);

    try {
        const resp = await fetch("/upload", { method: "POST", body: fd });
        const data = await resp.json();
        if (!resp.ok) {
            throw new Error(data.error || "上传失败");
        }
        startProgress(data.job_id, data.book_name);
    } catch (err) {
        $("form-error").textContent = err.message;
        submitBtn.disabled = false;
        submitBtn.textContent = "开始解析";
    }
});

// ---- 轮询进度 ----
function startProgress(jobId, bookName) {
    $("upload-card").classList.add("hidden");
    $("progress-card").classList.remove("hidden");
    $("job-title").textContent = bookName ? `《${bookName}》解析中…` : "解析中…";
    $("download-btn").classList.add("hidden");
    $("reset-btn").classList.add("hidden");
    $("log").textContent = "";

    const tick = async () => {
        try {
            const r = await fetch(`/progress/${jobId}`);
            const d = await r.json();
            if (!r.ok) {
                $("log").textContent += `\n⚠ ${d.error || "查询失败"}`;
                return;
            }
            renderProgress(d, jobId);
            if (d.status === "done" || d.status === "failed" || d.status === "cancelled") {
                clearInterval(pollTimer);
                onFinish(d, jobId);
            }
        } catch (err) {
            // 网络抖动，下一轮继续
        }
    };
    tick();
    pollTimer = setInterval(tick, 2500);
}

function renderProgress(d, jobId) {
    // 状态徽章
    const badge = $("job-status");
    badge.textContent = { running: "运行中", done: "已完成", failed: "失败", cancelled: "已取消" }[d.status] || d.status;
    badge.className = "badge " + (d.status || "running");

    // 统计
    $("stat-pages").textContent = d.total_pages ?? "-";
    $("stat-chunks").textContent = d.total_chunks ? `${d.chunks_done}/${d.total_chunks}` : "-";
    $("stat-current").textContent = d.total_chunks ? `第 ${d.current_chunk} 卷` : "-";
    $("stat-elapsed").textContent = d.elapsed ? fmtTime(d.elapsed) : "-";

    // 总进度：已完成分卷 + 当前卷内进度
    let pct = 0;
    if (d.total_chunks) {
        let done = d.chunks_done;
        if (d.status === "running" && d.chunk_total) {
            done += d.chunk_extracted / d.chunk_total;
        }
        pct = Math.min(100, (done / d.total_chunks) * 100);
    } else if (d.status === "done") {
        pct = 100;
    }
    pct = Math.round(pct);
    $("bar").style.width = pct + "%";
    $("bar-pct").textContent = pct + "%";

    // 日志
    $("log").textContent = (d.log || []).join("\n");
    const logEl = $("log");
    logEl.scrollTop = logEl.scrollHeight;
}

function onFinish(d, jobId) {
    $("submit-btn").textContent = "开始解析";
    $("cancel-btn").classList.add("hidden");
    $("reset-btn").classList.remove("hidden");

    if (d.status === "done") {
        $("job-title").textContent = "✅ 解析完成";
        const dl = $("download-btn");
        dl.href = `/download/${jobId}`;
        dl.classList.remove("hidden");
    } else if (d.status === "failed") {
        $("job-title").textContent = "❌ 解析失败";
        $("log").textContent += `\n\n失败原因：${d.error || "未知错误"}`;
    } else {
        $("job-title").textContent = "⏹ 已取消";
    }
}

// ---- 取消 / 重置 ----
$("cancel-btn").addEventListener("click", async () => {
    if (!confirm("确定取消该任务吗？")) return;
    const jobId = currentJobId();
    if (jobId) await fetch(`/cancel/${jobId}`, { method: "POST" });
});

$("reset-btn").addEventListener("click", () => {
    if (pollTimer) clearInterval(pollTimer);
    $("progress-card").classList.add("hidden");
    $("upload-card").classList.remove("hidden");
    $("upload-form").reset();
    $("dropzone-text").textContent = "点击或拖拽 PDF 到此处";
    $("dropzone-hint").textContent = "仅支持 .pdf";
    submitBtn.disabled = true;
    submitBtn.textContent = "开始解析";
    $("cancel-btn").classList.remove("hidden");
});

// 简单保存当前 job_id（从下载链接反查）
function currentJobId() {
    const href = $("download-btn").getAttribute("href") || "";
    return href.split("/download/")[1] || null;
}

// ---- 工具 ----
function fmtTime(s) {
    s = Math.floor(s);
    const m = Math.floor(s / 60);
    const sec = s % 60;
    return m > 0 ? `${m}分${sec}秒` : `${sec}秒`;
}
