#!/usr/bin/env python3
"""
YouTube playlist downloader.

Downloads every video in a YouTube playlist as MP3 (default) or MP4, with
configurable concurrency, bounded retries with exponential backoff, optional
proxy rotation, and skip-if-already-downloaded support.

Usage:
    python download_playlist.py "https://youtube.com/playlist?list=..."
    python download_playlist.py --format mp4 --concurrency 4 --output-dir out
    python download_playlist.py --proxies-file proxies.txt -v

Run `python download_playlist.py --help` for all options.
"""
import argparse
import concurrent.futures
import itertools
import logging
import os
import sys
import time
from typing import List, Optional, Tuple

import yt_dlp
from yt_dlp.utils import sanitize_filename

try:
    from tqdm import tqdm
    HAVE_TQDM = True
except ImportError:
    HAVE_TQDM = False

LOG = logging.getLogger("playlist_downloader")

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
)


def setup_logging(verbose: bool, log_file: Optional[str]) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    handlers: List[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
    )


def get_playlist_entries(playlist_url: str) -> List[Tuple[str, str]]:
    """
    Return a list of (video_url, title) pairs from a playlist in a single
    flat-extraction request. Avoids a separate per-video metadata fetch
    later just to learn the title.
    """
    ydl_opts = {"extract_flat": True, "quiet": True, "no_warnings": True}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            result = ydl.extract_info(playlist_url, download=False)
    except Exception as exc:
        LOG.error("Failed to read playlist: %s", exc)
        return []

    entries = []
    for entry in result.get("entries") or []:
        if entry is None:  # yt-dlp yields None for deleted/private entries
            continue
        video_id = entry.get("url") or entry.get("id")
        if not video_id:
            continue
        video_url = (
            video_id if video_id.startswith("http")
            else f"https://www.youtube.com/watch?v={video_id}"
        )
        title = entry.get("title") or video_id
        entries.append((video_url, title))
    return entries


def build_ydl_opts(output_folder: str, fmt: str, quality: str, proxy: Optional[str]) -> dict:
    opts = {
        "outtmpl": os.path.join(output_folder, "%(title)s.%(ext)s"),
        "http_headers": {"User-Agent": DEFAULT_USER_AGENT},
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
    }
    if proxy:
        opts["proxy"] = proxy

    if fmt == "mp3":
        opts["format"] = "bestaudio/best"
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": quality,
        }]
    else:  # mp4
        opts["format"] = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
        opts["merge_output_format"] = "mp4"

    return opts


def download_one(
    video_url: str,
    title: str,
    output_folder: str,
    fmt: str,
    quality: str,
    max_retries: int,
    base_wait: float,
    proxies: Optional[List[str]],
) -> Tuple[str, str]:
    """
    Download a single video.
    Returns (video_url, status) where status is one of:
    'ok', 'skipped', 'unavailable', 'failed'.
    """
    ext = "mp3" if fmt == "mp3" else "mp4"
    expected_file = os.path.join(output_folder, f"{sanitize_filename(title)}.{ext}")
    if os.path.exists(expected_file):
        LOG.info("Skipping (already exists): %s", title)
        return video_url, "skipped"

    proxy_cycle = itertools.cycle(proxies) if proxies else None
    wait = base_wait

    for attempt in range(1, max_retries + 1):
        proxy = next(proxy_cycle) if proxy_cycle else None
        ydl_opts = build_ydl_opts(output_folder, fmt, quality, proxy)
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([video_url])
            LOG.info("Downloaded: %s", title)
            return video_url, "ok"
        except Exception as exc:
            msg = str(exc)
            if any(s in msg for s in ("no longer available", "Video unavailable", "Private video")):
                LOG.warning("Unavailable, skipping: %s (%s)", title, msg)
                return video_url, "unavailable"

            LOG.warning("Attempt %d/%d failed for %s: %s", attempt, max_retries, title, msg)
            if attempt < max_retries:
                LOG.info("Retrying '%s' in %.0fs...", title, wait)
                time.sleep(wait)
                wait = min(wait * 2, 300)  # exponential backoff, capped at 5 minutes

    LOG.error("Giving up on '%s' after %d attempts", title, max_retries)
    return video_url, "failed"


def load_proxies(path: Optional[str]) -> Optional[List[str]]:
    if not path:
        return None
    if not os.path.exists(path):
        LOG.warning("Proxy file not found: %s (continuing without proxies)", path)
        return None
    with open(path, "r", encoding="utf-8") as f:
        proxies = [line.strip() for line in f if line.strip() and not line.startswith("#")]
    if not proxies:
        LOG.warning("Proxy file %s is empty, continuing without proxies", path)
        return None
    LOG.info("Loaded %d proxies from %s", len(proxies), path)
    return proxies


def run(args: argparse.Namespace) -> int:
    os.makedirs(args.output_dir, exist_ok=True)
    proxies = load_proxies(args.proxies_file)

    LOG.info("Reading playlist...")
    entries = get_playlist_entries(args.playlist_url)
    if not entries:
        LOG.error("No videos found in playlist. Exiting.")
        return 1
    LOG.info("Found %d videos.", len(entries))

    results = {"ok": 0, "skipped": 0, "unavailable": 0, "failed": 0}
    progress = tqdm(total=len(entries), unit="video") if HAVE_TQDM else None

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [
            pool.submit(
                download_one,
                url,
                title,
                args.output_dir,
                args.format,
                args.quality,
                args.max_retries,
                args.retry_wait,
                proxies,
            )
            for url, title in entries
        ]
        for future in concurrent.futures.as_completed(futures):
            _, status = future.result()
            results[status] += 1
            if progress:
                progress.update(1)

    if progress:
        progress.close()

    LOG.info(
        "Done. ok=%d skipped=%d unavailable=%d failed=%d",
        results["ok"], results["skipped"], results["unavailable"], results["failed"],
    )
    return 0 if results["failed"] == 0 else 2


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download every video in a YouTube playlist.")
    parser.add_argument("playlist_url", nargs="?", help="YouTube playlist URL (prompted if omitted)")
    parser.add_argument("-o", "--output-dir", default="downloads", help="Download folder (default: downloads)")
    parser.add_argument("-f", "--format", choices=["mp3", "mp4"], default="mp3", help="Output format (default: mp3)")
    parser.add_argument("-q", "--quality", default="192", help="MP3 quality in kbps (default: 192)")
    parser.add_argument("-c", "--concurrency", type=int, default=3, help="Parallel downloads (default: 3)")
    parser.add_argument("--max-retries", type=int, default=5, help="Max retry attempts per video (default: 5)")
    parser.add_argument(
        "--retry-wait", type=float, default=15.0,
        help="Initial retry wait in seconds; doubles each attempt up to 300s (default: 15)",
    )
    parser.add_argument("--proxies-file", default=None, help="Optional file of proxies to rotate through, one per line")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose (debug) logging")
    parser.add_argument("--log-file", default=None, help="Also write logs to this file")
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    setup_logging(args.verbose, args.log_file)

    if not args.playlist_url:
        args.playlist_url = input("Enter the YouTube playlist URL: ").strip()
    if not args.playlist_url:
        LOG.error("No playlist URL given.")
        return 1

    try:
        return run(args)
    except KeyboardInterrupt:
        LOG.warning("Interrupted by user.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
