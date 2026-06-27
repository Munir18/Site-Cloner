"""Generate a human-readable clone report."""

from datetime import datetime
from html import escape
from pathlib import Path
import os


def _folder_size(output_dir: Path) -> int:
    """Return total bytes for all files under the output folder."""
    total = 0
    for file_path in output_dir.rglob("*"):
        if file_path.is_file():
            total += file_path.stat().st_size
    return total


def _format_size(size: int) -> str:
    """Format bytes as a readable file size."""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size} B"


def _categorize_failures(failures: list[tuple[str, str]]) -> dict[str, list[str]]:
    """Group failed URLs into readable categories."""
    categories = {
        "Server blocked (403/401)": [],
        "Page not found (404)": [],
        "Timeout": [],
        "Skipped (external services)": [],
        "Other": [],
    }

    for url, error in failures:
        combined = f"{url} {error}".lower()
        if "skipped" in combined:
            categories["Skipped (external services)"].append(url)
        elif "403" in combined or "401" in combined:
            categories["Server blocked (403/401)"].append(url)
        elif "404" in combined:
            categories["Page not found (404)"].append(url)
        elif "timeout" in combined or "timed out" in combined:
            categories["Timeout"].append(url)
        else:
            categories["Other"].append(f"{url} ({error})")

    return categories


def generate_clone_report(
    output_dir: str | Path,
    site_url: str,
    downloaded_count: int,
    failures: list[tuple[str, str]],
    missing_assets: int,
) -> Path:
    """Create CLONE_REPORT.html in the output folder."""
    output_dir = Path(output_dir)
    pages = sorted(
        os.path.relpath(path, output_dir).replace(os.sep, "/")
        for path in output_dir.rglob("*.html")
        if path.name != "CLONE_REPORT.html"
    )
    categories = _categorize_failures(failures)
    total_size = _format_size(_folder_size(output_dir))

    category_html = []
    for title, items in categories.items():
        safe_items = "".join(f"<li>{escape(item)}</li>" for item in items) or "<li>None</li>"
        category_html.append(f"<section><h3>{escape(title)}</h3><ul>{safe_items}</ul></section>")

    page_items = "".join(f"<li>{escape(page)}</li>" for page in pages) or "<li>No HTML pages found</li>"
    report = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>SiteCloner Report</title>
  <style>
    body {{ margin: 0; background: #1a1a1a; color: #ffffff; font-family: Segoe UI, Arial, sans-serif; }}
    main {{ max-width: 980px; margin: 0 auto; padding: 36px 24px; }}
    h1, h2, h3 {{ margin-top: 0; }}
    h1 {{ color: #E8541C; font-size: 34px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin: 24px 0; }}
    .card, section {{ background: #2a2a2a; border: 1px solid #3a3a3a; border-radius: 8px; padding: 16px; }}
    .label {{ color: #c7c7c7; font-size: 13px; }}
    .value {{ font-size: 22px; font-weight: 700; margin-top: 6px; }}
    a {{ color: #E8541C; }}
    ul {{ padding-left: 20px; line-height: 1.6; }}
    .note {{ border-left: 4px solid #E8541C; padding-left: 14px; color: #f2f2f2; }}
  </style>
</head>
<body>
<main>
  <h1>SiteCloner Report</h1>
  <p><strong>Site URL cloned:</strong> {escape(site_url)}</p>
  <p><strong>Date and time of clone:</strong> {escape(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))}</p>
  <div class="grid">
    <div class="card"><div class="label">Files downloaded</div><div class="value">{downloaded_count}</div></div>
    <div class="card"><div class="label">Total size</div><div class="value">{escape(total_size)}</div></div>
    <div class="card"><div class="label">Pages cloned</div><div class="value">{len(pages)}</div></div>
    <div class="card"><div class="label">Missing assets</div><div class="value">{missing_assets}</div></div>
  </div>
  <p class="note">Open index.html to browse the cloned site offline.</p>
  <section>
    <h2>Pages Cloned</h2>
    <ul>{page_items}</ul>
  </section>
  <h2>Failed Downloads</h2>
  {''.join(category_html)}
</main>
</body>
</html>
"""
    report_path = output_dir / "CLONE_REPORT.html"
    report_path.write_text(report, encoding="utf-8")
    return report_path
