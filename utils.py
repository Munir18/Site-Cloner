"""Utility helpers for URL handling and local file path mapping."""

from pathlib import Path
from urllib.parse import parse_qsl, quote, unquote, urljoin, urlparse, urlunparse
import os
import re

try:
    import tldextract
except ImportError:
    tldextract = None


_TLD_EXTRACTOR = tldextract.TLDExtract(suffix_list_urls=()) if tldextract else None

ASSET_EXTENSIONS = {
    ".html", ".htm",
    ".css",
    ".js", ".mjs",
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".bmp", ".tiff",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".mp4", ".webm", ".ogg", ".mov", ".avi",
    ".mp3", ".wav", ".flac",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx",
    ".json", ".xml", ".csv",
}

SKIP_DOMAINS = [
    "x.com", "twitter.com", "instagram.com", "tiktok.com",
    "facebook.com", "linkedin.com", "t.me", "telegram.me",
    "binance.com", "youtube.com", "discord.com", "discord.gg",
    "cdn-cgi",
    "googletagmanager.com", "google-analytics.com", "analytics.google.com",
    "formspree.io",
    "player.vimeo.com",
    "player.youtube.com",
    "media.giphy.com",
    "xmlrpc.php",
    "wp-cron.php",
]

HTML_EXTENSIONS = {"", ".html", ".htm", ".php", ".asp", ".aspx", ".jsp"}
CSS_EXTENSIONS = {".css"}
JS_EXTENSIONS = {".js", ".mjs"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".bmp", ".tiff"}
FONT_EXTENSIONS = {".woff", ".woff2", ".ttf", ".otf", ".eot"}
VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov", ".avi"}
DOC_EXTENSIONS = {".pdf", ".doc", ".docx", ".xls", ".xlsx"}
INVALID_FILENAME_CHARS = r'<>:"\\|?*'
WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}


def get_domain(url: str) -> str:
    """Return the registered/root domain for folder naming."""
    host = get_hostname(url)
    if _TLD_EXTRACTOR:
        extracted = _TLD_EXTRACTOR(host)
        if extracted.domain and extracted.suffix:
            return sanitize_filename(f"{extracted.domain}.{extracted.suffix}")

    # Fallback for simple domains when tldextract has not been installed yet.
    parts = host.split(".")
    if len(parts) >= 2:
        return sanitize_filename(".".join(parts[-2:]))
    return sanitize_filename(host)


def get_hostname(url: str) -> str:
    """Return the full lowercase hostname without a port."""
    parsed = urlparse(url)
    return (parsed.hostname or parsed.netloc).lower()


def normalize_url(url: str, base_url: str) -> str | None:
    """Resolve a possibly relative URL, remove fragments, and skip unsupported schemes."""
    if not url:
        return None

    url = url.strip()
    if not url or url.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
        return None

    absolute = urljoin(base_url, url)
    parsed = urlparse(absolute)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None

    # Fragments point inside a document, so they should not create duplicate downloads.
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.params, parsed.query, ""))


def should_skip_url(url: str) -> bool:
    """Return True for social/third-party URLs that should never be downloaded."""
    return any(skip in (url or "").lower() for skip in SKIP_DOMAINS)


def should_skip_download(url: str) -> bool:
    """Return True for URLs that should stay live instead of being downloaded."""
    url = (url or "").lower()
    return should_skip_url(url) or "fonts.googleapis.com" in url


def is_same_domain(url: str, base_url: str) -> bool:
    """Return True when two URLs share the same registered/root domain."""
    return get_domain(url) == get_domain(base_url)


def sanitize_filename(name: str) -> str:
    """Remove characters that are invalid in Windows filenames."""
    cleaned = "".join("_" if char in INVALID_FILENAME_CHARS else char for char in unquote(name))
    cleaned = cleaned.strip().strip(".")
    if not cleaned:
        cleaned = "index"
    if cleaned.upper() in WINDOWS_RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    return cleaned


def _query_suffix(query: str) -> str:
    """Create a stable filename suffix for URLs whose query changes the response."""
    if not query:
        return ""

    pairs = parse_qsl(query, keep_blank_values=True)
    raw = "_".join(f"{key}-{value}" for key, value in pairs) or query
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("_")
    return f"__{safe[:80]}" if safe else ""


def url_to_local_path(url: str, output_dir: str | Path, start_url: str | None = None) -> Path:
    """Map a URL to an organized local file path under the chosen output directory."""
    parsed = urlparse(url)
    output_root = Path(output_dir)
    hostname = get_hostname(url)
    output_domain = output_root.name.lower()
    home_hostname = get_hostname(start_url) if start_url else output_domain
    home_hosts = {home_hostname, output_domain, f"www.{output_domain}"}
    is_external_asset = hostname and hostname not in home_hosts
    base_root = output_root / "_external" / sanitize_filename(hostname) if is_external_asset else output_root
    raw_path = unquote(parsed.path or "/")

    # Directory-like URLs and extensionless same-site pages become index.html.
    if raw_path.endswith("/"):
        parts = [sanitize_filename(part) for part in raw_path.strip("/").split("/") if part]
        return base_root.joinpath(*parts, "index.html")

    parts = [sanitize_filename(part) for part in raw_path.strip("/").split("/") if part]
    if not parts:
        return base_root / "index.html"

    filename = parts[-1]
    stem, ext = os.path.splitext(filename)
    ext = ext.lower()
    query_suffix = _query_suffix(parsed.query)

    if not ext:
        if is_external_asset:
            # CDN assets such as Google Fonts CSS often use extensionless endpoints.
            parts[-1] = f"{filename}{query_suffix}"
            return base_root.joinpath(*parts)

        # Treat extensionless paths as pages and store them in their own folder.
        return base_root.joinpath(*parts, "index.html")

    parts[-1] = f"{stem}{query_suffix}{ext}"

    if is_external_asset:
        return base_root.joinpath(*parts)

    # Same-site files are organized by asset type so offline output is tidy.
    if ext in CSS_EXTENSIONS:
        return output_root / "assets" / "css" / parts[-1]
    if ext in JS_EXTENSIONS:
        return output_root / "assets" / "js" / parts[-1]
    if ext in IMAGE_EXTENSIONS:
        return output_root / "assets" / "images" / parts[-1]
    if ext in FONT_EXTENSIONS:
        return output_root / "fonts" / parts[-1]
    if ext in VIDEO_EXTENSIONS:
        return output_root / "assets" / "videos" / parts[-1]
    if ext in DOC_EXTENSIONS:
        return output_root / "assets" / "docs" / parts[-1]
    if ext in {".html", ".htm"}:
        return output_root.joinpath(*parts)

    return output_root / "assets" / "other" / parts[-1]


def local_path_to_url(local_path: str | Path, output_dir: str | Path, base_url: str) -> str:
    """Best-effort reverse mapping from a local path back to its original URL."""
    local_path = Path(local_path)
    output_root = Path(output_dir)
    relative = local_path.relative_to(output_root).as_posix()
    parsed_base = urlparse(base_url)

    if relative.endswith("/index.html"):
        relative = relative[:-len("/index.html")] + "/"
    elif relative == "index.html":
        relative = "/"
    else:
        relative = "/" + quote(relative)

    return urlunparse((parsed_base.scheme, parsed_base.netloc, relative, "", "", ""))


def relative_path(from_file: str | Path, to_file: str | Path) -> str:
    """Return a browser-friendly relative path from one local file to another."""
    from_dir = Path(from_file).parent
    rel = os.path.relpath(Path(to_file), from_dir)
    return rel.replace(os.sep, "/")


def looks_like_html_url(url: str) -> bool:
    """Guess whether a URL should be parsed as HTML based on its extension."""
    ext = Path(urlparse(url).path).suffix.lower()
    return ext in HTML_EXTENSIONS


def has_downloadable_extension(url: str) -> bool:
    """Return True for known static asset/document extensions."""
    ext = Path(urlparse(url).path).suffix.lower()
    return ext in ASSET_EXTENSIONS
