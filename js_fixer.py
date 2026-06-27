"""Offline JavaScript compatibility fixes."""

from pathlib import Path
import re


OFFLINE_FETCH_STUB = """// Offline mode: external API calls disabled
if (typeof window !== 'undefined' && !window.__siteClonerOfflineFetchInstalled) {
  window.__siteClonerOfflineFetchInstalled = true;
  const _originalFetch = window.fetch ? window.fetch.bind(window) : null;
  window.fetch = function(url, opts) {
    if (typeof url === 'string' && url.startsWith('http')) {
      return Promise.resolve(new Response('{}', {status: 200}));
    }
    return _originalFetch ? _originalFetch(url, opts) : Promise.resolve(new Response('{}', {status: 200}));
  };
}

"""


FETCH_ABSOLUTE_PATTERN = re.compile(r"fetch\(\s*(['\"])https?://.*?\1\s*(?:,\s*.*?)?\)", re.DOTALL)
XHR_OPEN_PATTERN = re.compile(
    r"\.open\(\s*(['\"])(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\1\s*,\s*(['\"])https?://.*?\3",
    re.IGNORECASE | re.DOTALL,
)


def _fix_js_text(js_text: str, live_domain: str) -> str:
    """Disable obvious external API calls while leaving local scripts usable."""
    fixed = js_text

    # Replace direct absolute fetch calls with a resolved empty JSON response.
    fixed = FETCH_ABSOLUTE_PATTERN.sub("Promise.resolve(new Response('{}', {status: 200}))", fixed)

    # Make direct external XMLHttpRequest.open URLs inert without breaking syntax.
    fixed = XHR_OPEN_PATTERN.sub(".open('GET', 'data:application/json,{}'", fixed)

    # Convert hardcoded live-domain strings to relative roots where possible.
    if live_domain:
        fixed = fixed.replace(f"https://{live_domain}", "")
        fixed = fixed.replace(f"http://{live_domain}", "")
        if live_domain.startswith("www."):
            bare_domain = live_domain[4:]
            fixed = fixed.replace(f"https://{bare_domain}", "")
            fixed = fixed.replace(f"http://{bare_domain}", "")

    if "window.__siteClonerOfflineFetchInstalled" not in fixed:
        fixed = OFFLINE_FETCH_STUB + fixed

    return fixed


def fix_javascript_files(output_dir: str | Path, start_url: str, log_callback=None) -> int:
    """Patch every downloaded JavaScript file for friendlier offline behavior."""
    output_dir = Path(output_dir)
    live_domain = ""
    try:
        from urllib.parse import urlparse

        live_domain = (urlparse(start_url).hostname or "").lower()
    except Exception:
        live_domain = ""

    fixed_count = 0
    for js_file in output_dir.rglob("*.js"):
        try:
            original = js_file.read_text(encoding="utf-8", errors="ignore")
            fixed = _fix_js_text(original, live_domain)
            if fixed != original:
                js_file.write_text(fixed, encoding="utf-8")
                fixed_count += 1
        except OSError as exc:
            if log_callback:
                log_callback(f"Failed to patch JS {js_file.name}: {exc}")

    if log_callback:
        log_callback(f"Patched {fixed_count} JavaScript files for offline mode.")

    return fixed_count
