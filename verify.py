"""Post-download verification for rewritten local HTML assets."""

from pathlib import Path
from urllib.parse import urlparse, unquote
import os

from bs4 import BeautifulSoup


URL_ATTRIBUTES = ("src", "href")
SRCSET_ATTRIBUTES = ("srcset", "data-srcset")


def _is_local_reference(value: str) -> bool:
    """Return True for local file references that should exist on disk."""
    value = (value or "").strip()
    if not value or value.startswith(("#", "data:", "mailto:", "tel:", "javascript:")):
        return False

    parsed = urlparse(value)
    return parsed.scheme == "" and parsed.netloc == ""


def _reference_to_path(reference: str, html_file: Path) -> Path:
    """Convert a rewritten browser path into an absolute filesystem path."""
    parsed = urlparse(reference)
    clean_path = unquote(parsed.path)
    return (html_file.parent / clean_path).resolve()


def _iter_srcset_urls(value: str):
    """Yield just the URLs from a srcset-like value."""
    for item in (value or "").split(","):
        parts = item.strip().split()
        if parts:
            yield parts[0]


def verify(output_dir: str | Path, log_callback=None) -> dict[str, int]:
    """Check all local src/href references in downloaded HTML files."""
    output_dir = Path(output_dir)
    ok_count = 0
    missing: list[tuple[Path, str, Path]] = []

    for html_file in output_dir.rglob("*.html"):
        html = html_file.read_text(encoding="utf-8", errors="ignore")
        soup = BeautifulSoup(html, "html.parser")

        for tag in soup.find_all(True):
            for attr in URL_ATTRIBUTES:
                if not tag.has_attr(attr):
                    continue

                reference = tag[attr]
                if not _is_local_reference(reference):
                    continue

                target = _reference_to_path(reference, html_file)
                if target.exists():
                    ok_count += 1
                else:
                    missing.append((html_file, reference, target))

            for attr in SRCSET_ATTRIBUTES:
                if not tag.has_attr(attr):
                    continue

                for reference in _iter_srcset_urls(tag[attr]):
                    if not _is_local_reference(reference):
                        continue

                    target = _reference_to_path(reference, html_file)
                    if target.exists():
                        ok_count += 1
                    else:
                        missing.append((html_file, reference, target))

    report_path = output_dir / "missing_assets.txt"
    if missing:
        with report_path.open("w", encoding="utf-8") as report:
            for html_file, reference, target in missing:
                relative_html = os.path.relpath(html_file, output_dir).replace(os.sep, "/")
                report.write(f"{relative_html}\n  missing asset: {reference}\n  expected: {target}\n\n")
    elif report_path.exists():
        report_path.unlink()

    summary = {
        "verified_ok": ok_count,
        "missing": len(missing),
    }

    message = f"{ok_count} assets verified OK, {len(missing)} assets missing"
    if log_callback:
        log_callback(message)

    return summary
