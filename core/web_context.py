# -*- encoding: utf-8 -*-
"""实时网络多模态感知上下文：集成 DeepSeek 官方搜索与抓取能力，意图自适应路由与严格 400 字符预算控制。"""

from __future__ import annotations

import re
import threading
import time
from typing import Optional, Dict, Any, List

from .network_client import DeepSeekWebClient

URL_REGEX = re.compile(r'https?://[^\s<>"\)\]]+')
IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.ico')

EXPLICIT_SEARCH_TRIGGERS = [
    '歌伊查一下', '歌伊搜搜', '歌伊查查', '歌伊搜一下', '歌伊搜索',
    '歌伊找找', '歌伊这是什么', '歌伊帮我查', '歌伊帮我搜', '歌伊去搜',
    '歌伊去查', '歌伊查', '歌伊搜', '.search', '.web'
]


class WebContext:
    """全网实时感知服务中枢（支持网页自动抓取解析、深度搜索问答、字数严格预算与缓存）。"""

    def __init__(self, client: DeepSeekWebClient, config: dict):
        self.client = client
        self.config = config
        self._lock = threading.RLock()
        # session_key -> last_web_info
        self._session_last_query: Dict[str, Dict[str, Any]] = {}

    @staticmethod
    def extract_non_bili_url(text: str) -> Optional[str]:
        """提取消息中的通用网页链接（排除 B 站视频链接与静态图片资源）。"""
        if not text:
            return None
        matches = URL_REGEX.findall(text)
        for u in matches:
            u_clean = u.rstrip('.,;!?:，。！？')
            lower_u = u_clean.lower()
            if 'bilibili.com' in lower_u or 'b23.tv' in lower_u:
                continue
            if any(lower_u.endswith(ext) for ext in IMAGE_EXTENSIONS):
                continue
            return u_clean
        return None

    @staticmethod
    def extract_search_intent(text: str) -> Optional[str]:
        """智能判断消息中是否包含查证、搜索或时效资讯意图，并提取干净的搜索关键词。"""
        if not text:
            return None
        raw = text.strip()

        # 1. 显式前缀指令匹配
        for trig in EXPLICIT_SEARCH_TRIGGERS:
            pos = raw.find(trig)
            if pos != -1:
                q = raw[pos + len(trig):].strip(' ，。：:、！!~?？')
                if q and len(q) >= 2:
                    return q[:64]

        # 2. 自然口语化时效/新闻提问意图
        time_news_patterns = [
            r'今天(?:有什么|发生什么|有哪些)?(?:新闻|热搜|大事|八卦)',
            r'最近(?:有什么|发生什么)?(?:新闻|热搜|重大事件)',
            r'(?:最新|今天|昨天的)?(?:新闻|热点)\s*(?:是什么|有哪些)?',
        ]
        for pat in time_news_patterns:
            if re.search(pat, raw):
                return '今天 国际国内热点新闻'

        # 3. "什么是XXX" / "XXX是什么意思" 提问模式（当明确艾特或提到了歌伊）
        if '歌伊' in raw:
            what_is = re.search(r'(?:什么是|介绍一下|科普一下|了解一下)([\u4e00-\u9fa5A-Za-z0-9_\-\s]{2,20})', raw)
            if what_is:
                return what_is.group(1).strip()
            meaning_of = re.search(r'([\u4e00-\u9fa5A-Za-z0-9_\-\s]{2,20})(?:是什么意思|是什么梗|是什么玩意|指什么)', raw)
            if meaning_of:
                return meaning_of.group(1).strip()

        return None

    def resolve_for_message(self, msg: Any) -> Optional[str]:
        """针对接收到的消息，自动决策是否调用 Fetch/Search，并组装 500~600 字符的高密度网络简报。"""
        if not self.config.get('web_search_enabled', True):
            return None

        raw_text = getattr(msg, 'raw_text', '') or getattr(msg, 'text', '') or ''
        session_text = ''
        if hasattr(msg, 'session') and hasattr(msg.session, 'as_text'):
            session_text = msg.session.as_text()

        # ========== 场景 1：消息包含通用网页链接（自动抓取正文，放宽至 500-600 字） ==========
        url = self.extract_non_bili_url(raw_text)
        if url:
            fetch_res = self.client.fetch(url)
            if fetch_res and fetch_res.get('content'):
                title = (fetch_res.get('title') or '网页文章')[:45]
                # 清洗换行与格式符号，截取前 450 字符高密度正文
                clean_body = re.sub(r'[\r\n\t]+', ' ', fetch_res['content']).strip()
                clean_body = re.sub(r'\s{2,}', ' ', clean_body)[:450]

                briefing = (
                    f"【实时网络网页资料（你已通过联网工具抓取该链接，请以歌伊的性格自然回应群友，切勿机械念稿）】\n"
                    f"• 网页：《{title}》\n"
                    f"• 正文核心深度内容：{clean_body}\n"
                    f"• 链接来源：{url[:60]}"
                )

                if session_text:
                    with self._lock:
                        self._session_last_query[session_text] = {
                            'type': 'fetch',
                            'topic': title,
                            'brief': clean_body[:80]
                        }
                return briefing

        # ========== 场景 2：消息包含搜索/查证意图（官方深度搜索，双源交叉，放宽至 500-600 字） ==========
        query = self.extract_search_intent(raw_text)
        if query:
            results = self.client.search(query, count=2, ori_question=raw_text[:120], need_content=False)
            if results:
                # 优先挑选 rerank_score 最高的首条内容
                top_item = results[0]
                title = top_item.get('title', '')[:35]
                raw_sum = top_item.get('summary') or top_item.get('snippet') or ''
                clean_sum = re.sub(r'[\r\n\t]+', ' ', raw_sum).strip()
                clean_sum = re.sub(r'\s{2,}', ' ', clean_sum)[:340]

                source_name = top_item.get('site_name') or top_item.get('url') or ''
                if source_name.startswith('http'):
                    source_name = source_name.split('/')[2] if '/' in source_name else source_name

                # 补充第二条视角的关键结论（约 120 字符）
                second_part = ''
                if len(results) > 1 and results[1].get('summary'):
                    sec_item = results[1]
                    sec_title = sec_item.get('title', '')[:25]
                    sec_sum = re.sub(r'[\r\n\t]+', ' ', sec_item.get('summary', '')).strip()
                    sec_sum = re.sub(r'\s{2,}', ' ', sec_sum)[:120]
                    if sec_sum:
                        second_part = f"\n• 补充参考（{sec_title}）：{sec_sum}"

                briefing = (
                    f"【实时网络搜索资料（你已通过联网工具查阅，请以歌伊的性格自然回答群友，切勿机械念稿）】\n"
                    f"• 搜索主题：{query[:30]}\n"
                    f"• 核心事实：{clean_sum}{second_part}\n"
                    f"• 参考来源：{title} ({source_name[:25]})"
                )

                if session_text:
                    with self._lock:
                        self._session_last_query[session_text] = {
                            'type': 'search',
                            'topic': query,
                            'brief': clean_sum[:80]
                        }
                return briefing

        return None

    def on_web_replied(self, msg: Any, reply_text: str, store: Any = None) -> None:
        """歌伊回复完成后，进行轻量化网络见闻沉淀（严格控制在 40 字符以内）。"""
        session_text = ''
        if hasattr(msg, 'session') and hasattr(msg.session, 'as_text'):
            session_text = msg.session.as_text()

        with self._lock:
            data = self._session_last_query.pop(session_text, None)

        if not data or not store:
            return

        user_name = getattr(msg, 'sender_name', '群友')
        topic = data.get('topic', '')[:20]

        try:
            if hasattr(store, 'add_self_memory'):
                store.add_self_memory(
                    session_key=session_text,
                    content=f"群友【{user_name}】向歌伊询问了网络话题【{topic}】，歌伊联网查证后与对方作了探讨",
                    kind='event',
                    importance=0.7
                )
        except Exception:
            pass
