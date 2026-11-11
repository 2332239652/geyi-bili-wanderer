# 歌伊的B站漫游 (geyi_bili_wanderer)

> [newGeYi](https://github.com/2332239652/newGeYi) 的官方附属 OlivOS 插件：让歌伊在闲时自主逛 B 站看视频、动态进化兴趣词库、见闻融入四层记忆，并支持群友分享视频互动。

本仓库只放**插件本体**。它需要一个运行中的 `newGeYi` 主插件（同进程）才能发挥全部能力；未检测到本体时会退化为独立回复模式。

---

## 一、核心特性

1. **闲暇自主漫游（Wander in Idle）**
   - 在后台闲时（默认每 4 小时、避开深夜免打扰）自主在 B 站寻找感兴趣的视频并观看简介与高赞热评。
   - 见闻自动转化为第一人称角色视角的看后心得，沉淀进 `newGeYi` 的 **L3 自身经历记忆（`self_memories`）**。
   - 实测收益有限（容易挤占自身记忆名额），因此 **`auto_wander_enabled` 默认关闭**，只保留「群友发链接 → 她去看」和手动触发两条路径。

2. **动态进化兴趣词库（Self-Evolving Keywords）**
   - 首次启动种子词涵盖 `COC跑团`、`跑团模组`、`二次元新番`、`单机游戏`、`沙雕日常`、`AI科技资讯`、`AI最新工具`、`大语言模型`。
   - 五频段轮转（TRPG / 单机游戏 / 二次元 / 沙雕日常 / 前沿 AI） + 近 5 次防题材单一排除；每次有概率（默认 35%）由大模型发散新探索词，超出池容量时优先淘汰最拥挤频段里最久未用的动态词。

3. **主动分享与吐槽互动（Video Sharing Interaction）**
   - 群友在群聊或私聊中发送 **BV 号** 或 **B 站视频链接（含 `b23.tv` 短链）** 并提及歌伊时，插件预热 ≤520 字符的结构化视频背景（标题 / AI 摘要 / 字幕 / 热评），无缝挂载到本体 `PromptBuilder`。
   - 本体在线时由本体以完整人设生成唯一回复，插件保持静默不抢话；安利行为同时记入 **L2 用户画像（`user_facts`）**。

4. **实时网络感知（Web Context）**
   - 消息里的普通网页链接自动抓取正文，查证/新闻类意图自动调用搜索 API，生成 ≈500 字符网络简报注入本体上下文。

5. **即时测试指令（Manual Trigger）**
   - 发送 `.bili wander`、`.bili` 或 `歌伊去刷会儿b站` / `歌伊看b站`，立刻触发一次漫游并回汇报。

6. **极度轻量**
   - 纯 Python 原生 `requests` + WBI 签名协议，零重型依赖（无 Torch、无 aiohttp、无 Whisper）。
   - 全链路使用 B 站公开匿名接口即可工作，配置 Cookie 后额外解锁「官方 AI 摘要」与「全量字幕提取」。

---

## 二、安装

1. 将本仓库整个文件夹（目录名须为 `geyi_bili_wanderer`）复制到 OlivOS 的 `plugin/app/` 下：

   ```
   <OlivOS 根目录>/plugin/app/geyi_bili_wanderer/
   ├── __init__.py
   ├── app.json
   ├── main.py
   ├── config.py
   ├── config.example.json
   ├── bili/
   └── core/
   ```

2. 确认 `newGeYi` 本体已装在同一个 OlivOS 实例中。
3. 重载 OlivOS 插件或重启平台。插件会在 `init_after` 阶段自动探测同进程本体 Runtime，并挂载 `video_context` / `web_context`。

> 启动日志形如：`[geyi_bili_wanderer] 歌伊的B站漫游 v1.0.0 初始化完成 | 自动漫游: 关 | 模式: 匿名模式 | 全网感知与B站多模态已挂载至主进程 | 歌伊本体: 已就绪`

---

## 三、配置

**不要改仓库内的 `config.json`**（该文件被 `.gitignore` 忽略）。运行时配置位于 OlivOS 数据目录：

```
<OlivOS 根目录>/plugin/data/geyi_bili_wanderer/config.json
```

首次使用请复制 [`config.example.json`](config.example.json) 过去并按需要修改。所有字段都有内置默认值，未写的字段自动取 `config.py` 里的 `DEFAULT_CONFIG`。

| 字段 | 默认 | 说明 |
|---|---|---|
| `enabled` | `true` | 总开关 |
| `auto_wander_enabled` | `false` | 定时后台漫游；默认关闭，见上文说明 |
| `wander_interval_minutes` | `240` | 自动漫游间隔（分钟） |
| `quiet_hours` | `"01:00-07:00"` | 免打扰时段 |
| `seed_keywords` | 8 个词 | 初始兴趣词 |
| `dynamic_interest_ratio` | `0.35` | 大模型发散新词的概率 |
| `max_keywords_pool` | `30` | 词库容量上限 |
| `trigger_keywords` | 见示例 | 手动漫游指令 |
| `share_triggers` | 见示例 | 群友分享视频的引导词 |
| `auto_share_video_reply` | `true` | 本体缺失时的独立回退回复 |
| `max_daily_wanders` | `12` | 每日漫游上限 |
| `bilibili_cookie` | `""` | 可选；填入 `SESSDATA=...` 解锁官方 AI 摘要与全量字幕。留空时会自动尝试读取 `~/.dsh/bili/credential.json` |
| `web_search_enabled` | `true` | 是否启用联网搜索/抓取感知 |
| `deepseek_search_api_key` | `""` | DeepSeek Search / Fetch REST API Key；留空则联网感知自动降级关闭 |

也可用环境变量 `GEYI_BILI_DATA_DIR` 覆盖状态目录（配置 + `wanderer.sqlite3`）。

### 与凭据有关的说明

本仓库发布前已做去敏处理：

- 运行时 `config.json` 中的 **B 站 SESSDATA / bili_jct / DedeUserID** 与 **DeepSeek API Key** 全部替换为占位空串；
- `config.py` 的 `DEFAULT_CONFIG` 内嵌 Key 一并清空；
- 仓库只保留 `config.example.json`（无真实值），`.gitignore` 明确忽略 `config.json`、`*.local`、`.env*`、`*.sqlite3`、`*.log`；
- 源码里出现的 `Cookie` / `Authorization` 等字样均为**字段名与请求头构造逻辑**，不含任何具体值。

部署时请在服务器本地填好自己的凭据，**切勿提交回仓库**。

---

## 四、与 newGeYi 的联动接口

| 挂载点 | 作用 |
|---|---|
| `runtime.video_context` / `prompt_builder.video_context` | 消息含 BV 号时注入视频背景简报 |
| `runtime.web_context` / `prompt_builder.web_context` | 网页抓取 / 搜索简报 |
| `runtime.store.add_self_memory` | 写入 L3 自身经历见闻 |
| `runtime.store.add_user_fact` | 写入 L2 用户偏好 |
| `runtime.gateway.chat` | 复用本体多渠道故障转移大模型网关 |
| `runtime._suppressed_messages` | 跨插件消息互斥，避免双重应答 |

全部通过 `sys.modules` 同进程探测，本体缺失时优雅降级，不抛异常。

---

## 五、目录结构

```
├── app.json                 # OlivOS 插件清单（namespace / priority / support）
├── __init__.py              # 包入口
├── main.py                  # 事件入口：init_after / group_message / private_message / menu / save
├── config.py                # 默认配置、状态目录解析、Cookie 解析
├── config.example.json      # 配置模板（无敏感值）
├── bili/
│   ├── client.py            # B 站 REST 客户端（WBI 签名、AI 摘要、字幕、热评）
│   └── resolver.py          # BV / b23.tv 短链解析
└── core/
    ├── interop.py           # 与 newGeYi 本体的跨插件网关
    ├── keywords.py          # 五频段动态兴趣词库
    ├── network_client.py    # DeepSeek Search / Fetch 客户端（TTL 缓存）
    ├── video_context.py     # 视频感知上下文（字数预算 + 记忆沉淀）
    ├── wanderer.py          # 漫游引擎（SQLite 去重、见闻蒸馏、定时线程）
    └── web_context.py       # 联网感知上下文
```

---

## 六、License

[MIT](LICENSE) © 2026 歌靡
