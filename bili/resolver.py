# -*- encoding: utf-8 -*-
"""B站链接解析与 BV / AV 号提取。支持普通链接与 b23.tv 短链跳转还原。"""

from __future__ import annotations

import re
from typing import Optional
import requests

BV_PATTERN = re.compile(r'(BV[0-9A-Za-z]{10})', re.IGNORECASE)
AV_PATTERN = re.compile(r'(?:^|[^\w])av([0-9]+)', re.IGNORECASE)
B23_PATTERN = re.compile(r'https?://b23\.tv/[A-Za-z0-9_]+', re.IGNORECASE)


def extract_bvid(text: str) -> Optional[str]:
    """从文本或链接中提取出标准的 BV 号。如果是 b23.tv 短链则跟随跳转解析。"""
    if not text:
        return None

    # 清洗转义斜杠（例如 QQ 转发卡片 JSON 中的 \/）与编码
    text = text.replace(r'\/', '/').replace(r'\u002f', '/').replace(r'\U002F', '/')

    # 1. 直接正则匹配 BV 号
    match = BV_PATTERN.search(text)
    if match:
        raw_bvid = match.group(1)
        # 标准格式规范化为 BV 开头
        return 'BV' + raw_bvid[2:]

    # 2. 短链跳转解析 (b23.tv，支持无协议头或附带参数)
    b23_match = re.search(r'(?:https?://)?b23\.tv/([A-Za-z0-9_]+)', text, re.IGNORECASE)
    if b23_match:
        short_url = f"https://b23.tv/{b23_match.group(1)}"
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            # 优先使用 allow_redirects=False 直接从 302 Location 提取，免去请求被风控
            resp = requests.head(short_url, headers=headers, allow_redirects=False, timeout=5)
            loc = resp.headers.get('Location', '')
            if loc:
                m = BV_PATTERN.search(loc)
                if m:
                    return 'BV' + m.group(1)[2:]

            # 若未直接获取 Location，尝试跟随重定向
            if resp.status_code != 302:
                resp = requests.head(short_url, headers=headers, allow_redirects=True, timeout=5)
                final_url = resp.url
                match_after = BV_PATTERN.search(final_url)
                if match_after:
                    raw_bvid = match_after.group(1)
                    return 'BV' + raw_bvid[2:]
        except Exception:
            pass

    return None
