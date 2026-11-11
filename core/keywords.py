# -*- encoding: utf-8 -*-
"""歌伊自主兴趣词库：多频段领域轮转、负面排除与防题材单一动态进化。"""

from __future__ import annotations

import random
import re
import sqlite3
import time
from pathlib import Path
from typing import List, Optional, Callable, Dict, Any

# 歌伊的五大核心兴趣频段（各具明确特色与标签，防单极化收敛）
DOMAINS: Dict[str, Dict[str, Any]] = {
    'trpg': {
        'name': '跑团与克苏鲁TRPG',
        'desc': 'COC跑团模组、规则解说、跑团翻车切片、克苏鲁神话调查员趣事、Replay实况',
        'tags': ['coc', 'trpg', '跑团', '模组', '克苏鲁', '车卡', '调查员', 'kp', 'pl', 'dnd', '奈亚', '密斯卡托尼克'],
        'default': 'COC跑团',
    },
    'gaming': {
        'name': '单机与独立游戏',
        'desc': 'Steam独立神作、单机实况、硬核动作受苦、主机3A体验、沙雕联机互动',
        'tags': ['单机', '游戏', 'steam', '独立游戏', '主机', '任天堂', '魂', '联机', '实况', '黑神话', '塞尔达', '艾尔登法环', '博德之门'],
        'default': '单机游戏',
    },
    'anime': {
        'name': '二次元动漫与新番',
        'desc': '新番吐槽、经典神作盘点、名场面剪辑、动画作画演出、声优梗与漫评',
        'tags': ['二次元', '动漫', '新番', '番剧', '漫评', '动画', '声优', '漫画', 'acg', '手办', '剧场版'],
        'default': '二次元新番',
    },
    'funny': {
        'name': '沙雕搞笑与日常整活',
        'desc': '沙雕日常、生活搞笑翻车、鬼畜调教、萌宠治愈、人类迷惑行为、趣味挑战',
        'tags': ['沙雕', '搞笑', '鬼畜', '日常', '萌宠', '猫', '狗', '整活', '切片', '翻车', '幽默', '段子', '抽象'],
        'default': '沙雕日常',
    },
    'ai_tech': {
        'name': '前沿AI与硬核黑科技',
        'desc': 'AI最新工具测评、大语言模型进展、AI多模态整活、开源黑科技应用、前沿技术资讯',
        'tags': ['ai', '科技', '大模型', '工具', 'llm', '绘画', '算法', '深度学习', '自动化', 'chatgpt', 'gemini', 'claude', 'deepseek', '智能体'],
        'default': 'AI最新工具',
    },
}

ALL_DOMAIN_KEYS = list(DOMAINS.keys())


def classify_domain(keyword: str) -> str:
    """根据关键词特征，将其归纳为五大核心兴趣频段之一。"""
    lower = keyword.lower().strip()
    for domain, info in DOMAINS.items():
        for tag in info['tags']:
            if tag in lower:
                return domain
    return 'gaming'  # 兜底归类为游戏频段


class KeywordsManager:
    """具备五频段轮转与防单一机制的动态兴趣词库管理器。"""

    def __init__(self, db_path: Path, seed_keywords: List[str], max_pool: int = 30):
        self.db_path = db_path
        self.seed_keywords = seed_keywords
        self.max_pool = max_pool
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.execute('PRAGMA journal_mode=WAL')
        return conn

    def _init_db(self) -> None:
        with self._get_conn() as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS keywords (
                    keyword TEXT PRIMARY KEY,
                    source TEXT,
                    domain TEXT DEFAULT 'gaming',
                    hit_count INTEGER DEFAULT 0,
                    last_used_at REAL DEFAULT 0,
                    created_at REAL
                )
            ''')
            # 兼容老库结构迁移
            try:
                conn.execute('ALTER TABLE keywords ADD COLUMN domain TEXT DEFAULT "gaming"')
            except Exception:
                pass

            # 首次启动或补充种子词：将种子词归入对应频段
            now = time.time()
            for kw in self.seed_keywords:
                dom = classify_domain(kw)
                conn.execute('''
                    INSERT OR IGNORE INTO keywords (keyword, source, domain, hit_count, last_used_at, created_at)
                    VALUES (?, 'seed', ?, 0, 0, ?)
                ''', (kw, dom, now))
                # 刷新已有种子词的 domain
                conn.execute('UPDATE keywords SET domain = ? WHERE keyword = ? AND (domain IS NULL OR domain = "other")', (dom, kw))
            conn.commit()

    def get_all_keywords(self) -> List[str]:
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute('SELECT keyword FROM keywords ORDER BY last_used_at ASC')
            return [row[0] for row in cur.fetchall()]

    def add_keyword(self, keyword: str, source: str = 'dynamic', domain: Optional[str] = None) -> bool:
        kw = keyword.strip().replace('"', '').replace("'", '').replace('【', '').replace('】', '')
        if not kw or len(kw) < 2 or len(kw) > 20:
            return False
        dom = domain or classify_domain(kw)
        with self._get_conn() as conn:
            now = time.time()
            conn.execute('''
                INSERT OR REPLACE INTO keywords (keyword, source, domain, hit_count, last_used_at, created_at)
                VALUES (?, ?, ?, COALESCE((SELECT hit_count FROM keywords WHERE keyword=?), 0), ?, ?)
            ''', (kw, source, dom, kw, now, now))

            # 频段配额保护：当超出词库容量上限时，优先淘汰最拥挤频段且最久未用的动态词
            cur = conn.cursor()
            cur.execute('SELECT COUNT(*) FROM keywords')
            total = cur.fetchone()[0]
            if total > self.max_pool:
                cur.execute('SELECT domain, COUNT(*) FROM keywords GROUP BY domain ORDER BY COUNT(*) DESC')
                dom_counts = cur.fetchall()
                target_del_domain = dom_counts[0][0] if dom_counts else None

                deleted = 0
                if target_del_domain:
                    cur.execute('''
                        DELETE FROM keywords WHERE keyword IN (
                            SELECT keyword FROM keywords
                            WHERE source != 'seed' AND domain = ?
                            ORDER BY hit_count ASC, last_used_at ASC
                            LIMIT ?
                        )
                    ''', (target_del_domain, total - self.max_pool))
                    deleted = cur.rowcount

                if deleted < (total - self.max_pool):
                    rem = (total - self.max_pool) - deleted
                    conn.execute('''
                        DELETE FROM keywords WHERE keyword IN (
                            SELECT keyword FROM keywords
                            WHERE source != 'seed'
                            ORDER BY hit_count ASC, last_used_at ASC
                            LIMIT ?
                        )
                    ''', (rem,))
            conn.commit()
            return True

    def record_usage(self, keyword: str) -> None:
        with self._get_conn() as conn:
            conn.execute('''
                UPDATE keywords
                SET hit_count = hit_count + 1, last_used_at = ?
                WHERE keyword = ?
            ''', (time.time(), keyword))
            conn.commit()

    def select_next_domain(self, recent_domains: List[str]) -> str:
        """根据最近漫游过的频段历史，挑选出最冷门、最久未涉猎的目标频段。"""
        if not recent_domains:
            return random.choice(ALL_DOMAIN_KEYS)

        domain_last_pos = {}
        for d in ALL_DOMAIN_KEYS:
            try:
                # 记录在 recent_domains 中的最新位置（0 代表最近一次使用）
                domain_last_pos[d] = recent_domains.index(d)
            except ValueError:
                # 从未在最近漫游记录中出现过：赋予最高优先冷度
                domain_last_pos[d] = 999

        # 按冷门度降序排序（999 或在历史中最靠前者优先）
        sorted_domains = sorted(ALL_DOMAIN_KEYS, key=lambda d: domain_last_pos[d], reverse=True)
        # 从最冷门的前 2 个频道中挑选
        candidates = sorted_domains[:2]
        return random.choice(candidates)

    def pick_keyword(
        self,
        dynamic_ratio: float = 0.35,
        llm_generator: Optional[Callable[[str, List[str]], Optional[str]]] = None,
        recent_keywords: Optional[List[str]] = None,
        recent_domains: Optional[List[str]] = None,
    ) -> str:
        """多重防单一挑词核心：
        1. 轮转目标频道，避免连续扎堆在同一题材；
        2. 若触发大模型动态发散，注入近期已读排除列表与心理疲倦设定；
        3. 若从本地库挑词，排除近期已用词，并优先选取目标冷门频段候选。
        """
        recent_kws = [k.strip() for k in (recent_keywords or []) if k.strip()]
        recent_doms = list(recent_domains or [classify_domain(k) for k in recent_kws])
        target_domain = self.select_next_domain(recent_doms)

        # ========== 1. 大模型好奇心动态发散（带转场与排除约束） ==========
        if llm_generator and random.random() < dynamic_ratio:
            try:
                new_kw = llm_generator(target_domain, recent_kws)
                if new_kw and len(new_kw.strip()) >= 2:
                    raw_line = new_kw.strip().split('\n')[0]
                    clean_kw = re.sub(r'^(?:\d+[\.\s、\-]+|关键词\s*[:：]|推荐\s*[:：])', '', raw_line).strip()
                    clean_kw = clean_kw.strip(' .。,"\'“”‘’【】《》()（）')
                    if clean_kw and 2 <= len(clean_kw) <= 20 and clean_kw not in recent_kws:
                        dom = classify_domain(clean_kw)
                        self.add_keyword(clean_kw, source='dynamic', domain=dom)
                        self.record_usage(clean_kw)
                        return clean_kw
            except Exception:
                pass

        # ========== 2. 本地词库挑选：严格避开近期已用词，优先目标冷门频段 ==========
        with self._get_conn() as conn:
            cur = conn.cursor()
            # 优先在目标频段中找最冷门且近期未用的词
            cur.execute('''
                SELECT keyword FROM keywords
                WHERE domain = ? AND keyword NOT IN ({})
                ORDER BY last_used_at ASC, hit_count ASC
                LIMIT 3
            '''.format(','.join('?' for _ in recent_kws) if recent_kws else "''"),
            ([target_domain] + recent_kws) if recent_kws else [target_domain])
            target_rows = cur.fetchall()
            if target_rows:
                picked = random.choice([r[0] for r in target_rows])
                self.record_usage(picked)
                return picked

            # 次优先：在所有频段中排除近期已用词，挑选最久未使用的候选
            cur.execute('''
                SELECT keyword FROM keywords
                WHERE keyword NOT IN ({})
                ORDER BY last_used_at ASC, hit_count ASC
                LIMIT 4
            '''.format(','.join('?' for _ in recent_kws) if recent_kws else "''"),
            recent_kws if recent_kws else ())
            general_rows = cur.fetchall()
            if general_rows:
                picked = random.choice([r[0] for r in general_rows])
                self.record_usage(picked)
                return picked

            # 再次兜底：现有全部词库最久未用
            cur.execute('SELECT keyword FROM keywords ORDER BY last_used_at ASC LIMIT 3')
            fallback_rows = cur.fetchall()
            if fallback_rows:
                picked = random.choice([r[0] for r in fallback_rows])
                self.record_usage(picked)
                return picked

        # 极致兜底：使用该目标频段的预设默认词
        return DOMAINS.get(target_domain, {}).get('default', '单机游戏')
