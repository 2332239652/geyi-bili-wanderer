# -*- encoding: utf-8 -*-
"""歌伊的B站漫游：闲时自主刷B站 / 动态进化兴趣词库 / 见闻融入记忆 / 主动分享视频感知注入。

命名空间：geyi_bili_wanderer
优先级：12000（在 newGeYi 之前拦截专属指令并注入视频多模态感知）
消息模式：olivos_string
"""

from __future__ import annotations

import re
import threading
from typing import Optional, Any

from .config import load_config, get_db_path, resolve_bili_cookies, PLUGIN_VERSION
from .bili.client import BiliClient
from .bili.resolver import extract_bvid
from .core.keywords import KeywordsManager
from .core.wanderer import WandererEngine
from .core.video_context import VideoContext
from .core.network_client import DeepSeekWebClient
from .core.web_context import WebContext
from .core.interop import (
    is_geyi_available,
    get_session_key,
    mount_video_context,
    mount_web_context,
    suppress_message_in_geyi,
)

_engine: Optional[WandererEngine] = None
_keywords_mgr: Optional[KeywordsManager] = None
_video_ctx: Optional[VideoContext] = None
_web_ctx: Optional[WebContext] = None
_config: dict = {}
_proc: Any = None

OP_CODE_PATTERN = re.compile(r'\[OP:[^\]]+\]')


def _clean_plain_text(raw: str) -> str:
    """去除 OlivOS OP 码，得到纯净的聊天文本。"""
    if not raw:
        return ''
    return OP_CODE_PATTERN.sub('', raw).strip()


def _is_mentioned(event, text: str) -> bool:
    """检查消息中是否艾特了机器人，或者提到了歌伊。"""
    raw = str(getattr(event.data, 'message', '') or '')
    if '歌伊' in text or '歌伊' in raw:
        return True
    bot_info = getattr(event, 'bot_info', None)
    bot_id = str(getattr(bot_info, 'id', '') or '')
    if bot_id and f'target={bot_id}' in raw:
        return True
    return False


def _async_run(fn, *args, **kwargs):
    """在独立的临时守护线程中执行耗时网络请求，杜绝阻塞 OlivOS 核心事件循环。"""
    t = threading.Thread(target=fn, args=args, kwargs=kwargs, daemon=True)
    t.start()


class Event(object):
    @staticmethod
    def init(plugin_event, Proc):
        global _proc
        _proc = Proc

    @staticmethod
    def init_after(plugin_event, Proc):
        global _engine, _keywords_mgr, _video_ctx, _config, _proc
        _proc = Proc
        _config = load_config()

        db_path = get_db_path()
        _keywords_mgr = KeywordsManager(
            db_path=db_path,
            seed_keywords=_config.get('seed_keywords', []),
            max_pool=int(_config.get('max_keywords_pool', 30))
        )
        cookies = resolve_bili_cookies(_config)
        client = BiliClient(cookies=cookies)
        _video_ctx = VideoContext(client=client, config=_config)

        # 核心：将轻量视频多模态感知与全网搜索感知挂载至主进程 newGeYi
        video_mounted = mount_video_context(_video_ctx)

        # 初始化 DeepSeek 官方全网搜索与抓取感知（Search / Fetch REST API）
        ds_key = str(_config.get('deepseek_search_api_key') or '').strip()
        _web_client = DeepSeekWebClient(api_key=ds_key)
        _web_ctx = WebContext(client=_web_client, config=_config)
        web_mounted = mount_web_context(_web_ctx)

        _engine = WandererEngine(
            db_path=db_path,
            config=_config,
            keywords_mgr=_keywords_mgr,
            client=client
        )
        # 自动漫游默认关闭：只有 config.json 里 auto_wander_enabled=true 才起后台线程
        _engine.start_background_loop()
        auto_state = '开（每 %d 分钟）' % int(_config.get('wander_interval_minutes', 240)) \
            if _engine.auto_enabled else '关（只保留「发链接给她看」和「.bili wander」）'

        has_cred = bool(cookies.get('SESSDATA'))
        cred_info = '已启用凭据 (官方AI摘要+全量字幕开启)' if has_cred else '匿名模式 (依赖标题/简介/热评)'
        mount_info = '全网感知与B站多模态已挂载至主进程' if (video_mounted and web_mounted) else '独立运行模式'
        geyi_status = '已就绪' if is_geyi_available() else '未就绪'
        if Proc and hasattr(Proc, 'log'):
            Proc.log(2, f'[geyi_bili_wanderer] 歌伊的B站漫游 v{PLUGIN_VERSION} 初始化完成 | 自动漫游: {auto_state} | 模式: {cred_info} | {mount_info} | 歌伊本体: {geyi_status}', [])

    @staticmethod
    def group_message(plugin_event, Proc):
        Event._handle_msg(plugin_event, Proc, is_group=True)

    @staticmethod
    def private_message(plugin_event, Proc):
        Event._handle_msg(plugin_event, Proc, is_group=False)

    @staticmethod
    def _handle_msg(plugin_event, Proc, is_group: bool = True):
        global _engine, _video_ctx, _config
        if not _engine or not _config.get('enabled', True):
            return

        raw_msg = str(getattr(plugin_event.data, 'message', '') or '')
        plain = _clean_plain_text(raw_msg)
        if not plain:
            return

        sender = getattr(plugin_event.data, 'sender', None) or {}
        sender_name = str(sender.get('name', '') or '群友')
        sender_id = str(getattr(plugin_event.data, 'user_id', '') or '')
        group_id = str(getattr(plugin_event.data, 'group_id', '') or '')
        session_key = get_session_key(plugin_event, is_group=is_group)

        # ========== 1. 优先提取消息中的 B 站视频分享（BV号 / 链接 / 卡片） ==========
        bvid = extract_bvid(raw_msg)
        if bvid:
            # 预热并缓存该视频的极简结构化感知（≤500字符），供本体 PromptBuilder 瞬间命中
            if _video_ctx:
                _async_run(_video_ctx.resolve_video, bvid)

            # 核心融合设计：若歌伊本体已连接，视频感知已无缝接入本体 PromptBuilder。
            # 由本体以 100% 歌伊完整人设生成唯一的真实互动，附属插件保持静默，绝不抢话重回复！
            if is_geyi_available():
                return

            # 仅在本体缺失的独立回退模式下，才由本插件直接发送回复
            should_handle = not is_group or _is_mentioned(plugin_event, plain)
            if not should_handle:
                for share_trig in _config.get('share_triggers', []):
                    if share_trig in plain:
                        should_handle = True
                        break

            if should_handle and _config.get('auto_share_video_reply', True):
                plugin_event.reply(f'收到！歌伊这就点进这个视频（{bvid}）瞧一瞧~')

                def analyze_and_reply():
                    reaction = _engine.handle_user_shared_video(
                        bvid=bvid,
                        user_name=sender_name,
                        user_id=sender_id,
                        session_key=session_key
                    )
                    plugin_event.reply(reaction)

                _async_run(analyze_and_reply)
                return

        # ========== 2. 主动触发摸鱼指令（.bili wander / 歌伊去刷会儿b站 / 歌伊随机看个视频） ==========
        # 核心防误判：如果包含网址或视频链接，绝对不作为漫游摸鱼指令触发
        is_trigger_cmd = False
        lower_plain = plain.lower()
        custom_topic = None

        if not any(h in lower_plain for h in ('http://', 'https://', 'b23.tv', 'bilibili.com')):
            for trig in _config.get('trigger_keywords', []):
                t_low = trig.lower()
                if t_low.startswith('.'):
                    if lower_plain.startswith(t_low):
                        is_trigger_cmd = True
                        after_text = plain[len(trig):].strip(' ，。：:、！!~')
                        if after_text and len(after_text) <= 15:
                            custom_topic = after_text
                        break
                else:
                    pos = lower_plain.find(t_low)
                    if pos != -1:
                        is_trigger_cmd = True
                        after_text = plain[pos + len(trig):].strip(' ，。：:、！!~')
                        if after_text and len(after_text) <= 15:
                            custom_topic = after_text
                        break

        if is_trigger_cmd:
            # 跨进程/跨插件抑制主本体回复，避免抢话
            suppress_message_in_geyi(f"{group_id or sender_id}:{raw_msg.strip()}")
            suppress_message_in_geyi(f"{raw_msg.strip()}")
            setattr(plugin_event.data, '_geyi_handled', True)

            notice = f"好喔！歌伊这就去B站搜搜看【{custom_topic}】~（小跑去摸鱼~）" if custom_topic else "好喔！歌伊这就去B站转一圈，看看今天有什么新鲜事~（小跑去摸鱼~）"
            plugin_event.reply(notice)

            def wander_and_reply():
                res = _engine.do_wander(forced_keyword=custom_topic, session_key=session_key)
                if res:
                    msg = (
                        f"（小跑回来擦了擦汗）呼~ 刚才摸鱼刷到了UP主【{res['author']}】的《{res['title']}》！\n\n"
                        f"{res['note']}\n\n"
                        f"传送门送上：{res['url']}"
                    )
                    plugin_event.reply(msg)
                else:
                    plugin_event.reply("呜……刚才在B站转了一圈，网速好像不太给力，没刷到心仪的视频呢。")

            _async_run(wander_and_reply)
            return

    @staticmethod
    def save(plugin_event, Proc):
        global _engine
        if _engine:
            _engine.stop_background_loop()

    @staticmethod
    def menu(plugin_event, Proc):
        try:
            if getattr(plugin_event.data, 'namespace', '') != 'geyi_bili_wanderer':
                return
            global _engine, _keywords_mgr
            if not _engine:
                return
            kw_list = _keywords_mgr.get_all_keywords() if _keywords_mgr else []
            kw_show = '、'.join(kw_list[:8]) + ('…' if len(kw_list) > 8 else '')
            status = (
                f"【歌伊的B站漫游】运行状态：\n"
                f"- 歌伊本体联动：{'已连接 (主进程深度融合)' if is_geyi_available() else '未连接'}\n"
                f"- 当前兴趣词库（{len(kw_list)}个）：{kw_show}\n"
                f"- 测试指令：发送「.bili wander」或「歌伊去刷会儿b站」"
            )
            if Proc and hasattr(Proc, 'log'):
                Proc.log(1, status, [])
            try:
                plugin_event.reply(status)
            except Exception:
                pass
        except Exception:
            pass
