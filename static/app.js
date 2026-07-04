// PDF2MD 前端：批量上传 + 多 Key 并发 + 逐文件进度
const $ = (id) => document.getElementById(id);
let pollTimer = null;
let currentJobId = null;

// ---- 文件选择 / 拖拽（多选）----
const dropzone = $("dropzone");
const fileInput = $("file");
const submitBtn = $("submit-btn");

dropzone.addEventListener("click", () => fileInput.click());
["dragover", "dragenter"].forEach((ev) =>
    dropzone.addEventListener(ev, (e) => { e.preventDefault(); dropzone.classList.add("drag"); })
);
["dragleave", "drop"].forEach((ev) =>
    dropzone.addEventListener(ev, (e) => { e.preventDefault(); dropzone.classList.remove("drag"); })
);
dropzone.addEventListener("drop", (e) => {
    if (e.dataTransfer.files.length) {
        fileInput.files = e.dataTransfer.files;
        renderFileList();
    }
});
fileInput.addEventListener("change", renderFileList);

function renderFileList() {
    const list = $("file-list");
    list.innerHTML = "";
    const files = Array.from(fileInput.files);
    if (!files.length) {
        $("dropzone-text").textContent = "点击或拖拽 PDF 到此处（支持多选）";
        $("dropzone-hint").textContent = "仅支持 .pdf";
        submitBtn.disabled = true;
        return;
    }
    $("dropzone-text").textContent = `已选择 ${files.length} 个文件，点击重新选择`;
    $("dropzone-hint").textContent = files
        .map((f) => `${f.name}（${(f.size / 1024 / 1024).toFixed(1)}MB）`).join(" · ");
    files.forEach((f, i) => {
        const row = document.createElement("div");
        row.className = "file-chip";
        row.textContent = `${i + 1}. ${f.name} — ${(f.size / 1024 / 1024).toFixed(1)}MB`;
        list.appendChild(row);
    });
    submitBtn.disabled = false;
}

// ---- 命名方式联动 ----
document.querySelectorAll('input[name="naming"]').forEach((r) =>
    r.addEventListener("change", () => {
        $("custom_name").disabled =
            !(document.querySelector('input[name="naming"]:checked').value === "custom");
    })
);

// ---- 提交 ----
$("upload-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    $("form-error").textContent = "";

    const keys = $("api_keys").value.split("\n").map((s) => s.trim()).filter(Boolean);
    if (!keys.length) { $("form-error").textContent = "请至少填写一个 API Key"; return; }
    if (!fileInput.files.length) { $("form-error").textContent = "请至少选择一个 PDF"; return; }

    submitBtn.disabled = true;
    submitBtn.textContent = "提交中…";

    const fd = new FormData();
    for (const f of fileInput.files) fd.append("file", f);
    fd.append("api_keys", $("api_keys").value);
    fd.append("naming", document.querySelector('input[name="naming"]:checked').value);
    fd.append("custom_name", $("custom_name").value.trim());
    fd.append("pages_per_chunk", $("pages_per_chunk").value);

    try {
        const resp = await fetch("/upload", { method: "POST", body: fd });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.error || "上传失败");
        startProgress(data.job_id);
    } catch (err) {
        $("form-error").textContent = err.message;
        submitBtn.disabled = false;
        submitBtn.textContent = "开始批量解析";
    }
});

// ---- 轮询进度 ----
function startProgress(jobId) {
    currentJobId = jobId;
    $("upload-card").classList.add("hidden");
    $("progress-card").classList.remove("hidden");
    $("file-rows").innerHTML = "";
    $("download-all-btn").classList.add("hidden");
    $("reset-btn").classList.add("hidden");
    $("cancel-btn").classList.remove("hidden");

    const tick = async () => {
        try {
            const r = await fetch(`/progress/${jobId}`);
            const d = await r.json();
            if (!r.ok) return;
            renderProgress(d, jobId);
            if (["done", "failed", "partial", "cancelled"].includes(d.status)) {
                clearInterval(pollTimer);
                onFinish(d, jobId);
            }
        } catch (err) { /* 网络抖动，继续 */ }
    };
    tick();
    pollTimer = setInterval(tick, 2500);
}

const STATUS_TEXT = { pending: "排队", running: "处理中", done: "完成",
                      failed: "失败", cancelled: "已取消" };

function renderProgress(d, jobId) {
    const badge = $("job-status");
    badge.textContent = { running: "运行中", done: "已完成", failed: "失败",
                          partial: "部分完成", cancelled: "已取消" }[d.status] || d.status;
    badge.className = "badge " + (d.status || "running");

    $("stat-files").textContent = `${d.done_count}/${d.total}`;
    $("stat-conc").textContent = d.concurrency;
    $("stat-ok").textContent = `${d.done_count}/${d.fail_count}`;
    $("stat-elapsed").textContent = d.elapsed_text || "-";

    // 总进度 = 各文件百分比均值
    const pcts = d.files.map((f) => f.pct || 0);
    const overall = pcts.length ? Math.round(pcts.reduce((a, b) => a + b, 0) / pcts.length) : 0;
    $("bar").style.width = overall + "%";
    $("bar-pct").textContent = overall + "%";

    // 逐文件行
    const wrap = $("file-rows");
    wrap.innerHTML = "";
    d.files.forEach((f) => {
        const row = document.createElement("div");
        row.className = "file-row";

        const head = document.createElement("div");
        head.className = "file-row-head";
        const nameSpan = document.createElement("span");
        nameSpan.className = "file-row-name";
        nameSpan.textContent = f.name;
        const st = document.createElement("span");
        st.className = "mini-badge " + f.status;
        st.textContent = STATUS_TEXT[f.status] || f.status;
        head.appendChild(nameSpan);
        head.appendChild(st);

        const barWrap = document.createElement("div");
        barWrap.className = "mini-bar-wrap";
        const mb = document.createElement("div");
        mb.className = "mini-bar";
        mb.style.width = (f.pct || 0) + "%";
        barWrap.appendChild(mb);

        const foot = document.createElement("div");
        foot.className = "file-row-foot";
        const info = document.createElement("span");
        info.className = "muted";
        info.textContent = f.last_line || "";
        foot.appendChild(info);
        if (f.status === "done" && f.output_file) {
            const dl = document.createElement("a");
            dl.className = "link-btn";
            dl.href = `/download/${jobId}/${f.id}`;
            dl.textContent = "⬇ 下载";
            foot.appendChild(dl);
        }

        row.appendChild(head);
        row.appendChild(barWrap);
        row.appendChild(foot);
        wrap.appendChild(row);
    });
}

function onFinish(d, jobId) {
    $("submit-btn").textContent = "开始批量解析";
    $("cancel-btn").classList.add("hidden");
    $("reset-btn").classList.remove("hidden");

    const okCount = d.done_count;
    if (d.status === "done") {
        $("job-title").textContent = `✅ 全部完成（${okCount} 个）`;
    } else if (d.status === "partial") {
        $("job-title").textContent = `⚠ 完成 ${okCount} 个，失败 ${d.fail_count} 个`;
    } else if (d.status === "failed") {
        $("job-title").textContent = `❌ 全部失败`;
    } else {
        $("job-title").textContent = `⏹ 已取消`;
    }
    if (okCount > 0) {
        const all = $("download-all-btn");
        all.href = `/download_all/${jobId}`;
        all.classList.remove("hidden");
    }
}

// ---- 取消 / 重置 ----
$("cancel-btn").addEventListener("click", async () => {
    if (!confirm("确定取消整个批量任务吗？")) return;
    if (currentJobId) await fetch(`/cancel/${currentJobId}`, { method: "POST" });
});

$("reset-btn").addEventListener("click", () => {
    if (pollTimer) clearInterval(pollTimer);
    currentJobId = null;
    $("progress-card").classList.add("hidden");
    $("upload-card").classList.remove("hidden");
    $("upload-form").reset();
    $("file-list").innerHTML = "";
    $("custom_name").disabled = true;
    renderFileList();
    $("cancel-btn").classList.remove("hidden");
    $("job-title").textContent = "批量解析中…";
});
