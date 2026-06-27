"""Parallel file downloading and post-download rewriting."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse
import mimetypes
import threading

import requests
from tqdm import tqdm

from crawler import _extract_css_urls, get_rendered_html
from js_fixer import fix_javascript_files
from report import generate_clone_report
from rewriter import rewrite_css, rewrite_html
from verify import verify
from utils import (
    has_downloadable_extension,
    is_same_domain,
    local_path_to_url,
    looks_like_html_url,
    normalize_url,
    should_skip_download,
    should_skip_url,
    url_to_local_path,
)


HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
CHUNK_SIZE = 8192


def _is_html_response(url: str, content_type: str) -> bool:
    """Decide whether a response should be saved as an HTML page."""
    return "html" in content_type.lower() or looks_like_html_url(url)


def _is_css_response(url: str, content_type: str) -> bool:
    """Decide whether a response should be scanned as a CSS file."""
    return "css" in content_type.lower() or Path(urlparse(url).path).suffix.lower() == ".css"


def download_file(url: str, output_path: str | Path, rendered_html: str | None = None) -> tuple[str, bool, str, str]:
    """Download one URL to disk using binary streaming."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        if rendered_html is not None:
            output_path.write_text(rendered_html, encoding="utf-8")
            return url, True, "", "text/html"

        with requests.get(url, headers=HEADERS, timeout=15, stream=True, allow_redirects=True) as response:
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()

            with output_path.open("wb") as file:
                for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                    if chunk:
                        file.write(chunk)

        return url, True, "", content_type
    except Exception as exc:
        return url, False, str(exc), ""


def _discover_css_assets(css_path: Path, css_url: str) -> set[str]:
    """Scan a downloaded CSS file for extra url(...) assets."""
    try:
        css_text = css_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return set()

    found: set[str] = set()
    for raw_url in _extract_css_urls(css_text):
        normalized = normalize_url(raw_url, css_url)
        if normalized and not should_skip_download(normalized):
            found.add(normalized)
    return found


def _is_silent_failure(url: str) -> bool:
    """Return True for expected direct-download failures that should not clutter logs."""
    return "fonts.gstatic.com" in (url or "").lower()


def _write_failures(output_dir: Path, failures: list[tuple[str, str]]) -> None:
    """Persist failed downloads in a simple text report."""
    if not failures:
        return

    report_path = output_dir / "failed_downloads.txt"
    with report_path.open("w", encoding="utf-8") as report:
        for url, error in failures:
            report.write(f"{url}\n  {error}\n\n")


def download_all(urls, base_url, output_dir, progress_callback=None, log_callback=None):
    """Download all discovered URLs, scan CSS assets, then rewrite HTML/CSS paths."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    skipped_downloads = [(url, "Skipped external service") for url in urls if should_skip_url(url)]
    pending = {url for url in urls if not should_skip_download(url)}
    downloaded: dict[str, Path] = {}
    content_types: dict[str, str] = {}
    failures: list[tuple[str, str]] = []
    completed_count = 0
    total_count = len(pending)
    lock = threading.Lock()

    def log(message: str) -> None:
        if log_callback:
            log_callback(message)

    def update_progress() -> None:
        if progress_callback:
            progress_callback(completed_count, max(total_count, 1))

    def submit_url(executor: ThreadPoolExecutor, future_map: dict, url: str) -> None:
        if should_skip_download(url):
            return
        output_path = url_to_local_path(url, output_dir, start_url=base_url)
        rendered_html = get_rendered_html(url)
        future = executor.submit(download_file, url, output_path, rendered_html)
        future_map[future] = (url, output_path)

    with ThreadPoolExecutor(max_workers=8) as executor:
        future_map = {}
        for url in sorted(pending):
            submit_url(executor, future_map, url)

        with tqdm(total=total_count, desc="Downloading", unit="file") as progress:
            while future_map:
                for future in as_completed(list(future_map)):
                    url, output_path = future_map.pop(future)
                    result_url, success, error, content_type = future.result()

                    with lock:
                        completed_count += 1
                        progress.update(1)

                    if success:
                        downloaded[result_url] = output_path
                        content_types[result_url] = content_type
                        log(f"Downloaded: {result_url}")

                        # CSS can reveal fonts/background images only after it is downloaded.
                        if _is_css_response(result_url, content_type):
                            for asset_url in _discover_css_assets(output_path, result_url):
                                if asset_url not in pending and not should_skip_download(asset_url):
                                    pending.add(asset_url)
                                    total_count += 1
                                    progress.total = total_count
                                    progress.refresh()
                                    submit_url(executor, future_map, asset_url)
                    else:
                        if not _is_silent_failure(result_url):
                            failures.append((result_url, error))
                            log(f"Failed: {result_url} ({error})")

                    update_progress()
                    break

    _write_failures(output_dir, failures)

    # Rewriting happens after all available files are on disk.
    fix_javascript_files(output_dir, base_url, log_callback=log)

    # Rewriting happens after all available files are on disk.
    for url, path in downloaded.items():
        content_type = content_types.get(url, "")
        try:
            if _is_html_response(url, content_type):
                rewrite_html(path, url, output_dir, start_url=base_url)
                log(f"Rewritten HTML: {path.name}")
            elif _is_css_response(url, content_type):
                rewrite_css(path, url, output_dir, start_url=base_url)
                log(f"Rewritten CSS: {path.name}")
        except Exception as exc:
            failures.append((url, f"Rewrite failed: {exc}"))
            log(f"Rewrite failed: {url} ({exc})")

    _write_failures(output_dir, failures)
    verification = verify(output_dir, log_callback=log)
    report_path = generate_clone_report(
        output_dir=output_dir,
        site_url=base_url,
        downloaded_count=len(downloaded),
        failures=failures + skipped_downloads,
        missing_assets=verification["missing"],
    )
    log(f"Clone report created: {report_path}")

    return {
        "downloaded": len(downloaded),
        "failed": len(failures),
        "verified_ok": verification["verified_ok"],
        "missing_assets": verification["missing"],
        "report_path": report_path,
        "output_dir": output_dir,
    }
