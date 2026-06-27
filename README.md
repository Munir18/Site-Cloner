# SiteCloner

SiteCloner is a lightweight Python desktop application that downloads a website and its linked assets for offline browsing.

## Requirements

- Python 3.10+
- Windows 10 or Windows 11

SiteCloner uses BeautifulSoup with Python's built-in `html.parser`, so it does not require `lxml` or Microsoft C++ Build Tools.

## Setup

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

## Run

```bash
python main.py
```

## How to Use

1. Paste a URL that starts with `http://` or `https://`.
2. Click **Start Clone**.
3. Watch the live log and progress bar while files are downloaded.
4. Files are saved to `~/Downloads/SiteCloner/<domain>/`.
5. Open `index.html` in a browser to view the cloned site offline.

## Notes

- Internal links are crawled recursively up to depth 3.
- External website pages are not crawled, but external assets such as CDN images, fonts, CSS, and JavaScript can be downloaded when referenced.
- Failed downloads are written to `failed_downloads.txt` in the output folder.
