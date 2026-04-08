<script>
import dashboard from "./dashboardApp.js";
export default dashboard;
</script>
<template>
    <main
      class="shell"
      :class="{ 'is-dashboard': authed, 'is-mobile-nav-open': authed && ui.mobileNavOpen }"
    >
      <section v-if="!authed" class="login-shell">
        <div class="login-backdrop" aria-hidden="true" />
        <div class="login-card">
          <div class="login-brand">
            <span class="login-brand-mark" aria-hidden="true" />
            <div>
              <p class="eyebrow">Telegram Forwarder</p>
              <h1>控制台登录</h1>
            </div>
          </div>
          <p class="hero-text">
            使用在 .env 中配置的密码进入。登录后可管理多规则转发、队列与日志。
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
        </div>
      </section>

      <template v-else>
        <div class="dashboard-app" :class="{ 'is-mobile-nav-open': ui.mobileNavOpen }">
          <aside id="dashboard-sidebar" class="sidebar" aria-label="主导航">
            <div class="sidebar-brand">
              <span class="sidebar-brand-mark" aria-hidden="true" />
              <div>
                <span class="sidebar-brand-title">TG Forwarder</span>
                <span class="sidebar-brand-sub">控制台</span>
              </div>
            </div>
            <div class="sidebar-workspace-head" role="presentation">
              <span class="sidebar-workspace-glow" aria-hidden="true" />
              <span class="sidebar-workspace-title">工作区</span>
            </div>
            <nav class="sidebar-nav">
              <button
                v-for="tab in tabItems"
                :key="tab.key"
                type="button"
                class="sidebar-link"
                :data-tab="tab.key"
                :data-active="ui.activeTab === tab.key"
                @click="setActiveTab(tab.key)"
              >
                <span class="nav-ic" :class="'nav-ic--' + tab.key" aria-hidden="true" />
                <span class="sidebar-link-text">{{ tab.label }}</span>
              </button>
            </nav>
            <div class="sidebar-footer">
              <div class="sidebar-status">
                <span class="sidebar-status-label">服务</span>
                <strong class="status-pill status-pill--sidebar" :data-tone="statusTone">{{ statusText }}</strong>
              </div>
              <p class="sidebar-path" :title="status.config_path || configPath || ''">
                {{ status.config_path || configPath || '尚未加载配置' }}
              </p>
              <p v-if="status.last_error" class="sidebar-error">{{ status.last_error }}</p>
              <p v-if="status.snapshot" class="sidebar-meta">{{ statusRateLimitText }}</p>
              <p v-if="status.snapshot" class="sidebar-meta">
                {{ '\u961f\u5217 ' + statusGlobalQueueDepth + ' \u6761 \u00b7 \u5931\u8d25 ' + statusGlobalQueueFailed }}
              </p>
              <button type="button" class="sidebar-logout" @click="logout">退出登录</button>
              <p class="sidebar-build">Console UI · v{{ consoleUiVersion }}</p>
            </div>
          </aside>

          <div class="dashboard-main">
            <header class="dash-header">
              <div class="dash-header-main">
                <div class="dash-header-text">
                  <p class="dash-kicker">当前分区</p>
                  <h1 class="dash-title">{{ activeTabMeta.title }}</h1>
                  <p class="dash-subtitle">{{ activeTabMeta.sub }}</p>
                </div>
                <div class="dash-stat-row">
                  <div class="dash-stat" data-stat="rules">
                    <strong>{{ ruleCount }}</strong>
                    <span>规则</span>
                  </div>
                  <div class="dash-stat" data-stat="enabled">
                    <strong>{{ enabledRuleCount }}</strong>
                    <span>已启用</span>
                  </div>
                  <div class="dash-stat" data-stat="workers">
                    <strong>{{ runningWorkerCount }}</strong>
                    <span>Worker</span>
                  </div>
                </div>
              </div>
              <button
                type="button"
                class="mobile-menu-btn"
                aria-label="打开工作区菜单"
                :aria-expanded="ui.mobileNavOpen ? 'true' : 'false'"
                aria-controls="dashboard-sidebar"
                @click="toggleMobileNav"
              >
                <span class="mobile-menu-icon" aria-hidden="true">
                  <span class="mobile-menu-bar"></span>
                  <span class="mobile-menu-bar"></span>
                  <span class="mobile-menu-bar"></span>
                </span>
              </button>
            </header>

        <section class="action-bar" aria-label="快捷操作">
          <div class="action-bar-inner">
            <div class="toolbar-cluster">
              <span class="toolbar-cluster-label">配置</span>
              <div class="toolbar-cluster-btns">
                <button class="btn btn-primary" :disabled="saving" @click="saveConfig">
                  <span class="btn-label-full">{{ saving ? '保存中...' : '保存配置' }}</span>
                  <span class="btn-label-short">{{ saving ? '保存中' : '保存' }}</span>
                </button>
                <button class="btn btn-secondary" :disabled="validating" @click="validateConfig">
                  <span class="btn-label-full">{{ validating ? '校验中...' : '校验配置' }}</span>
                  <span class="btn-label-short">{{ validating ? '校验中' : '校验' }}</span>
                </button>
                <button class="btn btn-ghost" :disabled="saving" @click="exportConfigSnapshot">
                  <span class="btn-label-full">导出配置</span>
                  <span class="btn-label-short">导出</span>
                </button>
                <button class="btn btn-ghost" :disabled="saving" @click="triggerConfigImportPick">
                  <span class="btn-label-full">导入配置</span>
                  <span class="btn-label-short">导入</span>
                </button>
                <input
                  ref="configImportInput"
                  type="file"
                  accept=".json,application/json"
                  class="modules-import-input"
                  aria-hidden="true"
                  tabindex="-1"
                  @change="onConfigImportInputChange"
                />
              </div>
            </div>
            <span class="toolbar-cluster-gap" aria-hidden="true" />
            <div class="toolbar-cluster">
              <span class="toolbar-cluster-label">进程</span>
              <div class="toolbar-cluster-btns">
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
            </div>
          </div>
        </section>

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
                    首次登录无需终端：可用短信验证码，或用手机 Telegram 扫描网页二维码（与桌面客户端扫码登录相同流程）。成功后会自动保存 session 到 .env。
                  </p>
                </div>
                <span class="mini-pill" :data-tone="hasSessionString ? 'good' : 'muted'">
                  {{ hasSessionString ? '已保存 session' : '未登录' }}
                </span>
              </div>
              <div class="login-method-toggle" role="group" aria-label="登录方式">
                <button
                  type="button"
                  class="btn btn-small"
                  :class="telegramLogin.method === 'phone' ? 'btn-primary' : 'btn-ghost'"
                  :disabled="telegramLogin.busy"
                  @click="setTelegramLoginMethod('phone')"
                >
                  验证码登录
                </button>
                <button
                  type="button"
                  class="btn btn-small"
                  :class="telegramLogin.method === 'qr' ? 'btn-primary' : 'btn-ghost'"
                  :disabled="telegramLogin.busy"
                  @click="setTelegramLoginMethod('qr')"
                >
                  扫码登录
                </button>
              </div>
              <p class="flow-hint">
                <template v-if="telegramLogin.method === 'phone' && telegramLogin.step === 'idle'">
                  先填写手机号，然后点「发送验证码」。
                </template>
                <template v-else-if="telegramLogin.method === 'phone' && telegramLogin.step === 'code'">
                  验证码已发送到 {{ telegramLogin.phone }}，直接在这里输入即可。
                </template>
                <template v-else-if="telegramLogin.method === 'qr' && telegramLogin.step === 'qr_wait'">
                  打开手机 Telegram → 设置 → 设备 → 链接桌面设备，扫描下方二维码（与登录桌面版 Telegram 相同）。
                </template>
                <template v-else-if="telegramLogin.step === 'password'">
                  这个账号开启了二步验证，请继续输入二步验证密码。
                </template>
              </p>
              <div v-if="telegramLogin.method === 'qr' && telegramLogin.step === 'qr_wait' && telegramLogin.qrPngBase64" class="telegram-qr-wrap">
                <img
                  class="telegram-qr-img"
                  :src="'data:image/png;base64,' + telegramLogin.qrPngBase64"
                  alt="Telegram 登录二维码"
                  width="220"
                  height="220"
                />
                <p v-if="telegramLogin.qrExpiresAt" class="qr-expires-hint">
                  过期时间（UTC）：{{ telegramLogin.qrExpiresAt }}
                </p>
              </div>
              <div class="login-flow-grid">
                <template v-if="telegramLogin.method === 'phone'">
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
                </template>
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
              <div class="toolbar compact-toolbar login-flow-toolbar">
                <template v-if="telegramLogin.method === 'phone'">
                  <button class="btn btn-secondary btn-small" :disabled="telegramLogin.busy" @click="requestTelegramCode">
                    {{
                      telegramLogin.busy && telegramLogin.step === 'idle'
                        ? '发送中...'
                        : telegramLogin.loginId
                          ? '重新发送验证码'
                          : '发送验证码'
                    }}
                  </button>
                </template>
                <template v-if="telegramLogin.method === 'qr'">
                  <button class="btn btn-secondary btn-small" :disabled="telegramLogin.busy" @click="requestTelegramQr">
                    {{
                      telegramLogin.busy && telegramLogin.step === 'idle'
                        ? '生成中...'
                        : telegramLogin.loginId
                          ? '重新生成二维码'
                          : '生成二维码'
                    }}
                  </button>
                  <button
                    v-if="telegramLogin.loginId && telegramLogin.step === 'qr_wait'"
                    class="btn btn-secondary btn-small"
                    :disabled="telegramLogin.busy"
                    @click="refreshTelegramQr"
                  >
                    刷新二维码
                  </button>
                </template>
                <button
                  v-if="telegramLogin.step === 'code' || telegramLogin.step === 'password'"
                  class="btn btn-primary btn-small"
                  :disabled="telegramLogin.busy"
                  @click="completeTelegramLogin"
                >
                  {{
                    telegramLogin.busy
                      ? '提交中...'
                      : telegramLogin.step === 'password'
                        ? '提交密码'
                        : '完成登录'
                  }}
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

          <article v-show="ui.activeTab === 'sites'" class="panel">
            <div class="panel-head panel-head-wrap">
              <div>
                <h2>HDHive（hdhive.com）</h2>
                <p class="panel-subtext">
                  <strong>Premium</strong> 用户可使用开放接口
                  <code>POST https://hdhive.com/api/open/checkin</code>，请求头 <code>X-API-Key</code>。
                  <strong>非 Premium</strong> 用户无此 Key，需在浏览器登录站点后，从开发者工具里复制 Cookie、<code>Next-Action</code>、<code>Next-Router-State-Tree</code>
                  请求头，走与网页相同的 Next Server Action 签到（正文为 <code>[false]</code> / 博弈 <code>[true]</code>）。站点或框架更新后上述头可能失效，需重新复制。
                  自动签到由 Web 进程按本地日期每天最多尝试一次。修改后请点「保存配置」。
                </p>
              </div>
            </div>
            <div class="form-grid">
              <label class="span-2">
                <span>签到方式</span>
                <select v-model="config.hdhive_checkin_method" @change="onHdhiveCheckinMethodChange">
                  <option value="api_key">Premium — API Key（/api/open/checkin）</option>
                  <option value="cookie">非 Premium — Cookie + Next 头（POST 首页）</option>
                </select>
              </label>
              <label v-show="config.hdhive_checkin_method === 'api_key'" class="span-2 hdhive-api-key-block">
                <span>API Key（请求头 X-API-Key）</span>
                <input
                  v-model="config.hdhive_api_key"
                  :type="hdhiveApiKeyVisible ? 'text' : 'password'"
                  autocomplete="off"
                  placeholder="粘贴 hdhive 提供的 API Key"
                />
                <div class="hdhive-secret-actions">
                  <button type="button" class="btn btn-secondary btn-small" @click="hdhiveApiKeyVisible = !hdhiveApiKeyVisible">
                    {{ hdhiveApiKeyVisible ? '隐藏' : '显示' }}
                  </button>
                </div>
              </label>
              <template v-if="config.hdhive_checkin_method === 'cookie'">
                <div v-if="!hdhiveSensitiveVisible" class="span-2 hdhive-secrets-collapsed">
                  <p class="panel-subtext">
                    Cookie 与 Next 请求头已隐藏。点击下方可<strong>显示并编辑</strong>（或粘贴新值）；编辑完请「保存配置」。
                  </p>
                  <button type="button" class="btn btn-secondary btn-small" @click="hdhiveSensitiveVisible = true">
                    显示并编辑
                  </button>
                </div>
                <template v-else>
                  <div class="span-2 hdhive-secret-toolbar">
                    <button type="button" class="btn btn-secondary btn-small" @click="collapseHdhiveSecrets">
                      隐藏敏感信息
                    </button>
                  </div>
                  <label class="span-2">
                    <span>Cookie（完整一行，如 token=…）</span>
                    <textarea
                      v-model="config.hdhive_cookie"
                      rows="3"
                      autocomplete="off"
                      placeholder="从浏览器请求头复制 Cookie"
                    ></textarea>
                  </label>
                  <label class="span-2">
                    <span>Next-Action</span>
                    <input
                      v-model="config.hdhive_next_action"
                      type="text"
                      autocomplete="off"
                      placeholder="请求头 Next-Action 的值"
                    />
                  </label>
                  <label class="span-2">
                    <span>Next-Router-State-Tree</span>
                    <textarea
                      v-model="config.hdhive_next_router_state_tree"
                      rows="2"
                      autocomplete="off"
                      placeholder="请求头 Next-Router-State-Tree 的值（可为 URL 编码字符串）"
                    ></textarea>
                  </label>
                </template>
              </template>
              <label class="toggle span-2">
                <input type="checkbox" v-model="config.hdhive_checkin_enabled" />
                <span>开启自动每日签到（由 Web 服务后台轮询，每天最多尝试一次）</span>
              </label>
              <label class="toggle span-2">
                <input type="checkbox" v-model="config.hdhive_checkin_gambler" />
                <span>使用博弈模式签到（<code>is_gambler: true</code>，积分波动更大）</span>
              </label>
              <label class="toggle span-2">
                <input type="checkbox" v-model="config.hdhive_checkin_use_proxy" />
                <span>通过代理访问 HDHive（使用「系统与连接」里已保存的代理类型、地址与端口；与 Telegram 共用同一套单代理配置）</span>
              </label>
              <p class="panel-subtext span-2">
                勾选后，HDHive 请求与 Telegram 共用上方「系统与连接」里的<strong>单代理</strong>（<code>TG_PROXY_*</code>）：支持
                <code>http</code> / <code>https</code> / <code>socks5</code> / <code>socks4</code>，无需再单独改类型。
              </p>
            </div>
            <div class="sites-checkin-hint panel-subtext">
                <strong>测试签到</strong>：用当前表单中的 Key 或 Cookie/Next 头与「博弈模式」发请求，<strong>不必先保存</strong>；是否走代理以已保存的
                <strong>通过代理访问 HDHive</strong> 为准（勾选后请先保存配置）。成功或业务失败时提示优先显示接口返回的
                <strong>message</strong> 与 <strong>description</strong>。
                <strong>立即签到</strong>与<strong>自动签到</strong>均读 .env 已保存项。签到结果会写入<strong>日志</strong>页（不含 Cookie / Key 原文）。
            </div>

            <div class="toolbar sites-checkin-toolbar sites-checkin-actions">
              <button
                class="btn btn-secondary"
                :disabled="hdhiveTestBusy || hdhiveCheckinBusy"
                @click="triggerHdhiveCheckinTest"
              >
                {{ hdhiveTestBusy ? '测试中...' : '测试签到' }}
              </button>
              <button
                class="btn btn-primary"
                :disabled="hdhiveCheckinBusy || hdhiveTestBusy"
                @click="triggerHdhiveCheckinNow"
              >
                {{ hdhiveCheckinBusy ? '请求中...' : '立即签到（已保存配置）' }}
              </button>
            </div>

            <div class="sites-checkin-resolve">
              <input
                v-model="hdhiveResolveTestUrl"
                class="inline-input"
                placeholder="解析测试：粘贴 hdhive.com/resource/...（不保存）"
              />
              <div class="sites-checkin-resolve-actions">
                <button
                  class="btn btn-secondary btn-small"
                  :disabled="hdhiveResolveBusy"
                  @click="triggerHdhiveResolveTest"
                >
                  {{ hdhiveResolveBusy ? '解析中...' : '解析直链' }}
                </button>
              </div>
            </div>
            <p v-if="hdhiveResolveResult" class="panel-subtext span-2" style="margin-top: 0.6rem; word-break: break-all">
              <strong>redirect_url：</strong> <code>{{ hdhiveResolveResult }}</code>
            </p>
          </article>

          <article v-show="ui.activeTab === 'modules'" class="panel">
            <input
              ref="moduleZipInput"
              type="file"
              accept=".zip,application/zip"
              class="modules-import-input"
              aria-hidden="true"
              tabindex="-1"
              @change="onModuleZipInputChange"
            />
            <div class="panel-head panel-head-wrap">
              <div>
                <h2>扩展模块</h2>
                <p class="panel-subtext">
                  可点击下方<strong>导入模块 (.zip)</strong>上传；也可手动放子目录。带 <code>web/index.html</code> 的模块可点<strong>模块界面</strong>在弹窗中打开（
                  <code>/api/modules/ui/…</code>，需已登录）。示例模块提供与<strong>规则页</strong>类似的表单保存
                  <code>config.json</code>（<code>/api/modules/config/…</code>），并在 hooks 中导出
                  <code>dashboard_preview</code> 时支持<strong>试运行</strong>（<code>/api/modules/preview/…</code>，服务端执行与线上一致的检测逻辑）。
                  <code>after_match</code> 在规则匹配后执行；改 <code>hooks.py</code> 需<strong>重启 Worker</strong>。
                </p>
              </div>
              <div class="modules-toolbar">
                <button
                  type="button"
                  class="btn btn-primary btn-small"
                  :disabled="modulesImportBusy"
                  @click="triggerModuleImportPick"
                >
                  {{ modulesImportBusy ? '导入中...' : '导入模块 (.zip)' }}
                </button>
                <button type="button" class="btn btn-secondary btn-small" :disabled="modulesImportBusy" @click="fetchModules()">
                  刷新列表
                </button>
              </div>
            </div>
            <label class="toggle modules-import-overwrite">
              <input type="checkbox" v-model="modulesImportOverwrite" :disabled="modulesImportBusy" />
              <span>覆盖同名模块（删除原目录后解压）</span>
            </label>
            <p v-if="modulesFetchError" class="panel-subtext" style="color: var(--dash-warn, #f59e0b)">
              {{ modulesFetchError }}
            </p>
            <div v-if="!modulesList.length && !modulesFetchError" class="panel-subtext">
              当前未发现模块（无带 module.json 的子目录）。
            </div>
            <div v-else-if="modulesList.length" class="modules-grid">
              <article v-for="(mod, idx) in modulesList" :key="mod.directory || mod.id || idx" class="modules-card">
                <div class="modules-card-head">
                  <strong>{{ mod.name || mod.id || mod.directory }}</strong>
                  <span v-if="mod.version" class="mini-pill" data-tone="muted">v{{ mod.version }}</span>
                </div>
                <p v-if="mod.description" class="panel-subtext modules-card-desc">{{ mod.description }}</p>
                <p class="modules-card-meta">
                  <span>目录 <code>{{ mod.directory }}</code></span>
                  <span v-if="mod.author"> · {{ mod.author }}</span>
                </p>
                <p v-if="mod.capabilities && mod.capabilities.length" class="modules-card-caps">
                  <span v-for="cap in mod.capabilities" :key="cap" class="mini-pill" data-tone="muted">
                    {{
                      cap === 'config_edit'
                        ? '可改配置'
                        : cap === 'preview'
                          ? '可试运行'
                          : cap === 'rule_edit'
                            ? '可改规则'
                            : cap
                    }}
                  </span>
                </p>
                <div v-if="mod.has_ui" class="modules-card-ui">
                  <button type="button" class="btn btn-primary btn-small modules-card-ui-main" @click="openModuleUiModal(mod)">
                    模块界面
                  </button>
                </div>
              </article>
            </div>
          </article>

          <article v-show="ui.activeTab === 'rules'" class="panel">
            <div class="panel-head panel-head-wrap">
              <div>
                <h2>规则设置</h2>
                <p class="panel-subtext">每条规则可以监听一个或多个源，多个源可用逗号、分号或换行分隔。点击卡片头可以展开或收起详细配置，手机上会轻松很多。</p>
              </div>
              <button class="btn btn-primary btn-small" @click="addRule">新增规则</button>
            </div>

            <div class="rule-stack">
              <article
                v-for="(rule, index) in config.rules"
                :key="index"
                class="rule-card"
                :data-open="isRuleExpanded(index)"
              >
                <button class="rule-overview" @click="toggleRule(index)">
                  <div class="rule-overview-main">
                    <div class="rule-title-row">
                      <strong>{{ rule.name || ('rule_' + (index + 1)) }}</strong>
                      <span class="mini-pill" :data-tone="rule.enabled ? 'good' : 'muted'">
                        {{ rule.enabled ? '已启用' : '已停用' }}
                      </span>
                    </div>
                    <p class="rule-summary">{{ buildRuleSummary(rule) }}</p>
                  </div>
                  <span class="expand-mark">{{ isRuleExpanded(index) ? '收起' : '展开' }}</span>
                </button>

                <div v-show="isRuleExpanded(index)" class="rule-body">
                  <div class="rule-actions">
                    <button class="btn btn-ghost btn-small" @click="duplicateRule(index)">复制</button>
                    <button class="btn btn-ghost btn-small" @click="removeRule(index)">删除</button>
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
                      <span>源频道 / 群</span>
                      <textarea
                        v-model="rule.source_chat"
                        rows="3"
                        placeholder="@source_a&#10;@source_b"
                      ></textarea>
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
                    <label class="toggle span-2">
                      <input type="checkbox" v-model="rule.hdhive_resource_resolve_forward" />
                      <span>HDHive：识别 resource 链接并转发直链（redirect_url）</span>
                    </label>
                    <p class="panel-subtext span-2">
                      勾选后：当消息里出现 <code>hdhive.com/resource/…</code> 时，会用已保存的 <code>HDHIVE_COOKIE</code>（可选代理）
                      请求页面，从 <code>NEXT_REDIRECT</code> 解析 <code>redirect_url</code> 并用该直链发送到队列目标。
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
            <div v-else-if="search.loading" class="empty-state" data-tone="pending">
              正在搜索… 上一次结果已清空，请稍候。
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
                    <div class="toolbar compact-toolbar">
                      <button class="btn btn-secondary btn-small" :disabled="queue.actionBusy || !statusGlobalQueueFailed" @click="retryFailedQueue">
                        {{ queue.actionBusy ? '处理中...' : '重试失败任务' }}
                      </button>
                      <button class="btn btn-ghost btn-small" :disabled="queue.actionBusy || !statusGlobalQueueFailed" @click="clearFailedQueue">
                        清空失败任务
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
                    <p>源频道：{{ formatSourceList(worker.sources || worker.source) }}</p>
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
                    <p>源频道：{{ formatSourceList(worker.sources || worker.source) }}</p>
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
                <p class="panel-subtext">
                  默认展示<strong>全部日志</strong>。“签到日志”仅含 HDHive 自动/测试/立即签到相关；“转发监测”侧重投递过程；“实时检测日志”只看规则侧检测与入队（命中/未命中、跳过、补抓等）。
                </p>
              </div>
              <div class="log-head-meta">
                <span class="log-order-badge">最新在上</span>
              </div>
            </div>
            <div class="log-filter-bar">
              <button class="chip-btn" :data-active="ui.logFilter === 'all'" @click="setLogFilter('all')">
                全部日志 {{ sortedLogsForSourceScope.length }}
              </button>
              <button class="chip-btn" :data-active="ui.logFilter === 'hdhive'" @click="setLogFilter('hdhive')">
                签到日志 {{ hdhiveCheckinLogCount }}
              </button>
              <button class="chip-btn" :data-active="ui.logFilter === 'monitor'" @click="setLogFilter('monitor')">
                转发监测 {{ monitorLogCount }}
              </button>
              <button class="chip-btn" :data-active="ui.logFilter === 'error'" @click="setLogFilter('error')">
                错误日志 {{ errorLogCount }}
              </button>
              <button class="chip-btn" :data-active="ui.logFilter === 'detect'" @click="setLogFilter('detect')">
                实时检测日志 {{ detectionLogCount }}
              </button>
              <select class="log-source-select" v-model="ui.logSourceFilter" @change="setLogSourceFilter(ui.logSourceFilter)">
                <option v-for="opt in configuredLogSourceOptions()" :key="opt.key" :value="opt.key">
                  {{ opt.label }}
                </option>
              </select>
              <button class="btn btn-ghost btn-small" @click="clearCurrentLogs">清空当前筛选</button>
              <span class="log-filter-note">实时刷新，最新一条固定显示在最上面。</span>
            </div>
            <div ref="logBox" class="log-box" @scroll.passive="handleLogBoxScroll">
              <div v-if="!filteredLogs.length" class="log-empty">当前筛选下还没有日志输出。</div>
              <div
                v-for="(item, index) in filteredLogs"
                :key="item.sequence || index"
                class="log-line"
                :data-monitor="item.monitor ? 'true' : 'false'"
                :data-detect="item.detect ? 'true' : 'false'"
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
          </div>
          <div
            class="mobile-nav-backdrop"
            aria-hidden="true"
            @click="closeMobileNav"
          />
        </div>
      </template>
    </main>

    <Teleport to="body">
      <Transition name="msg-modal">
        <div
          v-if="notice || error"
          class="msg-modal-layer"
          role="presentation"
        >
          <div
            class="msg-modal-backdrop"
            aria-hidden="true"
            @click="dismissMessage"
          />
          <div
            class="msg-modal"
            :data-variant="error ? 'error' : 'success'"
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="msg-modal-title"
            aria-describedby="msg-modal-desc"
          >
            <button
              type="button"
              class="msg-modal-close"
              aria-label="关闭"
              @click="dismissMessage"
            >
              ×
            </button>
            <div class="msg-modal-icon-wrap" aria-hidden="true">
              <span class="msg-modal-icon" :data-kind="error ? 'error' : 'success'" />
            </div>
            <h2 id="msg-modal-title" class="msg-modal-title">
              {{ error ? '出错了' : '提示' }}
            </h2>
            <p id="msg-modal-desc" class="msg-modal-body">{{ error || notice }}</p>
            <p v-if="notice && !error" class="msg-modal-hint">约 5 秒后自动关闭，也可点击遮罩或按 Esc。</p>
            <button type="button" class="msg-modal-primary" @click="dismissMessage">
              知道了
            </button>
          </div>
        </div>
      </Transition>
    </Teleport>

    <Teleport to="body">
      <Transition name="module-ui-modal">
        <div
          v-if="moduleUiModal"
          class="module-ui-modal-layer"
          role="presentation"
        >
          <div
            class="module-ui-modal-backdrop"
            aria-hidden="true"
            @click="closeModuleUiModal"
          />
          <div
            class="module-ui-modal-shell"
            role="dialog"
            aria-modal="true"
            aria-labelledby="module-ui-modal-title"
            @click.stop
          >
            <header class="module-ui-modal-head">
              <div class="module-ui-modal-head-text">
                <p class="module-ui-modal-eyebrow">扩展模块</p>
                <h3 id="module-ui-modal-title">{{ moduleUiModal.name || moduleUiModal.directory }}</h3>
                <p v-if="moduleUiModal.directory" class="module-ui-modal-sub">
                  <code>{{ moduleUiModal.directory }}</code>
                </p>
              </div>
              <button
                type="button"
                class="module-ui-modal-close"
                aria-label="关闭"
                @click="closeModuleUiModal"
              >
                ×
              </button>
            </header>
            <div class="module-ui-modal-body">
              <iframe
                :src="moduleUiModalUrl()"
                class="module-ui-modal-iframe"
                title="模块界面"
                sandbox="allow-scripts allow-same-origin allow-forms"
              ></iframe>
            </div>
            <footer class="module-ui-modal-foot">
              <span class="module-ui-modal-hint">Esc 或点击遮罩关闭</span>
              <a
                class="module-ui-modal-external"
                :href="moduleUiModalUrl()"
                target="_blank"
                rel="noopener noreferrer"
              >
                新窗口打开
              </a>
            </footer>
          </div>
        </div>
      </Transition>
    </Teleport>
</template>
