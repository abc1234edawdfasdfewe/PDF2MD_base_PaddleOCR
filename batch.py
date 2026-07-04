# -*- coding: utf-8 -*-
"""批量并发编排：把多个 PDF 任务交给线程池并发处理。

并发模型：
- 并发数 = min(任务数, API Key 数)
- 用一个 Key 池（queue.Queue）保证「每个并发任务独占一个不同的 Key」，
  避免同一 Key 被多个任务同时使用而触发限流。
- 单 Key 时退化为顺序执行（逐个处理）。

Web 与 CLI 共用本模块。
"""
import queue
from concurrent.futures import ThreadPoolExecutor

from ocr_engine import OCREngine


def run_batch(tasks, api_keys, on_event=None, on_start=None, cancel_check=None):
    """并发运行一批 PDF 解析任务。

    Args:
        tasks: list[dict]，每个 dict 含：
            id, pdf_path, book_name, output_dir, pages_per_chunk(可选), model(可选)
        api_keys: list[str]，至少 1 个 API Key。
        on_event: 回调 (task_id, event_dict)，引擎事件（进度等）。
        on_start: 回调 (task_id, engine)，引擎创建后调用（用于注册/取消）。
        cancel_check: 无参回调，返回 True 表示整体已取消；
            用于在任务真正开始前跳过排队中的任务。

    Returns:
        dict {task_id: {"ok": bool, "output_file": str|None, "error": str|None,
                         "skipped": bool}}
    """
    n = len(tasks)
    m = max(1, len(api_keys))
    concurrency = min(n, m)  # 并发数 = 任务数与 Key 数的较小值

    # Key 池：每个并发 worker 取一个、用完归还，保证并发任务 Key 互不相同
    key_pool = queue.Queue()
    for k in api_keys:
        key_pool.put(k)

    results = {}

    def worker(task):
        tid = task["id"]
        # 排队中被取消 -> 直接跳过
        if cancel_check and cancel_check():
            return tid, {"ok": False, "output_file": None,
                         "error": "已取消", "skipped": True}
        api_key = key_pool.get()  # 并发数 ≤ Key 数，不会阻塞
        engine = None
        try:
            def cb(event):
                if on_event:
                    on_event(tid, event)

            engine = OCREngine(
                api_key=api_key,
                pdf_path=task["pdf_path"],
                book_name=task["book_name"],
                output_dir=task["output_dir"],
                pages_per_chunk=task.get("pages_per_chunk", 30),
                model=task.get("model"),
                on_progress=cb,
            )
            if on_start:
                on_start(tid, engine)
            engine.run()
            return tid, {"ok": True, "output_file": engine.combined_md,
                         "error": None, "skipped": False}
        except Exception as e:
            return tid, {"ok": False, "output_file": None,
                         "error": str(e), "skipped": False}
        finally:
            key_pool.put(api_key)

    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futures = [ex.submit(worker, t) for t in tasks]
        for fut in futures:
            tid, res = fut.result()
            results[tid] = res
    return results
