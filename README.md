# youtube-playlist-downloader

Download every video in a YouTube playlist as MP3 or MP4, with parallel
downloads, automatic retries, and optional proxy rotation.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

You'll also need [FFmpeg](https://ffmpeg.org/download.html) installed and on
your PATH — it's required for MP3 extraction and MP4 muxing.

## Usage

```bash
# Prompts for the playlist URL, downloads MP3s into ./downloads
python download_playlist.py

# Pass the URL directly, download as MP4 into a custom folder
python download_playlist.py "https://youtube.com/playlist?list=XXXX" -f mp4 -o my_videos

# More parallel downloads, verbose logging, logs saved to a file
python download_playlist.py "https://youtube.com/playlist?list=XXXX" -c 5 -v --log-file run.log
```

Run `python download_playlist.py --help` for the full list of options
(output format, quality, concurrency, retry count/backoff, proxy file, etc).

Videos that already exist in the output folder are skipped automatically, so
you can safely re-run the script on a partially-downloaded playlist.

### Using proxies (optional)

If you want to route downloads through proxies (e.g. to reduce rate-limit
risk on large playlists), copy `proxies.example.txt` to `proxies.txt`, add
your proxies (one per line — `http://`, `https://`, `socks4://`, or
`socks5://`), then validate them first:

```bash
python proxy_check.py proxies.txt -o working_proxies.txt
python download_playlist.py "https://youtube.com/playlist?list=XXXX" --proxies-file working_proxies.txt
```

`proxies.txt` is gitignored so you don't accidentally commit real proxy
credentials.

## Notes

- `yt-dlp` needs to stay reasonably up to date to keep working as YouTube
  changes its site; if downloads start failing, try `pip install -U yt-dlp`
  first.
- Failed downloads are retried with exponential backoff up to
  `--max-retries` (default 5) before being marked as failed; a summary of
  ok/skipped/unavailable/failed counts is printed at the end of each run.
