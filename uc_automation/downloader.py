"""Download course materials into organised folder structure."""

import re
import asyncio
import mimetypes
from pathlib import Path
from urllib.parse import urlparse, unquote

import aiofiles
from playwright.sync_api import Page, BrowserContext

from .config import BASE_DIR, BROWSER_TIMEOUT
from .scraper import Course, CourseFile


# ── Filename helpers ─────────────────────────────────────────────────────────

_SAFE_RE = re.compile(r"[^\w\s\-.]")


def safe_name(name: str, max_len: int = 80) -> str:
    """Convert arbitrary text to a safe filename."""
    name = unquote(name)
    name = _SAFE_RE.sub("_", name).strip().strip("_")
    name = re.sub(r"_+", "_", name)
    return name[:max_len]


def course_dir(course: Course, section: str = "", base: Path = BASE_DIR) -> Path:
    """Return (and create) the directory for a course section."""
    parts = [safe_name(course.short_name)]
    if section:
        parts.append(safe_name(section))
    path = base.joinpath(*parts)
    path.mkdir(parents=True, exist_ok=True)
    return path


# ── Download logic ───────────────────────────────────────────────────────────

def download_file(page: Page, file: CourseFile, dest_dir: Path) -> Path | None:
    """
    Download a single file via Playwright (handles auth cookies automatically).
    Returns the destination path, or None if skipped.
    """
    url = file.url
    filename = safe_name(file.name)

    # Add extension if missing
    if file.file_type and not filename.lower().endswith(f".{file.file_type}"):
        filename = f"{filename}.{file.file_type}"

    dest = dest_dir / filename

    if dest.exists():
        print(f"  [skip] {dest.name} (already exists)")
        return dest

    print(f"  [download] {filename} <- {url}")

    # Use Playwright's download event for proper auth-cookie handling
    with page.expect_download(timeout=BROWSER_TIMEOUT * 2) as dl_info:
        try:
            page.goto(url, timeout=BROWSER_TIMEOUT)
        except Exception:
            pass  # download triggers navigation failure — that's OK

    try:
        download = dl_info.value
        # Refine filename from suggested name if available
        suggested = download.suggested_filename
        if suggested:
            dest = dest_dir / safe_name(suggested)
        download.save_as(str(dest))
        print(f"  [saved]    {dest}")
        return dest
    except Exception as e:
        print(f"  [error]    Failed to download {url}: {e}")
        return None


def download_all_courses(
    page: Page,
    courses: list[Course],
    course_files: dict[str, list[CourseFile]],
    base_dir: Path = BASE_DIR,
) -> dict[str, list[Path]]:
    """
    Download all files for all courses.

    Args:
        page: Authenticated Playwright page.
        courses: List of Course objects.
        course_files: Mapping of course short_name -> list of CourseFile.
        base_dir: Root download directory.

    Returns:
        Mapping of course short_name -> list of downloaded Paths.
    """
    results: dict[str, list[Path]] = {}

    for course in courses:
        files = course_files.get(course.short_name, [])
        if not files:
            continue

        print(f"\n[downloader] Course: {course.short_name} ({len(files)} files)")
        downloaded: list[Path] = []

        for f in files:
            dest_dir = course_dir(course, f.section, base_dir)
            path = download_file(page, f, dest_dir)
            if path:
                downloaded.append(path)

        results[course.short_name] = downloaded
        print(f"[downloader] {course.short_name}: {len(downloaded)}/{len(files)} downloaded.")

    return results


def rename_recording(original: Path, course_short: str, week: int, topic: str) -> Path:
    """
    Rename a cryptically-named lecture recording to a descriptive filename.
    E.g. echo360_abc123.mp4 -> FORE242_Week3_LiDAR_Remote_Sensing.mp4
    """
    ext = original.suffix
    new_name = safe_name(f"{course_short}_Week{week}_{topic}") + ext
    new_path = original.parent / new_name
    original.rename(new_path)
    print(f"[rename] {original.name} -> {new_name}")
    return new_path
