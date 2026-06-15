# -*- coding: utf-8 -*-
"""命令行版：python cli.py <pdf路径> --api-key TOKEN [--book 书名] [--pages 30]

适合批量处理 / 服务器后台运行；与 Web 版共用同一个 OCREngine。
"""
import argparse
import os
import sys

from ocr_engine import OCREngine


def main():
    ap = argparse.ArgumentParser(description="PDF -> Markdown（基于 PaddleOCR-VL）")
    ap.add_argument("pdf", help="PDF 文件路径")
    ap.add_argument("--api-key", required=True, help="PaddleOCR API Key（access token）")
    ap.add_argument("--book", default=None, help="书名 / 输出名（默认用文件名）")
    ap.add_argument("--out", default="outputs", help="输出目录（默认 outputs/）")
    ap.add_argument("--pages", type=int, default=30, help="每卷页数（默认 30）")
    args = ap.parse_args()

    if not os.path.exists(args.pdf):
        print(f"文件不存在：{args.pdf}", file=sys.stderr)
        sys.exit(1)

    book = args.book or os.path.splitext(os.path.basename(args.pdf))[0]
    output_dir = os.path.join(args.out, book)

    def cb(event):
        phase = event.get("phase")
        if phase == "split_done":
            print(f"拆分完成：{event['total_chunks']} 卷 / {event['total_pages']} 页", flush=True)
        elif phase == "chunk_start":
            print(f"[{event['index']}/{event['total']}] 页 {event['start']}-{event['end']}", flush=True)
        elif phase == "process" and event.get("extracted") is not None:
            print(f"    OCR {event['extracted']}/{event['total']}", flush=True)
        elif phase == "done":
            print(f"\n✅ 完成！{event['total_pages']} 页 -> {event['output_file']}", flush=True)
        elif phase == "error":
            print(f"❌ {event['message']}", file=sys.stderr, flush=True)

    engine = OCREngine(
        api_key=args.api_key,
        pdf_path=args.pdf,
        book_name=book,
        output_dir=output_dir,
        pages_per_chunk=args.pages,
        on_progress=cb,
    )
    engine.run()


if __name__ == "__main__":
    main()
