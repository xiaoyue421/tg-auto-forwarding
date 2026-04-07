from __future__ import annotations


def translate_error(message: str) -> str:
    mapping = {
        "simple mode requires TG_API_ID": "请先填写 TG_API_ID。",
        "simple mode requires TG_API_HASH": "请先填写 TG_API_HASH。",
        "simple mode requires TG_SOURCE_CHAT": "请先填写源频道，例如 @source_channel。",
        "simple mode requires TG_TARGET_CHATS": "请先填写目标频道，多个目标请用逗号分隔。",
        "simple mode requires TG_TARGET_CHATS or TG_BOT_TARGET_CHATS": "请至少填写一个账号目标频道或 Bot 目标频道。",
        "simple mode requires at least one target in TG_TARGET_CHATS": "目标频道不能为空，请至少填写一个目标频道。",
        "simple mode cannot set both TG_SESSION_STRING and TG_SESSION_FILE": "TG_SESSION_STRING 和 TG_SESSION_FILE 不能同时填写。",
        "telegram.api_id is required": "请先填写 telegram.api_id。",
        "telegram.api_hash is required": "请先填写 telegram.api_hash。",
        "telegram cannot set both session_string and session_file": "session_string 和 session_file 不能同时填写。",
        "TG_RULES_JSON must be valid JSON": "多规则配置格式不正确，请在网页里重新保存一次规则。",
        "TG_RULES_JSON must be a JSON array": "多规则配置格式不正确，规则列表必须是数组。",
        "workers must be a non-empty list": "请至少添加一条转发规则。",
        "no runnable workers found, check workers[].enabled": "没有可运行的转发规则，请检查规则是否已启用。",
        "filters.media_only and filters.text_only cannot both be true": "同一条规则里不能同时开启“仅媒体”和“仅文本”。",
        "TG_CONTENT_MATCH_MODE must be one of: all, any": "媒体 / 文本关系配置不正确，请使用 all 或 any。",
        "keyword filters must be arrays of strings": "关键词配置格式不正确，请在网页里重新保存规则。",
        "regex filters must be arrays of strings": "正则配置格式不正确，请在网页里重新保存规则。",
        "telegram.proxy requires type, host and port": "代理配置不完整，请同时填写代理类型、地址和端口。",
        "simple mode proxy requires both TG_PROXY_HOST and TG_PROXY_PORT": "代理配置不完整，请同时填写 TG_PROXY_HOST 和 TG_PROXY_PORT。",
        "telegram.proxies must be a list": "多代理配置格式不正确，请使用列表。",
        "TG_PROXY_URLS is invalid": "多代理格式不正确，请一行一个代理，例如 socks5://127.0.0.1:7890。",
        "no searchable sources found": "当前没有可搜索的源频道，请先至少配置一条规则。",
        "search query is required": "搜索关键词不能为空，请先输入你要找的内容。",
        "dashboard session is not authorized": "当前登录会话失效了，请重新生成并填写 session_string。",
        "dashboard session is not configured": "当前还没有可用的登录会话，请先填写 session_string。",
        "message not found": "没有找到这条消息，可能已被删除，或者来源频道填写不正确。",
        "manual forward requires target_chats or bot_target_chats": "请至少填写一个账号目标或 Bot 目标后再执行指定转发。",
        "manual forward has no available targets for the selected strategy": "当前选择的发送策略没有可用目标，请检查这条消息的账号目标 / Bot 目标配置。",
        "TG_BOT_TOKEN is required for bot forwarding": "要使用 Bot 转发，请先填写全局 BOT TOKEN。",
        "web login requires api_id": "请先填写 API ID。",
        "web login requires api_hash": "请先填写 API HASH。",
        "web login requires phone": "请先填写手机号，格式例如 +8613800000000。",
        "web login requires code": "请输入 Telegram 验证码。",
        "web login requires password": "该账号开启了两步验证，请输入两步验证密码。",
        "web login session not found": "当前网页登录流程已失效，请重新发送验证码。",
        "web login proxy requires both proxy_host and proxy_port": "代理配置不完整，请同时填写代理地址和代理端口。",
        "web login proxy port must be an integer": "代理端口格式不正确，请填写数字端口。",
        "web login api_id must be an integer": "API ID 格式不正确，请填写纯数字。",
        "telegram login did not complete": "Telegram 登录没有完成，请重新发送验证码后再试。",
    }
    if message in mapping:
        return mapping[message]

    if message == "all configured bot tokens failed to initialize":
        return "所有已配置的 Bot Token 都初始化失败了，请检查 Token、Bot 权限和目标频道设置。"
    if "is missing session_string or session_file" in message:
        return "当前任务缺少登录会话，请先生成并填写 session_string。"
    if "cannot set both session_string and session_file" in message:
        return "session_string 和 session_file 不能同时填写。"
    if "invalid boolean value" in message:
        return "布尔配置格式不正确，请使用 true 或 false。"
    if "is invalid regex" in message:
        return "你填写的正则有语法错误，请检查后再保存。"
    if "must be one of: parallel, account_only, account_first, bot_only, bot_first" in message:
        return "转发策略不正确，请使用 parallel、account_only、account_first、bot_only 或 bot_first。"
    if "content_match_mode must be one of: all, any" in message:
        return "媒体 / 文本关系不正确，请使用 all 或 any。"
    if message.endswith("is invalid") and "proxy" in message.lower():
        return "代理格式不正确，请填写为 socks5://127.0.0.1:7890 这种形式。"
    if message.endswith("rate_limit_delay_seconds must be a non-negative number"):
        return "全局发送间隔格式不正确，请填写 0 或更大的数字。"
    if message.endswith("must be a non-negative number") and "RATE_LIMIT_DELAY_SECONDS" in message:
        return "全局发送间隔格式不正确，请填写 0 或更大的数字。"
    if message.startswith("workers[") and ".name is required" in message:
        return "请给每条转发规则填写名称。"
    if message.startswith("workers[") and ".source is required" in message:
        return "请给规则填写源频道。"
    if message.startswith("workers[") and ".sources is required" in message:
        return "请给规则填写源频道。"
    if message.startswith("workers[") and ".sources[" in message and "is required" in message:
        return "请给规则填写源频道。"
    if message.startswith("workers[") and ".targets must be a non-empty list" in message:
        return "请给规则至少填写一个目标频道。"
    if message.startswith("workers[") and "must set at least one target in targets or bot_targets" in message:
        return "请至少填写一个账号目标或 Bot 目标。"
    if message.startswith("workers[") and ".bot_targets must be a list" in message:
        return "Bot 目标格式不正确，请在网页里重新填写。"
    if "uses account_only but has no account targets" in message:
        return "这条规则已设为“只用账号发送”，但还没有填写账号目标频道。"
    if "uses bot_only but has no bot targets" in message:
        return "这条规则已设为“只用 Bot 发送”，但还没有填写 Bot 目标频道。"
    if "uses bot_only but TG_BOT_TOKEN is missing" in message:
        return "这条规则已设为“只用 Bot 发送”，请先在基础配置里填写 Bot Token。"
    if message.startswith("workers[") and "cannot set both session_string and session_file" in message:
        return "规则里的 session_string 和 session_file 不能同时填写。"
    if message.startswith("TG_RULES_JSON[") and "must be an object" in message:
        return "多规则配置格式不正确，请在网页里重新保存一次规则。"
    if message.startswith("duplicate worker name"):
        return "规则名称重复了，请修改后再保存。"
    if "BOT_FORWARDS_FORBIDDEN" in message or "CHAT_WRITE_FORBIDDEN" in message:
        return "Bot 没有目标频道的发送权限，请先把 Bot 拉进目标频道并授予发言权限。"
    if "CHANNEL_PRIVATE" in message or "CHAT_ADMIN_REQUIRED" in message:
        return "当前账号或 Bot 无法访问该频道，请检查是否已加入频道并具备权限。"
    return message
