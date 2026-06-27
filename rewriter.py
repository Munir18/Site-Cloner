"""Rewrite downloaded HTML and CSS files so local copies work offline."""

from pathlib import Path
from urllib.parse import urlparse
import os
import re

from bs4 import BeautifulSoup

from utils import normalize_url, url_to_local_path


CSS_URL_PATTERN = re.compile(r"url\(\s*['\"]?([^'\"\)]+?)['\"]?\s*\)", re.IGNORECASE)
URL_ATTRIBUTES = (
    "src",
    "href",
    "data-src",
    "data-lazy-src",
    "data-lazy",
    "data-original",
    "data-url",
    "data-bg",
    "poster",
    "action",
)
SRCSET_ATTRIBUTES = ("srcset", "data-srcset")
OFFLINE_CONTENT_FIXER_SCRIPT = """(function() {
  // Fix 1: Make all tab/toggle buttons work by showing/hiding sibling panels
  document.querySelectorAll('[data-tab], .tab-btn, .tab-button, [role="tab"]').forEach(function(btn) {
    btn.addEventListener('click', function() {
      // Remove active from siblings
      var parent = btn.closest('[class*="tab"], [role="tablist"], ul, nav') || btn.parentElement;
      parent.querySelectorAll('[data-tab], .tab-btn, .tab-button, [role="tab"]').forEach(function(b) {
        b.classList.remove('active', 'is-active', 'current');
      });
      btn.classList.add('active');

      // Find and show corresponding panel
      var target = btn.getAttribute('data-tab') || btn.getAttribute('data-target') || btn.getAttribute('aria-controls');
      if (target) {
        document.querySelectorAll('[data-panel], [data-tab-content], .tab-pane, .tab-content > div').forEach(function(panel) {
          panel.style.display = 'none';
        });
        var panel = document.querySelector('#' + target + ', [data-panel="' + target + '"]');
        if (panel) panel.style.display = 'block';
      }
    });
  });

  // Fix 2: Make images with data-src load (manual lazy load trigger)
  function loadLazyImages() {
    document.querySelectorAll('img[data-src], img[data-lazy-src], img[data-lazy], img[data-original], img[data-url]').forEach(function(img) {
      var src = img.getAttribute('data-src') || img.getAttribute('data-lazy-src') || img.getAttribute('data-lazy') || img.getAttribute('data-original') || img.getAttribute('data-url');
      if (src) {
        img.src = src;
        img.removeAttribute('data-src');
        img.removeAttribute('data-lazy-src');
        img.removeAttribute('data-lazy');
        img.removeAttribute('data-original');
        img.removeAttribute('data-url');
      }
    });
    // Also background images
    document.querySelectorAll('[data-bg]').forEach(function(el) {
      el.style.backgroundImage = 'url(' + el.getAttribute('data-bg') + ')';
    });
  }

  // Run immediately and also after a short delay for JS-rendered content
  loadLazyImages();
  setTimeout(loadLazyImages, 500);
  setTimeout(loadLazyImages, 1500);

  // Fix 3: Suppress all console errors from failed network requests offline
  window.addEventListener('unhandledrejection', function(e) { e.preventDefault(); });
  window.onerror = function() { return true; };

  // Fix 4: Intercept fetch calls to external URLs and return empty success response
  var _fetch = window.fetch;
  window.fetch = function(url, opts) {
    if (typeof url === 'string' && (url.startsWith('http') || url.startsWith('//'))) {
      return Promise.resolve(new Response(JSON.stringify({}), {
        status: 200,
        headers: {'Content-Type': 'application/json'}
      }));
    }
    return _fetch.apply(this, arguments);
  };

  // Fix 5: Intercept XMLHttpRequest to external URLs
  var _open = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function(method, url) {
    if (typeof url === 'string' && url.startsWith('http')) {
      this._blocked = true;
    }
    return _open.apply(this, arguments);
  };
  var _send = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.send = function() {
    if (this._blocked) {
      setTimeout(function() {
        Object.defineProperty(this, 'readyState', {value: 4});
        Object.defineProperty(this, 'status', {value: 200});
        Object.defineProperty(this, 'responseText', {value: '{}'});
        this.onreadystatechange && this.onreadystatechange();
      }.bind(this), 10);
      return;
    }
    return _send.apply(this, arguments);
  };
})();"""


def _is_rewritable(value: str) -> bool:
    """Skip anchors, scripts, data URLs, and other non-file references."""
    value = (value or "").strip()
    return bool(value) and not value.startswith(("#", "data:", "mailto:", "tel:", "javascript:"))


def _relative_or_live_url(
    raw_url: str,
    source_url: str,
    file_path: Path,
    output_dir: Path,
    start_url: str | None = None,
) -> str:
    """Return a relative local path when downloaded, otherwise an absolute live URL."""
    if not _is_rewritable(raw_url):
        return raw_url

    absolute_url = normalize_url(raw_url, source_url)
    if not absolute_url:
        return raw_url

    asset_local_path = url_to_local_path(absolute_url, output_dir, start_url=start_url)
    if not asset_local_path.exists():
        # If a CDN stylesheet or image failed to download, keep a live URL fallback.
        return absolute_url

    relative = os.path.relpath(asset_local_path, os.path.dirname(file_path))
    return relative.replace(os.sep, "/")


def _rewrite_srcset(
    value: str,
    source_url: str,
    file_path: Path,
    output_dir: Path,
    start_url: str | None = None,
) -> str:
    """Rewrite each URL in a srcset while preserving width and density descriptors."""
    rewritten_items = []
    for item in (value or "").split(","):
        parts = item.strip().split()
        if not parts:
            continue

        parts[0] = _relative_or_live_url(parts[0], source_url, file_path, output_dir, start_url)
        rewritten_items.append(" ".join(parts))

    return ", ".join(rewritten_items)


def _rewrite_css_text(
    css_text: str,
    source_url: str,
    file_path: Path,
    output_dir: Path,
    start_url: str | None = None,
) -> str:
    """Rewrite all url(...) references inside CSS text."""
    def replace(match: re.Match) -> str:
        raw_url = match.group(1).strip()
        rewritten = _relative_or_live_url(raw_url, source_url, file_path, output_dir, start_url)
        return f"url('{rewritten}')"

    return CSS_URL_PATTERN.sub(replace, css_text)


def rewrite_html(
    filepath: str | Path,
    source_url: str,
    output_dir: str | Path,
    start_url: str | None = None,
) -> None:
    """Rewrite HTML attributes and inline CSS paths in place."""
    filepath = Path(filepath)
    output_dir = Path(output_dir)
    html = filepath.read_text(encoding="utf-8", errors="ignore")
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup.find_all(True):
        # Normal URL attributes, including stylesheet hrefs.
        for attr in URL_ATTRIBUTES:
            if tag.has_attr(attr):
                tag[attr] = _relative_or_live_url(tag[attr], source_url, filepath, output_dir, start_url)

        # srcset-like attributes can contain several candidate URLs.
        for attr in SRCSET_ATTRIBUTES:
            if tag.has_attr(attr):
                tag[attr] = _rewrite_srcset(tag[attr], source_url, filepath, output_dir, start_url)

        # Inline style="" attributes can reference background images and fonts.
        if tag.has_attr("style"):
            tag["style"] = _rewrite_css_text(tag["style"], source_url, filepath, output_dir, start_url)

        meta_key = tag.get("property") or tag.get("name")
        if tag.name == "meta" and meta_key in {"og:image", "twitter:image"} and tag.has_attr("content"):
            tag["content"] = _relative_or_live_url(tag["content"], source_url, filepath, output_dir, start_url)

    # Inline <style> blocks use CSS url(...) syntax too.
    for style_tag in soup.find_all("style"):
        if style_tag.string:
            style_tag.string.replace_with(
                _rewrite_css_text(style_tag.string, source_url, filepath, output_dir, start_url)
            )
        else:
            style_text = style_tag.get_text()
            style_tag.clear()
            style_tag.append(_rewrite_css_text(style_text, source_url, filepath, output_dir, start_url))

    fallback_script = soup.new_tag("script")
    fallback_script.string = (
        "\n// Offline fallback: suppress all fetch/XHR errors silently\n"
        "window.addEventListener('unhandledrejection', function(e) { e.preventDefault(); });\n"
        "window.onerror = function(msg, src, line, col, err) { return true; };\n"
    )
    if soup.head:
        soup.head.insert(0, fallback_script)
    else:
        head = soup.new_tag("head")
        head.insert(0, fallback_script)
        if soup.html:
            soup.html.insert(0, head)
        else:
            soup.insert(0, head)

    if "Make all tab/toggle buttons work" not in html:
        body_script = soup.new_tag("script")
        body_script.string = "\n" + OFFLINE_CONTENT_FIXER_SCRIPT + "\n"
        if soup.body:
            soup.body.append(body_script)
        else:
            soup.append(body_script)

    filepath.write_text(str(soup), encoding="utf-8")


def rewrite_css(
    filepath: str | Path,
    source_url: str,
    output_dir: str | Path,
    start_url: str | None = None,
) -> None:
    """Rewrite url(...) references in one CSS file in place."""
    filepath = Path(filepath)
    output_dir = Path(output_dir)
    css = filepath.read_text(encoding="utf-8", errors="ignore")
    filepath.write_text(_rewrite_css_text(css, source_url, filepath, output_dir, start_url), encoding="utf-8")
