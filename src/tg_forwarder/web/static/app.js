const { createApp } = Vue;

function createRule(name = "rule_1") {
  return {
    name,
    enabled: true,
    group: "default",
    priority: 100,
    source_chat: "",
    target_chats: "",
    bot_target_chats: "",
    forward_strategy: "inherit",
    include_edits: false,
    forward_own_messages: false,
    keywords_any: "",
    keywords_all: "",
    block_keywords: "",
    regex_any: "",
    regex_all: "",
    regex_block: "",
    hdhive_resource_resolve_forward: false,
    hdhive_require_rule_match: false,
    resource_presets: [],
    media_only: false,
    text_only: false,
    content_match_mode: "all",
    case_sensitive: false,
  };
}

function createConfig() {
  return {
    api_id: "",
    api_hash: "",
    session_string: "",
    bot_token: "",
    forward_strategy: "parallel",
    rate_limit_protection: false,
    rate_limit_delay_seconds: 1.2,
    startup_notify_enabled: false,
    startup_notify_message: "",
    proxy_type: "socks5",
    proxy_host: "",
    proxy_port: "",
    proxy_user: "",
    proxy_password: "",
    proxy_rdns: true,
    search_default_mode: "fast",
    hdhive_checkin_method: "api_key",
    hdhive_api_key: "",
    hdhive_cookie: "",
    hdhive_login_username: "",
    hdhive_login_password: "",
    hdhive_checkin_enabled: false,
    hdhive_checkin_gambler: false,
    hdhive_checkin_use_proxy: true,
    hdhive_resource_unlock_enabled: false,
    hdhive_resource_unlock_max_points: 0,
    hdhive_resource_unlock_threshold_inclusive: true,
    hdhive_resource_unlock_skip_unknown_points: false,
    rules: [createRule()],
  };
}

function createForwardStrategyOptions() {
  return [
    {
      value: "account_only",
      label: "只用账号发送",
      help: "只用登录的用户账号发送，不会尝试 Bot。",
    },
    {
      value: "bot_only",
      label: "只用 Bot 发送",
      help: "只用 Bot 发送，不会回退到用户账号。",
    },
    {
      value: "parallel",
      label: "同时转发（账号和 Bot 都发）",
      help: "当你同时填了账号目标和 Bot 目标时，两边会同时尝试转发。",
    },
    {
      value: "account_first",
      label: "优先账号（失败后再试 Bot）",
      help: "先用登录账号转发，只有账号这一轮没有成功发出去时，才会回退到 Bot 转发。",
    },
    {
      value: "bot_first",
      label: "优先 Bot（失败后再试账号）",
      help: "先用 Bot 转发，只有 Bot 这一轮没有成功发出去时，才会回退到登录账号转发。",
    },
  ];
}

function createRuleForwardStrategyOptions() {
  return [
    {
      value: "inherit",
      label: "跟随全局",
      help: "这条规则直接使用“基础配置”里的全局发送策略。",
    },
    ...createForwardStrategyOptions(),
  ];
}

function createSearchState() {
  return {
    query: "",
    limit: 20,
    loading: false,
    forwardingKey: "",
    sourceFilter: "all",
    results: [],
  };
}

function createResourcePresetOptions() {
  return [
    { value: "115cdn", label: "115 资源", help: "识别 115cdn 链接" },
    { value: "ed2k", label: "ed2k", help: "识别 ed2k:// 下载地址" },
    { value: "magnet", label: "磁力", help: "识别 magnet: 磁力链接" },
    { value: "thunder", label: "迅雷", help: "识别 thunder:// 链接" },
    { value: "quark", label: "夸克 / UC", help: "识别 pan.quark.cn 和 drive.uc.cn" },
    { value: "aliyun", label: "阿里云盘", help: "识别 aliyundrive / alipan" },
    { value: "baidu", label: "百度网盘", help: "识别 pan.baidu / yun.baidu" },
  ];
}

function createTelegramLoginState() {
  return {
    phone: "",
    code: "",
    password: "",
    loginId: "",
    step: "idle",
    busy: false,
  };
}

const MONITOR_FIELD_MAP = {
  "\u6a21\u5f0f": "mode",
  "\u89c4\u5219": "ruleName",
  "\u6765\u6e90": "source",
  "\u76ee\u6807": "target",
  "\u6d88\u606fID": "messageId",
  "\u7c7b\u578b": "messageType",
  "\u5185\u5bb9": "preview",
};

const DISPATCH_SUCCESS_ACTIONS = new Set([
  "\u8d26\u53f7\u8f6c\u53d1\u6210\u529f",
  "\u8d26\u53f7\u91cd\u8bd5\u540e\u8f6c\u53d1\u6210\u529f",
  "Bot \u76f4\u8f6c\u6210\u529f",
  "Bot \u91cd\u8bd5\u540e\u76f4\u8f6c\u6210\u529f",
  "Bot \u590d\u5236\u6587\u672c\u6210\u529f",
  "Bot \u590d\u5236\u5a92\u4f53\u6210\u529f",
]);

function parseDispatchMonitorItem(item = {}) {
  const rawMessage = String(item.raw_message || item.message || "").trim();
  const parts = rawMessage
    .split(" | ")
    .map((part) => part.trim())
    .filter(Boolean);
  const parsed = {
    ...item,
    action: parts[0] || "",
    mode: "",
    ruleName: "",
    source: "",
    target: "",
    messageId: "",
    messageType: "",
    preview: "",
    note: "",
    isSuccessDispatch: false,
    channelLabel: "",
    channelTone: "muted",
    modeTone: "muted",
  };
  const extras = [];
  for (const part of parts.slice(1)) {
    const separatorIndex = part.indexOf("=");
    if (separatorIndex <= 0) {
      extras.push(part);
      continue;
    }
    const key = part.slice(0, separatorIndex).trim();
    const value = part.slice(separatorIndex + 1).trim();
    const mappedKey = MONITOR_FIELD_MAP[key];
    if (!mappedKey) {
      extras.push(part);
      continue;
    }
    parsed[mappedKey] = value;
  }
  parsed.note = extras.join(" | ");
  parsed.isSuccessDispatch = DISPATCH_SUCCESS_ACTIONS.has(parsed.action);
  parsed.channelLabel = parsed.action.startsWith("Bot") ? "Bot" : "\u8d26\u53f7";
  parsed.channelTone = parsed.action.startsWith("Bot") ? "warn" : "good";
  parsed.modeTone = String(parsed.mode || "").includes("\u961f\u5217") ? "warn" : "muted";
  return parsed;
}

createApp({
  data() {
    return {
      loading: false,
      saving: false,
      validating: false,
      actionBusy: false,
      authBusy: false,
      authed: false,
      passwordInput: localStorage.getItem("tg_dashboard_password") || "",
      notice: "",
      error: "",
      configPath: "",
      defaultStartupNotifyMessage: "",
      config: createConfig(),
      forwardStrategyOptions: createForwardStrategyOptions(),
      ruleForwardStrategyOptions: createRuleForwardStrategyOptions(),
      resourcePresetOptions: createResourcePresetOptions(),
      search: createSearchState(),
      telegramLogin: createTelegramLoginState(),
      status: {
        status: "stopped",
        config_path: "",
        last_error: null,
        snapshot: null,
      },
      health: {
        service: null,
        logs: null,
        queue: null,
        hdhive_checkin: null,
      },
      queue: {
        failedItems: [],
        actionBusy: false,
        successHistoryTotalCount: 0,
        successHistoryRules: [],
        successHistoryRuleName: "",
      },
      validation: null,
      logs: [],
      timers: [],
      ui: {
        activeTab: "rules",
        expandedRuleIndex: 0,
        collapsedRuleGroups: {},
        ruleGroupFilter: "all",
        showSessionString: false,
        showBotToken: false,
        hdhiveCredentialsOpen: false,
        logFilter: "monitor",
        showBackToTop: false,
        showLogBottom: false,
      },
      hdhiveResolveBusy: false,
      hdhiveResolveUnlockBusy: false,
      hdhiveResolveTestUrl: "",
      hdhiveResolveResult: "",
      hdhiveResolvePreview: null,
      hdhiveRealUnlockResult: null,
    };
  },
  computed: {
    statusTone() {
      switch (this.status.status) {
        case "running":
          return "good";
        case "starting":
        case "stopping":
          return "warn";
        case "error":
          return "bad";
        default:
          return "muted";
      }
    },
    statusText() {
      const map = {
        running: "运行中",
        starting: "启动中",
        stopping: "停止中",
        stopped: "已停止",
        error: "错误",
      };
      return map[this.status.status] || "未知";
    },
    hdhiveOutcomeLabel() {
      const o = this.hdhiveResolvePreview?.outcome;
      const map = {
        direct: "旧模式：Cookie 直连",
        auto_unlock: "将回退自动解锁（自动解锁规则）",
        openapi: "将回退自动解锁（自动解锁规则）",
        fail: "不会自动解锁",
        invalid_url: "链接无效",
      };
      return map[o] || "检测结果";
    },
    workerCards() {
      return this.status.snapshot?.workers || [];
    },
    statusRateLimitEnabled() {
      return Boolean(this.status.snapshot?.rate_limit_protection_enabled);
    },
    statusRateLimitDelaySeconds() {
      return this.normalizeNonNegativeNumber(
        this.status.snapshot?.rate_limit_delay_seconds,
        this.config.rate_limit_delay_seconds,
      );
    },
    statusGlobalQueueDepth() {
      return Math.floor(
        this.normalizeNonNegativeNumber(this.status.snapshot?.global_queue_depth, 0),
      );
    },
    statusGlobalQueueFailed() {
      return Math.floor(
        this.normalizeNonNegativeNumber(this.status.snapshot?.global_queue_failed, 0),
      );
    },
    statusGlobalQueueDeliveryDepth() {
      return Math.floor(
        this.normalizeNonNegativeNumber(this.status.snapshot?.global_queue_delivery_depth, 0),
      );
    },
    statusGlobalQueueDeliveryFailed() {
      return Math.floor(
        this.normalizeNonNegativeNumber(this.status.snapshot?.global_queue_delivery_failed, 0),
      );
    },
    dispatcherAlive() {
      return Boolean(this.status.snapshot?.dispatcher_alive);
    },
    dispatcherPid() {
      return this.status.snapshot?.dispatcher_pid || "";
    },
    statusQueueDbPath() {
      return this.status.snapshot?.queue_db_path || "";
    },
    failedQueueItems() {
      return this.queue.failedItems || [];
    },
    successHistoryRules() {
      return this.queue.successHistoryRules || [];
    },
    ruleGroups() {
      const groups = new Set();
      for (const rule of this.config.rules || []) {
        const key = String(rule.group || "").trim() || "default";
        groups.add(key);
      }
      return Array.from(groups.values()).sort((a, b) => a.localeCompare(b, "zh-CN"));
    },
    groupedRuleBuckets() {
      const groups = new Map();
      for (const rule of this.config.rules || []) {
        const g = String(rule.group || "").trim() || "default";
        if (!groups.has(g)) {
          groups.set(g, []);
        }
        groups.get(g).push(rule);
      }
      return Array.from(groups.entries())
        .sort((a, b) => a[0].localeCompare(b[0], "zh-CN"))
        .map(([group, rules]) => ({ group, rules }));
    },
    filteredGroupedRuleBuckets() {
      const selected = String(this.ui.ruleGroupFilter || "all").trim() || "all";
      if (selected === "all") {
        return this.groupedRuleBuckets;
      }
      return this.groupedRuleBuckets.filter((bucket) => bucket.group === selected);
    },
    selectedSuccessHistoryRule() {
      return (
        this.successHistoryRules.find(
          (item) => item.rule_name === this.queue.successHistoryRuleName,
        ) || null
      );
    },
    selectedSuccessHistoryCount() {
      return Number(this.selectedSuccessHistoryRule?.count || 0);
    },
    recentSuccessfulDispatches() {
      return this.sortedLogs
        .filter((item) => this.isMonitorLog(item))
        .map((item) => parseDispatchMonitorItem(item))
        .filter((item) => item.isSuccessDispatch)
        .slice(0, 12);
    },
    statusRateLimitText() {
      if (!this.status.snapshot) {
        return "";
      }
      if (!this.statusRateLimitEnabled) {
        return "\u81ea\u52a8\u6d88\u606f\u4f1a\u5148\u8fdb\u5165\u53d1\u9001\u961f\u5217\uff0c\u76ee\u524d\u672a\u5f00\u542f\u989d\u5916\u9650\u6d41";
      }
      return `\u81ea\u52a8\u6d88\u606f\u4f1a\u5148\u8fdb\u5165\u53d1\u9001\u961f\u5217\uff0c\u5df2\u5f00\u542f\u9650\u6d41\uff0c\u95f4\u9694 ${this.formatSeconds(this.statusRateLimitDelaySeconds)}`;
    },
    healthCheckin() {
      return this.health?.hdhive_checkin || null;
    },
    healthCheckinEnabled() {
      return Boolean(this.healthCheckin?.enabled);
    },
    healthCheckinMethodLabel() {
      return String(this.healthCheckin?.method || "") === "cookie"
        ? "网页账号（站点登录）"
        : "API Key 模式";
    },
    healthCheckinNextRetryText() {
      const epoch = Number(this.healthCheckin?.next_retry_epoch || 0);
      if (!epoch) {
        return "-";
      }
      return this.formatDateTime(epoch);
    },
    ruleCount() {
      return this.config.rules.length;
    },
    enabledRuleCount() {
      return this.config.rules.filter((rule) => rule.enabled).length;
    },
    runningWorkerCount() {
      return this.workerCards.filter((worker) => worker.is_alive).length;
    },
    searchResultCount() {
      return this.search.results.length;
    },
    searchVisibleCount() {
      return this.filteredSearchResults.length;
    },
    hasSessionString() {
      return Boolean((this.config.session_string || "").trim());
    },
    forwardStrategyHelpText() {
      const current = this.forwardStrategyOptions.find(
        (item) => item.value === this.config.forward_strategy,
      );
      return (
        current?.help ||
        "\u5f53\u4f60\u540c\u65f6\u586b\u4e86\u8d26\u53f7\u76ee\u6807\u548c Bot \u76ee\u6807\u65f6\uff0c\u53ef\u4ee5\u5728\u8fd9\u91cc\u51b3\u5b9a\u8c01\u5148\u8f6c\u53d1\u3002"
      );
    },
    sortedLogs() {
      return this.sortLogsNewestFirst(this.logs);
    },
    monitorLogCount() {
      return this.sortedLogs.filter((item) => this.isMonitorLog(item)).length;
    },
    errorLogCount() {
      return this.sortedLogs.filter((item) => item.level === "ERROR").length;
    },
    canSearch() {
      return Boolean(this.normalizeSearchQuery(this.search.query));
    },
    searchSourceOptions() {
      const groups = new Map();
      for (const item of this.search.results) {
        const key = item.source_key || item.source_chat || item.source_label || "unknown_source";
        if (!groups.has(key)) {
          groups.set(key, {
            key,
            label: item.source_label || item.source_chat || "未知频道",
            count: 0,
          });
        }
        groups.get(key).count += 1;
      }
      return [
        {
          key: "all",
          label: "全部",
          count: this.search.results.length,
        },
        ...Array.from(groups.values()),
      ];
    },
    activeSearchSourceOption() {
      return (
        this.searchSourceOptions.find((item) => item.key === this.search.sourceFilter) ||
        this.searchSourceOptions[0] || {
          key: "all",
          label: "全部",
          count: 0,
        }
      );
    },
    filteredSearchResults() {
      if (this.search.sourceFilter === "all") {
        return this.search.results;
      }
      return this.search.results.filter((item) => {
        const key = item.source_key || item.source_chat || item.source_label || "unknown_source";
        return key === this.search.sourceFilter;
      });
    },
    filteredLogs() {
      return this.getLogsByFilter(this.ui.logFilter);
    },
    tabItems() {
      return [
        { key: "config", label: "基础配置" },
        { key: "rules", label: `规则 ${this.ruleCount}` },
        { key: "search", label: `搜索 ${this.searchResultCount}` },
        { key: "status", label: `状态 ${this.runningWorkerCount}` },
        { key: "logs", label: "日志" },
      ];
    },
  },
  mounted() {
    window.addEventListener("scroll", this.handleWindowScroll, { passive: true });
    this.handleWindowScroll();
    if (this.passwordInput) {
      this.login();
    }
  },
  beforeUnmount() {
    window.removeEventListener("scroll", this.handleWindowScroll);
    this.stopPolling();
  },
  methods: {
    handleWindowScroll() {
      this.ui.showBackToTop = window.scrollY > 420;
    },
    scrollToTop() {
      window.scrollTo({ top: 0, behavior: "smooth" });
    },
    normalizeNonNegativeNumber(value, fallback = 0) {
      const numeric = Number(value);
      if (!Number.isFinite(numeric) || numeric < 0) {
        const fallbackNumber = Number(fallback);
        return Number.isFinite(fallbackNumber) && fallbackNumber >= 0 ? fallbackNumber : 0;
      }
      return numeric;
    },
    normalizeRateLimitDelayInput(value) {
      return this.normalizeNonNegativeNumber(value, 1.2);
    },
    formatSeconds(value) {
      const numeric = this.normalizeNonNegativeNumber(value, 0);
      const text = Number.isInteger(numeric)
        ? numeric.toFixed(0)
        : numeric.toFixed(1).replace(/\.0$/, "");
      return `${text} \u79d2`;
    },
    getDefaultStartupNotifyMessage() {
      return String(this.defaultStartupNotifyMessage || "");
    },
    resolveStartupNotifyMessage(value) {
      const text = String(value || "");
      return this.normalizeMultilineText(text)
        ? text
        : this.getDefaultStartupNotifyMessage();
    },
    serializeStartupNotifyMessage(value) {
      const text = String(value || "");
      const normalizedText = this.normalizeMultilineText(text);
      const normalizedDefault = this.normalizeMultilineText(this.getDefaultStartupNotifyMessage());
      if (!normalizedText) {
        return "";
      }
      if (normalizedDefault && normalizedText === normalizedDefault) {
        return "";
      }
      return text;
    },
    normalizeMultilineText(value) {
      return String(value || "").replace(/\r\n/g, "\n").trim();
    },
    getForwardStrategyLabel(value) {
      const current = this.forwardStrategyOptions.find((item) => item.value === value);
      return current?.label || value || "parallel";
    },
    getRuleForwardStrategyLabel(value) {
      const current = this.ruleForwardStrategyOptions.find((item) => item.value === value);
      return current?.label || this.getForwardStrategyLabel(value);
    },
    getRuleForwardStrategyHelpText(value) {
      const current = this.ruleForwardStrategyOptions.find((item) => item.value === value);
      return current?.help || "";
    },
    buildManualForwardNotice(response) {
      const accountCount = Number(response.data?.account_sent || 0);
      const botCount = Number(response.data?.bot_sent || 0);
      const attemptedAccount = Boolean(response.data?.attempted_account);
      const attemptedBot = Boolean(response.data?.attempted_bot);
      const summary = [];
      if (attemptedAccount) {
        summary.push(`\u8d26\u53f7\u6210\u529f ${accountCount} \u4e2a`);
      }
      if (attemptedBot) {
        summary.push(`Bot \u6210\u529f ${botCount} \u4e2a`);
      }
      const strategyLabel = this.getForwardStrategyLabel(
        response.data?.forward_strategy || this.config.forward_strategy,
      );
      const matchedRule = String(response.data?.matched_rule || "").trim();
      const usedTextOverride = Boolean(response.data?.used_text_override);
      let ruleHint = "";
      if (matchedRule) {
        ruleHint = usedTextOverride
          ? ` | \u5339\u914d\u89c4\u5219\u300c${matchedRule}\u300d\uff08\u5df2\u6309\u89e3\u6790\u6587\u672c/\u76f4\u94fe\u53d1\u9001\uff09`
          : ` | \u5339\u914d\u89c4\u5219\u300c${matchedRule}\u300d\uff08\u539f\u6837\u8f6c\u53d1\uff09`;
      }
      return summary.length
        ? `${response.message} ${strategyLabel} | ${summary.join(" | ")}${ruleHint}`
        : `${response.message} ${strategyLabel}${ruleHint}`;
    },
    handleLogBoxScroll() {
      this.syncLogBoxState();
    },
    syncLogBoxState() {
      const logBox = this.$refs.logBox;
      if (!logBox || this.ui.activeTab !== "logs") {
        this.ui.showLogBottom = false;
        return;
      }
      const maxScrollTop = Math.max(0, logBox.scrollHeight - logBox.clientHeight);
      this.ui.showLogBottom = maxScrollTop > 24 && logBox.scrollTop < maxScrollTop - 24;
    },
    scrollLogBoxToBottom() {
      const logBox = this.$refs.logBox;
      if (!logBox) {
        return;
      }
      logBox.scrollTo({ top: logBox.scrollHeight, behavior: "smooth" });
      window.setTimeout(() => this.syncLogBoxState(), 220);
    },
    authHeaders() {
      return this.passwordInput
        ? { "X-Dashboard-Password": this.passwordInput }
        : {};
    },
    normalizeRule(rule = {}, index = 0) {
      const normalizedPresets = Array.isArray(rule.resource_presets)
        ? rule.resource_presets.map((item) => String(item || "").trim()).filter(Boolean)
        : [];
      return {
        ...createRule(`rule_${index + 1}`),
        ...rule,
        name: (rule.name || `rule_${index + 1}`).trim(),
        group: String(rule.group || "default").trim() || "default",
        priority: Math.max(1, Number.parseInt(rule.priority, 10) || index + 1),
        forward_strategy: String(rule.forward_strategy || "inherit").trim() || "inherit",
        regex_any: this.normalizeMultilineText(rule.regex_any || ""),
        regex_all: this.normalizeMultilineText(rule.regex_all || ""),
        regex_block: this.normalizeMultilineText(rule.regex_block || ""),
        hdhive_resource_resolve_forward: Boolean(rule.hdhive_resource_resolve_forward),
        hdhive_require_rule_match: Boolean(rule.hdhive_require_rule_match),
        resource_presets: Array.from(new Set(normalizedPresets)),
      };
    },
    normalizeConfig(payload = {}) {
      const base = createConfig();
      const rules =
        Array.isArray(payload.rules) && payload.rules.length
          ? payload.rules.map((rule, index) => this.normalizeRule(rule, index))
          : [createRule("rule_1")];
      return {
        ...base,
        ...payload,
        rate_limit_delay_seconds: this.normalizeRateLimitDelayInput(
          payload.rate_limit_delay_seconds,
        ),
        startup_notify_message: this.resolveStartupNotifyMessage(payload.startup_notify_message),
        rules,
      };
    },
    normalizeSearchResult(item = {}) {
      return {
        ...item,
        source_key: item.source_chat || item.source_label || "unknown_source",
      };
    },
    splitList(value) {
      return String(value || "")
        .split(/[,;\r\n]+/)
        .map((item) => item.trim())
        .filter(Boolean);
    },
    splitRegexLines(value) {
      return String(value || "")
        .replace(/\r\n/g, "\n")
        .split("\n")
        .map((item) => item.trim())
        .filter(Boolean);
    },
    resultKey(result) {
      return `${result.source_chat}:${result.message_id}`;
    },
    formatDateTime(value) {
      if (!value) {
        return "";
      }
      try {
        if (typeof value === "number") {
          return new Date(value * 1000).toLocaleString("zh-CN", { hour12: false });
        }
        return new Date(value).toLocaleString("zh-CN", { hour12: false });
      } catch (_err) {
        return value;
      }
    },
    isMonitorLog(item = {}) {
      return Boolean(item.monitor);
    },
    normalizeSearchQuery(value) {
      return String(value || "").trim();
    },
    hasForwardTargets(result = {}) {
      return Boolean(result.default_target_chats || result.default_bot_target_chats);
    },
    displayLogMessage(item = {}) {
      return item.raw_message || item.message || "";
    },
    syncSearchSourceFilter() {
      if (this.search.sourceFilter === "all") {
        return;
      }
      const exists = this.search.results.some(
        (item) => (item.source_key || item.source_chat || item.source_label || "unknown_source") === this.search.sourceFilter,
      );
      if (!exists) {
        this.search.sourceFilter = "all";
      }
    },
    getLogsByFilter(filter) {
      if (filter === "monitor") {
        return this.sortedLogs.filter((item) => this.isMonitorLog(item));
      }
      if (filter === "error") {
        return this.sortedLogs.filter((item) => item.level === "ERROR");
      }
      return this.sortedLogs;
    },
    stringifyErrorPart(value) {
      if (value === null || value === undefined) {
        return "";
      }
      if (typeof value === "string") {
        return value.trim();
      }
      if (typeof value === "number" || typeof value === "boolean") {
        return String(value);
      }
      if (Array.isArray(value)) {
        return value
          .map((item) => this.stringifyErrorPart(item))
          .filter(Boolean)
          .join("；");
      }
      if (typeof value === "object") {
        return (
          this.stringifyErrorPart(value.detail) ||
          this.stringifyErrorPart(value.message) ||
          this.stringifyErrorPart(value.msg) ||
          this.stringifyErrorPart(value.error) ||
          this.stringifyErrorPart(value.reason) ||
          JSON.stringify(value)
        );
      }
      return String(value);
    },
    formatApiError(payload = {}) {
      return (
        this.stringifyErrorPart(payload?.detail) ||
        this.stringifyErrorPart(payload?.message) ||
        "请求失败"
      );
    },
    normalizeCaughtError(err) {
      return (
        this.stringifyErrorPart(err?.message) ||
        this.stringifyErrorPart(err) ||
        "请求失败"
      );
    },
    sortLogsNewestFirst(items = []) {
      return items
        .map((item, index) => ({ item, index }))
        .sort((left, right) => {
          const sequenceDiff = Number(right.item.sequence || 0) - Number(left.item.sequence || 0);
          if (sequenceDiff !== 0) {
            return sequenceDiff;
          }
          const timeDiff = Number(right.item.created_at || 0) - Number(left.item.created_at || 0);
          if (timeDiff !== 0) {
            return timeDiff;
          }
          return right.index - left.index;
        })
        .map((entry) => entry.item);
    },
    nextRuleName() {
      const usedNames = new Set(
        this.config.rules.map((rule) => (rule.name || "").trim()).filter(Boolean),
      );
      let index = this.config.rules.length + 1;
      let candidate = `rule_${index}`;
      while (usedNames.has(candidate)) {
        index += 1;
        candidate = `rule_${index}`;
      }
      return candidate;
    },
    buildCopyName(name) {
      const base = (name || "rule").trim() || "rule";
      const usedNames = new Set(
        this.config.rules.map((rule) => (rule.name || "").trim()).filter(Boolean),
      );
      let suffix = 1;
      let candidate = `${base}_copy`;
      while (usedNames.has(candidate)) {
        suffix += 1;
        candidate = `${base}_copy_${suffix}`;
      }
      return candidate;
    },
    setActiveTab(key) {
      this.ui.activeTab = key;
      this.$nextTick(() => this.syncLogBoxState());
    },
    setLogFilter(filter) {
      this.ui.logFilter = filter;
      this.$nextTick(() => this.syncLogBoxState());
    },
    expandRule(index) {
      this.ui.expandedRuleIndex = index;
      this.ui.activeTab = "rules";
    },
    toggleRule(index) {
      this.ui.expandedRuleIndex = this.ui.expandedRuleIndex === index ? -1 : index;
    },
    isRuleExpanded(index) {
      return this.ui.expandedRuleIndex === index;
    },
    countRuleTargets(rule) {
      return this.splitList(rule.target_chats).length;
    },
    countRuleBotTargets(rule) {
      return this.splitList(rule.bot_target_chats).length;
    },
    countRuleFilters(rule) {
      let count = 0;
      count += this.splitList(rule.keywords_any).length;
      count += this.splitList(rule.keywords_all).length;
      count += this.splitList(rule.block_keywords).length;
      count += this.splitRegexLines(rule.regex_any).length;
      count += this.splitRegexLines(rule.regex_all).length;
      count += this.splitRegexLines(rule.regex_block).length;
      if (rule.hdhive_resource_resolve_forward) count += 1;
      count += Array.isArray(rule.resource_presets) ? rule.resource_presets.length : 0;
      if (rule.media_only) count += 1;
      if (rule.text_only) count += 1;
      return count;
    },
    buildRuleSummary(rule) {
      const targetCount = this.countRuleTargets(rule);
      const botTargetCount = this.countRuleBotTargets(rule);
      const filterCount = this.countRuleFilters(rule);
      return [
        rule.source_chat || "未设置源",
        `分组 ${String(rule.group || "default").trim() || "default"}`,
        `优先级 ${Math.max(1, Number.parseInt(rule.priority, 10) || 1)}`,
        `账号目标 ${targetCount}`,
        `Bot 目标 ${botTargetCount}`,
        this.getRuleForwardStrategyLabel(rule.forward_strategy || "inherit"),
        `过滤 ${filterCount}`,
      ].join(" · ");
    },
    _normalizeRulePriority(rule, fallback = 1) {
      const parsed = Number.parseInt(rule.priority, 10);
      rule.priority = Math.max(1, parsed || fallback);
    },
    ruleIndex(rule) {
      return this.config.rules.indexOf(rule);
    },
    isGroupCollapsed(groupName) {
      const key = String(groupName || "").trim() || "default";
      return Boolean(this.ui.collapsedRuleGroups?.[key]);
    },
    toggleGroupCollapsed(groupName) {
      const key = String(groupName || "").trim() || "default";
      this.ui.collapsedRuleGroups = {
        ...(this.ui.collapsedRuleGroups || {}),
        [key]: !this.isGroupCollapsed(key),
      };
    },
    sortGroupByPriority(groupName) {
      const key = String(groupName || "").trim() || "default";
      const inGroup = [];
      const outGroup = [];
      for (const rule of this.config.rules) {
        const g = String(rule.group || "").trim() || "default";
        if (g === key) {
          inGroup.push(rule);
        } else {
          outGroup.push(rule);
        }
      }
      inGroup.sort((a, b) => {
        const pa = Math.max(1, Number.parseInt(a.priority, 10) || 1);
        const pb = Math.max(1, Number.parseInt(b.priority, 10) || 1);
        if (pa !== pb) return pa - pb;
        return String(a.name || "").localeCompare(String(b.name || ""), "zh-CN");
      });
      this.config.rules = [...outGroup, ...inGroup];
    },
    moveRule(index, direction) {
      const target = index + direction;
      if (target < 0 || target >= this.config.rules.length) {
        return;
      }
      const current = this.config.rules[index];
      this.config.rules.splice(index, 1);
      this.config.rules.splice(target, 0, current);
      if (this.ui.expandedRuleIndex === index) {
        this.ui.expandedRuleIndex = target;
      } else if (this.ui.expandedRuleIndex === target) {
        this.ui.expandedRuleIndex = index;
      }
    },
    toggleGroupEnabled(groupName, enabled) {
      const g = String(groupName || "").trim() || "default";
      this.config.rules = this.config.rules.map((rule) => {
        const rg = String(rule.group || "").trim() || "default";
        if (rg !== g) {
          return rule;
        }
        return { ...rule, enabled };
      });
    },
    setRuleGroupFilter(groupName) {
      const selected = String(groupName || "all").trim() || "all";
      this.ui.ruleGroupFilter = selected;
    },
    addRule(groupName = "") {
      const index = this.config.rules.length;
      const next = createRule(this.nextRuleName());
      const g = String(groupName || "").trim();
      if (g) {
        next.group = g;
      }
      this.config.rules.push(next);
      this.expandRule(index);
    },
    duplicateRule(index) {
      const sourceRule = this.config.rules[index];
      const clonedRule = this.normalizeRule(
        {
          ...sourceRule,
          name: this.buildCopyName(sourceRule.name),
        },
        index + 1,
      );
      this.config.rules.splice(index + 1, 0, clonedRule);
      this.expandRule(index + 1);
    },
    removeRule(index) {
      if (this.config.rules.length === 1) {
        this.config.rules = [createRule("rule_1")];
        this.ui.expandedRuleIndex = 0;
        return;
      }
      this.config.rules.splice(index, 1);
      if (this.ui.expandedRuleIndex >= this.config.rules.length) {
        this.ui.expandedRuleIndex = this.config.rules.length - 1;
      }
    },
    resetTelegramLogin(options = {}) {
      const preservePhone = Boolean(options.preservePhone);
      const phone = preservePhone ? this.telegramLogin.phone : "";
      this.telegramLogin = {
        ...createTelegramLoginState(),
        phone,
      };
    },
    async fetchJson(url, options = {}) {
      const mergedHeaders = {
        "Content-Type": "application/json",
        ...this.authHeaders(),
        ...(options.headers || {}),
      };
      const response = await fetch(url, {
        ...options,
        credentials: "include",
        headers: mergedHeaders,
      });
      const rawText = await response.text();
      const trimmed = rawText.trim();
      let payload = {};
      if (trimmed) {
        try {
          payload = JSON.parse(rawText);
        } catch (_e) {
          if (!response.ok) {
            const statusLine = `HTTP ${response.status}${response.statusText ? ` ${response.statusText}` : ""}`;
            throw new Error(`${statusLine}：${trimmed.slice(0, 800)}`);
          }
          throw new Error(`响应不是合法 JSON（HTTP ${response.status}）`);
        }
      }
      if (!response.ok) {
        const formatted = this.formatApiError(payload);
        if (formatted && formatted !== "请求失败") {
          throw new Error(formatted);
        }
        const statusLine = `HTTP ${response.status}${response.statusText ? ` ${response.statusText}` : ""}`;
        throw new Error(
          trimmed
            ? `${statusLine}（响应不是标准 JSON，无法解析错误详情）`
            : `${statusLine}（无响应正文；多为未连上后端、反代超时或服务未启动）`,
        );
      }
      return payload;
    },
    async login() {
      this.authBusy = true;
      this.error = "";
      this.notice = "";
      try {
        await this.fetchJson("/api/login", {
          method: "POST",
          body: JSON.stringify({ password: this.passwordInput }),
        });
        this.authed = true;
        localStorage.setItem("tg_dashboard_password", this.passwordInput);
        this.notice = "登录成功。";
        await this.bootstrap();
        this.startPolling();
      } catch (err) {
        this.authed = false;
        this.error = this.normalizeCaughtError(err);
      } finally {
        this.authBusy = false;
      }
    },
    logout() {
      this.authed = false;
      this.notice = "";
      this.error = "";
      this.validation = null;
      this.logs = [];
      this.defaultStartupNotifyMessage = "";
      this.config = createConfig();
      this.search = createSearchState();
      this.telegramLogin = createTelegramLoginState();
      this.status = {
        status: "stopped",
        config_path: "",
        last_error: null,
        snapshot: null,
      };
      this.health = {
        service: null,
        logs: null,
        queue: null,
        hdhive_checkin: null,
      };
      this.queue = {
        failedItems: [],
        actionBusy: false,
        successHistoryTotalCount: 0,
        successHistoryRules: [],
        successHistoryRuleName: "",
      };
      this.ui.showLogBottom = false;
      localStorage.removeItem("tg_dashboard_password");
      this.stopPolling();
    },
    async bootstrap() {
      this.loading = true;
      try {
        await Promise.all([
          this.fetchConfig(),
          this.fetchStatus(),
          this.fetchHealth(),
          this.fetchLogs(),
          this.fetchFailedQueue({ silent: true }),
          this.fetchSuccessHistorySummary({ silent: true }),
        ]);
      } finally {
        this.loading = false;
      }
    },
    startPolling() {
      this.stopPolling();
      this.timers.push(setInterval(() => this.fetchStatus({ silent: true }), 1000));
      this.timers.push(setInterval(() => this.fetchHealth({ silent: true }), 2500));
      this.timers.push(setInterval(() => this.fetchLogs({ silent: true }), 1200));
      this.timers.push(setInterval(() => this.fetchFailedQueue({ silent: true }), 3000));
      this.timers.push(setInterval(() => this.fetchSuccessHistorySummary({ silent: true }), 4000));
    },
    stopPolling() {
      this.timers.forEach((timerId) => clearInterval(timerId));
      this.timers = [];
    },
    async fetchConfig() {
      try {
        const response = await this.fetchJson("/api/config");
        this.defaultStartupNotifyMessage = String(response.data.defaultStartupNotifyMessage || "");
        this.config = this.normalizeConfig(response.data.config);
        this.configPath = response.data.configPath;
      } catch (err) {
        this.error = this.normalizeCaughtError(err);
      }
    },
    async fetchStatus(options = {}) {
      const silent = Boolean(options.silent);
      try {
        const response = await this.fetchJson("/api/status");
        this.status = response.data;
      } catch (err) {
        if (!silent) {
          this.error = this.normalizeCaughtError(err);
        }
      }
    },
    async fetchHealth(options = {}) {
      const silent = Boolean(options.silent);
      try {
        const response = await this.fetchJson("/api/health");
        this.health = response.data || {};
      } catch (err) {
        if (!silent) {
          this.error = this.normalizeCaughtError(err);
        }
      }
    },
    async fetchLogs(options = {}) {
      const silent = Boolean(options.silent);
      try {
        const response = await this.fetchJson("/api/logs?limit=220");
        this.logs = response.data.items || [];
        this.$nextTick(() => this.syncLogBoxState());
      } catch (err) {
        if (!silent) {
          this.error = this.normalizeCaughtError(err);
        }
      }
    },
    async fetchFailedQueue(options = {}) {
      const silent = Boolean(options.silent);
      try {
        const response = await this.fetchJson("/api/queue/failed?limit=20");
        this.queue.failedItems = response.data.items || [];
      } catch (err) {
        if (!silent) {
          this.error = this.normalizeCaughtError(err);
        }
      }
    },
    async fetchSuccessHistorySummary(options = {}) {
      const silent = Boolean(options.silent);
      try {
        const response = await this.fetchJson("/api/queue/success-history/summary");
        this.queue.successHistoryTotalCount = Number(response.data.total_count || 0);
        this.queue.successHistoryRules = response.data.rules || [];
        if (
          this.queue.successHistoryRuleName &&
          !this.queue.successHistoryRules.some(
            (item) => item.rule_name === this.queue.successHistoryRuleName,
          )
        ) {
          this.queue.successHistoryRuleName = "";
        }
      } catch (err) {
        if (!silent) {
          this.error = this.normalizeCaughtError(err);
        }
      }
    },
    async triggerHdhiveResolveTest() {
      this.hdhiveResolveBusy = true;
      this.hdhiveResolveResult = "";
      this.hdhiveResolvePreview = null;
      this.hdhiveRealUnlockResult = null;
      this.notice = "";
      this.error = "";
      try {
        const url = String(this.hdhiveResolveTestUrl ?? "").trim();
        if (!url) {
          this.error = "请先粘贴 hdhive.com/resource/... 链接。";
          return;
        }
        const response = await this.fetchJson("/api/hdhive/resolve-test", {
          method: "POST",
          body: JSON.stringify({ url }),
        });
        const preview = response.data?.preview ?? null;
        if (preview && typeof preview === "object" && !preview.auto_unlock_preview && preview.openapi_preview) {
          preview.auto_unlock_preview = preview.openapi_preview;
        }
        this.hdhiveResolvePreview = preview && typeof preview === "object" ? preview : null;
        const got = String(response.data?.redirect_url ?? "").trim();
        this.hdhiveResolveResult = got;
        this.notice = String(response.message || "").trim() || "检测完成。";
      } catch (err) {
        this.error = this.normalizeCaughtError(err);
      } finally {
        this.hdhiveResolveBusy = false;
      }
    },
    async triggerHdhiveResolveUnlockTest() {
      const url = String(this.hdhiveResolveTestUrl ?? "").trim();
      if (!url) {
        this.error = "请先粘贴 hdhive.com/resource/... 链接。";
        return;
      }
      if (
        !window.confirm(
          "将按与「HDHive 专用直链转发」相同的逻辑真实调用解锁接口，可能消耗积分。确定继续？",
        )
      ) {
        return;
      }
      this.hdhiveResolveUnlockBusy = true;
      this.hdhiveRealUnlockResult = null;
      this.notice = "";
      this.error = "";
      try {
        const response = await this.fetchJson("/api/hdhive/resolve-unlock-test", {
          method: "POST",
          body: JSON.stringify({ url }),
        });
        const d = response.data || {};
        this.hdhiveRealUnlockResult = {
          success: Boolean(d.success),
          share_link: String(d.share_link || "").trim(),
          skipped_reason: String(d.skipped_reason || "").trim(),
          error_message: String(d.error_message || "").trim(),
          slug: String(d.slug || "").trim(),
        };
        this.notice = String(response.message || "").trim() || (d.success ? "已获取直链。" : "解锁未完成。");
      } catch (err) {
        this.error = this.normalizeCaughtError(err);
      } finally {
        this.hdhiveResolveUnlockBusy = false;
      }
    },
    async saveConfig(options = {}) {
      const successMessage = options.successMessage || "";
      const throwOnError = Boolean(options.throwOnError);
      this.saving = true;
      this.notice = "";
      this.error = "";
      try {
        this.config.rate_limit_delay_seconds = this.normalizeRateLimitDelayInput(
          this.config.rate_limit_delay_seconds,
        );
        this.config.hdhive_resource_unlock_max_points = Math.max(
          0,
          Math.floor(Number(this.config.hdhive_resource_unlock_max_points) || 0),
        );
        const payload = {
          ...this.config,
          startup_notify_message: this.serializeStartupNotifyMessage(
            this.config.startup_notify_message,
          ),
        };
        const response = await this.fetchJson("/api/config", {
          method: "PUT",
          body: JSON.stringify(payload),
        });
        this.notice = successMessage || response.message;
        await this.fetchConfig();
      } catch (err) {
        this.error = this.normalizeCaughtError(err);
        if (throwOnError) {
          throw err;
        }
      } finally {
        this.saving = false;
      }
    },
    async validateConfig() {
      this.validating = true;
      this.notice = "";
      this.error = "";
      try {
        const response = await this.fetchJson("/api/validate", { method: "POST" });
        this.validation = response.data;
        this.notice = response.message;
        this.ui.activeTab = "status";
      } catch (err) {
        this.validation = null;
        this.error = this.normalizeCaughtError(err);
      } finally {
        this.validating = false;
      }
    },
    async runAction(action) {
      this.actionBusy = true;
      this.notice = "";
      this.error = "";
      try {
        const response = await this.fetchJson(`/api/${action}`, { method: "POST" });
        this.status = response.data;
        this.notice = response.message;
        await this.fetchStatus({ silent: true });
        await this.fetchHealth({ silent: true });
        await this.fetchLogs({ silent: true });
        await this.fetchFailedQueue({ silent: true });
        await this.fetchSuccessHistorySummary({ silent: true });
      } catch (err) {
        this.error = this.normalizeCaughtError(err);
      } finally {
        this.actionBusy = false;
      }
    },
    async runQueueAction(action, payload = null) {
      this.queue.actionBusy = true;
      this.notice = "";
      this.error = "";
      try {
        const response = await this.fetchJson(`/api/queue/${action}`, {
          method: "POST",
          body: payload == null ? null : JSON.stringify(payload),
        });
        this.notice = response.message;
        await Promise.all([
          this.fetchStatus({ silent: true }),
          this.fetchHealth({ silent: true }),
          this.fetchLogs({ silent: true }),
          this.fetchFailedQueue({ silent: true }),
          this.fetchSuccessHistorySummary({ silent: true }),
        ]);
      } catch (err) {
        this.error = this.normalizeCaughtError(err);
      } finally {
        this.queue.actionBusy = false;
      }
    },
    async startService() {
      await this.runAction("start");
    },
    async stopService() {
      await this.runAction("stop");
    },
    async restartService() {
      await this.runAction("restart");
    },
    async retryFailedQueue() {
      await this.runQueueAction("retry-failed");
    },
    async clearFailedQueue() {
      await this.runQueueAction("clear-failed");
    },
    async clearAllSuccessHistory() {
      if (!this.queue.successHistoryTotalCount) {
        return;
      }
      const confirmed = window.confirm(
        "清空全部已转发历史后，以后重启或重新命中时，这些历史消息可能再次进入转发判断。确定继续吗？",
      );
      if (!confirmed) {
        return;
      }
      await this.runQueueAction("clear-success-history", { rule_name: "" });
    },
    async clearSelectedSuccessHistory() {
      const ruleName = String(this.queue.successHistoryRuleName || "").trim();
      if (!ruleName) {
        this.error = "请先选择一条规则。";
        return;
      }
      const confirmed = window.confirm(
        `确定清空规则“${ruleName}”的已转发历史吗？清空后这条规则以后可能再次处理旧消息。`,
      );
      if (!confirmed) {
        return;
      }
      await this.runQueueAction("clear-success-history", { rule_name: ruleName });
    },
    async exportDiagnosticsBundle() {
      this.notice = "";
      this.error = "";
      try {
        const response = await this.fetchJson("/api/diagnostics/export");
        const diagnostics = response.data?.diagnostics || {};
        const filename = String(response.data?.filename || "").trim() || "tg-forwarder-diagnostics.json";
        const blob = new Blob([JSON.stringify(diagnostics, null, 2)], {
          type: "application/json;charset=utf-8",
        });
        const link = document.createElement("a");
        link.href = URL.createObjectURL(blob);
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(link.href);
        this.notice = response.message || "诊断包已导出。";
      } catch (err) {
        this.error = this.normalizeCaughtError(err);
      }
    },
    async requestTelegramCode() {
      this.telegramLogin.busy = true;
      this.notice = "";
      this.error = "";
      try {
        if (this.telegramLogin.loginId) {
          await this.fetchJson("/api/session/cancel", {
            method: "POST",
            body: JSON.stringify({ login_id: this.telegramLogin.loginId }),
          });
        }
        const response = await this.fetchJson("/api/session/request-code", {
          method: "POST",
          body: JSON.stringify({
            api_id: this.config.api_id,
            api_hash: this.config.api_hash,
            phone: this.telegramLogin.phone,
            proxy_type: this.config.proxy_type,
            proxy_host: this.config.proxy_host,
            proxy_port: this.config.proxy_port,
            proxy_user: this.config.proxy_user,
            proxy_password: this.config.proxy_password,
            proxy_rdns: this.config.proxy_rdns,
          }),
        });
        this.telegramLogin = {
          ...this.telegramLogin,
          code: "",
          password: "",
          loginId: response.data.login_id || "",
          step: "code",
        };
        this.notice = response.message;
      } catch (err) {
        this.error = this.normalizeCaughtError(err);
      } finally {
        this.telegramLogin.busy = false;
      }
    },
    async completeTelegramLogin() {
      if (!this.telegramLogin.loginId) {
        this.error = "请先发送验证码。";
        return;
      }
      this.telegramLogin.busy = true;
      this.notice = "";
      this.error = "";
      try {
        const response = await this.fetchJson("/api/session/complete", {
          method: "POST",
          body: JSON.stringify({
            login_id: this.telegramLogin.loginId,
            code: this.telegramLogin.code,
            password: this.telegramLogin.password,
          }),
        });
        if (response.data.status === "password_required") {
          this.telegramLogin = {
            ...this.telegramLogin,
            code: "",
            password: "",
            step: "password",
          };
          this.notice = response.message;
          return;
        }

        this.config.session_string = response.data.session_string || "";
        this.ui.showSessionString = false;
        this.resetTelegramLogin({ preservePhone: true });
        try {
          await this.saveConfig({
            successMessage: "Telegram 登录成功，session_string 已自动保存到 .env。",
            throwOnError: true,
          });
        } catch (_err) {
          this.notice = "Telegram 登录成功，但自动保存失败了，请点一次“保存配置”。";
        }
      } catch (err) {
        this.error = this.normalizeCaughtError(err);
      } finally {
        this.telegramLogin.busy = false;
      }
    },
    async cancelTelegramLogin() {
      const previousPhone = this.telegramLogin.phone;
      this.telegramLogin.busy = true;
      this.notice = "";
      this.error = "";
      try {
        await this.fetchJson("/api/session/cancel", {
          method: "POST",
          body: JSON.stringify({ login_id: this.telegramLogin.loginId }),
        });
        this.notice = "已取消当前网页登录流程。";
        this.resetTelegramLogin({ preservePhone: true });
        this.telegramLogin.phone = previousPhone;
      } catch (err) {
        this.error = this.normalizeCaughtError(err);
      } finally {
        this.telegramLogin.busy = false;
      }
    },
    async searchMessages() {
      this.notice = "";
      this.error = "";
      const query = this.normalizeSearchQuery(this.search.query);
      if (!query) {
        this.search.results = [];
        this.search.sourceFilter = "all";
        this.error = "搜索关键词不能为空，请先输入你要找的内容。";
        return;
      }
      this.search.loading = true;
      this.search.results = [];
      this.search.sourceFilter = "all";
      this.search.forwardingKey = "";
      try {
        const response = await this.fetchJson("/api/search", {
          method: "POST",
          body: JSON.stringify({
            query,
            limit: Number(this.search.limit) || 20,
          }),
        });
        this.search.results = (response.data.items || []).map((item) => this.normalizeSearchResult(item));
        this.syncSearchSourceFilter();
        this.notice = response.message;
        this.ui.activeTab = "search";
      } catch (err) {
        this.search.results = [];
        this.search.sourceFilter = "all";
        this.error = this.normalizeCaughtError(err);
      } finally {
        this.search.loading = false;
      }
    },
    async forwardSearchResult(result) {
      if (!this.hasForwardTargets(result)) {
        this.error = "这条搜索结果没有已配置的默认目标，先去规则里补上目标频道。";
        return;
      }
      const key = this.resultKey(result);
      this.search.forwardingKey = key;
      this.notice = "";
      this.error = "";
      try {
        const response = await this.fetchJson("/api/forward/manual", {
          method: "POST",
          body: JSON.stringify({
            source_chat: result.source_chat,
            message_id: result.message_id,
            target_chats: result.default_target_chats || "",
            bot_target_chats: result.default_bot_target_chats || "",
            forward_strategy: result.default_forward_strategy || "",
            rule_names: Array.isArray(result.rules) ? result.rules : [],
          }),
        });
        const accountCount = Array.isArray(response.data?.account_targets) ? response.data.account_targets.length : 0;
        const botCount = Array.isArray(response.data?.bot_targets) ? response.data.bot_targets.length : 0;
        const summary = [];
        if (accountCount) {
          summary.push(`账号目标 ${accountCount} 个`);
        }
        if (botCount) {
          summary.push(`Bot 目标 ${botCount} 个`);
        }
        this.notice = summary.length ? `${response.message} ${summary.join("，")}。` : response.message;
        this.notice = this.buildManualForwardNotice(response);
        this.error = "";
        await this.fetchLogs({ silent: true });
      } catch (err) {
        this.error = this.normalizeCaughtError(err);
      } finally {
        this.search.forwardingKey = "";
      }
    },
  },
  template: `
    <main class="shell">
      <section v-if="!authed" class="login-shell">
        <div class="login-card">
          <p class="eyebrow">TG 转发控制台</p>
          <h1>输入控制台密码</h1>
          <p class="hero-text">
            默认密码是 admin。登录后可以配置实时转发规则，也可以搜索历史消息后做指定转发。
          </p>
          <label class="login-label">
            <span>控制台密码</span>
            <input
              v-model="passwordInput"
              type="password"
              placeholder="请输入密码"
              @keyup.enter="login"
            />
          </label>
          <div class="toolbar">
            <button class="btn btn-primary" :disabled="authBusy" @click="login">
              {{ authBusy ? '登录中...' : '进入控制台' }}
            </button>
          </div>
          <section v-if="error" class="banner banner-error">{{ error }}</section>
        </div>
      </section>

      <template v-else>
        <section class="hero">
          <div class="hero-copy">
            <p class="eyebrow">TG 转发控制台</p>
            <h1>多端更顺手的设置面板</h1>
            <p class="hero-text">
              把长页面拆成几个清晰区域：基础配置、规则、搜索、状态、日志。手机上更容易点，桌面上也更容易看全局状态。
            </p>
            <div class="stat-strip">
              <div class="stat-chip">
                <strong>{{ ruleCount }}</strong>
                <span>规则总数</span>
              </div>
              <div class="stat-chip">
                <strong>{{ enabledRuleCount }}</strong>
                <span>已启用</span>
              </div>
              <div class="stat-chip">
                <strong>{{ runningWorkerCount }}</strong>
                <span>运行中</span>
              </div>
            </div>
          </div>
          <div class="hero-side">
            <div class="status-card">
              <span class="status-label">当前状态</span>
              <strong class="status-pill" :data-tone="statusTone">{{ statusText }}</strong>
              <p class="status-path">{{ status.config_path || configPath || '尚未加载配置文件' }}</p>
              <p v-if="status.last_error" class="status-error">{{ status.last_error }}</p>
              <p v-if="status.snapshot" class="status-meta">{{ statusRateLimitText }}</p>
              <p v-if="status.snapshot" class="status-meta">
                {{ '\u5f53\u524d\u961f\u5217\uff1a' + statusGlobalQueueDepth + ' \u6761\uff08\u542b\u53d1\u9001\u4e2d\uff09\uff0c\u5931\u8d25 ' + statusGlobalQueueFailed + ' \u6761' }}
              </p>
              <div class="toolbar compact-toolbar">
                <button class="btn btn-ghost" @click="logout">退出登录</button>
              </div>
            </div>
          </div>
        </section>

        <section class="action-bar">
          <div class="action-bar-inner">
            <button class="btn btn-primary" :disabled="saving" @click="saveConfig">
              <span class="btn-label-full">{{ saving ? '保存中...' : '保存配置' }}</span>
              <span class="btn-label-short">{{ saving ? '保存中' : '保存' }}</span>
            </button>
            <button class="btn btn-secondary" :disabled="validating" @click="validateConfig">
              <span class="btn-label-full">{{ validating ? '校验中...' : '校验配置' }}</span>
              <span class="btn-label-short">{{ validating ? '校验中' : '校验' }}</span>
            </button>
            <button class="btn btn-accent" :disabled="actionBusy" @click="startService">
              <span class="btn-label-full">{{ actionBusy ? '处理中...' : '启动后端' }}</span>
              <span class="btn-label-short">{{ actionBusy ? '处理中' : '启动' }}</span>
            </button>
            <button class="btn btn-ghost" :disabled="actionBusy" @click="restartService">
              <span class="btn-label-full">重启后端</span>
              <span class="btn-label-short">重启</span>
            </button>
            <button class="btn btn-danger" :disabled="actionBusy" @click="stopService">
              <span class="btn-label-full">停止后端</span>
              <span class="btn-label-short">停止</span>
            </button>
          </div>
        </section>

        <nav class="tab-nav" aria-label="页面分区">
          <button
            v-for="tab in tabItems"
            :key="tab.key"
            class="tab-pill"
            :data-active="ui.activeTab === tab.key"
            @click="setActiveTab(tab.key)"
          >
            {{ tab.label }}
          </button>
        </nav>

        <section v-if="notice" class="banner banner-ok">{{ notice }}</section>
        <section v-if="error" class="banner banner-error">{{ error }}</section>

        <section class="content-stack">
          <article v-show="ui.activeTab === 'config'" class="panel">
            <div class="panel-head">
              <div>
                <h2>基础配置</h2>
                <p class="panel-subtext">这些字段是所有规则共用的基础账号配置。保存后会写入 .env。</p>
              </div>
              <span class="panel-meta">{{ configPath || '.env' }}</span>
            </div>
            <div class="form-grid">
              <label>
                <span>API ID</span>
                <input v-model="config.api_id" placeholder="123456" />
              </label>
              <label>
                <span>API HASH</span>
                <input v-model="config.api_hash" placeholder="your_api_hash" />
              </label>
            </div>

            <div class="subpanel login-flow-panel">
              <div class="panel-head compact-head panel-head-wrap">
                <div>
                  <h3>网页登录 Telegram</h3>
                  <p class="panel-subtext">
                    首次登录不用再回终端执行命令。填手机号，收验证码，在这里完成登录，成功后会自动保存到 .env。
                  </p>
                </div>
                <span class="mini-pill" :data-tone="hasSessionString ? 'good' : 'muted'">
                  {{ hasSessionString ? '已保存 session' : '未登录' }}
                </span>
              </div>
              <p class="flow-hint">
                <template v-if="telegramLogin.step === 'idle'">
                  先填写手机号，然后点“发送验证码”。
                </template>
                <template v-else-if="telegramLogin.step === 'code'">
                  验证码已发送到 {{ telegramLogin.phone }}，直接在这里输入即可。
                </template>
                <template v-else-if="telegramLogin.step === 'password'">
                  这个账号开启了二步验证，请继续输入二步验证密码。
                </template>
              </p>
              <div class="login-flow-grid">
                <label>
                  <span>手机号</span>
                  <input
                    v-model="telegramLogin.phone"
                    inputmode="tel"
                    placeholder="+8613800000000"
                    @keyup.enter="requestTelegramCode"
                  />
                </label>
                <label v-if="telegramLogin.step === 'code'">
                  <span>验证码</span>
                  <input
                    v-model="telegramLogin.code"
                    inputmode="numeric"
                    placeholder="输入 Telegram 验证码"
                    @keyup.enter="completeTelegramLogin"
                  />
                </label>
                <label v-if="telegramLogin.step === 'password'">
                  <span>二步验证密码</span>
                  <input
                    v-model="telegramLogin.password"
                    type="password"
                    placeholder="输入二步验证密码"
                    @keyup.enter="completeTelegramLogin"
                  />
                </label>
              </div>
              <div class="toolbar compact-toolbar">
                <button class="btn btn-secondary btn-small" :disabled="telegramLogin.busy" @click="requestTelegramCode">
                  {{ telegramLogin.busy && telegramLogin.step === 'idle' ? '发送中...' : (telegramLogin.loginId ? '重新发送验证码' : '发送验证码') }}
                </button>
                <button
                  v-if="telegramLogin.step === 'code' || telegramLogin.step === 'password'"
                  class="btn btn-primary btn-small"
                  :disabled="telegramLogin.busy"
                  @click="completeTelegramLogin"
                >
                  {{ telegramLogin.busy ? '提交中...' : (telegramLogin.step === 'password' ? '提交密码' : '完成登录') }}
                </button>
                <button
                  v-if="telegramLogin.loginId"
                  class="btn btn-ghost btn-small"
                  :disabled="telegramLogin.busy"
                  @click="cancelTelegramLogin"
                >
                  取消本次登录
                </button>
              </div>
            </div>

            <div class="form-grid">
              <label class="span-2">
                <span>SESSION STRING</span>
                <textarea
                  v-if="ui.showSessionString"
                  v-model="config.session_string"
                  rows="4"
                  placeholder="网页登录成功后会自动写入这里，也可以手动粘贴已有 session_string"
                ></textarea>
                <textarea
                  v-else
                  :value="hasSessionString ? '已保存 session_string。点击下方按钮可查看、替换或清空。' : ''"
                  rows="2"
                  placeholder="网页登录成功后会自动写入这里，也可以手动粘贴已有 session_string"
                  readonly
                ></textarea>
              </label>
              <div class="inline-actions span-2">
                <button class="inline-toggle" @click="ui.showSessionString = !ui.showSessionString">
                  {{ ui.showSessionString ? '隐藏 session_string' : '显示 / 编辑 session_string' }}
                </button>
                <button v-if="hasSessionString" class="inline-toggle" @click="config.session_string = ''">
                  清空 session_string
                </button>
              </div>
              <label class="span-2">
                <span>BOT TOKEN</span>
                <input
                  v-model="config.bot_token"
                  :type="ui.showBotToken ? 'text' : 'password'"
                  title="支持多个 Bot Token，多个请用英文逗号分隔"
                  placeholder="可选：填写 1 个或多个 Telegram Bot Token，多个请用英文逗号分隔"
                />
              </label>
              <div class="inline-actions span-2">
                <button class="inline-toggle" @click="ui.showBotToken = !ui.showBotToken">
                  {{ ui.showBotToken ? '隐藏 Bot Token' : '显示 Bot Token' }}
                </button>
              </div>
              <p class="panel-subtext span-2">
                {{ "\u652f\u6301\u591a\u4e2a Bot Token\uff0c\u8bf7\u7528\u82f1\u6587\u9017\u53f7\u5206\u9694\uff0c\u4f8b\u5982\uff1atoken_1,token_2,token_3\u3002\u5f53\u8f6c\u53d1\u7b56\u7565\u9009\u62e9\u201c\u4f18\u5148 Bot\uff08\u5931\u8d25\u540e\u518d\u8bd5\u8d26\u53f7\uff09\u201d\u65f6\uff0c\u7cfb\u7edf\u4f1a\u6309\u987a\u5e8f\u5c1d\u8bd5 bot#1 -> bot#2 -> bot#3\uff0c\u5168\u90e8 Bot \u5931\u8d25\u540e\u518d\u56de\u9000\u5230\u767b\u5f55\u8d26\u53f7\u3002" }}
              </p>
              <label class="span-2">
                <span>{{ "\u8f6c\u53d1\u7b56\u7565" }}</span>
                <select v-model="config.forward_strategy">
                  <option
                    v-for="item in forwardStrategyOptions"
                    :key="item.value"
                    :value="item.value"
                  >
                    {{ item.label }}
                  </option>
                </select>
              </label>
              <p class="panel-subtext span-2">
                {{ forwardStrategyHelpText }}
              </p>
              <label class="toggle span-2">
                <input type="checkbox" v-model="config.rate_limit_protection" />
                <span>启用全局排队限流，多个规则同时命中时也会进入同一个发送队列，一个一个按顺序发，降低触发 Telegram 限制的概率</span>
              </label>
              <label class="span-2">
                <span>全局发送间隔（秒）</span>
                <input
                  v-model.number="config.rate_limit_delay_seconds"
                  type="number"
                  min="0"
                  step="0.1"
                  placeholder="1.2"
                />
              </label>
              <p class="panel-subtext span-2">
                {{ '\u5f00\u542f\u5168\u5c40\u6392\u961f\u9650\u6d41\u540e\u751f\u6548\uff0c\u6240\u6709\u89c4\u5219\u4f1a\u5171\u7528\u8fd9\u4e00\u5957\u7b49\u5f85\u95f4\u9694\u3002\u5f53\u524d\u8bbe\u7f6e\uff1a' + formatSeconds(config.rate_limit_delay_seconds) }}
              </p>
              <label class="toggle span-2">
                <input type="checkbox" v-model="config.startup_notify_enabled" />
                <span>启动或重启后，自动向已配置的目标群发送一条“启动成功”通知</span>
              </label>
              <p class="panel-subtext span-2">
                下方已经自动填入系统默认启动通知内容，你可以直接修改；如果保持默认文案，保存时仍会按“系统默认”处理。
              </p>
              <label class="span-2">
                <span>启动通知内容</span>
                <textarea
                  v-model="config.startup_notify_message"
                  rows="7"
                  placeholder="直接填写启动通知内容。支持 HTML，例如 &lt;b&gt;加粗&lt;/b&gt;、&lt;a href='https://example.com'&gt;链接&lt;/a&gt;。"
                ></textarea>
              </label>
            </div>

            <div class="subpanel hdhive-settings-block">
              <div class="panel-head compact-head">
                <div>
                  <h3>HDHive（hdhive.com）一站配置</h3>
                  <p class="panel-subtext hdhive-panel-intro">
                    签到方式 → 敏感凭证 → 自动签到。非 Premium 使用网页用户名与密码签到（hdhive_site_login_checkin）；Cookie 可选用于资源解析，签到成功后会写回。保存写入
                    <code>.env</code>。
                  </p>
                </div>
              </div>
              <div class="hdhive-stack">
                <section class="hdhive-stack-section" aria-labelledby="hdhive-method-heading">
                  <h4 id="hdhive-method-heading" class="hdhive-section-title">1. 签到方式</h4>
                  <div class="form-grid">
                    <label class="span-2">
                      <span>签到方式（仅影响自动签到 / 测试签到所用通道）</span>
                      <select v-model="config.hdhive_checkin_method">
                        <option value="api_key">Premium — API Key（/api/open/checkin）</option>
                        <option value="cookie">非 Premium — 网页账号登录并签到</option>
                      </select>
                    </label>
                    <p class="panel-subtext span-2">
                      Premium 走 OpenAPI 签到；非 Premium 走网页账号密码登录（<code>hdhive_site_login_checkin</code>）与首页签到。下方 Cookie 主要用于资源页解析，与签到方式无强制一一对应。
                    </p>
                  </div>
                </section>

                <section class="hdhive-stack-section hdhive-privacy-credentials" aria-labelledby="hdhive-cred-heading">
                  <h4 id="hdhive-cred-heading" class="hdhive-section-title">2. 敏感凭证（隐私）</h4>
                  <template v-if="!ui.hdhiveCredentialsOpen">
                    <p class="panel-subtext span-2">
                      凭证已遮盖；保存仍写入已配置值。需在本页修改时请展开。
                    </p>
                    <div class="inline-actions span-2">
                      <button type="button" class="inline-toggle" @click="ui.hdhiveCredentialsOpen = true">显示并编辑敏感凭证</button>
                    </div>
                  </template>
                  <template v-else>
                    <div class="form-grid">
                      <label class="span-2">
                        <span>HDHive API Key（请求头 X-API-Key）</span>
                        <input
                          v-model="config.hdhive_api_key"
                          type="text"
                          autocomplete="off"
                          spellcheck="false"
                          placeholder="用于签到、积分解锁等"
                        />
                      </label>
                      <template v-if="config.hdhive_checkin_method === 'cookie'">
                        <label class="span-2">
                          <span>网页登录用户名（可为邮箱）</span>
                          <input v-model="config.hdhive_login_username" type="text" autocomplete="username" spellcheck="false" />
                        </label>
                        <label class="span-2">
                          <span>网页登录密码</span>
                          <input v-model="config.hdhive_login_password" type="password" autocomplete="current-password" spellcheck="false" />
                        </label>
                        <p class="panel-subtext span-2">
                          签到由服务端加载 <code>hdhive/hdhive_site_login_checkin.py</code>；可选在 .env 填写
                          <code>HDHIVE_CHECKIN_NEXT_ACTION</code> 等覆盖内置解析。自动签到与测试/立即签到均只走账号密码登录。
                        </p>
                      </template>
                      <label class="span-2">
                        <span v-if="config.hdhive_checkin_method === 'cookie'">
                          Cookie（可选，资源解析；网页账号签到成功后可自动写回）
                        </span>
                        <span v-else>Cookie（可选，仅用于资源页解析，与 OpenAPI 签到无关）</span>
                        <textarea
                          v-model="config.hdhive_cookie"
                          rows="3"
                          autocomplete="off"
                          spellcheck="false"
                          :placeholder="
                            config.hdhive_checkin_method === 'cookie'
                              ? '可留空；网页账号签到成功后会写入 token 等'
                              : '可留空；须自行维护或从浏览器复制'
                          "
                        ></textarea>
                      </label>
                      <div class="inline-actions span-2">
                        <button type="button" class="inline-toggle" @click="ui.hdhiveCredentialsOpen = false">隐藏整块凭证区域</button>
                      </div>
                    </div>
                  </template>
                </section>

                <section class="hdhive-stack-section" aria-labelledby="hdhive-checkin-heading">
                  <h4 id="hdhive-checkin-heading" class="hdhive-section-title">3. 自动签到（可选）</h4>
                  <div class="form-grid">
                    <p v-if="config.hdhive_checkin_method !== 'cookie'" class="panel-subtext span-2">
                      当前为 API Key 签到：仍可在上方「敏感凭证」中填写 Cookie，用于资源页解析。
                    </p>
                    <label class="toggle span-2">
                      <input type="checkbox" v-model="config.hdhive_checkin_enabled" />
                      <span>开启自动每日签到（Web 进程按本地日期每天最多尝试一次）</span>
                    </label>
                    <label class="toggle span-2">
                      <input type="checkbox" v-model="config.hdhive_checkin_gambler" />
                      <span>博弈模式签到（API Key 模式下 is_gambler: true，积分波动更大）</span>
                    </label>
                    <label class="toggle span-2">
                      <input type="checkbox" v-model="config.hdhive_checkin_use_proxy" />
                      <span>访问 HDHive 走代理（使用下方「代理设置」里同一套单代理 TG_PROXY_*）</span>
                    </label>
                    <p v-if="config.hdhive_checkin_method === 'cookie'" class="panel-subtext span-2">
                      网页账号签到成功后会写回 <code>HDHIVE_COOKIE</code>，转发 Worker 解析资源时使用。
                    </p>
                    <p v-else class="panel-subtext span-2">
                      转发 Worker 若配置了 <code>HDHIVE_COOKIE</code> 会用于资源解析；Premium 签到不会自动改写该字段。
                    </p>
                  </div>
                </section>

                <section class="hdhive-stack-section" aria-labelledby="hdhive-unlock-heading">
                  <h4 id="hdhive-unlock-heading" class="hdhive-section-title">4. 转发 — 自动解锁策略</h4>
                  <div class="form-grid">
                    <label class="toggle span-2">
                      <input type="checkbox" v-model="config.hdhive_resource_unlock_enabled" />
                      <span>启用自动解锁回退（仅走 API 自动解锁，可能消耗积分）</span>
                    </label>
                    <p class="panel-subtext span-2">
                      开启后须配置 API Key；否则日志会提示已开解锁但未配置 Key 并跳过。
                    </p>
                    <label>
                      <span>自动解锁积分上限（单条）</span>
                      <input
                        type="number"
                        min="0"
                        step="1"
                        v-model.number="config.hdhive_resource_unlock_max_points"
                      />
                    </label>
                    <p class="panel-subtext span-2">
                      0 表示不限制。填 4 表示仅当解析到的所需积分 ≤ 4（含 4）时才自动解锁。
                    </p>
                    <label class="toggle span-2">
                      <input type="checkbox" v-model="config.hdhive_resource_unlock_threshold_inclusive" />
                      <span>上限包含边界（≤）；取消勾选则仅解锁严格小于上限的积分（&lt;）</span>
                    </label>
                    <label class="toggle span-2">
                      <input type="checkbox" v-model="config.hdhive_resource_unlock_skip_unknown_points" />
                      <span>无法从页面解析所需积分时跳过解锁（更保守）</span>
                    </label>
                    <p class="panel-subtext span-2">
                      自动解锁使用 OpenAPI「分享详情 / 解锁」，与 HDHive <strong>签到</strong>（Premium：OpenAPI；非 Premium：<code>hdhive_site_login_checkin</code>）不是同一接口。
                      即使签到测试未通过，只要 API Key 对分享/解锁有效，转发仍可尝试获取直链。
                    </p>
                  </div>
                </section>

                <section class="hdhive-stack-section" aria-labelledby="hdhive-forward-test-heading">
                  <h4 id="hdhive-forward-test-heading" class="hdhive-section-title">5. 转发路径检测（不耗积分）</h4>
                  <p class="panel-subtext span-2">
                    按<strong>已保存的 .env</strong>（含 <code>HDHIVE_BASE_URL</code>、<code>HDHIVE_ACCESS_TOKEN</code>）只读分享详情模拟转发；<strong>不会</strong>调用解锁接口。改开关后请先保存配置。
                    与<strong>测试签到</strong>无关：签到未通过时，此处仍可能显示将自动解锁。
                  </p>
                  <p class="panel-subtext span-2">
                    <strong>真实解锁测试</strong>与规则「HDHive 专用直链转发」相同逻辑，会<strong>真实调用解锁</strong>，可能扣积分。
                  </p>
                  <div class="form-grid span-2">
                    <label class="span-2">
                      <span>HDHive 资源链接</span>
                      <input
                        v-model="hdhiveResolveTestUrl"
                        type="text"
                        autocomplete="off"
                        spellcheck="false"
                        placeholder="https://hdhive.com/resource/115/..."
                      />
                    </label>
                    <div class="inline-actions span-2">
                      <button
                        type="button"
                        class="btn btn-secondary btn-small"
                        :disabled="hdhiveResolveBusy || hdhiveResolveUnlockBusy"
                        @click="triggerHdhiveResolveTest"
                      >
                        {{ hdhiveResolveBusy ? '检测中...' : '检测转发路径' }}
                      </button>
                      <button
                        type="button"
                        class="btn btn-primary btn-small"
                        :disabled="hdhiveResolveBusy || hdhiveResolveUnlockBusy"
                        @click="triggerHdhiveResolveUnlockTest"
                      >
                        {{ hdhiveResolveUnlockBusy ? '解锁中...' : '真实解锁测试（消耗积分）' }}
                      </button>
                    </div>
                    <div v-if="hdhiveResolvePreview" class="span-2 hdhive-resolve-preview-card">
                      <div class="hdhive-resolve-preview-head">
                        <span
                          class="hdhive-resolve-outcome"
                          :class="'hdhive-resolve-outcome--' + (hdhiveResolvePreview.outcome || 'fail')"
                        >
                          {{ hdhiveOutcomeLabel }}
                        </span>
                        <p class="hdhive-resolve-summary">{{ hdhiveResolvePreview.summary }}</p>
                      </div>
                      <p
                        v-if="(hdhiveResolvePreview.auto_unlock_preview || hdhiveResolvePreview.openapi_preview) && (hdhiveResolvePreview.auto_unlock_preview || hdhiveResolvePreview.openapi_preview).note"
                        class="panel-subtext"
                      >
                        {{ (hdhiveResolvePreview.auto_unlock_preview || hdhiveResolvePreview.openapi_preview).note }}
                      </p>
                      <ul v-if="hdhiveResolvePreview.detail_lines && hdhiveResolvePreview.detail_lines.length" class="hdhive-resolve-lines">
                        <li v-for="(line, idx) in hdhiveResolvePreview.detail_lines" :key="idx">{{ line }}</li>
                      </ul>
                      <p v-if="hdhiveResolveResult" class="panel-subtext" style="word-break: break-all">
                        <strong>直链：</strong><code>{{ hdhiveResolveResult }}</code>
                      </p>
                      <p v-if="hdhiveResolvePreview.slug" class="panel-subtext">
                        slug：<code>{{ hdhiveResolvePreview.slug }}</code>
                        <template v-if="hdhiveResolvePreview.unlock_points != null">
                          · unlock_points <strong>{{ hdhiveResolvePreview.unlock_points }}</strong>
                        </template>
                      </p>
                    </div>
                    <div v-if="hdhiveRealUnlockResult" class="span-2 hdhive-resolve-preview-card">
                      <div class="hdhive-resolve-preview-head">
                        <span
                          class="hdhive-resolve-outcome"
                          :class="'hdhive-resolve-outcome--' + (hdhiveRealUnlockResult.success ? 'auto_unlock' : 'fail')"
                        >
                          {{ hdhiveRealUnlockResult.success ? '真实解锁成功' : '真实解锁未成功' }}
                        </span>
                        <p class="hdhive-resolve-summary panel-subtext">
                          <template v-if="hdhiveRealUnlockResult.success">与 Worker 一致的解锁直链如下。</template>
                          <template v-else-if="hdhiveRealUnlockResult.skipped_reason">
                            跳过：<code>{{ hdhiveRealUnlockResult.skipped_reason }}</code>
                          </template>
                          <template v-else-if="hdhiveRealUnlockResult.error_message">
                            {{ hdhiveRealUnlockResult.error_message }}
                          </template>
                          <template v-else>请查看提示或日志。</template>
                        </p>
                      </div>
                      <p v-if="hdhiveRealUnlockResult.success && hdhiveRealUnlockResult.share_link" class="panel-subtext" style="word-break: break-all">
                        <strong>解锁直链：</strong><code>{{ hdhiveRealUnlockResult.share_link }}</code>
                      </p>
                      <p v-if="hdhiveRealUnlockResult.slug" class="panel-subtext">
                        slug：<code>{{ hdhiveRealUnlockResult.slug }}</code>
                      </p>
                    </div>
                  </div>
                </section>
              </div>
            </div>

            <div class="subpanel">
              <div class="panel-head compact-head">
                <div>
                  <h3>代理设置</h3>
                  <p class="panel-subtext">没有代理就留空。</p>
                </div>
              </div>
              <div class="form-grid">
                <label>
                  <span>代理类型</span>
                  <input v-model="config.proxy_type" placeholder="socks5" />
                </label>
                <label>
                  <span>代理地址</span>
                  <input v-model="config.proxy_host" placeholder="127.0.0.1" />
                </label>
                <label>
                  <span>代理端口</span>
                  <input v-model="config.proxy_port" placeholder="7890" />
                </label>
                <label>
                  <span>代理用户名</span>
                  <input v-model="config.proxy_user" placeholder="可留空" />
                </label>
                <label>
                  <span>代理密码</span>
                  <input v-model="config.proxy_password" placeholder="可留空" />
                </label>
                <label class="toggle">
                  <input type="checkbox" v-model="config.proxy_rdns" />
                  <span>启用 RDNS</span>
                </label>
              </div>
            </div>

          </article>

          <article v-show="ui.activeTab === 'rules'" class="panel">
            <div class="panel-head panel-head-wrap">
              <div>
                <h2>规则设置</h2>
                <p class="panel-subtext">每条规则只监听一个源。点击卡片头可以展开或收起详细配置，手机上会轻松很多。</p>
              </div>
              <div class="toolbar compact-toolbar">
                <button class="btn btn-secondary btn-small" :disabled="!ruleGroups.length" @click="ruleGroups.forEach((g) => toggleGroupEnabled(g, true))">
                  启用全部分组
                </button>
                <button class="btn btn-ghost btn-small" :disabled="!ruleGroups.length" @click="ruleGroups.forEach((g) => toggleGroupEnabled(g, false))">
                  停用全部分组
                </button>
                <select
                  class="queue-history-select"
                  v-model="ui.ruleGroupFilter"
                  @change="setRuleGroupFilter(ui.ruleGroupFilter)"
                >
                  <option value="all">显示全部分组</option>
                  <option v-for="g in ruleGroups" :key="g" :value="g">
                    仅显示：{{ g }}
                  </option>
                </select>
                <button class="btn btn-primary btn-small" @click="addRule">新增规则</button>
                <button
                  class="btn btn-ghost btn-small"
                  :disabled="ui.ruleGroupFilter === 'all'"
                  @click="addRule(ui.ruleGroupFilter)"
                >
                  在当前分组新增
                </button>
              </div>
            </div>
            <p v-if="ruleGroups.length" class="panel-subtext">
              当前分组：{{ ruleGroups.join("、") }}
            </p>

            <div class="rule-stack">
              <section v-for="bucket in filteredGroupedRuleBuckets" :key="bucket.group" class="subpanel">
                <div class="panel-head panel-head-wrap compact-head">
                  <div>
                    <h3>分组：{{ bucket.group }}</h3>
                    <p class="panel-subtext">共 {{ bucket.rules.length }} 条规则</p>
                  </div>
                  <div class="toolbar compact-toolbar">
                    <button class="btn btn-secondary btn-small" @click="toggleGroupEnabled(bucket.group, true)">启用本组</button>
                    <button class="btn btn-ghost btn-small" @click="toggleGroupEnabled(bucket.group, false)">停用本组</button>
                    <button class="btn btn-ghost btn-small" @click="sortGroupByPriority(bucket.group)">按优先级排序</button>
                    <button class="btn btn-ghost btn-small" @click="toggleGroupCollapsed(bucket.group)">
                      {{ isGroupCollapsed(bucket.group) ? '展开分组' : '收起分组' }}
                    </button>
                  </div>
                </div>
                <article
                  v-for="rule in bucket.rules"
                  v-show="!isGroupCollapsed(bucket.group)"
                  :key="rule.name + ':' + String(rule.source_chat || '') + ':' + String(rule.priority || '')"
                  class="rule-card"
                  :data-open="isRuleExpanded(ruleIndex(rule))"
                >
                  <button class="rule-overview" @click="toggleRule(ruleIndex(rule))">
                  <div class="rule-overview-main">
                    <div class="rule-title-row">
                      <strong>{{ rule.name || ('rule_' + (ruleIndex(rule) + 1)) }}</strong>
                      <span class="mini-pill" :data-tone="rule.enabled ? 'good' : 'muted'">
                        {{ rule.enabled ? '已启用' : '已停用' }}
                      </span>
                    </div>
                    <p class="rule-summary">{{ buildRuleSummary(rule) }}</p>
                  </div>
                  <span class="expand-mark">{{ isRuleExpanded(ruleIndex(rule)) ? '收起' : '展开' }}</span>
                </button>

                <div v-show="isRuleExpanded(ruleIndex(rule))" class="rule-body">
                  <div class="rule-actions">
                    <button class="btn btn-ghost btn-small" :disabled="ruleIndex(rule) === 0" @click="moveRule(ruleIndex(rule), -1)">上移</button>
                    <button class="btn btn-ghost btn-small" :disabled="ruleIndex(rule) >= config.rules.length - 1" @click="moveRule(ruleIndex(rule), 1)">下移</button>
                    <button class="btn btn-ghost btn-small" @click="duplicateRule(ruleIndex(rule))">复制</button>
                    <button class="btn btn-ghost btn-small" @click="removeRule(ruleIndex(rule))">删除</button>
                  </div>

                  <div class="form-grid rule-grid">
                    <label>
                      <span>规则名称</span>
                      <input v-model="rule.name" placeholder="news_to_main" />
                    </label>
                    <label class="toggle">
                      <input type="checkbox" v-model="rule.enabled" />
                      <span>启用这条规则</span>
                    </label>
                    <label>
                      <span>规则分组</span>
                      <input v-model="rule.group" placeholder="default / movie / news" />
                    </label>
                    <label>
                      <span>优先级（数字越小越靠前）</span>
                      <input v-model.number="rule.priority" type="number" min="1" @change="_normalizeRulePriority(rule, ruleIndex(rule) + 1)" />
                    </label>
                    <label>
                      <span>源频道 / 群</span>
                      <input v-model="rule.source_chat" placeholder="@source_channel" />
                    </label>
                    <label>
                      <span>账号目标频道 / 群</span>
                      <input v-model="rule.target_chats" placeholder="@target_1,@target_2" />
                    </label>
                    <label>
                      <span>Bot 目标频道 / 群</span>
                      <input v-model="rule.bot_target_chats" placeholder="@bot_target 或 chat_id" />
                    </label>
                    <label class="span-2">
                      <span>发送身份 / 转发策略</span>
                      <select v-model="rule.forward_strategy">
                        <option
                          v-for="item in ruleForwardStrategyOptions"
                          :key="item.value"
                          :value="item.value"
                        >
                          {{ item.label }}
                        </option>
                      </select>
                    </label>
                    <p class="panel-subtext span-2">
                      {{ getRuleForwardStrategyHelpText(rule.forward_strategy) }}
                    </p>
                    <label class="toggle">
                      <input type="checkbox" v-model="rule.include_edits" />
                      <span>监听编辑后的消息</span>
                    </label>
                    <label class="toggle">
                      <input type="checkbox" v-model="rule.forward_own_messages" />
                      <span>转发自己发送的消息（测试用）</span>
                    </label>
                    <p class="panel-subtext span-2">
                      {{ "\u5f00\u542f\u540e\uff0c\u5f53\u524d\u767b\u5f55\u8d26\u53f7\u5728\u6e90\u9891\u9053/\u7fa4\u81ea\u5df1\u53d1\u7684\u6d88\u606f\u4e5f\u4f1a\u53c2\u4e0e\u5b9e\u65f6\u8f6c\u53d1\uff0c\u65b9\u4fbf\u4f60\u6d4b\u8bd5\u3002\u5982\u679c\u6e90\u548c\u76ee\u6807\u914d\u7f6e\u6210\u4e86\u540c\u4e00\u4e2a\u5730\u65b9\uff0c\u8bf7\u8c28\u614e\u5f00\u542f\uff0c\u907f\u514d\u5faa\u73af\u8f6c\u53d1\u3002" }}
                    </p>
                    <label class="span-2">
                      <span>命中任一关键词才转发</span>
                      <textarea v-model="rule.keywords_any" rows="3" placeholder="ed2k://,115cdn.com,magnet:"></textarea>
                    </label>
                    <label class="span-2">
                      <span>必须全部命中</span>
                      <textarea v-model="rule.keywords_all" rows="3" placeholder="例如：115cdn.com&#10;4K"></textarea>
                    </label>
                    <label class="span-2">
                      <span>黑名单关键词</span>
                      <textarea v-model="rule.block_keywords" rows="3" placeholder="广告,spam"></textarea>
                    </label>
                    <div class="span-2 preset-grid">
                      <div class="preset-grid-head">
                        <span>资源类型快捷识别</span>
                        <p class="panel-subtext">直接勾选要识别的资源类型，不用手填关键词。</p>
                      </div>
                      <label
                        v-for="preset in resourcePresetOptions"
                        :key="preset.value"
                        class="toggle preset-toggle"
                      >
                        <input
                          :value="preset.value"
                          v-model="rule.resource_presets"
                          type="checkbox"
                        />
                        <span>{{ preset.label }}</span>
                      </label>
                    </div>
                    <label class="toggle span-2">
                      <input type="checkbox" v-model="rule.hdhive_resource_resolve_forward" />
                      <span>HDHive：识别 resource 链接并转发直链（默认出现链接即触发，仍受黑名单约束）</span>
                    </label>
                    <label class="toggle span-2">
                      <input type="checkbox" v-model="rule.hdhive_require_rule_match" :disabled="!rule.hdhive_resource_resolve_forward" />
                      <span>仅当命中下方关键词/正则时才转发 HDHive（须至少填写「命中任一」或「必须全部」或正则之一）</span>
                    </label>
                    <p class="panel-subtext span-2">
                      直链解锁读「站点设置」中的 API Key 与 <code>HDHIVE_BASE_URL</code> 等，与<strong>测试签到</strong>是否成功无关。
                    </p>
                    <label class="span-2">
                      <span>自定义正则：命中任一才转发</span>
                      <textarea
                        v-model="rule.regex_any"
                        rows="3"
                        placeholder="一行一个正则，例如：&#10;ed2k://\\|file\\|&#10;magnet:\\?xt=&#10;https?://(?:www\\.)?115cdn\\.com/"
                      ></textarea>
                    </label>
                    <label class="span-2">
                      <span>自定义正则：必须全部命中</span>
                      <textarea
                        v-model="rule.regex_all"
                        rows="3"
                        placeholder="一行一个正则，例如：&#10;115cdn\\.com&#10;4K"
                      ></textarea>
                    </label>
                    <label class="span-2">
                      <span>自定义正则：黑名单</span>
                      <textarea
                        v-model="rule.regex_block"
                        rows="3"
                        placeholder="一行一个正则，例如：&#10;广告&#10;(?i)spam"
                      ></textarea>
                    </label>
                    <p class="panel-subtext span-2">
                      {{ "匹配会同时检查正文、caption、按钮文字、按钮跳转链接，以及消息里直接带出的链接文本。你可以三种方式控制：1. 普通关键词；2. 直接勾选资源类型；3. 填自定义正则。如果同时填了“必须全部命中”和“命中任一才转发”，系统会先检查“全部条件”，满足就转发；否则再检查“任一条件”。黑名单关键词和黑名单正则始终优先级最高。" }}
                    </p>
                    <label class="toggle">
                      <input type="checkbox" v-model="rule.media_only" />
                      <span>需要媒体</span>
                    </label>
                    <label class="toggle">
                      <input type="checkbox" v-model="rule.text_only" />
                      <span>需要文本内容</span>
                    </label>
                    <label v-if="rule.media_only && rule.text_only" class="span-2">
                      <span>媒体 / 文本关系</span>
                      <select v-model="rule.content_match_mode">
                        <option value="all">同时满足</option>
                        <option value="any">任一满足</option>
                      </select>
                    </label>
                    <label class="toggle">
                      <input type="checkbox" v-model="rule.case_sensitive" />
                      <span>关键词区分大小写</span>
                    </label>
                    <p class="panel-subtext span-2">
                      {{ "\u53ef\u4ee5\u540c\u65f6\u52fe\u9009\u201c\u9700\u8981\u5a92\u4f53\u201d\u548c\u201c\u9700\u8981\u6587\u672c\u5185\u5bb9\u201d\u3002\u5982\u679c\u4e24\u4e2a\u90fd\u5f00\u542f\uff0c\u4f60\u53ef\u4ee5\u5728\u4e0a\u9762\u9009\u62e9\u201c\u540c\u65f6\u6ee1\u8db3\u201d\u6216\u201c\u4efb\u4e00\u6ee1\u8db3\u201d\u3002" }}
                    </p>
                  </div>
                </div>
              </article>
              </section>
            </div>
          </article>

          <article v-show="ui.activeTab === 'search'" class="panel">
            <div class="panel-head panel-head-wrap">
              <div>
                <h2>消息搜索</h2>
                <p class="panel-subtext">从所有已配置的源频道里做关键词模糊搜索，只检查 Telegram 原消息本身。搜到后可以直接按规则里已配置的目标转发。</p>
              </div>
              <div class="search-toolbar">
                <input
                  v-model="search.query"
                  class="search-input"
                  placeholder="请输入关键词，例如 电影、更新、合集"
                  @keyup.enter="canSearch ? searchMessages() : null"
                />
                <input
                  v-model.number="search.limit"
                  class="search-limit"
                  type="number"
                  min="1"
                  max="100"
                />
                <button
                  class="btn btn-secondary btn-small"
                  :disabled="search.loading || !canSearch"
                  @click="searchMessages"
                >
                  {{ search.loading ? '搜索中...' : (canSearch ? '搜索所有源' : '先输入关键词') }}
                </button>
              </div>
              <p class="panel-subtext search-mode-note">搜索范围只包含消息正文、caption、按钮文字和消息里直接带的链接文本。</p>
            </div>

            <div v-if="search.results.length" class="search-layout">
              <aside class="search-sidebar">
                <div class="search-sidebar-card">
                  <p class="eyebrow search-sidebar-eyebrow">频道切换</p>
                  <strong class="search-sidebar-title">{{ activeSearchSourceOption.label }}</strong>
                  <p class="search-sidebar-meta">当前显示 {{ searchVisibleCount }} / {{ search.results.length }} 条结果</p>
                  <div class="search-source-list">
                    <button
                      v-for="item in searchSourceOptions"
                      :key="item.key"
                      class="search-source-btn"
                      :data-active="search.sourceFilter === item.key"
                      @click="search.sourceFilter = item.key"
                    >
                      <span class="search-source-btn-label">{{ item.label }}</span>
                      <span class="search-source-btn-count">{{ item.count }}</span>
                    </button>
                  </div>
                </div>
              </aside>
              <section class="search-results-panel">
                <div class="search-results-head">
                  <div>
                    <strong class="search-results-title">{{ activeSearchSourceOption.label }}</strong>
                    <p class="search-results-note">按时间从新到旧排列，点开结果后可直接按已配置目标转发。</p>
                  </div>
                  <span class="panel-meta">{{ searchVisibleCount }} 条</span>
                </div>
                <div v-if="filteredSearchResults.length" class="search-results-list">
                  <article v-for="result in filteredSearchResults" :key="resultKey(result)" class="search-card">
                    <div class="search-head">
                    <div>
                      <strong>{{ result.source_label }}</strong>
                      <p class="search-meta">消息 ID：{{ result.message_id }}</p>
                      <p class="search-meta">时间：{{ formatDateTime(result.date) || '未知' }}</p>
                      <p class="search-meta">命中规则：{{ result.rules.join('、') || '无' }}</p>
                    </div>
                      <a v-if="result.link" class="text-link" :href="result.link" target="_blank" rel="noopener noreferrer">
                        打开原消息
                      </a>
                    </div>
                    <p class="search-preview">{{ result.preview }}</p>
                    <div class="toolbar compact-toolbar">
                      <button
                        class="btn btn-accent btn-small"
                        :disabled="search.forwardingKey === resultKey(result) || !hasForwardTargets(result)"
                        @click="forwardSearchResult(result)"
                      >
                        {{ search.forwardingKey === resultKey(result) ? '转发中...' : '按已配置目标转发' }}
                      </button>
                      <span v-if="!hasForwardTargets(result)" class="search-tip">这条结果没有默认目标，先去规则里补目标频道。</span>
                    </div>
                  </article>
                </div>
                <div v-else class="empty-state">
                  当前频道下还没有结果，切换到其他频道看看，或者重新搜索。
                </div>
              </section>
            </div>
            <div v-else class="empty-state">
              还没有搜索结果。输入关键词后点“搜索所有源”，系统会做模糊匹配并按时间从新到旧展示。
            </div>
          </article>

          <article v-show="ui.activeTab === 'status'" class="panel">
            <div class="panel-grid panel-grid-tight">
              <section class="subpanel">
                <div class="panel-head">
                  <div>
                    <h2>发送队列</h2>
                    <p class="panel-subtext">自动消息会先进入本地队列，再由 dispatcher 按策略发送。这样重启后能继续，失败目标也能单独重试。</p>
                  </div>
                  <span class="panel-meta">{{ statusGlobalQueueDepth }} 条队列任务</span>
                </div>
                <div class="worker-list queue-panel-stack">
                  <div class="worker-card">
                    <div class="worker-row">
                      <strong>Dispatcher</strong>
                      <span class="mini-pill" :data-tone="dispatcherAlive ? 'good' : 'bad'">
                        {{ dispatcherAlive ? '运行中' : '未运行' }}
                      </span>
                    </div>
                    <p>进程 PID：{{ dispatcherPid || '-' }}</p>
                    <p>数据库：{{ statusQueueDbPath || '未提供' }}</p>
                    <p>任务队列：{{ statusGlobalQueueDepth }} 条（待发送/发送中）</p>
                    <p>失败任务：{{ statusGlobalQueueFailed }} 条</p>
                    <p>目标队列：{{ statusGlobalQueueDeliveryDepth }} 个（待发送/发送中）</p>
                    <p>失败目标：{{ statusGlobalQueueDeliveryFailed }} 个</p>
                    <p>已转发去重历史：{{ queue.successHistoryTotalCount }} 条</p>
                    <p>{{ statusRateLimitText }}</p>
                    <p>日志缓冲：{{ health.logs?.in_memory_total || 0 }} / {{ health.logs?.capacity || '-' }}</p>
                    <p>
                      签到健康：{{ healthCheckinEnabled ? '已开启' : '已关闭' }} · {{ healthCheckinMethodLabel }}
                      · 最近成功：{{ healthCheckin?.last_success_date || '-' }}
                    </p>
                    <p>
                      签到重试：今日 {{ healthCheckin?.attempt_count_today || 0 }} 次 · 下次计划 {{ healthCheckinNextRetryText }}
                    </p>
                    <p v-if="healthCheckin?.proxy_error" class="dispatch-history-note">
                      签到代理提示：{{ healthCheckin.proxy_error }}
                    </p>
                    <div class="toolbar compact-toolbar">
                      <button class="btn btn-secondary btn-small" :disabled="queue.actionBusy || !statusGlobalQueueFailed" @click="retryFailedQueue">
                        {{ queue.actionBusy ? '处理中...' : '重试失败任务' }}
                      </button>
                      <button class="btn btn-ghost btn-small" :disabled="queue.actionBusy || !statusGlobalQueueFailed" @click="clearFailedQueue">
                        清空失败任务
                      </button>
                      <button class="btn btn-ghost btn-small" :disabled="actionBusy || loading" @click="exportDiagnosticsBundle">
                        导出诊断包
                      </button>
                    </div>
                    <div class="toolbar compact-toolbar">
                      <select
                        v-model="queue.successHistoryRuleName"
                        class="queue-history-select"
                        :disabled="queue.actionBusy || !successHistoryRules.length"
                      >
                        <option value="">全部规则</option>
                        <option
                          v-for="item in successHistoryRules"
                          :key="item.rule_name"
                          :value="item.rule_name"
                        >
                          {{ item.rule_name }}（{{ item.count }}）
                        </option>
                      </select>
                      <button
                        class="btn btn-secondary btn-small"
                        :disabled="queue.actionBusy || !queue.successHistoryRuleName || !selectedSuccessHistoryCount"
                        @click="clearSelectedSuccessHistory"
                      >
                        清空当前规则历史
                      </button>
                      <button
                        class="btn btn-ghost btn-small"
                        :disabled="queue.actionBusy || !queue.successHistoryTotalCount"
                        @click="clearAllSuccessHistory"
                      >
                        清空全部已转发历史
                      </button>
                    </div>
                    <p v-if="queue.successHistoryRuleName">
                      当前选中规则历史：{{ selectedSuccessHistoryCount }} 条
                    </p>
                    <div v-if="successHistoryRules.length" class="dispatch-history-list">
                      <div
                        v-for="item in successHistoryRules.slice(0, 8)"
                        :key="item.rule_name"
                        class="dispatch-history-card"
                      >
                        <div class="dispatch-history-head">
                          <strong>{{ item.rule_name }}</strong>
                          <span class="mini-pill" data-tone="good">{{ item.count }} 条</span>
                        </div>
                        <p class="dispatch-history-note">
                          最近一次成功时间：{{ formatDateTime(item.last_completed_at) || '-' }}
                        </p>
                      </div>
                    </div>
                  </div>
                  <div class="queue-history-grid">
                    <div class="worker-card">
                      <div class="worker-row">
                        <strong>最近失败任务</strong>
                        <span class="mini-pill" :data-tone="failedQueueItems.length ? 'bad' : 'good'">
                          {{ failedQueueItems.length ? (failedQueueItems.length + ' 条') : '暂无失败' }}
                        </span>
                      </div>
                      <div v-if="failedQueueItems.length" class="worker-list">
                        <div v-for="item in failedQueueItems" :key="item.id" class="worker-card">
                          <div class="worker-row">
                            <strong>{{ item.rule_name }}</strong>
                            <span class="mini-pill" data-tone="bad">失败 {{ item.failed_delivery_count }} 个目标</span>
                          </div>
                          <p>来源：{{ item.source_chat }}</p>
                          <p>消息 ID：{{ item.message_id }}</p>
                          <p>更新时间：{{ formatDateTime(item.updated_at) || '-' }}</p>
                          <p>预览：{{ item.preview || '无文本内容' }}</p>
                          <p>原因：{{ item.last_error || '未知错误' }}</p>
                        </div>
                      </div>
                      <div v-else class="empty-state">
                        当前没有待处理的失败任务。
                      </div>
                    </div>
                    <div class="worker-card">
                      <div class="worker-row">
                        <strong>最近成功发送</strong>
                        <span class="mini-pill" :data-tone="recentSuccessfulDispatches.length ? 'good' : 'muted'">
                          {{ recentSuccessfulDispatches.length ? (recentSuccessfulDispatches.length + ' 条') : '暂无记录' }}
                        </span>
                      </div>
                      <div v-if="recentSuccessfulDispatches.length" class="dispatch-history-list">
                        <div
                          v-for="item in recentSuccessfulDispatches"
                          :key="String(item.sequence || item.created_at) + ':' + String(item.target || item.messageId)"
                          class="dispatch-history-card"
                        >
                          <div class="dispatch-history-head">
                            <strong>{{ item.target || '未识别目标' }}</strong>
                            <div class="dispatch-history-badges">
                              <span class="mini-pill" :data-tone="item.channelTone">{{ item.channelLabel }}</span>
                              <span class="mini-pill" :data-tone="item.modeTone">{{ item.mode || '未知模式' }}</span>
                            </div>
                          </div>
                          <p class="dispatch-history-meta">
                            来源：{{ item.source || '未知来源' }} · 规则：{{ item.ruleName || '未标记规则' }}
                          </p>
                          <p class="dispatch-history-meta">
                            消息 ID：{{ item.messageId || '-' }} · 类型：{{ item.messageType || '未知' }} · 时间：{{ formatDateTime(item.created_at) || '-' }}
                          </p>
                          <p class="dispatch-history-preview">{{ item.preview || '无文本内容' }}</p>
                          <p v-if="item.note" class="dispatch-history-note">{{ item.note }}</p>
                        </div>
                      </div>
                      <div v-else class="empty-state">
                        最近还没有新的成功发送记录。
                      </div>
                    </div>
                  </div>
                </div>
              </section>
              <section class="subpanel">
                <div class="panel-head">
                  <div>
                    <h2>运行状态</h2>
                    <p class="panel-subtext">这里显示当前已经启动的 worker 进程。连续异常退出过多时会自动暂停，避免反复重启刷日志。</p>
                  </div>
                  <span class="panel-meta">{{ workerCards.length }} 个 worker</span>
                </div>
                <div v-if="workerCards.length" class="worker-list">
                  <div v-for="worker in workerCards" :key="worker.name" class="worker-card">
                    <div class="worker-row">
                      <strong>{{ worker.name }}</strong>
                      <span
                        class="mini-pill"
                        :data-tone="worker.paused ? 'warn' : (worker.is_alive ? 'good' : 'bad')"
                      >
                        {{ worker.paused ? '已暂停' : (worker.is_alive ? '运行中' : '已停止') }}
                      </span>
                    </div>
                    <p>源频道：{{ worker.source }}</p>
                    <p>账号目标：{{ worker.targets.join('、') }}</p>
                    <p v-if="worker.bot_targets && worker.bot_targets.length">Bot 目标：{{ worker.bot_targets.join('、') }}</p>
                    <p>发送策略：{{ getForwardStrategyLabel(worker.forward_strategy) }}</p>
                    <p>进程 PID：{{ worker.pid || '-' }}</p>
                    <p>最近退出码：{{ worker.exit_code == null ? '-' : worker.exit_code }}</p>
                    <p>连续失败：{{ worker.failure_count || 0 }} 次</p>
                    <p v-if="worker.pause_reason" class="dispatch-history-note">{{ worker.pause_reason }}</p>
                    <p>监听编辑：{{ worker.include_edits ? '开启' : '关闭' }}</p>
                    <p>转发自己消息：{{ worker.forward_own_messages ? '开启' : '关闭' }}</p>
                  </div>
                </div>
                <div v-else class="empty-state">
                  还没有运行中的任务。保存并校验规则后，点击“启动后端”即可开始实时监听。
                </div>
              </section>

              <section v-if="validation" class="subpanel">
                <div class="panel-head">
                  <div>
                    <h2>校验结果</h2>
                    <p class="panel-subtext">校验通过后，这些规则会被加载为实际运行任务。</p>
                  </div>
                  <span class="panel-meta">{{ validation.workers.length }} 条可运行规则</span>
                </div>
                <div class="worker-list">
                  <div v-for="worker in validation.workers" :key="worker.name" class="worker-card">
                    <div class="worker-row">
                      <strong>{{ worker.name }}</strong>
                      <span class="mini-pill" data-tone="good">校验通过</span>
                    </div>
                    <p>源频道：{{ worker.source }}</p>
                    <p>账号目标：{{ worker.targets.join('、') }}</p>
                    <p v-if="worker.bot_targets && worker.bot_targets.length">Bot 目标：{{ worker.bot_targets.join('、') }}</p>
                    <p>发送策略：{{ getForwardStrategyLabel(worker.forward_strategy) }}</p>
                    <p>监听编辑：{{ worker.include_edits ? '开启' : '关闭' }}</p>
                    <p>转发自己消息：{{ worker.forward_own_messages ? '开启' : '关闭' }}</p>
                  </div>
                </div>
              </section>
            </div>
          </article>

          <article v-show="ui.activeTab === 'logs'" class="panel">
            <div class="panel-head panel-head-wrap">
              <div>
                <h2>最近日志</h2>
                <p class="panel-subtext">这里可以看到加载规则、自动转发、Bot 转发和手动指定转发等实时日志。切到“转发监测”就能只看具体转发了什么。</p>
              </div>
              <div class="log-head-meta">
                <span class="log-order-badge">最新在上</span>
              </div>
            </div>
            <div class="log-filter-bar">
              <button class="chip-btn" :data-active="ui.logFilter === 'monitor'" @click="setLogFilter('monitor')">
                转发监测 {{ monitorLogCount }}
              </button>
              <button class="chip-btn" :data-active="ui.logFilter === 'all'" @click="setLogFilter('all')">
                全部日志 {{ sortedLogs.length }}
              </button>
              <button class="chip-btn" :data-active="ui.logFilter === 'error'" @click="setLogFilter('error')">
                错误日志 {{ errorLogCount }}
              </button>
              <span class="log-filter-note">实时刷新，最新一条固定显示在最上面。</span>
            </div>
            <div ref="logBox" class="log-box" @scroll.passive="handleLogBoxScroll">
              <div v-if="!filteredLogs.length" class="log-empty">当前筛选下还没有日志输出。</div>
              <div
                v-for="(item, index) in filteredLogs"
                :key="item.sequence || index"
                class="log-line"
                :data-monitor="item.monitor ? 'true' : 'false'"
                :data-level="item.level"
              >
                <div class="log-time-wrap">
                  <span class="log-time">{{ formatDateTime(item.created_at) }}</span>
                  <span v-if="index === 0" class="log-fresh-badge">最新</span>
                </div>
                <span class="log-level">{{ item.level }}</span>
                <span class="log-name">{{ item.logger }}</span>
                <span class="log-message">{{ displayLogMessage(item) }}</span>
                <details v-if="item.full_content" class="log-details">
                  <summary>查看完整内容</summary>
                  <pre class="log-full-content">{{ item.full_content }}</pre>
                </details>
              </div>
            </div>
          </article>
        </section>
        <button
          v-if="ui.activeTab === 'logs' && ui.showLogBottom"
          class="log-jump-btn"
          type="button"
          @click="scrollLogBoxToBottom"
        >
          日志到底
        </button>
        <button
          v-if="ui.showBackToTop"
          class="back-to-top-btn"
          type="button"
          @click="scrollToTop"
        >
          返回顶部
        </button>
      </template>
    </main>
  `,
}).mount("#app");
