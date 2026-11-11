# -*- encoding: utf-8 -*-
"""B站视频多模态感知上下文：提供严格字数预算控制的视频摘要、字幕与热评提炼，并无缝挂载至主进程 newGeYi。"""

from __future__ import annotations

import re
import threading
import time
from typing import Optional, Dict, Any

from ..bili.client import BiliClient
from ..bili.resolver import extract_bvid


class VideoContext:
    """会话级 B 站视频轻量化感知服务（带严格 TTL 缓存与上下文长度预算控制）。"""

    def __init__(self, client: BiliClient, config: dict):
        self.client = client
        self.config = config
        self._lock = threading.RLock()
        # bvid -> (briefing_text, title, author, expire_at)
        self._cache: Dict[str, Dict[str, Any]] = {}
        # session_key -> last_video_info
        self._session_last_video: Dict[str, Dict[str, Any]] = {}

    def _clean_expired(self) -> None:
        now = time.time()
        with self._lock:
            expired_keys = [k for k, v in self._cache.items() if now > v.get('expire_at', 0)]
            for k in expired_keys:
                self._cache.pop(k, None)

    def resolve_video(self, bvid: str) -> Optional[Dict[str, Any]]:
        """获取视频结构化精简摘要，严格控制在 320 字符以内，杜绝上下文膨胀与延迟飙升。"""
        if not bvid:
            return None

        self._clean_expired()
        now = time.time()
        with self._lock:
            if bvid in self._cache:
                cached = self._cache[bvid]
                if now < cached.get('expire_at', 0):
                    return cached

        # 1. 抓取基础信息
        info = self.client.get_video_info(bvid)
        if not info:
            return None

        title = str(info.get('title', '') or '').strip()
        author = str(info.get('author', '') or '').strip()
        desc = str(info.get('desc', '') or '').strip()
        aid = int(info.get('aid') or 0)
        cid = int(info.get('cid') or 0)
        up_mid = int(info.get('up_mid') or 0)

        # 2. 抓取官方 AI 摘要、要点大纲与字幕对白（放宽字数至 500 字深度感知）
        content_items = []
        ai_sum = self.client.get_ai_summary(bvid, cid, up_mid) if (cid and up_mid) else None
        subtitles = self.client.get_subtitles(aid, cid, max_chars=500) if (aid and cid) else None

        if ai_sum and ai_sum.get('summary'):
            content_items.append(f"AI核心总结：{ai_sum['summary'].strip()[:200]}")
            outlines = ai_sum.get('outline') or []
            if outlines:
                pts = [sec.get('title', '').strip() for sec in outlines[:3] if sec.get('title', '').strip()]
                if pts:
                    content_items.append(f"要点大纲：{' ➔ '.join(pts)}")

        if subtitles:
            content_items.append(f"台词对白摘录：{subtitles.strip()[:220]}")

        if not content_items and desc:
            clean_desc = re.sub(r'(?:求三连|点赞|关注|投币|交流群|BGM|如题|简介).*', '', desc).strip()
            content_items.append(f"内容简介：{(clean_desc or desc)[:250]}")

        if not content_items:
            content_items.append("内容提要：（视频以画面展示与背景音乐为主，无字幕台词）")

        summary_block = '\n• '.join(content_items)

        # 3. 抓取热评（最多 3 条，单条限 50 字，总长约 130 字）
        comments = self.client.get_hot_comments(aid, limit=3) if aid else []
        clean_comments = []
        for c in comments:
            c_text = c.strip()[:50]
            if c_text and not c_text.startswith(('http', 'bv', 'BV')):
                clean_comments.append(c_text)

        comment_str = '；'.join(clean_comments) if clean_comments else '（暂无热评）'

        # 4. 组装高密度、全方位的精简背景块（总字数约 480~520 字符）
        briefing = (
            f"【B站视频背景（你已在后台自动读取该视频，请结合内容以歌伊的性格自然回应）】\n"
            f"• 视频：《{title[:60]}》（UP主：{author[:25]}）\n"
            f"• {summary_block}\n"
            f"• 观众热评：{comment_str[:130]}"
        )

        data = {
            'bvid': bvid,
            'title': title,
            'author': author,
            'briefing': briefing,
            'summary': summary_block[:160],
            'expire_at': now + 300,  # 缓存 5 分钟
        }

        with self._lock:
            self._cache[bvid] = data
        return data

    def is_wander_command(self, text: str) -> bool:
        """检查文本是否属于 B 站漫游专属触发指令（用于主进程 CommandRouter 拦截让出）。"""
        if not text:
            return False
        lower = text.lower().strip()
        # 核心防误判：如果包含任何视频链接、网址或BV号，说明是视频分享，绝对不是漫游摸鱼指令
        if any(h in lower for h in ('http://', 'https://', 'b23.tv', 'bilibili.com')) or re.search(r'BV[0-9A-Za-z]{10}', text, re.I):
            return False

        triggers = self.config.get('trigger_keywords', []) or [
            '.bili wander', '.bili',
            '歌伊随机看个视频', '随机看个视频', '随便看个视频', '随机刷个视频',
            '歌伊随便看个视频', '歌伊刷个视频', '歌伊看个视频', '歌伊看视频',
            '歌伊去摸鱼', '歌伊摸鱼', '歌伊去刷会儿b站', '歌伊刷b站', '歌伊看b站', '歌伊去刷b站'
        ]
        for trig in triggers:
            t_low = trig.lower()
            if t_low.startswith('.'):
                if lower.startswith(t_low):
                    return True
            elif t_low in lower:
                return True
        return False

    def get_briefing_for_message(self, msg: Any) -> Optional[str]:
        """针对接收到的 IncomingMessage，检测并生成注入 Prompt 的极简视频背景。"""
        raw_text = getattr(msg, 'raw_text', '') or getattr(msg, 'text', '') or ''
        bvid = extract_bvid(raw_text)
        if not bvid:
            return None

        data = self.resolve_video(bvid)
        if not data:
            return None

        # 记录到本会话最近视频记录，用于回复后的轻量记忆沉淀
        session_text = ''
        if hasattr(msg, 'session') and hasattr(msg.session, 'as_text'):
            session_text = msg.session.as_text()
        elif hasattr(msg, 'session_key'):
            session_text = str(msg.session_key)

        if session_text:
            with self._lock:
                self._session_last_video[session_text] = data

        return data.get('briefing')

    def on_video_replied(self, msg: Any, reply_text: str, store: Any = None) -> None:
        """当歌伊成功回复后，进行轻量化记忆沉淀（严格控制事实长度在 50 字以内，杜绝数据库膨胀）。"""
        session_text = ''
        if hasattr(msg, 'session') and hasattr(msg.session, 'as_text'):
            session_text = msg.session.as_text()
        elif hasattr(msg, 'session_key'):
            session_text = str(msg.session_key)

        with self._lock:
            data = self._session_last_video.pop(session_text, None)

        if not data or not store:
            return

        title = data.get('title', '')[:30]
        author = data.get('author', '')[:16]
        user_name = getattr(msg, 'sender_name', '群友')
        user_id = str(getattr(msg, 'sender_id', '') or '')

        try:
            # 1. L2 用户偏好轻量记忆（只记 35 字）
            if user_id and hasattr(store, 'add_user_fact'):
                store.add_user_fact(
                    session_key=session_text,
                    user_id=user_id,
                    fact=f"向歌伊安利了B站视频《{title}》(UP主:{author})",
                    category='bili_share',
                    source='explicit',
                    importance=0.75
                )

            # 2. L3 歌伊经历轻量记忆（只记 50 字）
            if hasattr(store, 'add_self_memory'):
                store.add_self_memory(
                    session_key=session_text,
                    content=f"群友【{user_name}】给歌伊分享了B站视频《{title}》，歌伊很感兴趣地和对方聊了聊",
                    kind='event',
                    importance=0.75
                )
        except Exception:
            pass
