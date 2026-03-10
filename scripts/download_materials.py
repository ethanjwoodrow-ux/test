#!/usr/bin/env python3
"""
Script: Download all course materials from UC Learn.

Usage:
    python scripts/download_materials.py [--course COURSE_ID]

Downloads all files from every enrolled course into organised folders:
    ~/Desktop/Uni/2026/<CourseCode>/<Section>/<filename>

Pass --course to limit to a single course by its short name.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from playwright.sync_api import sync_playwright

from uc_automation.session import get_authenticated_page
from uc_automation.scraper import get_courses, get_course_files
from uc_automation.downloader import download_all_courses
from uc_automation.config import BASE_DIR


def main() -> None:
    parser = argparse.ArgumentParser(description="Download all UC Learn course materials.")
    parser.add_argument("--course", help="Limit to a specific course short name", default=None)
    parser.add_argument("--dest", help="Override download destination", default=None)
    args = parser.parse_args()

    dest = Path(args.dest) if args.dest else BASE_DIR

    with sync_playwright() as p:
        browser, context, page = get_authenticated_page(p)
        try:
            courses = get_courses(page)
            if args.course:
                courses = [c for c in courses if args.course.lower() in c.short_name.lower()]
                if not courses:
                    print(f"[error] No course matching '{args.course}' found.")
                    return

            course_files = {}
            for course in courses:
                files = get_course_files(page, course)
                course_files[course.short_name] = files

            results = download_all_courses(page, courses, course_files, dest)

            total = sum(len(v) for v in results.values())
            print(f"\n[done] Downloaded {total} files to {dest}")
        finally:
            browser.close()


if __name__ == "__main__":
    main()
