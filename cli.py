# -*- coding: utf-8 -*-
"""命令行版（批量 + 多 Key 并发）。

示例：
    # 单文件单 Key（顺序）
    python cli.py book.pdf --api-key TOKEN

    # 多文件多 Key（并发数 = Key 数）
    python cli.py a.pdf b.pdf c.pdf --api-key K1 --api-key K2 --api-key K3

    # 用逗号/分号分隔多个 Key 亦可
    python cli.py a.pdf b.pdf --api-key "K1,K2"

与 Web 版共用 batch.run_batch。
"""
import argparse
import os
import sys
import threading

from batch import run_batch

_print_lock = threading.Lock()


def main():
    ap = argparse.ArgumentParser(description="PDF -> Markdown（批量 + 多 Key 并发）")
    ap.add_argument("pdfs", nargs="+", help="一个或多个 PDF 文件路径")
    ap.add_argument("--api-key", action="append", default=[],
                    help="API Key，可多次指定（或用逗号/分号分隔）；多个 Key 即多路并发")
    ap.add_argument("--out", default="outputs", help="输出根目录（默认 outputs/）")
    ap.add_argument("--pages", type=int, default=30, help="每卷页数（默认 30）")
    args = ap.parse_args()

    # 展开 "K1,K2" 形式
    api_keys = []
    for k in args.api_key:
        for part in k.replace(";", ",").split(","):
            part = part.strip()
            if part:
                api_keys.append(part)
    if not api_keys:
        print("错误：请至少提供一个 --api-key", file=sys.stderr)
        sys.exit(1)

    # 校验文件
    tasks = []
    for i, pdf in enumerate(args.pdfs):
        if not os.path.exists(pdf):
            print(f"文件不存在：{pdf}", file=sys.stderr)
            sys.exit(1)
        name = os.path.splitext(os.path.basename(pdf))[0]
        tasks.append({
            "id": f"f{i}",
            "pdf_path": pdf,
            "book_name": name,
            "output_dir": os.path.join(args.out, name),
            "pages_per_chunk": args.pages,
        })

    concurrency = min(len(tasks), len(api_keys))
    print(f"待处理 {len(tasks)} 个文件，并发数 {concurrency}（Key 数 {len(api_keys)}）", flush=True)

    def on_event(task_id, event):
        phase = event.get("phase")
        tag = f"[{task_id}]"
        line = None
        if phase == "split_done":
            line = f"{tag} 拆分完成：{event['total_chunks']} 卷 / {event['total_pages']} 页"
        elif phase == "chunk_start":
            line = f"{tag} 卷 {event['index']}/{event['total']}（页 {event['start']}-{event['end']}）"
        elif phase == "process" and event.get("extracted") is not None:
            line = f"{tag}   OCR {event['extracted']}/{event['total']}"
        elif phase == "done":
            line = f"{tag} ✅ 完成 {event['total_pages']} 页 -> {event['output_file']}"
        elif phase == "error":
            line = f"{tag} ❌ {event['message']}"
        if line:
            with _print_lock:
                print(line, flush=True)

    results = run_batch(tasks, api_keys, on_event=on_event)

    # 汇总
    ok = sum(1 for r in results.values() if r["ok"])
    fail = len(results) - ok
    print(f"\n=== 完成 {ok} 个，失败 {fail} 个 ===", flush=True)
    for t in tasks:
        r = results[t["id"]]
        mark = "✅" if r["ok"] else "❌"
        out = r["output_file"] or r["error"]
        print(f"  {mark} {t['book_name']}: {out}", flush=True)
    sys.exit(0 if fail == 0 else 1)


if __name__ == "__main__":
    main()
