from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.bill_splitter import default_register_keywords_text, parse_register_keywords, split_batch_file


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Split scanned batches into bill PDFs while ignoring register pages, checks, cover sheets, and blanks."
    )
    parser.add_argument("paths", nargs="+", help="One or more batch PDFs or scan images on disk.")
    parser.add_argument(
        "--keywords",
        default=default_register_keywords_text(),
        help="Comma-separated register page keywords. Matching pages are ignored and break bill groups.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    register_keywords = parse_register_keywords(args.keywords)
    exit_code = 0

    for raw_path in args.paths:
        source_path = Path(raw_path).expanduser()
        try:
            result = split_batch_file(source_path, register_keywords=register_keywords)
        except Exception as error:
            exit_code = 1
            print(f"{source_path}: error: {error}", file=sys.stderr)
            continue

        print(f"{result.source_path}: wrote {len(result.outputs)} bill PDF(s) to {result.output_dir}")
        if result.register_pages:
            register_pages = ", ".join(str(page_number) for page_number in result.register_pages)
            print(f"  register pages: {register_pages}")
        for output in result.outputs:
            page_numbers = ", ".join(str(page_number) for page_number in output.page_numbers)
            print(f"  - {output.path.name}: pages {page_numbers}")

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
