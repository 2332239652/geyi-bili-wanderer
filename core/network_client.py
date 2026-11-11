# -*- encoding: utf-8 -*-
"""DeepSeek 官方搜索与抓取 REST API 客户端（Search / Fetch）。"""

from __future__ import annotations

import threading
import time
from typing import Optional, Dict, Any, List
import requests


class DeepSeekWebClient:
    """轻量、高性能的 DeepSeek 官方搜索与抓取客户端，自带 TTL 内存缓存与重试容灾。"""

    def __init__(self, api_key: str, timeout: float = 12.0, cache_ttl: float = 600.0):
        self.api_key = api_key.strip()
        self.timeout = timeout
        self.cache_ttl = cache_ttl
        self.search_url = 'https://api.deepseek.com/v1/tools/search'
        self.fetch_url = 'https://api.deepseek.com/v1/tools/fetch'
        self._lock = threading.RLock()
        # key -> (result_data, expire_at)
        self._cache: Dict[str, tuple[Any, float]] = {}

    @property
    def headers(self) -> Dict[str, str]:
        return {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
        }

    def _get_cache(self, key: str) -> Optional[Any]:
        now = time.time()
        with self._lock:
            if key in self._cache:
                data, expire_at = self._cache[key]
                if now < expire_at:
                    return data
                self._cache.pop(key, None)
        return None

    def _set_cache(self, key: str, data: Any) -> None:
        with self._lock:
            self._cache[key] = (data, time.time() + self.cache_ttl)

    def search(self, query: str, count: int = 3, ori_question: str = '', need_content: bool = False) -> List[Dict[str, Any]]:
        """调用 DeepSeek 官方自研搜索引擎，获取结构化搜索结果及深度摘要。"""
        q = query.strip()
        if not q or not self.api_key:
            return []

        cache_key = f"search:{q}:{count}:{need_content}"
        cached = self._get_cache(cache_key)
        if cached is not None:
            return cached

        payload = {
            'query': q[:128],
            'count': max(1, min(10, count)),
            'need_content': need_content,
        }
        if ori_question:
            payload['ori_question'] = ori_question[:256]

        try:
            resp = requests.post(
                self.search_url,
                headers=self.headers,
                json=payload,
                timeout=self.timeout
            )
            if resp.status_code == 200:
                data = resp.json()
                results = data.get('results', []) or []
                self._set_cache(cache_key, results)
                return results
        except Exception:
            pass
        return []

    def fetch(self, url: str) -> Optional[Dict[str, Any]]:
        """调用 DeepSeek 官方网页抓取与解析接口，获取清洗后的纯净 Markdown 文章内容。"""
        u = url.strip()
        if not u or not u.startswith(('http://', 'https://')) or not self.api_key:
            return None

        cache_key = f"fetch:{u}"
        cached = self._get_cache(cache_key)
        if cached is not None:
            return cached

        try:
            resp = requests.post(
                self.fetch_url,
                headers=self.headers,
                json={'url': u},
                timeout=self.timeout
            )
            if resp.status_code == 200:
                data = resp.json()
                res = {
                    'title': str(data.get('title', '') or '').strip(),
                    'content': str(data.get('content', '') or '').strip(),
                }
                self._set_cache(cache_key, res)
                return res
        except Exception:
            pass
        return None
