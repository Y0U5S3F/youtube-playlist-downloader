#!/usr/bin/env python3
"""
Validate a list of proxies (http/https/socks4/socks5) and optionally write
the working ones out to a clean file, ready to feed into:

    python download_playlist.py --proxies-file working_proxies.txt

Usage:
    python proxy_check.py proxies.txt
    python proxy_check.py proxies.txt -o working_proxies.txt -v
"""
import argparse
import asyncio
import logging
import time
from typing import List, Tuple

import aiohttp
from aiohttp_socks import ProxyConnector

LOG = logging.getLogger("proxy_check")
CHECK_URL = "https://httpbin.org/ip"


async def check_proxy(session: aiohttp.ClientSession, proxy: str, timeout: float) -> Tuple[str, bool]:
    start = time.monotonic()
    try:
        if proxy.startswith(("socks4://", "socks5://")):
            connector = ProxyConnector.from_url(proxy)
            async with aiohttp.ClientSession(connector=connector) as proxy_session:
                async with proxy_session.get(CHECK_URL, timeout=timeout) as resp:
                    ok = resp.status == 200
                    data = await resp.json() if ok else None
        else:
            async with session.get(CHECK_URL, proxy=proxy, timeout=timeout) as resp:
                ok = resp.status == 200
                data = await resp.json() if ok else None
    except Exception as exc:
        LOG.debug("%s failed: %s", proxy, exc)
        return proxy, False

    elapsed = time.monotonic() - start
    if ok:
        LOG.info("%s OK (%.2fs, exit IP %s)", proxy, elapsed, data.get("origin") if data else "?")
    else:
        LOG.info("%s returned non-200 status", proxy)
    return proxy, ok


async def check_all(proxies: List[str], timeout: float) -> List[Tuple[str, bool]]:
    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [check_proxy(session, p, timeout) for p in proxies]
        return await asyncio.gather(*tasks)


def load_proxies(path: str) -> List[str]:
    with open(path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip() and not line.startswith("#")]


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a list of proxies.")
    parser.add_argument("proxies_file", help="Path to a text file of proxies, one per line")
    parser.add_argument("-o", "--output", help="Write working proxies to this file")
    parser.add_argument("-t", "--timeout", type=float, default=10.0, help="Per-proxy timeout in seconds (default: 10)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(message)s")

    proxies = load_proxies(args.proxies_file)
    if not proxies:
        LOG.error("No proxies found in %s", args.proxies_file)
        return 1

    results = asyncio.run(check_all(proxies, args.timeout))
    working = [p for p, ok in results if ok]

    print(f"\n{len(working)}/{len(proxies)} proxies working:")
    for p in working:
        print(p)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write("\n".join(working) + ("\n" if working else ""))
        print(f"\nWrote working proxies to {args.output}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
