import { CONSOLE_UI_VERSION } from "./consoleUiVersion.js";

function createRule(name = "rule_1") {
  return {
    name,
    enabled: true,
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
    rules: [createRule()],
    hdhive_checkin_method: "api_key",
    hdhive_api_key: "",
    hdhive_cookie: "",
    hdhive_checkin_enabled: false,
    hdhive_checkin_gambler: false,
    hdhive_checkin_use_proxy: false,
    hdhive_resource_unlock_enabled: false,
    hdhive_resource_unlock_max_points: 0,
    hdhive_resource_unlock_threshold_inclusive: true,
    hdhive_resource_unlock_skip_unknown_points: false,
    hdhive_cookie_refresh_enabled: false,
    hdhive_cookie_refresh_interval_sec: 1800,
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

function createTelegramLoginState() {
  return {
    method: "phone",
    phone: "",
    code: "",
    password: "",
    loginId: "",
    step: "idle",
    busy: false,
    qrPngBase64: "",
    qrExpiresAt: "",
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

export default {
  data() {
    return {
      consoleUiVersion: CONSOLE_UI_VERSION,
      loading: false,
      saving: false,
      validating: false,
      actionBusy: false,
      hdhiveCheckinBusy: false,
      hdhiveRefreshCookieBusy: false,
      hdhiveTestBusy: false,
      hdhiveResolveBusy: false,
      hdhiveResolveTestUrl: "",
      hdhiveResolveResult: "",
      hdhiveResolvePreview: null,
      /** API Key / Cookie / Next 头：整块默认折叠（隐私）；展开后直接编辑，无二次显示隐藏 */
      hdhiveCredentialsOpen: false,
      authBusy: false,
      authed: false,
      passwordInput: "",
      notice: "",
      error: "",
      configPath: "",
      defaultStartupNotifyMessage: "",
      config: createConfig(),
      forwardStrategyOptions: createForwardStrategyOptions(),
      ruleForwardStrategyOptions: createRuleForwardStrategyOptions(),
      search: createSearchState(),
      telegramLogin: createTelegramLoginState(),
      status: {
        status: "stopped",
        config_path: "",
        last_error: null,
        snapshot: null,
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
      /** 服务端内存日志总条数（可能与当前拉取的 items 长度不同） */
      logBufferTotal: 0,
      timers: [],
      /** 扫码登录轮询（独立于服务状态轮询 timers） */
      telegramQrPollTimer: null,
      modulesList: [],
      modulesLoaded: false,
      modulesFetchError: "",
      modulesImportBusy: false,
      modulesImportOverwrite: true,
      /** 模块界面弹窗：当前打开的模块元数据，null 表示关闭 */
      moduleUiModal: null,
      moduleUiModalToken: 0,
      ui: {
        activeTab: "config",
        mobileNavOpen: false,
        expandedRuleIndex: -1,
        showSessionString: false,
        showBotToken: false,
        logFilter: "all",
        logSourceFilter: "all",
        showBackToTop: false,
        showLogBottom: false,
      },
      noticeDismissTimer: null,
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
    ruleCount() {
      return this.config.rules.length;
    },
    moduleCount() {
      return this.modulesList.length;
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
    /** Logs visible under the current「来源」dropdown (same scope as chip counts). */
    sortedLogsForSourceScope() {
      return this.getLogsBySourceFilter(this.sortedLogs);
    },
    monitorLogCount() {
      return this.sortedLogsForSourceScope.filter((item) => this.isMonitorLog(item)).length;
    },
    errorLogCount() {
      return this.sortedLogsForSourceScope.filter((item) => item.level === "ERROR").length;
    },
    detectionLogCount() {
      return this.sortedLogsForSourceScope.filter((item) => this.isDetectionLog(item)).length;
    },
    hdhiveOutcomeLabel() {
      const o = this.hdhiveResolvePreview?.outcome;
      const map = {
        direct: "Cookie 直连（免积分）",
        openapi: "将回退 OpenAPI 积分解锁",
        fail: "直链不可用 / 不会自动解锁",
        invalid_url: "链接无效",
      };
      return map[o] || "检测结果";
    },
    hdhiveCheckinLogCount() {
      return this.sortedLogsForSourceScope.filter((item) => this.isHdhiveCheckinLog(item)).length;
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
      return this.getLogsBySourceFilter(this.getLogsByFilter(this.ui.logFilter));
    },
    tabItems() {
      return [
        { key: "config", label: "基础配置", shortLabel: "配置" },
        { key: "sites", label: "站点设置", shortLabel: "站点" },
        { key: "modules", label: `模块 ${this.moduleCount}`, shortLabel: "模块" },
        { key: "rules", label: `规则 ${this.ruleCount}`, shortLabel: "规则" },
        { key: "search", label: `搜索 ${this.searchResultCount}`, shortLabel: "搜索" },
        { key: "status", label: `状态 ${this.runningWorkerCount}`, shortLabel: "状态" },
        {
          key: "logs",
          label: `日志 ${this.logBufferTotal || this.sortedLogs.length}`,
          shortLabel: `日志 ${this.logBufferTotal || this.sortedLogs.length}`,
        },
      ];
    },
    activeTabMeta() {
      const tab = this.ui.activeTab;
      const map = {
        config: { title: "基础配置", sub: "Telegram API、会话、Bot 与全局发送策略" },
        sites: { title: "站点设置", sub: "第三方站点（如 HDHive）API 与自动签到" },
        modules: { title: "模块", sub: "可选扩展与已发现模块列表" },
        rules: { title: "转发规则", sub: "多源多目标、关键词与资源预设" },
        search: { title: "消息搜索", sub: "按关键词检索历史并手动转发" },
        status: { title: "运行与队列", sub: "Worker、dispatcher、失败重试与校验结果" },
        logs: { title: "系统日志", sub: "全部、HDHive 签到、转发监测、实时检测与错误" },
      };
      return map[tab] || { title: "控制台", sub: "" };
    },
  },
  watch: {
    notice(val) {
      if (this.noticeDismissTimer) {
        clearTimeout(this.noticeDismissTimer);
        this.noticeDismissTimer = null;
      }
      if (val && !this.error) {
        this.noticeDismissTimer = setTimeout(() => {
          this.noticeDismissTimer = null;
          if (this.notice === val) {
            this.notice = "";
          }
        }, 5200);
      }
    },
    error(val) {
      if (val && this.noticeDismissTimer) {
        clearTimeout(this.noticeDismissTimer);
        this.noticeDismissTimer = null;
      }
    },
    "ui.activeTab"(val) {
      if (val === "modules") {
        this.fetchModules({ silent: true });
      }
    },
  },
  async mounted() {
    window.addEventListener("scroll", this.handleWindowScroll, { passive: true });
    window.addEventListener("message", this.handleModuleUiEvent);
    document.addEventListener("keydown", this.handleMessageEscape);
    this.handleWindowScroll();
    await this.tryResumeSession();
  },
  beforeUnmount() {
    window.removeEventListener("scroll", this.handleWindowScroll);
    window.removeEventListener("message", this.handleModuleUiEvent);
    document.removeEventListener("keydown", this.handleMessageEscape);
    if (this.noticeDismissTimer) {
      clearTimeout(this.noticeDismissTimer);
      this.noticeDismissTimer = null;
    }
    this.stopTelegramQrPoll();
    this.stopPolling();
  },
  methods: {
    handleModuleUiEvent(event) {
      try {
        const data = event?.data || {};
        if (!data || data.source !== "tg-forwarder-module-ui") {
          return;
        }
        const message = String(data.message || "").trim();
        if (!message) {
          return;
        }
        const level = String(data.level || "notice").toLowerCase();
        if (level === "error") {
          this.error = message;
        } else {
          this.notice = message;
        }
      } catch (_err) {
        // ignore malformed postMessage payloads
      }
    },
    dismissMessage() {
      if (this.noticeDismissTimer) {
        clearTimeout(this.noticeDismissTimer);
        this.noticeDismissTimer = null;
      }
      this.notice = "";
      this.error = "";
    },
    handleMessageEscape(e) {
      if (e.key !== "Escape") {
        return;
      }
      if (this.moduleUiModal) {
        this.closeModuleUiModal();
        return;
      }
      if (this.ui.mobileNavOpen) {
        this.closeMobileNav();
        return;
      }
      if (this.notice || this.error) {
        this.dismissMessage();
      }
    },
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
      return {
        ...createRule(`rule_${index + 1}`),
        ...rule,
        name: (rule.name || `rule_${index + 1}`).trim(),
        source_chat: this.normalizeMultilineText(rule.source_chat || ""),
        forward_strategy: String(rule.forward_strategy || "inherit").trim() || "inherit",
        regex_any: this.normalizeMultilineText(rule.regex_any || ""),
        regex_all: this.normalizeMultilineText(rule.regex_all || ""),
        regex_block: this.normalizeMultilineText(rule.regex_block || ""),
        hdhive_resource_resolve_forward: Boolean(rule.hdhive_resource_resolve_forward),
        hdhive_require_rule_match: Boolean(rule.hdhive_require_rule_match),
      };
    },
    normalizeSourceItems(value) {
      if (Array.isArray(value)) {
        return value.map((item) => String(item || "").trim()).filter(Boolean);
      }
      return this.splitList(value);
    },
    formatSourceList(value) {
      const items = this.normalizeSourceItems(value);
      return items.length ? items.join("、") : "未设置源";
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
        hdhive_checkin_method: String(payload.hdhive_checkin_method ?? "api_key").trim() || "api_key",
        hdhive_api_key: String(payload.hdhive_api_key ?? "").trim(),
        hdhive_cookie: String(payload.hdhive_cookie ?? "").trim(),
        hdhive_checkin_enabled: Boolean(payload.hdhive_checkin_enabled),
        hdhive_checkin_gambler: Boolean(payload.hdhive_checkin_gambler),
        hdhive_checkin_use_proxy: Boolean(payload.hdhive_checkin_use_proxy),
        hdhive_resource_unlock_enabled: Boolean(payload.hdhive_resource_unlock_enabled),
        hdhive_resource_unlock_max_points: Math.max(
          0,
          Math.floor(Number(payload.hdhive_resource_unlock_max_points) || 0),
        ),
        hdhive_resource_unlock_threshold_inclusive:
          payload.hdhive_resource_unlock_threshold_inclusive !== false,
        hdhive_resource_unlock_skip_unknown_points: Boolean(
          payload.hdhive_resource_unlock_skip_unknown_points,
        ),
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
    isDetectionLog(item = {}) {
      return Boolean(item.detect);
    },
    isHdhiveCheckinLog(item = {}) {
      const logger = String(item.logger || "");
      if (logger === "tg_forwarder.hdhive" || logger.includes("hdhive")) {
        return true;
      }
      const text = this.displayLogMessage(item);
      return String(text).includes("HDHive");
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
      if (filter === "hdhive") {
        return this.sortedLogs.filter((item) => this.isHdhiveCheckinLog(item));
      }
      if (filter === "error") {
        return this.sortedLogs.filter((item) => item.level === "ERROR");
      }
      if (filter === "detect") {
        return this.sortedLogs.filter((item) => this.isDetectionLog(item));
      }
      return this.sortedLogs;
    },
    configuredLogSourceOptions() {
      const seen = new Set();
      const sources = [];
      const rules = Array.isArray(this.config.rules) ? this.config.rules : [];
      for (const rule of rules) {
        const items = this.normalizeSourceItems(rule.source_chat || "");
        for (const item of items) {
          const key = String(item || "").trim();
          if (!key || seen.has(key)) continue;
          seen.add(key);
          sources.push(key);
        }
      }
      sources.sort((a, b) => a.localeCompare(b, "zh"));
      return [{ key: "all", label: "全部源" }, ...sources.map((key) => ({ key, label: key }))];
    },
    normalizeLogSourceKey(value) {
      const s = String(value || "").trim();
      if (!s) {
        return "";
      }
      return s.startsWith("@") ? s.slice(1) : s;
    },
    extractLogSourceKey(item = {}) {
      const direct = String(item.source || "").trim();
      if (direct) {
        return this.normalizeLogSourceKey(direct);
      }
      const text = this.displayLogMessage(item);
      const m = String(text).match(/来源=([^|]+)/);
      return m ? this.normalizeLogSourceKey(m[1]) : "";
    },
    getLogsBySourceFilter(items) {
      const selected = String(this.ui.logSourceFilter || "all").trim() || "all";
      if (selected === "all") {
        return items;
      }
      const want = this.normalizeLogSourceKey(selected);
      return (items || []).filter((item) => {
        const got = this.extractLogSourceKey(item);
        if (!got) {
          return true;
        }
        return got === want;
      });
    },
    setLogSourceFilter(value) {
      this.ui.logSourceFilter = String(value || "all").trim() || "all";
      this.$nextTick(() => this.syncLogBoxState());
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
      this.ui.mobileNavOpen = false;
      if (key === "modules" && !this.modulesLoaded) {
        this.fetchModules({ silent: true });
      }
      this.$nextTick(() => this.syncLogBoxState());
    },
    toggleMobileNav() {
      this.ui.mobileNavOpen = !this.ui.mobileNavOpen;
    },
    closeMobileNav() {
      this.ui.mobileNavOpen = false;
    },
    setLogFilter(filter) {
      this.ui.logFilter = filter;
      this.$nextTick(() => this.syncLogBoxState());
    },
    expandRule(index) {
      this.ui.expandedRuleIndex = index;
      this.setActiveTab("rules");
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
    countRuleSources(rule) {
      return this.normalizeSourceItems(rule.source_chat).length;
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
      if (rule.media_only) count += 1;
      if (rule.text_only) count += 1;
      return count;
    },
    buildRuleSummary(rule) {
      const sourceCount = this.countRuleSources(rule);
      const targetCount = this.countRuleTargets(rule);
      const botTargetCount = this.countRuleBotTargets(rule);
      const filterCount = this.countRuleFilters(rule);
      return [
        sourceCount > 1 ? `源 ${sourceCount} 个` : this.formatSourceList(rule.source_chat),
        `账号目标 ${targetCount}`,
        `Bot 目标 ${botTargetCount}`,
        this.getRuleForwardStrategyLabel(rule.forward_strategy || "inherit"),
        `过滤 ${filterCount}`,
      ].join(" · ");
    },
    addRule() {
      const index = this.config.rules.length;
      this.config.rules.push(createRule(this.nextRuleName()));
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
        this.ui.expandedRuleIndex = -1;
        return;
      }
      const wasExpanded = this.ui.expandedRuleIndex;
      this.config.rules.splice(index, 1);
      if (wasExpanded === index) {
        this.ui.expandedRuleIndex = -1;
      } else if (wasExpanded > index) {
        this.ui.expandedRuleIndex = wasExpanded - 1;
      }
    },
    resetTelegramLogin(options = {}) {
      const preservePhone = Boolean(options.preservePhone);
      const phone = preservePhone ? this.telegramLogin.phone : "";
      this.stopTelegramQrPoll();
      this.telegramLogin = {
        ...createTelegramLoginState(),
        phone,
      };
    },
    stopTelegramQrPoll() {
      if (this.telegramQrPollTimer != null) {
        clearInterval(this.telegramQrPollTimer);
        this.telegramQrPollTimer = null;
      }
    },
    startTelegramQrPoll() {
      this.stopTelegramQrPoll();
      this.pollTelegramQrOnce();
      this.telegramQrPollTimer = window.setInterval(() => this.pollTelegramQrOnce(), 1500);
    },
    async pollTelegramQrOnce() {
      if (
        !this.telegramLogin.loginId ||
        this.telegramLogin.method !== "qr" ||
        this.telegramLogin.step === "password"
      ) {
        this.stopTelegramQrPoll();
        return;
      }
      try {
        const response = await this.fetchJson(
          `/api/session/qr-status?login_id=${encodeURIComponent(this.telegramLogin.loginId)}`,
        );
        const d = response.data || {};
        const st = d.status;
        if (st === "waiting") {
          return;
        }
        if (st === "completed") {
          this.stopTelegramQrPoll();
          this.config.session_string = d.session_string || "";
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
          return;
        }
        if (st === "password_required") {
          this.stopTelegramQrPoll();
          this.telegramLogin = {
            ...this.telegramLogin,
            step: "password",
            password: "",
            code: "",
          };
          this.notice = "该账号开启了两步验证，请在下方输入二步验证密码。";
          return;
        }
        if (st === "expired") {
          this.stopTelegramQrPoll();
          this.notice = "二维码已过期，请点击「刷新二维码」后重新扫描。";
          return;
        }
        if (st === "error") {
          this.stopTelegramQrPoll();
          this.error = String(d.message || "").trim() || "扫码登录失败。";
        }
      } catch (err) {
        this.stopTelegramQrPoll();
        this.error = this.normalizeCaughtError(err);
      }
    },
    async setTelegramLoginMethod(method) {
      const next = method === "qr" ? "qr" : "phone";
      if (this.telegramLogin.method === next) {
        return;
      }
      if (this.telegramLogin.loginId) {
        try {
          await this.fetchJson("/api/session/cancel", {
            method: "POST",
            body: JSON.stringify({ login_id: this.telegramLogin.loginId }),
          });
        } catch (_err) {
          /* 忽略 */
        }
      }
      this.stopTelegramQrPoll();
      const phone = this.telegramLogin.phone;
      this.telegramLogin = {
        ...createTelegramLoginState(),
        phone,
        method: next,
      };
    },
    async requestTelegramQr() {
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
        const response = await this.fetchJson("/api/session/request-qr", {
          method: "POST",
          body: JSON.stringify({
            api_id: this.config.api_id,
            api_hash: this.config.api_hash,
            proxy_type: this.config.proxy_type,
            proxy_host: this.config.proxy_host,
            proxy_port: this.config.proxy_port,
            proxy_user: this.config.proxy_user,
            proxy_password: this.config.proxy_password,
            proxy_rdns: this.config.proxy_rdns,
          }),
        });
        const data = response.data || {};
        this.telegramLogin = {
          ...this.telegramLogin,
          code: "",
          password: "",
          loginId: data.login_id || "",
          qrPngBase64: data.qr_png_base64 || "",
          qrExpiresAt: data.expires_at || "",
          step: "qr_wait",
        };
        this.notice = response.message;
        this.startTelegramQrPoll();
      } catch (err) {
        this.error = this.normalizeCaughtError(err);
      } finally {
        this.telegramLogin.busy = false;
      }
    },
    async refreshTelegramQr() {
      if (!this.telegramLogin.loginId || this.telegramLogin.method !== "qr") {
        return;
      }
      this.telegramLogin.busy = true;
      this.notice = "";
      this.error = "";
      try {
        const response = await this.fetchJson("/api/session/qr-refresh", {
          method: "POST",
          body: JSON.stringify({ login_id: this.telegramLogin.loginId }),
        });
        const data = response.data || {};
        this.telegramLogin = {
          ...this.telegramLogin,
          qrPngBase64: data.qr_png_base64 || "",
          qrExpiresAt: data.expires_at || "",
          step: "qr_wait",
          password: "",
          code: "",
        };
        this.notice = response.message;
        this.startTelegramQrPoll();
      } catch (err) {
        this.error = this.normalizeCaughtError(err);
      } finally {
        this.telegramLogin.busy = false;
      }
    },
    applyConfigPayload(payload) {
      this.defaultStartupNotifyMessage = String(payload.data?.defaultStartupNotifyMessage || "");
      this.config = this.normalizeConfig(payload.data.config);
      this.configPath = payload.data.configPath;
      const ruleCount = this.config.rules?.length ?? 0;
      if (
        this.ui.expandedRuleIndex < 0 ||
        this.ui.expandedRuleIndex >= ruleCount
      ) {
        this.ui.expandedRuleIndex = -1;
      }
    },
    async tryResumeSession() {
      const legacy = localStorage.getItem("tg_dashboard_password");
      if (legacy) {
        this.passwordInput = legacy;
        await this.login();
        return;
      }
      try {
        const response = await fetch("/api/config", {
          method: "GET",
          credentials: "include",
          headers: { "Content-Type": "application/json", ...this.authHeaders() },
        });
        if (!response.ok) {
          return;
        }
        const payload = await response.json();
        this.authed = true;
        this.applyConfigPayload(payload);
        this.loading = true;
        try {
          await Promise.all([
            this.fetchStatus(),
            this.fetchLogs(),
            this.fetchModules({ silent: true }),
            this.fetchFailedQueue({ silent: true }),
            this.fetchSuccessHistorySummary({ silent: true }),
          ]);
        } finally {
          this.loading = false;
        }
        this.startPolling();
      } catch (_err) {
        /* 无有效 Cookie 会话 */
      }
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
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(this.formatApiError(payload));
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
        localStorage.removeItem("tg_dashboard_password");
        this.passwordInput = "";
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
    async logout() {
      try {
        await fetch("/api/logout", {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json", ...this.authHeaders() },
        });
      } catch (_err) {
        /* 忽略网络错误，仍清理本地状态 */
      }
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
      this.queue = {
        failedItems: [],
        actionBusy: false,
        successHistoryTotalCount: 0,
        successHistoryRules: [],
        successHistoryRuleName: "",
      };
      this.modulesList = [];
      this.modulesLoaded = false;
      this.modulesFetchError = "";
      this.modulesImportBusy = false;
      this.modulesImportOverwrite = true;
      this.moduleUiModal = null;
      this.ui.showLogBottom = false;
      this.ui.mobileNavOpen = false;
      localStorage.removeItem("tg_dashboard_password");
      this.stopPolling();
    },
    async bootstrap() {
      this.loading = true;
      try {
        await Promise.all([
          this.fetchConfig(),
          this.fetchStatus(),
          this.fetchLogs(),
          this.fetchModules({ silent: true }),
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
        this.applyConfigPayload(response);
      } catch (err) {
        this.error = this.normalizeCaughtError(err);
      }
    },
    exportConfigSnapshot() {
      this.notice = "";
      this.error = "";
      try {
        const payload = this.buildSaveConfigPayload();
        const snapshot = {
          exported_at: new Date().toISOString(),
          schema: "tg-forwarder.dashboard-config.v1",
          config: payload,
        };
        const blob = new Blob([JSON.stringify(snapshot, null, 2)], {
          type: "application/json;charset=utf-8",
        });
        const now = new Date();
        const stamp = [
          now.getFullYear(),
          String(now.getMonth() + 1).padStart(2, "0"),
          String(now.getDate()).padStart(2, "0"),
          "-",
          String(now.getHours()).padStart(2, "0"),
          String(now.getMinutes()).padStart(2, "0"),
          String(now.getSeconds()).padStart(2, "0"),
        ].join("");
        const link = document.createElement("a");
        link.href = URL.createObjectURL(blob);
        link.download = `tg-forwarder-config-${stamp}.json`;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(link.href);
        this.notice = "配置已导出。";
      } catch (err) {
        this.error = this.normalizeCaughtError(err);
      }
    },
    triggerConfigImportPick() {
      this.$refs.configImportInput?.click();
    },
    async onConfigImportInputChange(event) {
      const input = event.target;
      const file = input.files && input.files[0];
      input.value = "";
      if (!file) {
        return;
      }
      this.notice = "";
      this.error = "";
      try {
        const text = await file.text();
        const raw = JSON.parse(text);
        const imported =
          raw && typeof raw === "object" && raw.data && typeof raw.data === "object" && raw.data.config
            ? raw.data.config
            : raw && typeof raw === "object" && raw.config && typeof raw.config === "object"
              ? raw.config
              : raw;
        if (!imported || typeof imported !== "object" || Array.isArray(imported)) {
          throw new Error("导入文件格式不正确：根对象缺少 config。");
        }
        this.config = this.normalizeConfig(imported);
        this.notice = "配置已导入到页面，请点击“保存配置”写入后端。";
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
    async fetchModules(options = {}) {
      const silent = Boolean(options.silent);
      try {
        const response = await this.fetchJson("/api/modules");
        this.modulesList = response.data?.items || [];
        this.modulesLoaded = true;
        this.modulesFetchError = "";
      } catch (err) {
        this.modulesList = [];
        this.modulesLoaded = false;
        const msg = this.normalizeCaughtError(err);
        if (!silent) {
          this.error = msg;
        } else {
          this.modulesFetchError = msg;
        }
      }
    },
    triggerModuleImportPick() {
      this.$refs.moduleZipInput?.click();
    },
    onModuleZipInputChange(event) {
      const input = event.target;
      const file = input.files && input.files[0];
      input.value = "";
      if (!file) {
        return;
      }
      this.importModuleZip(file);
    },
    moduleUiEntryUrl(mod, token = 0) {
      if (!mod || !mod.has_ui || !mod.directory) {
        return "";
      }
      const d = encodeURIComponent(mod.directory);
      const entry = String(mod.ui_entry || "index.html")
        .split("/")
        .filter(Boolean)
        .map((seg) => encodeURIComponent(seg))
        .join("/");
      const origin = typeof window !== "undefined" ? window.location.origin : "";
      return `${origin}/api/modules/ui/${d}/${entry}?_ts=${encodeURIComponent(String(token || Date.now()))}`;
    },
    openModuleUiModal(mod) {
      if (!mod || !mod.has_ui) {
        return;
      }
      this.moduleUiModal = { ...mod };
      this.moduleUiModalToken = Date.now();
    },
    closeModuleUiModal() {
      this.moduleUiModal = null;
    },
    moduleUiModalUrl() {
      if (!this.moduleUiModal) {
        return "";
      }
      return this.moduleUiEntryUrl(this.moduleUiModal, this.moduleUiModalToken);
    },
    async importModuleZip(file) {
      const name = String(file.name || "").toLowerCase();
      if (!name.endsWith(".zip")) {
        this.error = "请选择 .zip 模块包";
        return;
      }
      this.modulesImportBusy = true;
      this.error = "";
      this.notice = "";
      try {
        const form = new FormData();
        form.append("file", file);
        form.append("overwrite", this.modulesImportOverwrite ? "true" : "false");
        const response = await fetch("/api/modules/import", {
          method: "POST",
          credentials: "include",
          headers: { ...this.authHeaders() },
          body: form,
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(this.formatApiError(payload));
        }
        this.notice = payload.message || "导入成功";
        await this.fetchModules({ silent: true });
      } catch (err) {
        this.error = this.normalizeCaughtError(err);
      } finally {
        this.modulesImportBusy = false;
      }
    },
    async fetchLogs(options = {}) {
      const silent = Boolean(options.silent);
      try {
        const response = await this.fetchJson("/api/logs?limit=220");
        this.logs = response.data.items || [];
        this.logBufferTotal = Number(response.data.total ?? this.logs.length);
        this.$nextTick(() => this.syncLogBoxState());
      } catch (err) {
        if (!silent) {
          this.error = this.normalizeCaughtError(err);
        }
      }
    },
    async clearCurrentLogs() {
      this.actionBusy = true;
      this.notice = "";
      this.error = "";
      try {
        const source = String(this.ui.logSourceFilter || "").trim();
        const kind = String(this.ui.logFilter || "all").trim();
        const response = await this.fetchJson("/api/logs/clear", {
          method: "POST",
          body: JSON.stringify({
            source: source === "all" ? "" : source,
            kind,
          }),
        });
        this.notice = response.message || "已清空。";
        await this.fetchLogs({ silent: true });
      } catch (err) {
        this.error = this.normalizeCaughtError(err);
      } finally {
        this.actionBusy = false;
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
    buildSaveConfigPayload() {
      this.config.rate_limit_delay_seconds = this.normalizeRateLimitDelayInput(
        this.config.rate_limit_delay_seconds,
      );
      this.config.hdhive_resource_unlock_max_points = Math.max(
        0,
        Math.floor(Number(this.config.hdhive_resource_unlock_max_points) || 0),
      );
      this.config.hdhive_cookie_refresh_interval_sec = Math.max(
        60,
        Math.min(86400, Math.floor(Number(this.config.hdhive_cookie_refresh_interval_sec) || 1800)),
      );
      const rules = Array.isArray(this.config.rules)
        ? this.config.rules.map((rule, index) => this.normalizeRule(rule, index))
        : [createRule("rule_1")];
      return {
        api_id: String(this.config.api_id ?? "").trim(),
        api_hash: String(this.config.api_hash ?? "").trim(),
        session_string: String(this.config.session_string ?? "").trim(),
        bot_token: String(this.config.bot_token ?? "").trim(),
        forward_strategy: this.config.forward_strategy,
        rate_limit_protection: Boolean(this.config.rate_limit_protection),
        rate_limit_delay_seconds: this.normalizeRateLimitDelayInput(
          this.config.rate_limit_delay_seconds,
        ),
        startup_notify_enabled: Boolean(this.config.startup_notify_enabled),
        startup_notify_message: this.serializeStartupNotifyMessage(
          this.config.startup_notify_message,
        ),
        proxy_type: String(this.config.proxy_type ?? "socks5").trim(),
        proxy_host: String(this.config.proxy_host ?? "").trim(),
        proxy_port: String(this.config.proxy_port ?? "").trim(),
        proxy_user: String(this.config.proxy_user ?? "").trim(),
        proxy_password: String(this.config.proxy_password ?? "").trim(),
        proxy_rdns: Boolean(this.config.proxy_rdns),
        search_default_mode: this.config.search_default_mode || "fast",
        hdhive_checkin_method: String(this.config.hdhive_checkin_method ?? "api_key").trim() || "api_key",
        hdhive_api_key: String(this.config.hdhive_api_key ?? "").trim(),
        hdhive_cookie: String(this.config.hdhive_cookie ?? "").trim(),
        hdhive_checkin_enabled: Boolean(this.config.hdhive_checkin_enabled),
        hdhive_checkin_gambler: Boolean(this.config.hdhive_checkin_gambler),
        hdhive_checkin_use_proxy: Boolean(this.config.hdhive_checkin_use_proxy),
        hdhive_resource_unlock_enabled: Boolean(this.config.hdhive_resource_unlock_enabled),
        hdhive_resource_unlock_max_points: this.config.hdhive_resource_unlock_max_points,
        hdhive_resource_unlock_threshold_inclusive: Boolean(
          this.config.hdhive_resource_unlock_threshold_inclusive,
        ),
        hdhive_resource_unlock_skip_unknown_points: Boolean(
          this.config.hdhive_resource_unlock_skip_unknown_points,
        ),
        hdhive_cookie_refresh_enabled: Boolean(this.config.hdhive_cookie_refresh_enabled),
        hdhive_cookie_refresh_interval_sec: this.config.hdhive_cookie_refresh_interval_sec,
        rules,
      };
    },
    _looksLikeHdhiveRscBody(text) {
      const s = typeof text === "string" ? text : String(text ?? "");
      if (s.length < 60) {
        return false;
      }
      const keys = ["$Sreact", "OutletBoundary", "ViewportBoundary", "MetadataBoundary", "__PAGE__"];
      return keys.filter((k) => s.includes(k)).length >= 2;
    },
    _hdhiveCheckinNoticeFromResponse(response) {
      const title = String(response.data?.checkin_message ?? "").trim();
      const desc = String(response.data?.checkin_description ?? "").trim();
      if (title && desc) {
        return `${title}\n\n${desc}`;
      }
      if (title) {
        return title;
      }
      if (desc) {
        return desc;
      }
      const bodyRaw = response.data?.body ?? "";
      const bodyStr = typeof bodyRaw === "string" ? bodyRaw : String(bodyRaw ?? "");
      if (this._looksLikeHdhiveRscBody(bodyStr)) {
        return (
          String(response.message || "").trim() ||
          "服务端返回了 Next.js 页面数据流（RSC），已隐藏原始正文。请更新并保存 HDHIVE_COOKIE；若站点大改版，需更新程序内置签到元数据。"
        );
      }
      let parsed = null;
      try {
        parsed = JSON.parse(bodyStr);
      } catch (_e) {
        parsed = null;
      }
      const fromJson = parsed && (parsed.message || parsed.data?.message);
      if (fromJson) {
        return String(fromJson);
      }
      if (bodyStr.length > 0 && bodyStr.length <= 400) {
        return bodyStr;
      }
      if (bodyStr.length > 400) {
        return (
          String(response.message || "").trim() ||
          `响应较长（${bodyStr.length} 字符），未识别为 JSON；请查看日志或重试。`
        );
      }
      return String(response.message || "").trim() || "";
    },
    async triggerHdhiveCheckinNow() {
      this.hdhiveCheckinBusy = true;
      this.notice = "";
      this.error = "";
      try {
        const response = await this.fetchJson("/api/hdhive/checkin-now", { method: "POST" });
        const notice = String(
          this._hdhiveCheckinNoticeFromResponse(response) || response.message || "已完成请求。",
        );
        this.notice = notice;
        if (notice.includes("已根据响应 Set-Cookie")) {
          await this.fetchConfig();
        }
      } catch (err) {
        this.error = this.normalizeCaughtError(err);
      } finally {
        this.hdhiveCheckinBusy = false;
      }
    },
    async triggerHdhiveRefreshCookie() {
      this.hdhiveRefreshCookieBusy = true;
      this.notice = "";
      this.error = "";
      try {
        const response = await this.fetchJson("/api/hdhive/refresh-cookie", { method: "POST" });
        this.notice = String(response.message || "已完成请求。");
        if (response.data && response.data.updated) {
          await this.fetchConfig();
        }
      } catch (err) {
        this.error = this.normalizeCaughtError(err);
      } finally {
        this.hdhiveRefreshCookieBusy = false;
      }
    },
    async triggerHdhiveCheckinTest() {
      this.hdhiveTestBusy = true;
      this.notice = "";
      this.error = "";
      try {
        const response = await this.fetchJson("/api/hdhive/checkin-test", {
          method: "POST",
          body: JSON.stringify({
            checkin_method: String(this.config.hdhive_checkin_method ?? "api_key").trim() || "api_key",
            api_key: String(this.config.hdhive_api_key ?? "").trim(),
            cookie: String(this.config.hdhive_cookie ?? "").trim(),
            is_gambler: Boolean(this.config.hdhive_checkin_gambler),
          }),
        });
        const notice = String(
          this._hdhiveCheckinNoticeFromResponse(response) || response.message || "测试请求已完成。",
        );
        this.notice = notice;
        if (notice.includes("已根据响应 Set-Cookie")) {
          await this.fetchConfig();
        }
      } catch (err) {
        this.error = this.normalizeCaughtError(err);
      } finally {
        this.hdhiveTestBusy = false;
      }
    },
    async triggerHdhiveResolveTest() {
      this.hdhiveResolveBusy = true;
      this.hdhiveResolveResult = "";
      this.hdhiveResolvePreview = null;
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
    async saveConfig(options = {}) {
      const successMessage = options.successMessage || "";
      const throwOnError = Boolean(options.throwOnError);
      this.saving = true;
      this.notice = "";
      this.error = "";
      try {
        const payload = this.buildSaveConfigPayload();
        const response = await this.fetchJson("/api/config", {
          method: "POST",
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
        this.setActiveTab("status");
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
    async requestTelegramCode() {
      this.stopTelegramQrPoll();
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
          method: "phone",
          code: "",
          password: "",
          loginId: response.data.login_id || "",
          step: "code",
          qrPngBase64: "",
          qrExpiresAt: "",
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
        this.error =
          this.telegramLogin.method === "qr" ? "请先生成二维码。" : "请先发送验证码。";
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

        this.stopTelegramQrPoll();
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
      this.stopTelegramQrPoll();
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
        this.setActiveTab("search");
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
};
