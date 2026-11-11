# -*- encoding: utf-8 -*-
"""轻量 Bilibili REST 客户端：基于 requests 与 WBI 签名，零重型第三方依赖。"""

from __future__ import annotations

import hashlib
import re
import time
import urllib.parse
from typing import Optional, List, Dict, Any
import requests

MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49, 33, 9, 42, 19, 29, 28,
    14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54,
    21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]

HTML_TAG_PATTERN = re.compile(r'<[^>]+>')


def clean_html(text: str) -> str:
    """去除搜索结果中的 <em class=\"keyword\"> 标签。"""
    if not text:
        return ''
    return HTML_TAG_PATTERN.sub('', text).strip()


class BiliClient:
    """B站客户端，支持公共匿名与凭据模式，自带 WBI 签名、AI 摘要与字幕提取。"""

    def __init__(self, cookies: Optional[Dict[str, str]] = None, timeout: float = 6.0):
        self.cookies = cookies or {}
        self.timeout = timeout
        self._cached_mixin_key: Optional[str] = None
        self._key_expires_at: float = 0.0
        self.headers = {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'
            ),
            'Referer': 'https://www.bilibili.com/',
        }

    def _get_mixin_key(self) -> str:
        now = time.time()
        if self._cached_mixin_key and now < self._key_expires_at:
            return self._cached_mixin_key

        try:
            resp = requests.get(
                'https://api.bilibili.com/x/web-interface/nav',
                headers=self.headers,
                cookies=self.cookies,
                timeout=self.timeout
            ).json()
            wbi_img = resp.get('data', {}).get('wbi_img', {})
            img_url = wbi_img.get('img_url', '')
            sub_url = wbi_img.get('sub_url', '')
            img_key = img_url.rsplit('/', 1)[-1].split('.')[0]
            sub_key = sub_url.rsplit('/', 1)[-1].split('.')[0]
            raw = img_key + sub_key
            key = ''.join(raw[i] for i in MIXIN_KEY_ENC_TAB if i < len(raw))[:32]
            if len(key) == 32:
                self._cached_mixin_key = key
                self._key_expires_at = now + 43200  # 缓存 12 小时
                return key
        except Exception:
            pass

        # 兜底：若网络异常无法拉取新 key，使用已知有效的备用混淆 key
        return 'ea1db124af3c7062474693fa704f4ff8'

    def _wbi_sign(self, params: Dict[str, Any]) -> Dict[str, Any]:
        mixin_key = self._get_mixin_key()
        signed = dict(params)
        signed['wts'] = int(time.time())
        sorted_items = sorted(signed.items())
        query = urllib.parse.urlencode(sorted_items)
        w_rid = hashlib.md5((query + mixin_key).encode('utf-8')).hexdigest()
        signed['w_rid'] = w_rid
        return signed

    def search_videos(self, keyword: str, page: int = 1, order: str = 'pubdate') -> List[Dict[str, Any]]:
        """按关键词搜索视频列表。"""
        params = self._wbi_sign({
            'search_type': 'video',
            'keyword': keyword,
            'page': page,
            'order': order,
        })
        try:
            resp = requests.get(
                'https://api.bilibili.com/x/web-interface/wbi/search/type',
                params=params,
                headers=self.headers,
                cookies=self.cookies,
                timeout=self.timeout
            ).json()
            if resp.get('code') != 0:
                return []
            raw_list = resp.get('data', {}).get('result', [])
            items = []
            for item in raw_list:
                items.append({
                    'bvid': item.get('bvid', ''),
                    'aid': item.get('aid', 0),
                    'title': clean_html(item.get('title', '')),
                    'author': item.get('author', ''),
                    'description': clean_html(item.get('description', '')),
                    'duration': item.get('duration', ''),
                    'play': item.get('play', 0),
                    'pubdate': item.get('pubdate', 0),
                    'arcurl': item.get('arcurl', ''),
                    'pic': item.get('pic', ''),
                })
            return items
        except Exception:
            return []

    def get_video_info(self, bvid: str) -> Optional[Dict[str, Any]]:
        """获取单个视频的详细信息（标题、简介、UP主、分区、aid、cid、up_mid等）。"""
        data = None
        # 1. 优先使用带 WBI 签名的 wbi/view 接口（免登录/风控双重穿透）
        try:
            params = self._wbi_sign({'bvid': bvid})
            resp = requests.get(
                'https://api.bilibili.com/x/web-interface/wbi/view',
                params=params,
                headers=self.headers,
                cookies=self.cookies,
                timeout=self.timeout
            ).json()
            if resp.get('code') == 0:
                data = resp.get('data', {})
        except Exception:
            pass

        # 2. 兜底普通 view 接口
        if not data:
            try:
                resp = requests.get(
                    'https://api.bilibili.com/x/web-interface/view',
                    params={'bvid': bvid},
                    headers=self.headers,
                    cookies=self.cookies,
                    timeout=self.timeout
                ).json()
                if resp.get('code') == 0:
                    data = resp.get('data', {})
            except Exception:
                pass

        if not data:
            return None

        return {
            'bvid': data.get('bvid', bvid),
            'aid': data.get('aid', 0),
            'cid': data.get('cid', 0),
            'up_mid': data.get('owner', {}).get('mid', 0),
            'title': clean_html(data.get('title', '')),
            'desc': clean_html(data.get('desc', '')),
            'author': data.get('owner', {}).get('name', ''),
            'tname': data.get('tname', ''),
            'duration': data.get('duration', 0),
            'pic': data.get('pic', ''),
            'stat': data.get('stat', {}),
        }

    def get_ai_summary(self, bvid: str, cid: int, up_mid: int) -> Optional[Dict[str, Any]]:
        """获取 B 站官方 AI 视频结论与章节大纲（需 Cookie）。"""
        if not bvid or not cid or not up_mid:
            return None
        try:
            params = self._wbi_sign({
                'bvid': bvid,
                'cid': str(cid),
                'up_mid': str(up_mid),
            })
            resp = requests.get(
                'https://api.bilibili.com/x/web-interface/view/conclusion/get',
                params=params,
                headers=self.headers,
                cookies=self.cookies,
                timeout=self.timeout
            ).json()
            if resp.get('code') != 0:
                return None
            mr = resp.get('data', {}).get('model_result')
            if not mr or not isinstance(mr, dict):
                return None
            summary = str(mr.get('summary', '') or '').strip()
            outlines = []
            for sec in mr.get('outline', []) or []:
                sec_title = str(sec.get('title', '') or '').strip()
                pts = []
                for pt in sec.get('part_outline', []) or []:
                    cnt = str(pt.get('content', '') or '').strip()
                    if cnt:
                        pts.append(cnt)
                if sec_title or pts:
                    outlines.append({'title': sec_title, 'points': pts})
            return {
                'summary': summary,
                'outline': outlines,
            }
        except Exception:
            return None

    def get_subtitles(self, aid: int, cid: int, max_chars: int = 1500) -> Optional[str]:
        """获取 B 站官方/AI字幕文本流（需 Cookie 或有开放字幕）。"""
        if not aid or not cid:
            return None
        try:
            params = self._wbi_sign({
                'aid': str(aid),
                'cid': str(cid),
            })
            resp = requests.get(
                'https://api.bilibili.com/x/player/wbi/v2',
                params=params,
                headers=self.headers,
                cookies=self.cookies,
                timeout=self.timeout
            ).json()
            if resp.get('code') != 0:
                return None
            sub_list = resp.get('data', {}).get('subtitle', {}).get('subtitles', []) or []
            if not sub_list:
                return None

            # 优先选中文或排在第一的字幕轨
            target_track = None
            for track in sub_list:
                lan = track.get('lan', '')
                if 'zh' in lan:
                    target_track = track
                    break
            if not target_track:
                target_track = sub_list[0]

            sub_url = target_track.get('subtitle_url', '')
            if not sub_url:
                return None
            if sub_url.startswith('//'):
                sub_url = 'https:' + sub_url

            sub_body = requests.get(sub_url, timeout=self.timeout).json()
            lines = []
            total_len = 0
            for item in sub_body.get('body', []) or []:
                content = str(item.get('content', '') or '').strip()
                if content:
                    lines.append(content)
                    total_len += len(content)
                    if total_len >= max_chars:
                        break
            if lines:
                return ' '.join(lines)
        except Exception:
            pass
        return None

    def get_hot_comments(self, aid: int, limit: int = 3) -> List[str]:
        """获取视频的热门/置顶高赞评论（用于提炼吐槽点和梗）。"""
        if not aid:
            return []
        replies = []
        # 1. 优先使用 WBI 评论接口
        try:
            params = self._wbi_sign({'type': 1, 'oid': aid, 'mode': 3})
            resp = requests.get(
                'https://api.bilibili.com/x/v2/reply/wbi/main',
                params=params,
                headers=self.headers,
                cookies=self.cookies,
                timeout=self.timeout
            ).json()
            if resp.get('code') == 0:
                replies = resp.get('data', {}).get('replies', []) or []
        except Exception:
            pass

        # 2. 兜底普通评论接口
        if not replies:
            try:
                resp = requests.get(
                    'https://api.bilibili.com/x/v2/reply/main',
                    params={'type': 1, 'oid': aid, 'mode': 3},
                    headers=self.headers,
                    cookies=self.cookies,
                    timeout=self.timeout
                ).json()
                if resp.get('code') == 0:
                    replies = resp.get('data', {}).get('replies', []) or []
            except Exception:
                pass

        comments = []
        for r in replies[:limit]:
            msg = r.get('content', {}).get('message', '').strip()
            if msg:
                # 单条过长的评论截断前 100 字
                comments.append(msg[:100])
        return comments
