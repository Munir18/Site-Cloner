"""Tkinter GUI for the SiteCloner application."""

from pathlib import Path
import os
import subprocess
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from crawler import crawl, ensure_playwright_browsers
from downloader import download_all
from utils import get_domain, normalize_url


BG = "#1a1a1a"
PANEL = "#2a2a2a"
TEXT = "#ffffff"
MUTED = "#c7c7c7"
ACCENT = "#E8541C"


class SiteClonerApp(tk.Tk):
    """Main dark-themed desktop window."""

    def __init__(self):
        super().__init__()
        self.title("SiteCloner")
        self.geometry("700x500")
        self.minsize(700, 500)
        self.configure(bg=BG)

        self.output_folder: Path | None = None
        self._build_styles()
        self._build_widgets()

    def _build_styles(self) -> None:
        """Configure ttk widgets to match the dark theme."""
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(
            "SiteCloner.Horizontal.TProgressbar",
            background=ACCENT,
            troughcolor=PANEL,
            bordercolor=PANEL,
            lightcolor=ACCENT,
            darkcolor=ACCENT,
        )

    def _build_widgets(self) -> None:
        """Create and place all Tkinter widgets."""
        title = tk.Label(self, text="SiteCloner", bg=BG, fg=TEXT, font=("Segoe UI", 24, "bold"))
        title.pack(pady=(18, 8))

        input_frame = tk.Frame(self, bg=BG)
        input_frame.pack(fill="x", padx=24, pady=(4, 10))

        self.url_input = tk.Entry(
            input_frame,
            bg=PANEL,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            font=("Segoe UI", 11),
        )
        self.url_input.pack(side="left", fill="x", expand=True, ipady=9)
        self.url_input.insert(0, "https://example.com")

        self.start_button = tk.Button(
            input_frame,
            text="Start Clone",
            command=self.start_clone,
            bg=ACCENT,
            fg=TEXT,
            activebackground="#ff6a2b",
            activeforeground=TEXT,
            relief="flat",
            font=("Segoe UI", 10, "bold"),
            padx=18,
            pady=8,
        )
        self.start_button.pack(side="left", padx=(10, 0))

        self.progress_bar = ttk.Progressbar(
            self,
            style="SiteCloner.Horizontal.TProgressbar",
            orient="horizontal",
            mode="determinate",
            maximum=100,
        )
        self.progress_bar.pack(fill="x", padx=24, pady=(2, 12))

        log_frame = tk.Frame(self, bg=BG)
        log_frame.pack(fill="both", expand=True, padx=24, pady=(0, 14))

        self.log_area = tk.Text(
            log_frame,
            bg="#101010",
            fg=MUTED,
            insertbackground=TEXT,
            relief="flat",
            wrap="word",
            font=("Consolas", 10),
        )
        self.log_area.pack(side="left", fill="both", expand=True)

        scrollbar = tk.Scrollbar(log_frame, command=self.log_area.yview)
        scrollbar.pack(side="right", fill="y")
        self.log_area.config(yscrollcommand=scrollbar.set)

        self.open_button = tk.Button(
            self,
            text="Open Folder",
            command=self.open_folder,
            bg=PANEL,
            fg=TEXT,
            activebackground="#3a3a3a",
            activeforeground=TEXT,
            relief="flat",
            font=("Segoe UI", 10, "bold"),
            padx=18,
            pady=8,
        )

    def log(self, message: str) -> None:
        """Safely append a line to the GUI log from any thread."""
        def append() -> None:
            self.log_area.insert("end", f"{message}\n")
            self.log_area.see("end")

        self.after(0, append)

    def update_progress(self, completed: int, total: int) -> None:
        """Safely update the progress bar from any thread."""
        percent = 0 if total <= 0 else min(100, int((completed / total) * 100))
        self.after(0, lambda: self.progress_bar.config(value=percent))

    def start_clone(self) -> None:
        """Validate user input and start work in a background thread."""
        raw_url = self.url_input.get().strip()
        if not raw_url.startswith(("http://", "https://")):
            messagebox.showerror("Invalid URL", "URL must start with http:// or https://")
            return

        normalized_url = normalize_url(raw_url, raw_url)
        if not normalized_url:
            messagebox.showerror("Invalid URL", "Please enter a valid website URL.")
            return

        domain = get_domain(normalized_url)
        self.output_folder = Path.home() / "Downloads" / "SiteCloner" / domain

        self.start_button.config(state="disabled")
        self.open_button.pack_forget()
        self.progress_bar.config(value=0)
        self.log_area.delete("1.0", "end")
        self.log(f"Output folder: {self.output_folder}")

        worker = threading.Thread(target=self._clone_worker, args=(normalized_url,), daemon=True)
        worker.start()

    def _clone_worker(self, url: str) -> None:
        """Run crawler and downloader outside the GUI event loop."""
        try:
            ensure_playwright_browsers(log_callback=self.log)
            self.log("Starting crawl...")
            urls = crawl(url, max_depth=3, log_callback=self.log)
            self.log(f"Page crawl complete. Found {len(urls)} total URLs.")
            self.log(f"Downloading {len(urls)} assets...")

            summary = download_all(
                urls,
                base_url=url,
                output_dir=self.output_folder,
                progress_callback=self.update_progress,
                log_callback=self.log,
            )

            self.update_progress(1, 1)
            self.log(f"Done. {summary['downloaded']} files downloaded, {summary['failed']} failed.")
            self.log(
                f"Verification: {summary['verified_ok']} assets verified OK, "
                f"{summary['missing_assets']} assets missing."
            )
            self.after(0, self._show_completion)
        except Exception as exc:
            self.log(f"Clone failed: {exc}")
            self.after(0, lambda: messagebox.showerror("Clone failed", str(exc)))
        finally:
            self.after(0, lambda: self.start_button.config(state="normal"))

    def _show_completion(self) -> None:
        """Reveal the Open Folder button after a successful run."""
        self.open_button.pack(pady=(0, 18))

    def open_folder(self) -> None:
        """Open the output folder in Windows File Explorer."""
        if self.output_folder and self.output_folder.exists():
            os.startfile(self.output_folder)
        else:
            messagebox.showinfo("Folder missing", "The output folder does not exist yet.")


def run() -> None:
    """Launch the Tkinter app."""
    app = SiteClonerApp()
    app.mainloop()
