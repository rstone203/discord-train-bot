"""
TikTok account monitor with multi-method fallback.

Method 1: TikTok oEmbed API (semi-official, most stable)
Method 2: RSSHub public mirror (rss.app / rsshub.app)
Method 3: Lightweight HTML scraping with rotating user agents

Each method is tried in order. Failure of one method silently falls through
to the next. The monitor tracks the last seen video ID per account to avoid
duplicate posts.
"""

import asyncio
import logging
import re
import time
from datetime import datetime
from typing import Optional, Dict, Tuple

import aiohttp

logger = logging.getLogger('discord_bot.tiktok_monitor')

# Rotating user agents to reduce blocking
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1',
]

_ua_index = 0

def _next_ua() -> str:
    global _ua_index
    ua = USER_AGENTS[_ua_index % len(USER_AGENTS)]
    _ua_index += 1
    return ua


async def _fetch_via_oembed(username: str, session: aiohttp.ClientSession) -> Optional[Dict]:
    """
    Method 1: Check TikTok oEmbed by constructing the profile URL.
    Tries to find the latest video by probing the user's profile page for
    a canonical video link, then fetches its oEmbed.
    """
    try:
        profile_url = f'https://www.tiktok.com/@{username}'
        headers = {
            'User-Agent': _next_ua(),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Referer': 'https://www.tiktok.com/',
        }
        async with session.get(profile_url, headers=headers, timeout=aiohttp.ClientTimeout(total=12), allow_redirects=True) as resp:
            if resp.status != 200:
                return None
            html = await resp.text(errors='replace')

        # Extract video IDs from profile HTML
        video_ids = re.findall(r'(?:video|item)/(\d{15,20})', html)
        if not video_ids:
            # Try JSON-LD or __UNIVERSAL_DATA__ patterns
            video_ids = re.findall(r'"id"\s*:\s*"(\d{15,20})"', html)

        if not video_ids:
            return None

        # Deduplicate preserving order, take first (most recent)
        seen = set()
        unique_ids = []
        for vid in video_ids:
            if vid not in seen:
                seen.add(vid)
                unique_ids.append(vid)

        latest_id = unique_ids[0]
        video_url = f'https://www.tiktok.com/@{username}/video/{latest_id}'

        # Fetch oEmbed for this video
        async with session.get(
            'https://www.tiktok.com/oembed',
            params={'url': video_url},
            headers={'User-Agent': _next_ua()},
            timeout=aiohttp.ClientTimeout(total=8)
        ) as oe_resp:
            if oe_resp.status == 200:
                data = await oe_resp.json(content_type=None)
                return {
                    'video_id': latest_id,
                    'url': video_url,
                    'title': data.get('title', '').strip() or f'New TikTok from @{username}',
                    'author': data.get('author_name', '').strip() or username,
                    'author_url': data.get('author_url', '') or f'https://www.tiktok.com/@{username}',
                    'thumbnail': data.get('thumbnail_url', ''),
                    'method': 'oembed',
                }

        # oEmbed failed but we have the URL
        return {
            'video_id': latest_id,
            'url': video_url,
            'title': f'New TikTok from @{username}',
            'author': username,
            'author_url': f'https://www.tiktok.com/@{username}',
            'thumbnail': '',
            'method': 'oembed_partial',
        }

    except Exception as e:
        logger.debug(f'TikTok oEmbed method failed for @{username}: {e}')
        return None


async def _fetch_via_rss(username: str, session: aiohttp.ClientSession) -> Optional[Dict]:
    """
    Method 2: RSSHub public instance mirrors TikTok user feeds as RSS.
    Tries multiple public RSSHub mirrors in sequence.
    """
    rss_urls = [
        f'https://rsshub.app/tiktok/user/@{username}',
        f'https://rss.app/feeds/tiktok/{username}.xml',
        f'https://www.tiktok.com/@{username}/rss',
    ]
    for rss_url in rss_urls:
        try:
            headers = {'User-Agent': _next_ua(), 'Accept': 'application/rss+xml,application/xml,text/xml,*/*'}
            async with session.get(rss_url, headers=headers, timeout=aiohttp.ClientTimeout(total=10), allow_redirects=True) as resp:
                if resp.status != 200:
                    continue
                text = await resp.text(errors='replace')

            # Extract first item from RSS
            # <link> tag inside <item>
            items = re.findall(r'<item[^>]*>(.*?)</item>', text, re.DOTALL)
            if not items:
                continue

            first_item = items[0]
            link_match = re.search(r'<link[^>]*>([^<]+)</link>', first_item)
            title_match = re.search(r'<title[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>', first_item, re.DOTALL)

            if not link_match:
                continue

            video_url = link_match.group(1).strip()
            title = title_match.group(1).strip() if title_match else f'New TikTok from @{username}'

            # Extract video ID from URL
            vid_match = re.search(r'/video/(\d+)', video_url)
            video_id = vid_match.group(1) if vid_match else video_url[-20:]

            return {
                'video_id': video_id,
                'url': video_url,
                'title': title,
                'author': username,
                'author_url': f'https://www.tiktok.com/@{username}',
                'thumbnail': '',
                'method': 'rss',
            }

        except Exception as e:
            logger.debug(f'TikTok RSS method failed ({rss_url}) for @{username}: {e}')
            continue

    return None


async def _fetch_via_scrape(username: str, session: aiohttp.ClientSession) -> Optional[Dict]:
    """
    Method 3: Lightweight scrape of the TikTok mobile site.
    """
    try:
        url = f'https://www.tiktok.com/@{username}?lang=en'
        headers = {
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
        }
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15), allow_redirects=True) as resp:
            if resp.status != 200:
                return None
            html = await resp.text(errors='replace')

        # Look for video URLs in page source
        patterns = [
            r'https://www\.tiktok\.com/@[^"\'\\]+/video/(\d{15,20})',
            r'"webVideoUrl":"(https://www\.tiktok\.com/@[^"]+/video/\d+)"',
            r'"itemId"\s*:\s*"(\d{15,20})"',
        ]

        video_id = None
        video_url = None

        for pattern in patterns:
            matches = re.findall(pattern, html)
            if matches:
                if 'tiktok.com' in matches[0]:
                    video_url = matches[0].replace('\\u002F', '/').replace('\\/', '/')
                    vid_m = re.search(r'/video/(\d+)', video_url)
                    video_id = vid_m.group(1) if vid_m else matches[0][-20:]
                else:
                    video_id = matches[0]
                    video_url = f'https://www.tiktok.com/@{username}/video/{video_id}'
                break

        if not video_id:
            return None

        return {
            'video_id': video_id,
            'url': video_url or f'https://www.tiktok.com/@{username}/video/{video_id}',
            'title': f'New TikTok from @{username}',
            'author': username,
            'author_url': f'https://www.tiktok.com/@{username}',
            'thumbnail': '',
            'method': 'scrape',
        }

    except Exception as e:
        logger.debug(f'TikTok scrape method failed for @{username}: {e}')
        return None


async def get_latest_video(username: str) -> Optional[Dict]:
    """
    Try all methods in order and return the first successful result.
    Returns None if all methods fail.
    """
    username = username.lstrip('@').lower().strip()
    async with aiohttp.ClientSession() as session:
        for method_fn in [_fetch_via_oembed, _fetch_via_rss, _fetch_via_scrape]:
            result = await method_fn(username, session)
            if result:
                logger.debug(f'TikTok @{username}: got latest video via {result["method"]}: {result["video_id"]}')
                return result

    logger.warning(f'TikTok @{username}: all methods failed')
    return None
