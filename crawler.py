"""Website crawling and URL discovery logic."""

from collections import deque
from urllib.parse import urljoin, urlparse
import re
import subprocess
import sys

import requests
from bs4 import BeautifulSoup

from utils import is_same_domain, normalize_url, should_skip_url


HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
CSS_URL_PATTERN = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.IGNORECASE)
PLAYWRIGHT_SKIP_PATTERNS = (
    "google-analytics",
    "googletagmanager",
    "facebook.com/tr",
    "hotjar",
    "clarity.ms",
    "doubleclick",
    "player.vimeo",
    "player.youtube",
    "formspree.io",
    "cdn-cgi/l/email",
)
IMAGE_ATTRS = (
    "src",
    "data-src",
    "data-lazy-src",
    "data-lazy",
    "data-original",
    "data-url",
    "data-bg",
    "poster",
)
SRCSET_ATTRS = ("srcset", "data-srcset")
DYNAMIC_CLASS_KEYWORDS = ("tab", "slider", "carousel", "accordion", "panel")
RENDERED_HTML_CACHE: dict[str, str] = {}


def ensure_playwright_browsers(log_callback=None) -> bool:
    """Install Chromium for Playwright if it is missing."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return False

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            browser.close()
        return True
    except Exception:
        if log_callback:
            log_callback("Installing browser engine... (one time only)")
        try:
            subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
            return True
        except Exception as exc:
            if log_callback:
                log_callback(f"Browser engine install failed: {exc}")
            return False


def get_rendered_html(url: str) -> str | None:
    """Return cached Playwright-rendered HTML for a crawled page."""
    return RENDERED_HTML_CACHE.get(url)


def resolve_url(base_url: str, href: str) -> str:
    """Resolve a link against a page URL after stripping HTML filenames from the base."""
    parsed = urlparse(base_url)
    if parsed.path.endswith((".html", ".htm")):
        clean_path = parsed.path.rsplit("/", 1)[0] + "/"
        base_url = parsed._replace(path=clean_path).geturl()
    return urljoin(base_url, href)


def _normalize_discovered_url(raw_url: str, base_url: str) -> str | None:
    """Resolve, normalize, and filter a crawler-discovered URL."""
    if not raw_url:
        return None

    resolved = resolve_url(base_url, raw_url)
    normalized = normalize_url(resolved, base_url)
    if normalized and not should_skip_url(normalized):
        return normalized
    return None


def _add_normalized(targets: set[str], raw_url: str, base_url: str) -> str | None:
    """Normalize a discovered URL and add it to a set."""
    normalized = _normalize_discovered_url(raw_url, base_url)
    if normalized:
        targets.add(normalized)
    return normalized


def _remember_rendered_html(url: str, html: str) -> None:
    """Cache rendered HTML under both the requested and normalized URL forms."""
    normalized = normalize_url(url, url)
    if normalized:
        RENDERED_HTML_CACHE[normalized] = html
    RENDERED_HTML_CACHE[url] = html


def _track_asset(assets_found: set[str], raw_url: str, base_url: str) -> None:
    """Normalize a browser-observed asset URL and add it when useful."""
    normalized = _normalize_discovered_url(raw_url, base_url)
    if normalized and not any(skip in normalized.lower() for skip in PLAYWRIGHT_SKIP_PATTERNS):
        assets_found.add(normalized)


def _click_interactive_elements(page) -> None:
    """Click tabs and accordions so their panels render before saving HTML."""
    tab_selectors = [
        '[role="tab"]',
        "[data-tab]",
        ".tab-btn",
        ".tab-button",
        '[class*="tab-item"]',
        '[class*="tab_item"]',
    ]
    accordion_selectors = [".accordion-header", "[data-accordion]", ".faq-question"]

    for selector in tab_selectors:
        try:
            for button in page.query_selector_all(selector):
                try:
                    button.click(timeout=1000)
                    page.wait_for_timeout(300)
                except Exception:
                    pass
        except Exception:
            pass

    for selector in accordion_selectors:
        try:
            for item in page.query_selector_all(selector):
                try:
                    item.click(timeout=1000)
                    page.wait_for_timeout(200)
                except Exception:
                    pass
        except Exception:
            pass


def fetch_page_with_playwright(url: str, log_callback=None) -> tuple[str, set[str]]:
    """Fetch a fully rendered page and every resource requested by the browser."""
    from playwright.sync_api import sync_playwright

    assets_found: set[str] = set()
    if log_callback:
        log_callback("Launching browser engine...")
        log_callback(f"Loading page: {url} (waiting for JS to render...)")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            viewport={"width": 1920, "height": 1080},
        )
        page = context.new_page()

        def handle_request(request):
            _track_asset(assets_found, request.url, url)

        page.on("request", handle_request)
        page.goto(url, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(2000)

        page.evaluate(
            """async () => {
                await new Promise(resolve => {
                    let y = 0;
                    const step = setInterval(() => {
                        window.scrollBy(0, 400);
                        y += 400;
                        if (y >= document.body.scrollHeight) {
                            clearInterval(step);
                            window.scrollTo(0, 0);
                            resolve();
                        }
                    }, 150);
                });
            }"""
        )
        page.wait_for_timeout(1500)
        _click_interactive_elements(page)
        rendered_html = page.content()
        final_url = page.url
        browser.close()

    _remember_rendered_html(url, rendered_html)
    _remember_rendered_html(final_url, rendered_html)
    assets_found.add(normalize_url(final_url, url) or final_url)
    if log_callback:
        log_callback(f"Page loaded. Found {len(assets_found)} assets via network interception.")
    return rendered_html, assets_found


def fetch_page_with_fallback(url: str, log_callback=None) -> tuple[str, set[str], str]:
    """Use Playwright for rendered pages, falling back to requests if needed."""
    try:
        html, assets = fetch_page_with_playwright(url, log_callback=log_callback)
        return html, assets, url
    except Exception as exc:
        if log_callback:
            log_callback(f"Playwright failed: {exc}, falling back to requests")

        response = requests.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
        response.raise_for_status()
        html = response.text
        page_links, assets = extract_urls_from_html(html, response.url)
        _remember_rendered_html(url, html)
        _remember_rendered_html(response.url, html)
        return html, assets | page_links, response.url


def _extract_srcset(value: str) -> list[str]:
    """Pull URLs out of srcset values like 'image.webp 1x, image@2x.webp 2x'."""
    urls = []
    for item in (value or "").split(","):
        parts = item.strip().split()
        if parts:
            urls.append(parts[0])
    return urls


def _extract_css_urls(css_text: str) -> list[str]:
    """Find url(...) references in inline CSS or downloaded CSS files."""
    results = []
    for match in CSS_URL_PATTERN.finditer(css_text or ""):
        raw = match.group(2).strip()
        if raw and not raw.startswith(("data:", "#")):
            results.append(raw)
    return results


def detect_wordpress(html: str) -> bool:
    """Detect common WordPress fingerprints in a page."""
    soup = BeautifulSoup(html, "html.parser")
    generator = soup.find("meta", attrs={"name": re.compile("^generator$", re.IGNORECASE)})
    if generator and "wordpress" in generator.get("content", "").lower():
        return True

    return "/wp-content/" in html or "/wp-includes/" in html


def detect_dynamic_content(html: str) -> bool:
    """Detect tabs, sliders, accordions, and scripts that load content dynamically."""
    soup = BeautifulSoup(html, "html.parser")

    if soup.select("[data-tab], [data-panel], [data-section]"):
        return True

    for tag in soup.find_all(class_=True):
        class_text = " ".join(tag.get("class", [])).lower()
        if any(keyword in class_text for keyword in DYNAMIC_CLASS_KEYWORDS):
            return True

    for script in soup.find_all("script"):
        script_text = script.get_text() or ""
        if any(pattern in script_text for pattern in ("fetch(", "axios(", "$.ajax(", "$.get(")):
            return True

    return False


def _site_root(url: str) -> str:
    """Return scheme and netloc for building well-known WordPress URLs."""
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _discover_wordpress_json(root_url: str, endpoint: str, log_callback=None) -> set[str]:
    """Read WordPress REST API items and return their public links."""
    discovered: set[str] = set()
    api_url = urljoin(root_url, endpoint)
    try:
        response = requests.get(api_url, headers=HEADERS, timeout=15, allow_redirects=True)
        response.raise_for_status()
        for item in response.json():
            link = item.get("link") if isinstance(item, dict) else None
            normalized = _normalize_discovered_url(link, root_url) if link else None
            if normalized:
                discovered.add(normalized)
    except Exception as exc:
        if log_callback:
            log_callback(f"WordPress API skipped {api_url}: {exc}")
    return discovered


def _discover_sitemap_urls(sitemap_url: str, root_url: str, log_callback=None) -> set[str]:
    """Parse sitemap XML and return page URLs, following sitemap indexes one level."""
    discovered: set[str] = set()
    try:
        response = requests.get(sitemap_url, headers=HEADERS, timeout=15, allow_redirects=True)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        locs = [loc.get_text(strip=True) for loc in soup.find_all("loc")]
        for loc in locs:
            if loc.endswith(".xml"):
                discovered.update(_discover_sitemap_urls(loc, root_url, log_callback))
                continue
            normalized = _normalize_discovered_url(loc, root_url)
            if normalized:
                discovered.add(normalized)
    except Exception as exc:
        if log_callback:
            log_callback(f"Sitemap skipped {sitemap_url}: {exc}")
    return discovered


def _discover_robots_sitemaps(root_url: str, log_callback=None) -> set[str]:
    """Read robots.txt and discover URLs from referenced sitemaps."""
    discovered: set[str] = set()
    robots_url = urljoin(root_url, "/robots.txt")
    try:
        response = requests.get(robots_url, headers=HEADERS, timeout=15, allow_redirects=True)
        response.raise_for_status()
        for line in response.text.splitlines():
            if line.lower().startswith("sitemap:"):
                sitemap_url = line.split(":", 1)[1].strip()
                discovered.update(_discover_sitemap_urls(sitemap_url, root_url, log_callback))
    except Exception as exc:
        if log_callback:
            log_callback(f"robots.txt skipped {robots_url}: {exc}")
    return discovered


def _discover_wordpress_pages(page_url: str, log_callback=None) -> set[str]:
    """Discover additional WordPress posts/pages from REST and sitemap sources."""
    root_url = _site_root(page_url)
    discovered: set[str] = set()
    discovered.update(_discover_wordpress_json(root_url, "/wp-json/wp/v2/posts?per_page=50", log_callback))
    discovered.update(_discover_wordpress_json(root_url, "/wp-json/wp/v2/pages?per_page=50", log_callback))
    discovered.update(_discover_sitemap_urls(urljoin(root_url, "/sitemap.xml"), root_url, log_callback))
    discovered.update(_discover_sitemap_urls(urljoin(root_url, "/sitemap_index.xml"), root_url, log_callback))
    discovered.update(_discover_robots_sitemaps(root_url, log_callback))
    return {url for url in discovered if is_same_domain(url, root_url)}


def extract_urls_from_html(html: str, page_url: str) -> tuple[set[str], set[str]]:
    """Return (internal page links, asset links) discovered in one HTML document."""
    soup = BeautifulSoup(html, "html.parser")
    internal_pages: set[str] = set()
    assets: set[str] = set()

    # Anchor tags are crawl candidates only when they stay on the starting domain.
    for tag in soup.find_all("a", href=True):
        normalized = _normalize_discovered_url(tag["href"], page_url)
        if normalized and is_same_domain(normalized, page_url):
            internal_pages.add(normalized)

    # These attributes reference static files, including lazy-loaded images.
    for tag in soup.find_all(True):
        if tag.name == "link" and tag.has_attr("href"):
            _add_normalized(assets, tag["href"], page_url)

        for attr in IMAGE_ATTRS:
            if tag.has_attr(attr):
                _add_normalized(assets, tag[attr], page_url)

        for attr in SRCSET_ATTRS:
            if tag.has_attr(attr):
                for srcset_url in _extract_srcset(tag[attr]):
                    _add_normalized(assets, srcset_url, page_url)

        # Inline style attributes can contain lazy background images.
        style = tag.get("style", "")
        if "url(" in style:
            for css_url in _extract_css_urls(style):
                _add_normalized(assets, css_url, page_url)

    for meta in soup.find_all("meta"):
        meta_key = meta.get("property") or meta.get("name")
        if meta_key in {"og:image", "twitter:image"}:
            content = meta.get("content")
            if content:
                _add_normalized(assets, content, page_url)

    # Inline styles can reference background images and font files.
    for style_tag in soup.find_all("style"):
        for css_url in _extract_css_urls(style_tag.get_text()):
            _add_normalized(assets, css_url, page_url)

    return internal_pages, assets


def crawl(start_url: str, max_depth: int = 3, log_callback=None) -> set[str]:
    """Crawl same-domain pages with BFS and return every URL that should be downloaded."""
    start_url = normalize_url(start_url, start_url)
    if not start_url or should_skip_url(start_url):
        return set()

    urls_to_download: set[str] = {start_url}
    visited_pages: set[str] = set()
    queue: deque[tuple[str, int]] = deque([(start_url, 0)])
    wordpress_checked = False

    while queue:
        page_url, depth = queue.popleft()
        if page_url in visited_pages or depth > max_depth:
            continue

        visited_pages.add(page_url)
        if log_callback:
            log_callback(f"Crawling depth {depth}: {page_url}")

        try:
            rendered_html, network_assets, fetched_url = fetch_page_with_fallback(page_url, log_callback)
            page_links, html_assets = extract_urls_from_html(rendered_html, fetched_url)
            assets = network_assets | html_assets

            if detect_dynamic_content(rendered_html) and log_callback:
                log_callback(f"Dynamic content detected: {fetched_url}")

            if not wordpress_checked and detect_wordpress(rendered_html):
                wordpress_checked = True
                if log_callback:
                    log_callback("WordPress site detected")
                page_links.update(_discover_wordpress_pages(fetched_url, log_callback))

            urls_to_download.update(url for url in assets if not should_skip_url(url))
            urls_to_download.add(page_url)

            if depth < max_depth:
                for link in sorted(page_links):
                    if link not in visited_pages and not should_skip_url(link):
                        urls_to_download.add(link)
                        queue.append((link, depth + 1))
        except Exception as exc:
            if log_callback:
                log_callback(f"Failed to crawl {page_url}: {exc}")

    return urls_to_download
