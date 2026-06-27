"""Entry point for the SiteCloner desktop application."""

from gui import run
from crawler import ensure_playwright_browsers


if __name__ == "__main__":
    # The GUI also runs this with visible log output before crawling.
    ensure_playwright_browsers()
    run()
