# -*- encoding: utf-8 -*-
"""歌伊本体跨插件互通网关：提供安全的同进程 API 调用与记忆写入。"""

from __future__ import annotations

import re
import sys
from typing import Optional, Any


def _strip_thinking(text: str) -> str:
    """剥除模型输出中混入的思考过程与分析标签。"""
    if not text:
        return ''
    # 剥除 XML 思考标签
    text = re.sub(r'<(?:think|thought)>[\s\S]*?</(?:think|thought)>', '', text, flags=re.IGNORECASE)
    if re.match(r'^\s*<(?:think|thought)>', text, re.IGNORECASE):
        parts = re.split(r'</(?:think|thought)>', text, maxsplit=1, flags=re.IGNORECASE)
        if len(parts) > 1:
            text = parts[1]
    # 剥除元草稿提示
    meta_anchor = re.search(r'(?:最终回复|实际回复|正式回复|输出|回复)\s*[:：]\s*([^\n]+(?:\n[^\n]+)*)$', text)
    if meta_anchor:
        return meta_anchor.group(1).strip()
    return text.strip()


def get_geyi_runtime() -> Optional[Any]:
    """探测并获取当前 Python 进程中运行的 newGeYi 核心 Runtime 实例。"""
    try:
        for mod_name in ('plugin.app.newGeYi.main', 'newGeYi.main', 'newGeYi'):
            if mod_name in sys.modules:
                rt = getattr(sys.modules[mod_name], '_runtime', None)
                if rt:
                    return rt
        # 尝试动态引用
        try:
            import plugin.app.newGeYi.main as geyi_main
            return getattr(geyi_main, '_runtime', None)
        except Exception:
            pass
        try:
            import newGeYi.main as geyi_main
            return getattr(geyi_main, '_runtime', None)
        except Exception:
            pass
    except Exception:
        pass
    return None


def is_geyi_available() -> bool:
    return get_geyi_runtime() is not None


def get_geyi_role_prompt() -> str:
    """获取歌伊本体的核心人设设定卡，用于漫游见闻蒸馏，使其与日常聊天口吻高度统一。"""
    runtime = get_geyi_runtime()
    if runtime and hasattr(runtime, 'prompt_builder') and runtime.prompt_builder:
        try:
            rp = getattr(runtime.prompt_builder, 'role_prompt', '')
            if rp and isinstance(rp, str):
                return rp.strip()
        except Exception:
            pass
    if runtime and hasattr(runtime, 'load_role_prompt'):
        try:
            return runtime.load_role_prompt().strip()
        except Exception:
            pass
    return (
        "你是白黑修女服人设的二次元跑团爱好者「歌伊」。"
        "你的性格活泼亲切、有点小傲娇与小调皮，爱好COC跑团模组、二次元动漫新番、单机游戏、沙雕日常与前沿AI科技。"
    )


def call_geyi_llm(user_prompt: str, system_prompt: str = '', kind: str = 'chat') -> Optional[str]:
    """复用歌伊现有的多渠道故障转移大模型网关（Gemini / DeepSeek）。"""
    runtime = get_geyi_runtime()
    if not runtime or not hasattr(runtime, 'gateway') or not runtime.gateway:
        return None

    messages = []
    if system_prompt:
        messages.append({'role': 'system', 'content': system_prompt})
    messages.append({'role': 'user', 'content': user_prompt})

    # 获取或构造 ModelRequest
    ModelReqCls = None
    for mod_name in ('plugin.app.newGeYi.models', 'newGeYi.models'):
        if mod_name in sys.modules:
            ModelReqCls = getattr(sys.modules[mod_name], 'ModelRequest', None)
            if ModelReqCls:
                break

    if ModelReqCls is None:
        try:
            from plugin.app.newGeYi.models import ModelRequest as ModelReqCls
        except Exception:
            try:
                from newGeYi.models import ModelRequest as ModelReqCls
            except Exception:
                from dataclasses import dataclass

                @dataclass(frozen=True)
                class ModelReqCls:
                    messages: list
                    model: str = ''
                    temperature: float = 0.7
                    max_tokens: int = 800
                    request_id: str = ''

    model = ''
    if hasattr(runtime, 'config') and isinstance(runtime.config, dict):
        model = runtime.config.get('fast_model', '')

    req = ModelReqCls(
        messages=messages,
        model=model,
        temperature=0.7,
        max_tokens=600
    )

    try:
        resp = runtime.gateway.chat(req, kind=kind)
        if resp and getattr(resp, 'ok', False) and getattr(resp, 'text', None):
            cleaned = _strip_thinking(resp.text)
            return cleaned if cleaned else resp.text.strip()
    except Exception:
        pass
    return None


def get_session_key(event: Any, is_group: bool = True) -> str:
    """获取与 newGeYi 格式统一规范的会话唯一标识。"""
    runtime = get_geyi_runtime()
    if runtime and hasattr(runtime, '_session_key'):
        try:
            sk_obj = runtime._session_key(event, is_group)
            if hasattr(sk_obj, 'as_text'):
                return sk_obj.as_text()
        except Exception:
            pass

    bot = getattr(event, 'bot_info', None)
    bot_hash = str(getattr(bot, 'hash', '') or 'default')
    platform = (getattr(event, 'platform', None) or {}).get('platform', 'qq')
    sdk = (getattr(event, 'platform', None) or {}).get('sdk', 'onebot')
    if is_group:
        host_id = str(getattr(event.data, 'host_id', '') or '')
        gid = str(getattr(event.data, 'group_id', '') or '-')
        return f"{bot_hash}:{platform}:{sdk}:group:{host_id or '-'}:{gid}"
    uid = str(getattr(event.data, 'user_id', '') or '-')
    return f"{bot_hash}:{platform}:{sdk}:private:-:{uid}"


def inject_self_memory(content: str, session_key: str = 'global', kind: str = 'event', importance: float = 0.8) -> bool:
    """向歌伊的四层记忆库直接写入 L3 自身经历/见闻记忆。

    当 session_key 为 'global' 时，将漫游见闻同步注入到近期活跃的群聊会话中，
    使歌伊在之后的各群互动中能自然聊起在B站的所见所闻。
    """
    runtime = get_geyi_runtime()
    if not runtime or not hasattr(runtime, 'store') or not runtime.store:
        return False

    try:
        if session_key == 'global':
            target_sessions = []
            try:
                cur = runtime.store._conn().execute(
                    'SELECT DISTINCT session_key FROM turns ORDER BY id DESC LIMIT 20'
                )
                target_sessions = [r[0] for r in cur.fetchall() if r[0]]
            except Exception:
                pass
            if not target_sessions:
                target_sessions = ['global']
            for sk in target_sessions:
                runtime.store.add_self_memory(
                    session_key=sk,
                    content=content,
                    kind=kind,
                    importance=importance
                )
            return True
        else:
            runtime.store.add_self_memory(
                session_key=session_key,
                content=content,
                kind=kind,
                importance=importance
            )
            return True
    except Exception:
        return False


def inject_user_share_memory(session_key: str, user_id: str, fact: str) -> bool:
    """当群友向歌伊分享视频时，记录为该群友的私人偏好画像（L2 user_facts）。"""
    runtime = get_geyi_runtime()
    if not runtime or not hasattr(runtime, 'store') or not runtime.store:
        return False

    try:
        runtime.store.add_user_fact(
            session_key=session_key,
            user_id=str(user_id),
            fact=fact,
            category='bili_share',
            source='explicit',
            importance=0.75
        )
        return True
    except Exception:
        return False


def mount_video_context(video_ctx: Any) -> bool:
    """将视频感知上下文无缝挂载到主进程 newGeYi 的 Runtime 与 PromptBuilder 上。"""
    runtime = get_geyi_runtime()
    if not runtime:
        return False
    try:
        runtime.video_context = video_ctx
        if hasattr(runtime, 'prompt_builder') and runtime.prompt_builder:
            runtime.prompt_builder.video_context = video_ctx
        return True
    except Exception:
        return False


def mount_web_context(web_ctx: Any) -> bool:
    """将全网搜索与抓取感知上下文无缝挂载到主进程 newGeYi 的 Runtime 与 PromptBuilder 上。"""
    runtime = get_geyi_runtime()
    if not runtime:
        return False
    try:
        runtime.web_context = web_ctx
        if hasattr(runtime, 'prompt_builder') and runtime.prompt_builder:
            runtime.prompt_builder.web_context = web_ctx
        return True
    except Exception:
        return False


def suppress_message_in_geyi(suppress_key: str) -> None:
    """跨插件消息互斥抑制：通知主进程 newGeYi 忽略指定专属指令，防止双重应答。"""
    runtime = get_geyi_runtime()
    if not runtime:
        return
    try:
        if not hasattr(runtime, '_suppressed_messages'):
            runtime._suppressed_messages = set()
        runtime._suppressed_messages.add(str(suppress_key))
    except Exception:
        pass
