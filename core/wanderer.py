# -*- encoding: utf-8 -*-
"""闲时漫游核心引擎：定时搜索、第一人称见闻蒸馏、记忆注入与视频分享互动。"""

from __future__ import annotations

import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional, Dict, Any, List

from ..bili.client import BiliClient
from .keywords import KeywordsManager, classify_domain, DOMAINS
from .interop import call_geyi_llm, inject_self_memory, inject_user_share_memory, get_geyi_role_prompt


def _parse_quiet_hours(quiet_hours: str, now: float) -> bool:
    """检查当前时间是否处于免打扰时段（例如 01:00-07:00）。"""
    if not quiet_hours or '-' not in quiet_hours:
        return False
    try:
        start_str, end_str = quiet_hours.split('-', 1)
        sh, sm = map(int, start_str.strip().split(':'))
        eh, em = map(int, end_str.strip().split(':'))
        lt = time.localtime(now)
        current_m = lt.tm_hour * 60 + lt.tm_min
        start_m = sh * 60 + sm
        end_m = eh * 60 + em
        if start_m <= end_m:
            return start_m <= current_m < end_m
        else:
            return current_m >= start_m or current_m < end_m
    except Exception:
        return False


class WandererEngine:
    def __init__(self, db_path: Path, config: dict, keywords_mgr: KeywordsManager, client: Optional[BiliClient] = None):
        self.db_path = db_path
        self.config = config
        self.keywords_mgr = keywords_mgr
        self.client = client or BiliClient()
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._bg_thread: Optional[threading.Thread] = None
        self._last_wander_time = 0.0
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.execute('PRAGMA journal_mode=WAL')
        return conn

    def _init_db(self) -> None:
        with self._get_conn() as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS seen_videos (
                    bvid TEXT PRIMARY KEY,
                    title TEXT,
                    author TEXT,
                    first_seen_at REAL,
                    summary TEXT
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS wander_notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bvid TEXT,
                    title TEXT,
                    keyword TEXT,
                    content TEXT,
                    created_at REAL,
                    shared_count INTEGER DEFAULT 0
                )
            ''')
            conn.commit()

    def is_video_seen(self, bvid: str) -> bool:
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute('SELECT 1 FROM seen_videos WHERE bvid = ?', (bvid,))
            return cur.fetchone() is not None

    def record_seen_video(self, bvid: str, title: str, author: str, summary: str = '') -> None:
        with self._get_conn() as conn:
            conn.execute('''
                INSERT OR REPLACE INTO seen_videos (bvid, title, author, first_seen_at, summary)
                VALUES (?, ?, ?, ?, ?)
            ''', (bvid, title, author, time.time(), summary))
            conn.commit()

    def record_wander_note(self, bvid: str, title: str, keyword: str, content: str) -> int:
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute('''
                INSERT INTO wander_notes (bvid, title, keyword, content, created_at, shared_count)
                VALUES (?, ?, ?, ?, ?, 0)
            ''', (bvid, title, keyword, content, time.time()))
            conn.commit()
            return cur.lastrowid

    def get_recent_fresh_notes(self, limit: int = 3) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute('''
                SELECT bvid, title, keyword, content, created_at, shared_count
                FROM wander_notes
                WHERE shared_count = 0
                ORDER BY created_at DESC
                LIMIT ?
            ''', (limit,))
            rows = cur.fetchall()
            return [
                {
                    'bvid': r[0],
                    'title': r[1],
                    'keyword': r[2],
                    'content': r[3],
                    'created_at': r[4],
                    'shared_count': r[5],
                }
                for r in rows
            ]

    def mark_note_shared(self, bvid: str) -> None:
        with self._get_conn() as conn:
            conn.execute('''
                UPDATE wander_notes
                SET shared_count = shared_count + 1
                WHERE bvid = ?
            ''', (bvid,))
            conn.commit()

    def get_recent_wander_keywords(self, limit: int = 5) -> List[str]:
        """获取最近几次漫游已搜索过的关键词列表，用于防题材单一与审美疲劳轮转。"""
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute('''
                SELECT keyword FROM wander_notes
                WHERE keyword IS NOT NULL AND keyword != ''
                ORDER BY id DESC
                LIMIT ?
            ''', (int(limit),))
            rows = cur.fetchall()
            return [r[0] for r in rows if r[0]]

    def _generate_dynamic_keyword(self, target_domain: str = 'gaming', recent_keywords: Optional[List[str]] = None) -> Optional[str]:
        """通过大模型让歌伊在指定的冷门频段发散新词，并注入近期历史排除约束。"""
        dom_info = DOMAINS.get(target_domain, DOMAINS['gaming'])
        dom_name = dom_info['name']
        dom_desc = dom_info['desc']
        recent_kws = recent_keywords or []
        recent_str = '、'.join(f'「{k}」' for k in recent_kws[:4]) if recent_kws else '无'

        system_prompt = (
            "你是白黑修女服人设的二次元跑团爱好者「歌伊」。"
            "你在B站涉猎广泛，兴趣丰富，横跨跑团TRPG、单机游戏、二次元动漫、沙雕日常和前沿AI科技。"
            "你很有探索欲，从不单恋某一个题材，刷完一个领域就会主动切换到另一个截然不同的频道换换脑子。"
        )
        user_prompt = (
            f"【近期观影记录】：你最近刚连续刷了关于 {recent_str} 的视频。\n"
            f"现在你对这些已经有点看腻了，感到明显的审美疲劳，急切地想换个频道透透气！\n\n"
            f"【本次切换的频道】：{dom_name}（具体涵盖：{dom_desc}）\n\n"
            f"请在这个【{dom_name}】频道内，发散你此刻最强烈的探索好奇心，给出一个具体想搜的短语（2~8个字，例如：独立神作实况、经典动漫吐槽、搞笑整活翻车、AI多模态实测等）。\n"
            f"【严格要求】：\n"
            f"1. 必须完全符合【{dom_name}】频道内容；\n"
            f"2. 绝对严禁输出与近期搜索（{recent_str}）题材相似的词汇；\n"
            f"3. 只输出这1个简短搜索词本身，严禁包含任何标点符号、解释说明或前后缀！"
        )
        return call_geyi_llm(user_prompt, system_prompt=system_prompt)

    def do_wander(self, forced_keyword: Optional[str] = None,
                  session_key: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """执行一次完整的漫游：选词 -> 搜视频 -> 选新视频 -> 读简介与热评 -> 蒸馏见闻 -> 注入L3记忆。

        `session_key` 给了就只写那个会话的记忆；不给（老行为）才会广播到最近 20 个会话。
        手动触发一律应该传它——以前手动摸一次鱼，20 个群都会凭空多出一条 importance 0.8
        的「我在B站刷到了…」，把「自身记忆」的三个名额全占了。
        """
        with self._lock:
            # 1. 挑选关键词（带有五频段轮转与近期防单一排除）
            recent_kws = self.get_recent_wander_keywords(limit=5)
            recent_doms = [classify_domain(k) for k in recent_kws]

            if forced_keyword:
                keyword = forced_keyword.strip()
            else:
                keyword = self.keywords_mgr.pick_keyword(
                    dynamic_ratio=float(self.config.get('dynamic_interest_ratio', 0.35)),
                    llm_generator=self._generate_dynamic_keyword,
                    recent_keywords=recent_kws,
                    recent_domains=recent_doms
                )

            # 2. 搜索视频
            videos = self.client.search_videos(keyword, page=1)
            if not videos:
                return None

            # 3. 过滤已看视频，挑选第一个新鲜视频
            target_video = None
            for v in videos:
                if not self.is_video_seen(v['bvid']):
                    target_video = v
                    break

            if not target_video:
                target_video = videos[0]

            bvid = target_video['bvid']
            title = target_video['title']
            author = target_video['author']

            # 4. 获取详细简介、官方 AI 摘要、字幕与热评
            info = self.client.get_video_info(bvid)
            desc = (info.get('desc') if info else target_video.get('description', ''))[:300]
            aid = info.get('aid') if info else target_video.get('aid', 0)
            cid = info.get('cid') if info else 0
            up_mid = info.get('up_mid') if info else 0

            # 尝鲜提取 B 站官方 AI 总结与大纲（需 Cookie）
            ai_sum = self.client.get_ai_summary(bvid, cid, up_mid) if (cid and up_mid) else None
            # 尝鲜提取官方字幕/台词（需 Cookie 或开放字幕）
            subtitles = self.client.get_subtitles(aid, cid, max_chars=1000) if (aid and cid) else None

            comments = self.client.get_hot_comments(aid, limit=3)
            comments_str = '\n'.join(f'- {c}' for c in comments) if comments else '（暂无热评）'

            # 组装深度内容上下文：告别简介空白，直击视频真正讲了什么
            content_parts = []
            if ai_sum and ai_sum.get('summary'):
                content_parts.append(f"【B站官方AI总结】：{ai_sum['summary']}")
                if ai_sum.get('outline'):
                    outline_lines = []
                    for sec in ai_sum['outline'][:2]:
                        pts = '；'.join(sec.get('points', [])[:2])
                        outline_lines.append(f"• {sec.get('title')}: {pts}")
                    content_parts.append(f"【视频要点大纲】：\n" + '\n'.join(outline_lines))
            elif subtitles:
                content_parts.append(f"【视频字幕台词摘录】：{subtitles[:600]}")

            if desc and desc.strip() and desc.strip() not in ('如题', '简介', '求三连'):
                content_parts.append(f"【UP主简介】：{desc}")
            elif not content_parts:
                content_parts.append("【UP主简介】：（UP主未填写详细简介）")

            detailed_content = '\n\n'.join(content_parts)

            # 5. 调用大模型蒸馏成歌伊的第一人称看后感（融合正品设定卡与真实口吻）
            core_role = get_geyi_role_prompt()
            system_prompt = (
                f"{core_role}\n\n"
                "【当前情境】：你刚刚在B站看完了这个视频，请以歌伊真实的性格习惯、语言口吻和情绪反应，"
                "写一段第一人称的随笔见闻心得（80~120字）。第一句话直接说内容，严禁输出任何思考过程或草稿。"
            )
            user_prompt = (
                f"【视频标题】：{title}\n"
                f"【UP主】：{author}\n"
                f"{detailed_content}\n"
                f"【热门评论】：\n{comments_str}\n\n"
                "请以歌伊的第一人称写一段生动真实的看后见闻分享（80~120字）。"
                "可以结合具体台词或大纲要点吐槽、开玩笑、表达惊喜，口吻要自然灵动，符合修女服跑团人设。"
                "第一句话直接说内容，严禁输出任何思考过程、草稿或【思考过程】标签！"
            )

            distilled_note = call_geyi_llm(user_prompt, system_prompt=system_prompt)
            if not distilled_note:
                # 兜底生成
                distilled_note = f"在B站刷到了UP主【{author}】发的《{title}》，感觉挺有意思的，记在心里啦~"

            # 6. 记录到本地库
            self.record_seen_video(bvid, title, author, distilled_note)
            self.record_wander_note(bvid, title, keyword, distilled_note)
            self._last_wander_time = time.time()

            # 7. 写入歌伊 L3 自身经历记忆（严格限制字数，杜绝数据库与记忆膨胀）
            memory_content = f"在B站刷视频看到了UP主【{author}】制作的《{title[:30]}》：{distilled_note[:80]}"
            inject_self_memory(memory_content, session_key=session_key or 'global',
                               kind='event', importance=0.8)

            return {
                'bvid': bvid,
                'title': title,
                'author': author,
                'keyword': keyword,
                'note': distilled_note,
                'url': f"https://www.bilibili.com/video/{bvid}",
            }

    def handle_user_shared_video(
        self,
        bvid: str,
        user_name: str = '群友',
        user_id: str = '',
        session_key: str = 'global'
    ) -> str:
        """群友向歌伊主动分享视频时的即时互动响应。"""
        info = self.client.get_video_info(bvid)
        if not info:
            return f"诶？这个视频链接（{bvid}）歌伊好像打不开呢，是不是失效或者被删啦？"

        title = info.get('title', '')
        author = info.get('author', '')
        desc = (info.get('desc', ''))[:300]
        aid = info.get('aid', 0)
        cid = info.get('cid', 0)
        up_mid = info.get('up_mid', 0)

        # 尝鲜提取 B 站官方 AI 总结与大纲（需 Cookie）
        ai_sum = self.client.get_ai_summary(bvid, cid, up_mid) if (cid and up_mid) else None
        # 尝鲜提取官方字幕/台词（需 Cookie 或开放字幕）
        subtitles = self.client.get_subtitles(aid, cid, max_chars=1000) if (aid and cid) else None

        comments = self.client.get_hot_comments(aid, limit=3)
        comments_str = '\n'.join(f'- {c}' for c in comments) if comments else '（暂无热评）'

        content_parts = []
        if ai_sum and ai_sum.get('summary'):
            content_parts.append(f"【B站官方AI总结】：{ai_sum['summary']}")
            if ai_sum.get('outline'):
                outline_lines = []
                for sec in ai_sum['outline'][:2]:
                    pts = '；'.join(sec.get('points', [])[:2])
                    outline_lines.append(f"• {sec.get('title')}: {pts}")
                content_parts.append(f"【视频要点大纲】：\n" + '\n'.join(outline_lines))
        elif subtitles:
            content_parts.append(f"【视频字幕台词摘录】：{subtitles[:600]}")

        if desc and desc.strip() and desc.strip() not in ('如题', '简介', '求三连'):
            content_parts.append(f"【UP主简介】：{desc}")
        elif not content_parts:
            content_parts.append("【UP主简介】：（UP主未填写详细简介）")

        detailed_content = '\n\n'.join(content_parts)

        system_prompt = (
            "你是白黑修女服人设的二次元跑团爱好者「歌伊」。群友正在群里向你安利/分享一个B站视频。"
            "请给出自然、生动、符合歌伊性格的即时观后感或感谢互动。"
        )
        user_prompt = (
            f"群友【{user_name}】向你分享了B站视频：\n"
            f"【标题】：{title}\n"
            f"【UP主】：{author}\n"
            f"{detailed_content}\n"
            f"【视频热评】：\n{comments_str}\n\n"
            "请以歌伊的口吻给出真实自然的互动回复（80~150字）。"
            "直接回复内容，严禁输出任何思考过程或草稿。"
        )

        reply = call_geyi_llm(user_prompt, system_prompt=system_prompt)
        if not reply:
            reply = f"哇！谢谢{user_name}安利的《{title}》！歌伊已经把UP主【{author}】加进待看清单啦~"

        # 安全过滤：剔除可能混入的内部表情宏
        reply = re.sub(r'\[EMOTE:[^\]]+\]', '', reply).strip()

        # 记录为该用户的偏好记忆（L2 user_facts）
        if user_id:
            inject_user_share_memory(
                session_key=session_key,
                user_id=user_id,
                fact=f"喜欢看B站视频并向歌伊安利了《{title}》(UP主:{author})"
            )

        # 记录为歌伊的自身经历记忆（L3 self_memories）
        inject_self_memory(
            f"群友【{user_name}】给歌伊安利了B站视频《{title}》，歌伊很开心地跟对方讨论了一番",
            session_key=session_key,
            kind='event',
            importance=0.75
        )

        # 标记为已看
        self.record_seen_video(bvid, title, author, reply)
        return reply

    @property
    def auto_enabled(self) -> bool:
        """自动漫游是否开启（总开关 + 自动开关，两个都要开）。"""
        return bool(self.config.get('auto_wander_enabled', False)) and \
            bool(self.config.get('enabled', True))

    def start_background_loop(self) -> None:
        """启动后台定时漫游线程（仅在开启自动漫游时）。"""
        if not self.auto_enabled:
            return
        if self._bg_thread and self._bg_thread.is_alive():
            return
        self._stop_event.clear()
        self._bg_thread = threading.Thread(target=self._loop, name='geyi-bili-wanderer', daemon=True)
        self._bg_thread.start()

    def stop_background_loop(self) -> None:
        self._stop_event.set()

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            # 关掉自动漫游之后线程立刻退出（配置可能是重载时才改的）
            if not self.auto_enabled:
                return
            now = time.time()
            interval_sec = int(self.config.get('wander_interval_minutes', 240)) * 60
            quiet = _parse_quiet_hours(self.config.get('quiet_hours', '01:00-07:00'), now)

            if not quiet and now - self._last_wander_time >= interval_sec:
                try:
                    self.do_wander()
                except Exception:
                    pass
            # 每 60 秒检查一次
            self._stop_event.wait(60.0)
