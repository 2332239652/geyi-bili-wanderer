# -*- encoding: utf-8 -*-
"""配置管理与持久化目录解析。"""

from __future__ import annotations

import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR_NAME = 'geyi_bili_wanderer'
PLUGIN_VERSION = '1.0.0'

DEFAULT_CONFIG = {
    'enabled': True,
    # 四小时自动漫游（后台定时自己找视频看）。
    # 2026-10-29 默认关闭：实测收益为零——她刷到的视频以一条 L3 记忆的形式写进
    # 最近 20 个会话（importance 0.8），挤占「自身记忆」的三个名额，而模型对它
    # 几乎没有注意力，群里也完全无感。现在只保留「群友发链接 → 她去看」和
    # 手动「.bili wander」两条路径。
    'auto_wander_enabled': False,
    'wander_interval_minutes': 240,
    'quiet_hours': '01:00-07:00',
    'seed_keywords': [
        'COC跑团',
        '跑团模组',
        '二次元新番',
        '单机游戏',
        '沙雕日常',
        'AI科技资讯',
        'AI最新工具',
        '大语言模型',
    ],
    'dynamic_interest_ratio': 0.35,
    'max_keywords_pool': 30,
    'trigger_keywords': [
        '.bili wander',
        '.bili',
        '歌伊去刷会儿b站',
        '歌伊刷b站',
        '歌伊看b站',
        '歌伊看视频',
        '歌伊去刷b站',
    ],
    'share_triggers': [
        '歌伊看看',
        '歌伊这是什么',
        '歌伊评评',
        '歌伊看一下',
        '歌伊来吐槽',
    ],
    'auto_share_video_reply': True,
    'max_daily_wanders': 12,
    # 可选 B 站登录凭据（SESSDATA）：配置后可解锁「官方 AI 摘要」与「全量字幕提取」
    # 若留空，插件会自动尝试读取本机 ~/.dsh/bili/credential.json
    'bilibili_cookie': '',
    # DeepSeek 官方自研全网搜索与网页抓取（Search / Fetch REST API）
    # 在 data/geyi_bili_wanderer/config.json 中填入自己的 Key；仓库内绝不存放真实凭据。
    'web_search_enabled': True,
    'deepseek_search_api_key': '',
}


def _find_plugin_root() -> Path:
    current = BASE_DIR
    for _ in range(8):
        if (current / 'app').is_dir() and (current / 'data').is_dir():
            return current
        if (current / 'plugin').is_dir() and (current / 'plugin' / 'data').is_dir():
            return current / 'plugin'
        if current.parent == current:
            break
        current = current.parent
    return BASE_DIR.parent


def get_state_dir() -> Path:
    """获取插件持久化数据目录（配置、SQLite数据库、历史去重记录）。"""
    override = os.environ.get('GEYI_BILI_DATA_DIR', '').strip()
    if override:
        path = Path(override)
    else:
        path = _find_plugin_root() / 'data' / DATA_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_db_path() -> Path:
    return get_state_dir() / 'wanderer.sqlite3'


def load_config() -> dict:
    """安全加载配置，以默认配置为底做增量合并。"""
    config = dict(DEFAULT_CONFIG)
    state_file = get_state_dir() / 'config.json'
    bundled_file = BASE_DIR / 'config.json'

    target = state_file if state_file.exists() else bundled_file
    if target.exists():
        try:
            with open(target, 'r', encoding='utf-8') as f:
                saved = json.load(f)
            if isinstance(saved, dict):
                config.update(saved)
        except Exception:
            pass
    return config


def save_config(config: dict) -> None:
    target = get_state_dir() / 'config.json'
    try:
        with open(target, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def resolve_bili_cookies(config: dict) -> dict:
    """提取有效的 B 站 Cookie 字典（SESSDATA / bili_jct / DedeUserID）。
    若配置中未指定，则尝试安全读取本机 ~/.dsh/bili/credential.json。
    """
    raw_cookie = str(config.get('bilibili_cookie') or '').strip()
    if raw_cookie:
        cookies = {}
        for part in raw_cookie.split(';'):
            if '=' in part:
                k, v = part.strip().split('=', 1)
                cookies[k.strip()] = v.strip()
        if 'SESSDATA' not in cookies and raw_cookie:
            cookies['SESSDATA'] = raw_cookie
        return cookies

    # 尝试自动复用 DSH 本地登录凭据
    try:
        cred_path = Path.home() / '.dsh' / 'bili' / 'credential.json'
        if cred_path.exists():
            with open(cred_path, 'r', encoding='utf-8') as f:
                cred = json.load(f)
            sessdata = cred.get('sessdata', '')
            if sessdata:
                return {
                    'SESSDATA': sessdata,
                    'bili_jct': cred.get('biliJct', ''),
                    'DedeUserID': cred.get('dedeUserId', ''),
                }
    except Exception:
        pass
    return {}
