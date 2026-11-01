/* ============================================================
 * 盾语 · 银行 盾语 · 银行 AI 智能体（仿真环境） · 前端交互
 *
 * 仅使用浏览器原生 API；所有请求均为同源相对路径。
 * 事件契约见 docs/CONTRACT.md §9 / §10。
 * ============================================================ */
(function () {
  'use strict';

  /* ============================================================
   * 1. 常量与文案
   * ============================================================ */

  const APP_TITLE = '盾语 · 银行 AI 智能体（仿真环境）';
  const SESSION_KEY = 'bankagent.session.v1';
  const RECENT_KEY = 'bankagent.recent.v1';
  const HISTORY_KEY = 'bankagent.history.v1';
  const MAX_HISTORY = 20;
  const MAX_HISTORY_MSGS = 200;
  const DEVICE_KEY = 'bankagent.device.v1';
  function getDeviceId() {
    try {
      let value = window.localStorage.getItem(DEVICE_KEY);
      if (!value) {
        value = (window.crypto && window.crypto.randomUUID)
          ? 'dev-' + window.crypto.randomUUID()
          : 'dev-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10);
        window.localStorage.setItem(DEVICE_KEY, value);
      }
      return value;
    } catch (err) {
      return 'dev-fallback-0001';
    }
  }

  const SETTINGS_KEY = 'bankagent.settings.v1';
  const DEFAULT_SETTINGS = { font: 'normal', density: 'comfortable', details: false,
                             voice: true, enter: true, history: true };
  const MAX_RECENT = 5;
  const CONFIRM_TTL_MS = 300 * 1000;
  const INPUT_PLACEHOLDER = '说说您想办理的业务…';
  const LOCKED_PLACEHOLDER = '会话已锁定，请开启新会话后继续';
  const REDUCED_MOTION = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);

  const SSE_EVENT_TYPES = ['thinking', 'plan', 'policy', 'confirm', 'stepup', 'step', 'result', 'error', 'audit'];
  const TYPING_TEXT = '正在处理…';

  const $ = (selector, root) => (root || document).querySelector(selector);
  const $$ = (selector, root) => Array.prototype.slice.call((root || document).querySelectorAll(selector));

  const STAGE_LABELS = {
    nlu: '正在理解您的意图…',
    parse: '正在理解您的意图…',
    planner: '正在生成执行计划…',
    plan: '正在生成执行计划…',
    validator: '正在校验计划的完整性…',
    policy: '正在核查安全策略…',
    confirm: '正在等待您的确认…',
    stepup: '正在进行强验证…',
    executor: '正在执行计划…',
    execute: '正在执行计划…',
    tools: '正在调用银行服务…',
    tool: '正在调用银行服务…',
    trust: '正在核对事实与数字…',
    render: '正在整理结果…',
    result: '正在整理结果…'
  };

  const SESSION_META = {
    NORMAL: { label: 'NORMAL', desc: '会话状态正常' },
    WATCH: { label: 'WATCH', desc: '观察中 · 加强验证' },
    LOCKED: { label: 'LOCKED', desc: '已锁定 · 暂停办理' }
  };

  const LEVEL_META = {
    green: { label: '绿色 · 直通', short: '绿色' },
    yellow: { label: '黄色 · 需确认', short: '黄色' },
    red: { label: '红色 · 强验证', short: '红色' },
    deny: { label: '策略拒绝', short: '拒绝' }
  };

  const RESULT_LEVEL_LABELS = {
    green: '绿色 · 免确认',
    yellow: '黄色 · 已确认',
    red: '红色 · 已强验证',
    deny: '已拒绝'
  };

  const POLICY_TITLES = {
    green: '策略校验通过',
    yellow: '需要您的确认',
    red: '需要强验证',
    deny: '策略拒绝'
  };

  const POLICY_TAGS = {
    green: '放行',
    yellow: '需确认',
    red: '强验证',
    deny: '拒绝'
  };

  const ACTION_LABELS = {
    transfer: '转账',
    create_transfer: '转账',
    submit_transfer: '大额转账',
    cancel_subscription: '取消订阅',
    subscription_cancel: '取消订阅',
    report_card_lost: '卡片挂失',
    card_lost: '卡片挂失',
    purchase_wealth: '理财申购',
    redeem_wealth: '理财赎回',
    create_standing_plan: '长期计划',
    freeze_amount: '资金锁定',
    order_gift: '礼品下单',
    adjust_card_limit: '额度调整',
    apply_virtual_card: '申请虚拟卡'
  };

  const FACTOR_LABELS = {
    sms: '短信验证码',
    face: '人脸识别',
    face_or_ushield: '人脸识别或 U 盾',
    ushield: 'U 盾',
    password: '交易密码',
    device: '设备确认'
  };

  const ERROR_HINTS = {
    E_CONTACT_NOT_FOUND: '未找到对应联系人，请换一个称呼试试。',
    E_INSUFFICIENT_FUND: '账户余额不足，无法完成本次操作。',
    E_LIMIT_EXCEEDED: '本次操作超过日累计额度，请调整金额。',
    E_POLICY_DENIED: '该操作被安全策略拒绝。',
    E_CONFIRM_EXPIRED: '确认已过期，请重新发起指令。',
    E_CONFIRM_USED: '该确认已被使用，请勿重复提交。',
    E_CONFIRM_TOKEN: '确认凭证无效，请重新发起指令。',
    E_CONFIRM_PENDING: '还有一笔操作待确认，请先处理。',
    E_PARAM: '请求参数不完整，请检查后重试。',
    E_STEPUP_FAIL: '验证码不正确，请重新输入。',
    E_STEPUP_LOCK: '强验证失败次数过多，会话已锁定。',
    E_STEPUP_LOCKED: '强验证失败次数过多，会话已锁定。',
    E_STEPUP_EXPIRED: '验证已过期，请重新发起。',
    E_NOT_FOUND: '未找到对应资源。',
    E_RATE_LIMITED: '请求过于频繁，请稍后再试。',
    E_INTERNAL: '银行服务内部错误，请稍后重试。',
    E_NETWORK: '无法连接银行服务，请确认后端已启动。',
    E_TIMEOUT: '请求超时，请稍后重试。',
    E_ABORTED: '请求已取消。',
    E_BAD_RESPONSE: '服务返回了无法解析的数据。'
  };

  const TOOL_INFO = {
    get_accounts: { label: '查询账户与余额', risk: 'green' },
    get_transactions: { label: '查询交易流水', risk: 'green' },
    analyze_spending: { label: '分析支出结构', risk: 'green' },
    detect_anomaly: { label: '识别异常交易', risk: 'green' },
    get_bill_report: { label: '生成账单报告', risk: 'green' },
    get_contacts: { label: '查询联系人', risk: 'green' },
    get_subscriptions: { label: '列出订阅与代扣', risk: 'green' },
    get_wealth_products: { label: '查询理财产品', risk: 'green' },
    get_profile: { label: '读取用户档案', risk: 'green' },
    quote_transfer: { label: '试算转账', risk: 'green' },
    create_transfer: { label: '发起转账', risk: 'yellow' },
    cancel_subscription: { label: '取消订阅', risk: 'yellow' },
    apply_virtual_card: { label: '申请虚拟卡', risk: 'yellow' },
    create_collection_request: { label: '发起 AA 收款', risk: 'yellow' },
    set_reminder: { label: '设置提醒', risk: 'yellow' },
    create_standing_plan: { label: '创建长期计划', risk: 'yellow' },
    freeze_amount: { label: '锁定活期资金', risk: 'yellow' },
    order_gift: { label: '下单礼品', risk: 'yellow' },
    release_freeze: { label: '解除资金冻结', risk: 'yellow' },
    submit_transfer: { label: '发起大额转账', risk: 'red' },
    report_card_lost: { label: '卡片挂失', risk: 'red' },
    unlock_card: { label: '解除挂失', risk: 'red' },
    purchase_wealth: { label: '申购理财', risk: 'red' },
    redeem_wealth: { label: '赎回理财', risk: 'red' },
    adjust_card_limit: { label: '调整卡片额度', risk: 'red' },
    notify: { label: '发送通知', risk: 'none' }
  };

  const RISK_LABELS = { green: '只读', yellow: '需确认', red: '强验证', none: '系统' };

  const RANGE_LABELS = {
    this_month: '本月',
    last_month: '上月',
    this_year: '今年',
    last_7d: '近 7 天',
    last_30d: '近 30 天',
    all: '全部'
  };

  const QUICK_COMMANDS = [
    { group: '查询', icon: 'chart', label: '这个月钱都去哪了', tag: '账单分析', text: '我这个月钱都去哪了' },
    { group: '查询', icon: 'card', label: '今天有什么提醒', tag: '主动服务', text: '今天有什么提醒' },
    { group: '查询', icon: 'repeat', label: '这三个会员我早就不用了', tag: '订阅管理', text: '这三个会员我早就不用了' },
    { group: '办理', icon: 'transfer', label: '再给我妈转 800', tag: '累计升级', text: '再给我妈转 800' },
    { group: '办理', icon: 'gift', label: '老婆生日，预算 1000', tag: '长期计划', text: '我老婆下个月生日，帮我准备一下，别超过 1000' },
    { group: '办理', icon: 'card', label: '我卡丢了', tag: '卡片挂失', text: '我卡丢了' },
    { group: '办理', icon: 'send', label: '帮我转 500 给我妈', tag: '基础转账', text: '帮我转 500 给我妈' }
  ];

  const RECENT_STATUS_CN = {
    done: '已完成', awaiting_confirm: '待确认', awaiting_stepup: '待验证',
    denied: '已拒绝', error: '失败', locked: '已锁定', cancelled: '已取消'
  };

  const ACTOR_META = {
    user: { cn: '用户', tone: 'blue', cls: 'actor-user' },
    agent: { cn: '智能体', tone: 'violet', cls: 'actor-nlu' },
    plan: { cn: '计划校验', tone: 'amber', cls: 'actor-validator' },
    nlu: { cn: '意图识别', tone: 'violet', cls: 'actor-nlu' },
    planner: { cn: '任务规划', tone: 'violet', cls: 'actor-planner' },
    validator: { cn: '计划校验', tone: 'amber', cls: 'actor-validator' },
    policy: { cn: '策略引擎', tone: 'amber', cls: 'actor-policy' },
    trust: { cn: '事实校验', tone: 'violet', cls: 'actor-trust' },
    executor: { cn: '编排执行', tone: 'blue', cls: 'actor-executor' },
    orchestrator: { cn: '编排器', tone: 'blue', cls: 'actor-orchestrator' },
    tools: { cn: '工具调用', tone: 'blue', cls: 'actor-tools' },
    tool: { cn: '工具调用', tone: 'blue', cls: 'actor-tool' },
    bankcore: { cn: '账务核心', tone: 'green', cls: 'actor-bankcore' },
    confirm: { cn: '用户确认', tone: 'green', cls: 'actor-confirm' },
    stepup: { cn: '强验证', tone: 'green', cls: 'actor-stepup' },
    system: { cn: '系统', tone: 'blue', cls: 'actor-system' }
  };

  const DECISION_META = {
    allow: { cn: '通过', cls: 'decision-allow' },
    ok: { cn: '成功', cls: 'decision-ok' },
    execute: { cn: '执行', cls: 'decision-execute' },
    success: { cn: '成功', cls: 'decision-success' },
    deny: { cn: '拒绝', cls: 'decision-deny' },
    error: { cn: '失败', cls: 'decision-error' },
    fail: { cn: '失败', cls: 'decision-fail' },
    escalate: { cn: '升级', cls: 'decision-escalate' },
    confirm: { cn: '待确认', cls: 'decision-confirm' },
    wait: { cn: '等待', cls: 'decision-wait' },
    audit: { cn: '记录', cls: 'decision-audit' },
    record: { cn: '记录', cls: 'decision-record' },
    ingest: { cn: '接收', cls: 'decision-record' },
    parsed: { cn: '已解析', cls: 'decision-allow' },
    planned: { cn: '已规划', cls: 'decision-allow' },
    valid: { cn: '校验通过', cls: 'decision-allow' },
    rejected: { cn: '拒绝', cls: 'decision-deny' },
    'awaiting_confirm': { cn: '待确认', cls: 'decision-escalate' },
    'awaiting_stepup': { cn: '待验证', cls: 'decision-escalate' },
    verified: { cn: '已验证', cls: 'decision-ok' },
    locked: { cn: '已锁定', cls: 'decision-deny' },
    cancelled: { cn: '已取消', cls: 'decision-deny' },
    confirmed: { cn: '已确认', cls: 'decision-confirm' },
    done: { cn: '完成', cls: 'decision-ok' },
    fallback: { cn: '回退', cls: 'decision-escalate' },
    green: { cn: '绿色', cls: 'decision-allow' },
    yellow: { cn: '黄色', cls: 'decision-escalate' },
    red: { cn: '红色', cls: 'decision-deny' }
  };

  /* 内联 SVG 图标（仅内部常量，不包含用户数据） */
  const SHIELD = '<path d="M12 3l7.5 3v5.2c0 4.9-3.2 8.3-7.5 10.1-4.3-1.8-7.5-5.2-7.5-10.1V6L12 3z"/>';
  const ICONS = {
    shield: SHIELD,
    shieldCheck: SHIELD + '<path d="M9 12l2 2 4-4"/>',
    shieldAlert: SHIELD + '<path d="M12 8v4"/><circle cx="12" cy="15.5" r=".85" fill="currentColor" stroke="none"/>',
    check: '<path d="M5 12.5l4.2 4.2L19 7"/>',
    x: '<path d="M6 6l12 12M18 6L6 18"/>',
    alert: '<path d="M12 8.5v4.2"/><circle cx="12" cy="16.4" r=".9" fill="currentColor" stroke="none"/><path d="M10.3 4.2L3.5 17.1a1.5 1.5 0 0 0 1.3 2.2h14.4a1.5 1.5 0 0 0 1.3-2.2L13.7 4.2a1.9 1.9 0 0 0-3.4 0z"/>',
    clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h9"/>',
    history: '<path d="M3.6 12a8.4 8.4 0 1 0 2.5-6"/><path d="M3.5 4.5V9H8"/><path d="M12 7.7V12l2.8 1.8"/>',
    refresh: '<path d="M20 11a8 8 0 1 0-2.4 5.7"/><path d="M20 4v7h-7"/>',
    download: '<path d="M12 3.5v11"/><path d="M7.5 10L12 14.5 16.5 10"/><path d="M5 20h14"/>',
    chevronRight: '<path d="M9 5l7 7-7 7"/>',
    chevronLeft: '<path d="M15 5l-7 7 7 7"/>',
    chevronDown: '<path d="M5 9l7 7 7-7"/>',
    chart: '<path d="M4 19V9"/><path d="M9.3 19V4"/><path d="M14.7 19v-6.5"/><path d="M20 19V7"/><path d="M3 21h18"/>',
    repeat: '<path d="M17 2.5l3.5 3.5L17 9.5"/><path d="M3.5 11V9a3 3 0 0 1 3-3h14"/><path d="M7 21.5L3.5 18 7 14.5"/><path d="M20.5 13v2a3 3 0 0 1-3 3h-14"/>',
    transfer: '<path d="M3.5 7.5h14"/><path d="M14 4l3.5 3.5L14 11"/><path d="M20.5 16.5h-14"/><path d="M10 13l-3.5 3.5L10 20"/>',
    gift: '<rect x="3.5" y="8.5" width="17" height="12" rx="1.8"/><path d="M2.5 8.5h19v3.5h-19z"/><path d="M12 8.5v12"/><path d="M12 8.5C10.5 8.5 7.2 7.8 8 5.2 8.8 2.8 11.2 4.6 12 8.5z"/><path d="M12 8.5c1.5 0 4.8-.7 4-3.3-.8-2.4-3.2-.6-4 3.3z"/>',
    card: '<rect x="2.5" y="5" width="19" height="14" rx="2.6"/><path d="M2.5 9.5h19"/><path d="M6 14.5h4"/>',
    send: '<path d="M4.5 11.5L20 4.5l-4.2 15.2-4.4-6.2-5.1-2z"/><path d="M11.4 13.5l4.4-4.5"/>',
    flow: '<circle cx="5.5" cy="6" r="2.2"/><circle cx="18.5" cy="6" r="2.2"/><circle cx="12" cy="18" r="2.2"/><path d="M7.7 6h8.6"/><path d="M6.6 8l4.3 8"/><path d="M17.4 8L13.1 16"/>',
    lock: '<rect x="5" y="10.5" width="14" height="10" rx="2"/><path d="M8 10.5V8a4 4 0 0 1 8 0v2.5"/>',
    info: '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5"/><circle cx="12" cy="7.8" r=".9" fill="currentColor" stroke="none"/>',
    link: '<path d="M9.5 14.5l5-5"/><path d="M7.2 17.8l-1 1a3.5 3.5 0 0 1-5-5l3-3a3.5 3.5 0 0 1 5 0"/><path d="M16.8 6.2l1-1a3.5 3.5 0 0 1 5 5l-3 3a3.5 3.5 0 0 1-5 0"/>',
    user: '<circle cx="12" cy="8" r="3.5"/><path d="M5.5 20a6.5 6.5 0 0 1 13 0"/>',
    wallet: '<path d="M3.5 7.5A2.5 2.5 0 0 1 6 5h11a2 2 0 0 1 2 2v1"/><rect x="3.5" y="7.5" width="17" height="12" rx="2.5"/><path d="M15 13.5h4"/>',
    external: '<path d="M13 5h6v6"/><path d="M19 5l-8 8"/><path d="M19 14v4.5a1.5 1.5 0 0 1-1.5 1.5h-12A1.5 1.5 0 0 1 4 18.5v-12A1.5 1.5 0 0 1 5.5 5H10"/>'
  };

  const TIME_FORMATTER = new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23'
  });

  const TS_FORMATTER = new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hourCycle: 'h23'
  });

  /* ============================================================
   * 2. DOM 引用
   * ============================================================ */

  const dom = {
    chatView: $('#chat-view'),
    auditView: $('#audit-view'),
    chatScroll: $('#chat-scroll'),
    chatStream: $('#chat-stream'),
    welcome: $('#welcome-message'),
    welcomeTime: $('#welcome-time'),

    quickList: $('#quick-list'),
    quickStrip: $('#quick-strip'),
    recentSection: $('#recent-section'),
    recentList: $('#recent-list'),
    historySection: $('#history-section'),
    historyList: $('#history-list'),

    settingsShortcut: $('#settings-shortcut'),
    mobileSettingsBtn: $('#mobile-settings-btn'),
    settingsView: $('#settings-view'),
    settingsClose: $('#settings-close'),
    settingsNav: $('.settings-nav'),
    setFont: $('#set-font'),
    setDensity: $('#set-density'),
    setDetails: $('#set-details'),
    setVoice: $('#set-voice'),
    setEnter: $('#set-enter'),
    setHistory: $('#set-history'),
    brainMode: $('#brain-mode'),
    brainPill: $('#brain-pill'),
    brainPillLabel: $('#brain-pill-label'),
    sideSecurity: $('#side-security'),
    welcomeSecurity: $('#welcome-security'),
    brainModeNote: $('#brain-mode-note'),
    llmKeyInput: $('#llm-key-input'),
    llmKeySave: $('#llm-key-save'),
    llmKeyClear: $('#llm-key-clear'),
    llmKeyNote: $('#llm-key-note'),
    voiceEngine: $('#voice-engine'),
    voiceDetail: $('#voice-detail'),
    voiceRetest: $('#voice-retest'),
    clearHistory: $('#clear-history'),
    exportTransactions: $('#export-transactions'),
    exportDialog: $('#export-dialog'),
    exportBackdrop: $('#export-backdrop'),
    exportClose: $('#export-close'),
    exportRange: $('#export-range'),
    exportParty: $('#export-party'),
    exportFormat: $('#export-format'),
    exportDownload: $('#export-download'),
    aboutVersion: $('#about-version'),
    aboutMode: $('#about-mode'),
    aboutPort: $('#about-port'),
    aboutAsr: $('#about-asr'),
    opsMetrics: $('#ops-metrics'),
    opsBadcases: $('#ops-badcases'),
    opsRefresh: $('#ops-refresh'),
    opsKnowledge: $('#ops-knowledge'),
    kbEntry: $('#kb-entry'),
    kbContent: $('#kb-content'),
    kbDraft: $('#kb-draft'),
    opsTickets: $('#ops-tickets'),
    opsFlags: $('#ops-flags'),
    opsAb: $('#ops-ab'),
    securityResult: $('#security-result'),
    demoRun: $('#demo-run'),
    demoScenes: $('#demo-scenes'),
    identityGrid: $('#identity-grid'),
    identityDevice: $('#identity-device'),
    identityBind: $('#identity-bind'),
    authName: $('#auth-name'),
    authRelation: $('#auth-relation'),
    authScope: $('#auth-scope'),
    authDays: $('#auth-days'),
    authAdd: $('#auth-add'),
    authList: $('#auth-list'),

    overviewStrip: $('#overview-strip'),
    ovBalance: $('#ov-balance'),
    ovSpend: $('#ov-spend'),
    ovNext: $('#ov-next'),
    fabTools: $('#fab-tools'),
    toolSheet: $('#tool-sheet'),
    toolBackdrop: $('#tool-backdrop'),
    toolClose: $('#tool-close'),
    transferForm: $('#transfer-form'),
    tfContact: $('#tf-contact'),
    tfAmount: $('#tf-amount'),
    tfMemo: $('#tf-memo'),

    sessionId: $('#sidebar-session-id'),
    copySessionBtn: $('#copy-session-btn'),
    newSessionBtn: $('#new-session-btn'),

    mobileAuditBtn: $('#mobile-audit-btn'),
    mobileMenuBtn: $('#mobile-menu-btn'),
    sidebar: $('#sidebar'),
    sidebarBackdrop: $('#sidebar-backdrop'),
    sidebarClose: $('#sidebar-close'),
    auditShortcut: $('#audit-shortcut'),
    auditAllBtn: $('#audit-all-btn'),
    auditAllSide: $('#audit-all-side'),
    auditIndex: $('#audit-index'),
    auditIndexBackdrop: $('#audit-index-backdrop'),
    auditIndexClose: $('#audit-index-close'),
    auditIndexSearch: $('#audit-index-search'),
    auditIndexList: $('#audit-index-list'),
    auditIndexCount: $('#audit-index-count'),

    stateBadges: $$('[data-state-badge]'),
    stateLabels: $$('[data-state-label]'),
    stateDescs: $$('[data-state-desc]'),
    stateDots: $$('[data-state-dot]'),
    stateRail: $('.state-rail'),

    connectionBanner: $('#connection-banner'),
    connectionTitle: $('#connection-banner strong'),
    connectionText: $('#connection-text'),
    connectionRetry: $('#connection-retry'),
    connDot: $('#conn-dot'),
    backendVersionText: $('#backend-version-text'),

    sessionBanner: $('#session-banner'),
    sessionBannerTitle: $('#session-banner-title'),
    sessionBannerText: $('#session-banner-text'),
    sessionBannerAction: $('#session-banner-action'),

    composerForm: $('#composer-form'),
    composer: $('.composer'),
    composerInput: $('#composer-input'),
    voiceBtn: $('#voice-btn'),
    sendBtn: $('#send-btn'),
    charCount: $('#char-count'),

    auditContent: $('#audit-content'),
    auditTraceId: $('#audit-trace-id'),
    auditCopyBtn: $('#audit-copy-btn'),
    auditDownloadBtn: $('#audit-download-btn'),
    auditBackBtn: $('#audit-back-btn'),

    stepupModal: $('#stepup-modal'),
    stepupBackdrop: $('#stepup-backdrop'),
    stepupClose: $('#stepup-close'),
    stepupLater: $('#stepup-later'),
    stepupForm: $('#stepup-form'),
    stepupDesc: $('#stepup-desc'),
    stepupHint: $('#stepup-hint'),
    stepupError: $('#stepup-error'),
    stepupSubmit: $('#stepup-submit'),
    stepupInputs: $$('.code-input'),
    modalPanel: $('.modal-panel'),

    toastStack: $('#toast-stack')
  };

  /* ============================================================
   * 3. 应用状态
   * ============================================================ */

  const state = {
    sessionId: readSessionIdOrCreate(),
    sessionState: 'NORMAL',
    sessionPending: null,
    brainMode: 'rules',
    connected: false,
    healthChecked: false,
    busy: new Set(),
    route: 'chat',
    currentTurn: null,
    lastTraceId: null,
    recent: loadRecent(),
    confirmContexts: new Set(),
    countdownTimer: null,
    stepupContexts: new Map(),
    activeStepup: null,
    modalTrigger: null,
    auditData: null,
    auditTraceId: null,
    auditAbort: null,
    autoSubmitTimer: null,
    statePollTimer: null,
    localLock: false,
    voiceActive: false,
    voiceReady: false,
    settings: null,
    turnSeq: 0
  };

  /* ============================================================
   * 4. 基础工具
   * ============================================================ */

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function append(parent) {
    for (let i = 1; i < arguments.length; i += 1) {
      const child = arguments[i];
      if (child === null || child === undefined) continue;
      if (Array.isArray(child)) {
        child.forEach((item) => {
          if (item !== null && item !== undefined) parent.append(item);
        });
      } else {
        parent.append(child);
      }
    }
    return parent;
  }

  function iconHTML(name, cls) {
    const body = ICONS[name] || ICONS.info;
    return (
      '<svg class="icon' + (cls ? ' ' + cls : '') +
      '" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" ' +
      'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + body + '</svg>'
    );
  }

  function iconNode(name, cls) {
    const template = document.createElement('template');
    template.innerHTML = iconHTML(name, cls);
    return template.content.firstElementChild;
  }

  function clearNode(node) {
    if (node) node.replaceChildren();
  }

  function pad2(value) {
    return String(value).padStart(2, '0');
  }

  function parseDateMs(value) {
    if (value === null || value === undefined || value === '') return null;
    if (typeof value === 'number') {
      if (!Number.isFinite(value)) return null;
      return value < 1e12 ? value * 1000 : value;
    }
    const text = String(value).trim();
    if (!text) return null;
    const ms = Date.parse(text);
    return Number.isFinite(ms) ? ms : null;
  }

  function timestampParts(value) {
    const ms = parseDateMs(value);
    if (ms === null) {
      return { time: String(value || '—'), date: '', full: String(value || '—') };
    }
    try {
      const parts = TS_FORMATTER.formatToParts(new Date(ms));
      const map = {};
      parts.forEach((part) => {
        if (part.type !== 'literal') map[part.type] = part.value;
      });
      const time = String(map.hour || '00') + ':' + String(map.minute || '00') + ':' + String(map.second || '00');
      const date = String(map.month || '') + '-' + String(map.day || '');
      return { time: time, date: date, full: date + ' ' + time };
    } catch (err) {
      return { time: '—', date: '', full: String(value) };
    }
  }

  function clockNow(date) {
    const target = date instanceof Date ? date : new Date();
    try {
      return TIME_FORMATTER.format(target);
    } catch (err) {
      return pad2(target.getHours()) + ':' + pad2(target.getMinutes());
    }
  }

  function fmtMoney(value) {
    if (value === null || value === undefined || value === '') return '';
    if (typeof value === 'number' && !Number.isFinite(value)) return String(value);
    const text = String(value).replace(/,/g, '').trim();
    const number = Number(text);
    if (!Number.isFinite(number)) return String(value);
    try {
      return number.toLocaleString('zh-CN', {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2
      });
    } catch (err) {
      return number.toFixed(2);
    }
  }

  /* 计划参数中的金额按契约使用“分” */
  function fmtPlanAmount(value) {
    if (value === null || value === undefined || value === '') return '';
    if (typeof value === 'string' && value.indexOf('.') >= 0) return fmtMoney(value);
    const number = Number(String(value).replace(/,/g, ''));
    if (!Number.isFinite(number)) return String(value);
    return fmtMoney(number / 100);
  }

  function fmtCardAmount(value) {
    if (value === null || value === undefined || value === '') return '—';
    return fmtMoney(value);
  }

  function truncate(text, maxLength) {
    const str = String(text === null || text === undefined ? '' : text);
    if (str.length <= maxLength) return str;
    return str.slice(0, Math.max(0, maxLength - 1)) + '…';
  }

  function shortHash(value, length) {
    const str = String(value || '');
    const clean = str.replace(/^sha256:/, '');
    const size = length || 12;
    return clean.length > size ? clean.slice(0, size) + '…' : clean;
  }

  function sleep(ms) {
    return new Promise((resolve) => window.setTimeout(resolve, ms));
  }

  function safeJson(value) {
    if (value === null || value === undefined) return '';
    if (typeof value === 'string') {
      const trimmed = value.trim();
      if ((trimmed.charAt(0) === '{' && trimmed.charAt(trimmed.length - 1) === '}') ||
          (trimmed.charAt(0) === '[' && trimmed.charAt(trimmed.length - 1) === ']')) {
        try {
          return JSON.stringify(JSON.parse(trimmed), null, 2);
        } catch (err) {
          return value;
        }
      }
      return value;
    }
    try {
      return JSON.stringify(value, null, 2);
    } catch (err) {
      return String(value);
    }
  }

  function normalizeLevel(value) {
    const level = String(value || '').toLowerCase();
    return Object.prototype.hasOwnProperty.call(LEVEL_META, level) ? level : '';
  }

  function levelLabel(level, resultMode) {
    const meta = LEVEL_META[level];
    if (!meta) return '策略校验';
    if (resultMode) return RESULT_LEVEL_LABELS[level] || meta.label;
    return meta.label;
  }

  function actionLabel(action) {
    return ACTION_LABELS[action] || '业务办理';
  }

  function riskLabel(risk) {
    return RISK_LABELS[risk] || RISK_LABELS.green;
  }

  function normalizeFactors(factors) {
    if (!Array.isArray(factors)) return [];
    return factors.map((factor) => FACTOR_LABELS[factor] || String(factor));
  }

  function createId(prefix) {
    const bytes = new Uint8Array(8);
    if (window.crypto && typeof window.crypto.getRandomValues === 'function') {
      window.crypto.getRandomValues(bytes);
    } else {
      for (let i = 0; i < bytes.length; i += 1) bytes[i] = Math.floor(Math.random() * 256);
    }
    let hex = '';
    for (let i = 0; i < bytes.length; i += 1) hex += bytes[i].toString(16).padStart(2, '0');
    return prefix + hex;
  }

  function readSessionIdOrCreate() {
    try {
      const existing = window.sessionStorage.getItem(SESSION_KEY);
      if (existing && /^[A-Za-z0-9_-]{6,80}$/.test(existing)) return existing;
      const created = createId('sess_');
      window.sessionStorage.setItem(SESSION_KEY, created);
      return created;
    } catch (err) {
      return createId('sess_');
    }
  }

  function writeSessionId(sessionId) {
    try {
      window.sessionStorage.setItem(SESSION_KEY, sessionId);
    } catch (err) {
      /* 隐私模式下退化为内存会话 */
    }
  }

  function loadRecent() {
    try {
      const raw = window.localStorage.getItem(RECENT_KEY);
      if (!raw) return [];
      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) return [];
      return parsed
        .filter((item) => item && typeof item.trace_id === 'string' && item.trace_id)
        .slice(0, MAX_RECENT)
        .map((item) => ({
          trace_id: item.trace_id,
          text: typeof item.text === 'string' ? item.text : '',
          ts: item.ts || ''
        }));
    } catch (err) {
      return [];
    }
  }

  function saveRecent() {
    try {
      window.localStorage.setItem(RECENT_KEY, JSON.stringify(state.recent));
    } catch (err) {
      /* 存储不可用时仅保留内存记录 */
    }
  }

  function addRecent(traceId, text, status) {
    if (!traceId) return;
    state.recent = state.recent.filter((item) => item.trace_id !== traceId);
    state.recent.unshift({
      trace_id: String(traceId),
      text: String(text || ''),
      status: String(status || ''),
      ts: new Date().toISOString()
    });
    state.recent = state.recent.slice(0, MAX_RECENT);
    saveRecent();
    renderRecent();
  }

  function isNearBottom() {
    if (!dom.chatScroll) return true;
    const node = dom.chatScroll;
    return node.scrollHeight - node.scrollTop - node.clientHeight < 170;
  }

  function scrollToBottom(smooth) {
    if (!dom.chatScroll) return;
    const behavior = smooth && !REDUCED_MOTION ? 'smooth' : 'auto';
    window.requestAnimationFrame(() => {
      try {
        dom.chatScroll.scrollTo({ top: dom.chatScroll.scrollHeight, behavior: behavior });
      } catch (err) {
        dom.chatScroll.scrollTop = dom.chatScroll.scrollHeight;
      }
    });
  }

  function showToast(message, tone) {
    if (!dom.toastStack || !message) return;
    const kind = tone || 'info';
    const node = el('div', 'toast is-' + kind);
    const iconName = kind === 'error' ? 'alert' : kind === 'success' ? 'check' : 'info';
    append(node, iconNode(iconName), el('span', null, String(message)));
    dom.toastStack.append(node);
    while (dom.toastStack.children.length > 3) {
      dom.toastStack.firstElementChild.remove();
    }
    window.setTimeout(() => {
      node.style.opacity = '0';
      node.style.transform = 'translateY(4px)';
      node.style.transition = 'opacity .18s ease, transform .18s ease';
      window.setTimeout(() => node.remove(), 200);
    }, 3200);
  }

  async function copyText(text) {
    const value = String(text === null || text === undefined ? '' : text);
    if (!value) return false;
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(value);
        return true;
      }
    } catch (err) {
      /* 回退到 execCommand */
    }
    try {
      const area = el('textarea');
      area.value = value;
      area.setAttribute('readonly', 'readonly');
      area.style.position = 'fixed';
      area.style.top = '-1000px';
      area.style.opacity = '0';
      document.body.append(area);
      area.select();
      const ok = document.execCommand('copy');
      area.remove();
      return ok;
    } catch (err) {
      return false;
    }
  }

  function describeError(err) {
    if (!err) return ERROR_HINTS.E_INTERNAL;
    if (err instanceof ApiError) {
      return err.message || ERROR_HINTS[err.code] || '请求失败。';
    }
    return err.message || String(err);
  }

  /* ============================================================
   * 5. API 客户端
   * ============================================================ */

  class ApiError extends Error {
    constructor(code, message, status, payload) {
      super(message || '请求失败。');
      this.name = 'ApiError';
      this.code = code || 'E_INTERNAL';
      this.status = status || 0;
      this.payload = payload || null;
    }
  }

  async function fetchJSON(path, options) {
    const opts = options || {};
    const method = opts.method || 'GET';
    const timeout = opts.timeout === undefined ? 30000 : opts.timeout;
    const externalSignal = opts.signal || null;
    const controller = new AbortController();
    let timedOut = false;
    let timer = null;

    const forwardAbort = () => {
      try {
        controller.abort();
      } catch (err) {
        /* noop */
      }
    };
    if (externalSignal) {
      if (externalSignal.aborted) controller.abort();
      else externalSignal.addEventListener('abort', forwardAbort, { once: true });
    }
    if (timeout > 0) {
      timer = window.setTimeout(() => {
        timedOut = true;
        try {
          controller.abort();
        } catch (err) {
          /* noop */
        }
      }, timeout);
    }

    try {
      const request = {
        method: method,
        headers: {},
        signal: controller.signal,
        credentials: 'same-origin',
        cache: 'no-store'
      };
      request.headers['X-Device-Id'] = getDeviceId();
      if (opts.body !== undefined) {
        request.headers['Content-Type'] = 'application/json';
        request.body = JSON.stringify(opts.body);
      }

      const response = await fetch(path, request);
      const raw = await response.text();
      let payload = null;
      if (raw) {
        try {
          payload = JSON.parse(raw);
        } catch (err) {
          if (response.ok) {
            throw new ApiError('E_BAD_RESPONSE', '服务返回了无法解析的数据。', response.status);
          }
        }
      }

      if (!response.ok) {
        const body = payload && typeof payload === 'object' ? payload : {};
        const code = body.code || 'E_HTTP_' + response.status;
        const message = body.message || ERROR_HINTS[code] || '请求失败（HTTP ' + response.status + '）。';
        throw new ApiError(code, message, response.status, body);
      }

      markServiceOnline();
      return payload && typeof payload === 'object' ? payload : {};
    } catch (err) {
      if (err instanceof ApiError) throw err;
      if (timedOut) {
        throw new ApiError('E_TIMEOUT', ERROR_HINTS.E_TIMEOUT, 0);
      }
      if (externalSignal && externalSignal.aborted) {
        throw new ApiError('E_ABORTED', ERROR_HINTS.E_ABORTED, 0);
      }
      if (err && err.name === 'AbortError') {
        throw new ApiError('E_ABORTED', ERROR_HINTS.E_ABORTED, 0);
      }
      throw new ApiError('E_NETWORK', ERROR_HINTS.E_NETWORK, 0, err);
    } finally {
      if (timer) window.clearTimeout(timer);
      if (externalSignal) externalSignal.removeEventListener('abort', forwardAbort);
    }
  }

  /* ============================================================
   * 6. 连接与会话状态
   * ============================================================ */

  let healthRetryTimer = null;
  function scheduleHealthRetry() {
    if (healthRetryTimer) return;
    healthRetryTimer = window.setTimeout(async () => {
      healthRetryTimer = null;
      const ok = await checkHealth();
      if (!ok) scheduleHealthRetry();
    }, 3000);
  }

  function markServiceOnline(version, brain) {
    if (healthRetryTimer) { window.clearTimeout(healthRetryTimer); healthRetryTimer = null; }
    state.connected = true;
    state.healthChecked = true;
    if (dom.connDot) {
      dom.connDot.classList.add('is-online');
      dom.connDot.classList.remove('is-offline');
    }
    if (dom.backendVersionText) {
      const mode = brain && brain.enabled ? '大模型' : '规则模式';
      if (version) {
        dom.backendVersionText.textContent = '服务在线 · v' + version + ' · ' + mode;
      } else if (!state.connectedWasOnline) {
        dom.backendVersionText.textContent = '服务在线 · ' + mode;
      }
    }
    state.connectedWasOnline = true;
    if (dom.connectionBanner) dom.connectionBanner.hidden = true;
  }

  function markServiceOffline() {
    state.connected = false;
    state.connectedWasOnline = false;
    state.healthChecked = true;
    if (dom.connDot) {
      dom.connDot.classList.add('is-offline');
      dom.connDot.classList.remove('is-online');
    }
    if (dom.backendVersionText) dom.backendVersionText.textContent = '服务未连接';
  }

  function showConnectionBanner(title, message) {
    if (!dom.connectionBanner) return;
    dom.connectionBanner.hidden = false;
    if (dom.connectionTitle) dom.connectionTitle.textContent = title;
    if (dom.connectionText) dom.connectionText.textContent = message;
  }

  function reportTransportError(err) {
    if (!err) return;
    if (err.code !== 'E_NETWORK' && err.code !== 'E_TIMEOUT') return;
    markServiceOffline();
    showConnectionBanner(
      '银行服务暂时无法连接',
      err.code === 'E_TIMEOUT'
        ? '请求超时。请确认后端已启动，或稍后点击重试。'
        : '无法连接银行服务。请确认后端已启动，界面可继续浏览。'
    );
    scheduleHealthRetry();
  }

  async function checkHealth(options) {
    const opts = options || {};
    try {
      const health = await fetchJSON('/health', { timeout: 6000 });
      markServiceOnline(health.version, health.brain);
      return true;
    } catch (err) {
      markServiceOffline();
      showConnectionBanner('银行服务暂时无法连接', describeError(err));
      if (opts.notify) showToast('仍无法连接银行服务，请检查后端是否已启动。', 'error');
      scheduleHealthRetry();
      return false;
    }
  }

  async function refreshSessionState(options) {
    const opts = options || {};
    try {
      const data = await fetchJSON('/v1/state/' + encodeURIComponent(state.sessionId), { timeout: 8000 });
      state.sessionPending = data && data.pending ? data.pending : null;
      setSessionState((data && data.state) || 'NORMAL');
      loadOverview();
      return data;
    } catch (err) {
      if (!err || err.code === 'E_ABORTED') return null;
      if (err.status === 404) return null;
      if (err.code === 'E_NETWORK' || err.code === 'E_TIMEOUT') {
        markServiceOffline();
      }
      showConnectionBanner(
        err.code === 'E_NETWORK' || err.code === 'E_TIMEOUT' ? '银行服务暂时无法连接' : '会话状态读取失败',
        err.code + ' · ' + describeError(err)
      );
      if (!opts.quiet) showToast(describeError(err), 'error');
      return null;
    }
  }

  function setSessionState(rawState) {
    const key = String(rawState || 'NORMAL').toUpperCase();
    const next = Object.prototype.hasOwnProperty.call(SESSION_META, key) ? key : 'NORMAL';
    if (state.localLock && next !== 'LOCKED') {
      updateInteractiveState();
      return;
    }
    if (next === 'LOCKED') state.localLock = true;
    state.sessionState = next;
    document.body.dataset.sessionState = next;

    const meta = SESSION_META[next];
    dom.stateBadges.forEach((node) => {
      node.dataset.state = next;
    });
    dom.stateDots.forEach((node) => {
      node.dataset.state = next;
    });
    dom.stateLabels.forEach((node) => {
      node.textContent = meta.label;
    });
    dom.stateDescs.forEach((node) => {
      node.textContent = meta.desc;
    });

    if (dom.stateRail) {
      dom.stateRail.dataset.state = next;
      $$('[data-rail]', dom.stateRail).forEach((node) => {
        node.classList.toggle('is-active', node.dataset.rail === next);
      });
    }

    if (next === 'NORMAL') {
      dom.sessionBanner.hidden = true;
    } else if (next === 'WATCH') {
      dom.sessionBanner.hidden = false;
      dom.sessionBanner.dataset.tone = 'watch';
      dom.sessionBanner.setAttribute('role', 'status');
      dom.sessionBannerTitle.textContent = '会话处于观察状态';
      dom.sessionBannerText.textContent = '系统检测到需要留意的操作，敏感指令可能要求额外验证。';
      dom.sessionBannerAction.hidden = true;
    } else {
      dom.sessionBanner.hidden = false;
      dom.sessionBanner.dataset.tone = 'locked';
      dom.sessionBanner.setAttribute('role', 'alert');
      dom.sessionBannerTitle.textContent = '会话已触发安全锁定';
      dom.sessionBannerText.textContent = '连续异常或验证失败将暂停办理。可开启新会话继续演示。';
      dom.sessionBannerAction.hidden = false;
    }

    updateInteractiveState();
  }

  function markBusy(reason) {
    state.busy.add(reason);
    updateInteractiveState();
  }

  function clearBusy(reason) {
    state.busy.delete(reason);
    updateInteractiveState();
  }

  function updateInteractiveState() {
    const locked = state.sessionState === 'LOCKED';
    const busy = state.busy.size > 0;
    const hasText = !!(dom.composerInput && dom.composerInput.value.trim());

    if (dom.composerInput) {
      dom.composerInput.disabled = locked;
      dom.composerInput.placeholder = locked ? LOCKED_PLACEHOLDER : INPUT_PLACEHOLDER;
    }
    if (dom.composer) dom.composer.classList.toggle('is-locked', locked);
    if (dom.sendBtn) dom.sendBtn.disabled = locked || busy || !hasText;
    if (dom.voiceBtn) dom.voiceBtn.disabled = locked || busy || voiceBusy;

    $$('.quick-item, .quick-chip').forEach((button) => {
      button.disabled = locked || busy;
    });
  }

  /* ============================================================
   * 7. 快捷指令、最近审计、会话标识
   * ============================================================ */


  /* ============================================================
   * 6.5 移动端侧边栏（抽屉）
   * ============================================================ */

  /* ============================================================
   * 6.6 概览条 + 快捷功能
   * ============================================================ */
  function yuan(cents) {
    return '¥' + fmtMoney((Number(cents) || 0) / 100);
  }

  async function loadOverview() {
    if (!dom.overviewStrip) return;
    try {
      const data = await fetchJSON('/v1/overview', { timeout: 8000 });
      if (!data) { dom.overviewStrip.hidden = true; return; }
      dom.overviewStrip.hidden = false;
      dom.ovBalance.textContent = yuan(data.balance_cents);
      dom.ovSpend.textContent = yuan(data.month && data.month.spend_cents);
      const next = data.subscription && data.subscription.next;
      dom.ovNext.textContent = next ? next.name + ' ' + yuan(next.amount_cents) : '无';
      if (dom.tfContact && Array.isArray(data.contacts)) {
        dom.tfContact.replaceChildren.apply(dom.tfContact, data.contacts.map((item) => {
          const option = document.createElement('option');
          option.value = item.alias || item.name;
          option.textContent = item.alias ? item.alias + '（' + item.name + '）' : item.name;
          return option;
        }));
      }
    } catch (err) {
      dom.overviewStrip.hidden = true;
    }
  }

  function openTools() {
    if (!dom.toolSheet || !dom.toolBackdrop) return;
    dom.toolSheet.hidden = false;
    dom.toolBackdrop.hidden = false;
    if (dom.fabTools) dom.fabTools.setAttribute('aria-expanded', 'true');
    loadOverview();
  }

  function closeTools() {
    if (dom.toolSheet) dom.toolSheet.hidden = true;
    if (dom.toolBackdrop) dom.toolBackdrop.hidden = true;
    if (dom.transferForm) dom.transferForm.hidden = true;
    if (dom.fabTools) dom.fabTools.setAttribute('aria-expanded', 'false');
  }


  async function openExportDialog() {
    if (!dom.exportDialog) return;
    closeTools();
    dom.exportDialog.hidden = false;
    dom.exportBackdrop.hidden = false;
    try {
      const data = await fetchJSON('/v1/export/options', { timeout: 8000 });
      const ranges = (data && data.ranges) || [];
      const parties = (data && data.counterparties) || [];
      if (dom.exportRange && ranges.length) {
        dom.exportRange.replaceChildren.apply(dom.exportRange, ranges.map((item) => {
          const option = document.createElement('option');
          option.value = item.key;
          option.textContent = item.label;
          return option;
        }));
      }
      if (dom.exportParty) {
        const all = document.createElement('option');
        all.value = '';
        all.textContent = '全部对象';
        dom.exportParty.replaceChildren(all, ...parties.map((name) => {
          const option = document.createElement('option');
          option.value = name;
          option.textContent = name;
          return option;
        }));
      }
    } catch (err) {
      /* 用默认值 */
    }
  }

  function closeExportDialog() {
    if (dom.exportDialog) dom.exportDialog.hidden = true;
    if (dom.exportBackdrop) dom.exportBackdrop.hidden = true;
  }

  function downloadExport() {
    const range = dom.exportRange ? dom.exportRange.value : 'this_month';
    const party = dom.exportParty ? dom.exportParty.value : '';
    const format = dom.exportFormat && dom.exportFormat.value === 'csv' ? 'csv' : 'xlsx';
    let url = '/v1/export/transactions.' + format + '?range=' + encodeURIComponent(range || 'this_month');
    if (party) url += '&counterparty=' + encodeURIComponent(party);
    window.location.href = url;
    closeExportDialog();
  }

  function bindTools() {
    if (dom.fabTools) dom.fabTools.addEventListener('click', openTools);
    if (dom.toolClose) dom.toolClose.addEventListener('click', closeTools);
    if (dom.toolBackdrop) dom.toolBackdrop.addEventListener('click', closeTools);
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') closeTools();
    });
    $$('[data-tool]').forEach((button) => {
      button.addEventListener('click', () => {
        const tool = button.dataset.tool;
        if (tool === 'transfer') {
          if (dom.transferForm) dom.transferForm.hidden = false;
          if (dom.tfAmount) dom.tfAmount.focus();
          return;
        }
        if (tool === 'export') {
          closeTools();
          openExportDialog();
          return;
        }
        const text = {
          bill: '我这个月钱都去哪了',
          subs: '这三个会员我早就不用了',
          card: '我卡丢了',
          wealth: '把上个月的理财收益算一下',
          plan: '我老婆下个月生日，帮我准备一下，别超过 1000',
          quota: '我今天还能转多少',
          annual: '我一年订阅要花多少',
          latest: '刚才那笔到哪了',
          contacts: '我有哪些联系人',
          anomaly: '帮我看看有没有乱扣费的',
          fee: '转账多久到账，有手续费吗',
          budget: '本月预算还剩多少',
          calc: '1万分12期手续费多少',
          repay: '我要还房贷',
          risk: '做风险测评',
          password: '重置密码'
        }[tool];
        if (!text) return;
        closeTools();
        if (state.busy.size === 0) sendText(text);
      });
    });
    if (dom.transferForm) {
      dom.transferForm.addEventListener('submit', (event) => {
        event.preventDefault();
        const amount = parseFloat(dom.tfAmount.value);
        const payee = dom.tfContact.value;
        if (!Number.isFinite(amount) || amount <= 0 || !payee) {
          showToast('请填写收款人和金额。', 'error');
          return;
        }
        const memo = (dom.tfMemo.value || '').trim();
        const text = '转 ' + amount + ' 给' + payee + (memo ? '，备注 ' + memo : '');
        closeTools();
        dom.tfAmount.value = '';
        dom.tfMemo.value = '';
        if (state.busy.size === 0) sendText(text);
      });
    }
  }
  function isMobileViewport() {
    return window.matchMedia('(max-width: 920px)').matches;
  }

  function openSidebar() {
    if (!dom.sidebar || !dom.sidebarBackdrop) return;
    document.body.classList.add('sidebar-open');
    dom.sidebarBackdrop.hidden = false;
    if (dom.mobileMenuBtn) dom.mobileMenuBtn.setAttribute('aria-expanded', 'true');
    window.requestAnimationFrame(() => {
      if (dom.sidebarClose) dom.sidebarClose.focus({ preventScroll: true });
    });
  }

  function closeSidebar() {
    document.body.classList.remove('sidebar-open');
    if (dom.sidebarBackdrop) dom.sidebarBackdrop.hidden = true;
    if (dom.mobileMenuBtn) dom.mobileMenuBtn.setAttribute('aria-expanded', 'false');
  }
  function onQuickCommand(text) {
    if (isMobileViewport()) closeSidebar();
    if (state.sessionState === 'LOCKED') {
      showToast('会话已锁定，请先开启新会话。', 'error');
      return;
    }
    if (state.busy.size > 0) {
      showToast('请等待当前操作完成后再发送。', 'info');
      return;
    }
    sendText(text);
  }

  function buildQuickCommands() {
    if (!dom.quickList || !dom.quickStrip) return;

    const sidebarNodes = [];
    let currentGroup = '';
    QUICK_COMMANDS.forEach((command) => {
      if (command.group && command.group !== currentGroup) {
        currentGroup = command.group;
        sidebarNodes.push(el('div', 'quick-group-label', command.group));
      }
      const button = el('button', 'quick-item');
      button.type = 'button';
      button.title = command.text;
      button.dataset.command = command.text;

      const iconWrap = el('span', 'quick-icon');
      iconWrap.append(iconNode(command.icon));
      const copy = el('span', 'quick-copy');
      copy.append(
        el('span', 'quick-label', command.label),
        el('span', 'quick-tag', command.tag)
      );
      button.append(iconWrap, copy);
      button.addEventListener('click', () => onQuickCommand(command.text));
      sidebarNodes.push(button);
    });
    dom.quickList.replaceChildren.apply(dom.quickList, sidebarNodes);

    const stripNodes = QUICK_COMMANDS.map((command) => {
      const button = el('button', 'quick-chip', command.label);
      button.type = 'button';
      button.title = command.text;
      button.dataset.command = command.text;
      button.addEventListener('click', () => onQuickCommand(command.text));
      return button;
    });
    dom.quickStrip.replaceChildren.apply(dom.quickStrip, stripNodes);
  }

  function renderRecent() {
    if (!dom.recentSection || !dom.recentList) return;
    dom.recentSection.hidden = state.recent.length === 0;
    const nodes = state.recent.slice(0, 4).map((item) => {
      const button = el('button', 'recent-item');
      button.type = 'button';
      button.title = (item.text || '') + ' · ' + item.trace_id;
      const time = item.ts ? timestampParts(item.ts) : null;
      const stateText = RECENT_STATUS_CN[item.status] || '';
      const metaText = [time ? time.time.slice(0, 5) : '', stateText].filter(Boolean).join(' · ');
      const main = el('span', 'recent-main');
      main.append(
        el('span', 'recent-command', item.text || '（未命名指令）'),
        el('span', 'recent-meta', metaText)
      );
      const dot = el('span', 'recent-dot');
      dot.dataset.status = item.status || '';
      append(button, dot, main, iconNode('chevronRight'));
      button.addEventListener('click', () => openAudit(item.trace_id));
      return button;
    });
    dom.recentList.replaceChildren.apply(dom.recentList, nodes);
  }


  /* ============================================================
   * 7.5 历史会话（localStorage）
   * ============================================================ */
  function loadHistory() {
    try {
      const raw = window.localStorage.getItem(HISTORY_KEY);
      const list = JSON.parse(raw || '[]');
      return Array.isArray(list) ? list : [];
    } catch (err) {
      return [];
    }
  }

  function saveHistory() {
    try {
      window.localStorage.setItem(HISTORY_KEY, JSON.stringify(state.history.slice(0, MAX_HISTORY)));
    } catch (err) {
      /* 存储不可用时仅保留内存 */
    }
  }

  function historyEntry(id, create) {
    let entry = state.history.find((item) => item && item.id === id);
    if (!entry && create) {
      entry = { id: id, title: '', createdAt: new Date().toISOString(),
                updatedAt: new Date().toISOString(), messages: [] };
      state.history.unshift(entry);
    }
    return entry;
  }

  function recordMessage(role, text) {
    if (state.settings && state.settings.history === false) return;
    const content = String(text || '').trim();
    if (!content) return;
    const entry = historyEntry(state.sessionId, true);
    if (!entry) return;
    entry.messages.push({ role: role, text: content, ts: new Date().toISOString() });
    if (entry.messages.length > MAX_HISTORY_MSGS) {
      entry.messages = entry.messages.slice(-MAX_HISTORY_MSGS);
    }
    if (!entry.title && role === 'user') entry.title = content.slice(0, 20);
    entry.updatedAt = new Date().toISOString();
    saveHistory();
    renderHistory();
  }

  function renderHistory() {
    if (!dom.historySection || !dom.historyList) return;
    const items = state.history.slice()
      .sort((a, b) => String(b.updatedAt || '').localeCompare(String(a.updatedAt || '')))
      .slice(0, 8);
    dom.historySection.hidden = items.length === 0;
    const nodes = items.map((item) => {
      const button = el('button', 'history-item');
      button.type = 'button';
      button.title = item.title || '未命名会话';
      if (item.id === state.sessionId) button.classList.add('is-active');
      const icon = el('span', 'history-icon');
      icon.append(iconNode('history'));
      const copy = el('span', 'history-copy');
      const parts = timestampParts(item.updatedAt);
      copy.append(
        el('span', 'history-title', item.title || '（空会话）'),
        el('span', 'history-meta', (item.messages ? item.messages.length : 0) + ' 条 · ' + (parts.time || '').slice(0, 5))
      );
      button.append(icon, copy);
      button.addEventListener('click', () => switchToSession(item.id));
      return button;
    });
    dom.historyList.replaceChildren.apply(dom.historyList, nodes);
  }

  function buildHistoryAssistant(text, ts) {
    const row = el('article', 'msg assistant');
    const avatar = el('span', 'avatar assistant-avatar');
    avatar.setAttribute('aria-hidden', 'true');
    avatar.append(iconNode('shieldCheck'));
    const body = el('div', 'msg-body');
    const meta = el('div', 'msg-meta');
    meta.append(el('span', 'msg-author', 'AI 助手'));
    const parts = timestampParts(ts);
    if (parts.time) meta.append(el('span', 'meta-sep', ' · '), el('span', 'msg-time', parts.time.slice(0, 5)));
    meta.append(el('span', 'meta-sep', ' · '), el('span', 'history-badge', '历史回放'));
    body.append(meta, el('div', 'history-text', text));
    row.append(avatar, body);
    return row;
  }

  function resetChatView() {
    if (state.currentTurn) closeTurnStream(state.currentTurn, { intentional: true });
    closeStepupModal({ keepContext: false, silent: true, force: true });
    state.confirmContexts.forEach((ctx) => {
      if (ctx.timer) window.clearInterval(ctx.timer);
    });
    state.confirmContexts.clear();
    state.stepupContexts.clear();
    state.busy.clear();
    state.currentTurn = null;
    state.lastTraceId = null;
    state.localLock = false;
    state.sessionPending = null;
    if (dom.chatStream) {
      $$('.msg', dom.chatStream).forEach((node) => {
        if (node !== dom.welcome) node.remove();
      });
      $$('.chat-date', dom.chatStream).forEach((node) => node.remove());
      const date = el('div', 'chat-date');
      date.append(el('span', null, '今天 · 演示会话'));
      dom.chatStream.insertBefore(date, dom.welcome);
    }
  }

  function renderHistoryMessages(entry) {
    if (!entry || !entry.messages || !entry.messages.length) {
      if (dom.welcome) dom.welcome.hidden = false;
      return;
    }
    if (dom.welcome) dom.welcome.hidden = true;
    entry.messages.forEach((message) => {
      if (message.role === 'user') {
        dom.chatStream.append(buildUserMessage(message.text));
      } else {
        dom.chatStream.append(buildHistoryAssistant(message.text, message.ts));
      }
    });
    scrollToBottom(false);
  }

  function switchToSession(id) {
    if (!id || id === state.sessionId) return;
    const entry = historyEntry(id, false);
    if (!entry) return;
    resetChatView();
    state.sessionId = id;
    writeSessionId(state.sessionId);
    renderSessionId();
    renderHistoryMessages(entry);
    renderHistory();
    setSessionState('NORMAL');
    refreshSessionState({ quiet: true });
    closeSidebar();
    updateComposerMeta();
  }
  function renderSessionId() {
    if (!dom.sessionId) return;
    const sid = String(state.sessionId || '');
    dom.sessionId.textContent = sid.length > 14 ? sid.slice(0, 13) + '…' : sid;
    dom.sessionId.title = sid;
  }

  /* ============================================================
   * 8. 输入框
   * ============================================================ */


  /* ============================================================
   * 8.5 语音输入（浏览器 Web Speech API）
   * ============================================================ */
  let voiceRecorder = null;
  let voiceBusy = false;
  let browserRec = null;

  function stopVoice() {
    if (voiceRecorder) {
      try { voiceRecorder.processor.disconnect(); } catch (err) { /* 忽略 */ }
      try { voiceRecorder.source.disconnect(); } catch (err) { /* 忽略 */ }
      try { voiceRecorder.stream.getTracks().forEach((t) => t.stop()); } catch (err) { /* 忽略 */ }
      try { voiceRecorder.ctx.close(); } catch (err) { /* 忽略 */ }
      window.clearTimeout(voiceRecorder.timer);
      voiceRecorder = null;
    }
    state.voiceActive = false;
    if (dom.voiceBtn) {
      dom.voiceBtn.classList.remove('is-listening');
      dom.voiceBtn.setAttribute('aria-pressed', 'false');
    }
    if (browserRec) {
      try { browserRec.stop(); } catch (err) { /* 忽略 */ }
    }
  }

  function writeAscii(view, offset, text) {
    for (let i = 0; i < text.length; i += 1) view.setUint8(offset + i, text.charCodeAt(i));
  }

  function encodeWavBase64(samples, inputRate, outputRate) {
    const rate = outputRate || 16000;
    const ratio = inputRate / rate;
    const length = Math.max(1, Math.round(samples.length / ratio));
    const pcm = new Int16Array(length);
    for (let i = 0; i < length; i += 1) {
      const pos = i * ratio;
      const lo = Math.floor(pos);
      const hi = Math.min(lo + 1, samples.length - 1);
      const value = samples[lo] * (1 - (pos - lo)) + samples[hi] * (pos - lo);
      pcm[i] = Math.max(-1, Math.min(1, value)) * 0x7fff;
    }
    const buffer = new ArrayBuffer(44 + pcm.length * 2);
    const view = new DataView(buffer);
    writeAscii(view, 0, 'RIFF');
    view.setUint32(4, 36 + pcm.length * 2, true);
    writeAscii(view, 8, 'WAVE');
    writeAscii(view, 12, 'fmt ');
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, 1, true);
    view.setUint32(24, rate, true);
    view.setUint32(28, rate * 2, true);
    view.setUint16(32, 2, true);
    view.setUint16(34, 16, true);
    writeAscii(view, 36, 'data');
    view.setUint32(40, pcm.length * 2, true);
    new Int16Array(buffer, 44).set(pcm);
    const bytes = new Uint8Array(buffer);
    let binary = '';
    const chunk = 0x8000;
    for (let i = 0; i < bytes.length; i += chunk) {
      binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
    }
    return window.btoa(binary);
  }

  async function startRecording() {
    if (!navigator.mediaDevices || !window.isSecureContext) {
      showToast('录音需要 HTTPS 或 localhost 才能使用麦克风。', 'error');
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: false, noiseSuppression: true, autoGainControl: true }
      });
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      const ctx = new AudioCtx();
      const source = ctx.createMediaStreamSource(stream);
      const processor = ctx.createScriptProcessor(4096, 1, 1);
      const chunks = [];
      processor.onaudioprocess = (event) => {
        chunks.push(new Float32Array(event.inputBuffer.getChannelData(0)));
      };
      const mute = ctx.createGain();
      mute.gain.value = 0;
      source.connect(processor);
      processor.connect(mute);
      mute.connect(ctx.destination);
      voiceRecorder = { stream: stream, ctx: ctx, source: source, processor: processor,
                        chunks: chunks, rate: ctx.sampleRate, timer: null };
      voiceRecorder.timer = window.setTimeout(() => { stopRecording(); }, 10000);
      state.voiceActive = true;
      dom.voiceBtn.classList.add('is-listening');
      dom.voiceBtn.setAttribute('aria-pressed', 'true');
      showToast('正在录音…说完再点一次结束（最长 10 秒）。', 'info');
    } catch (err) {
      showToast('无法打开麦克风：' + describeError(err), 'error');
    }
  }

  async function stopRecording() {
    if (!voiceRecorder) return;
    const rec = voiceRecorder;
    voiceRecorder = null;
    state.voiceActive = false;
    dom.voiceBtn.classList.remove('is-listening');
    dom.voiceBtn.setAttribute('aria-pressed', 'false');
    window.clearTimeout(rec.timer);
    try { rec.processor.disconnect(); } catch (err) { /* 忽略 */ }
    try { rec.source.disconnect(); } catch (err) { /* 忽略 */ }
    rec.stream.getTracks().forEach((track) => track.stop());
    try { rec.ctx.close(); } catch (err) { /* 忽略 */ }

    const total = rec.chunks.reduce((sum, chunk) => sum + chunk.length, 0);
    if (total < rec.rate * 0.3) {
      showToast('录音太短，请再说一次。', 'error');
      return;
    }
    const merged = new Float32Array(total);
    let offset = 0;
    rec.chunks.forEach((chunk) => { merged.set(chunk, offset); offset += chunk.length; });
    let peak = 0;
    for (let i = 0; i < merged.length; i += 1) {
      const value = Math.abs(merged[i]);
      if (value > peak) peak = value;
    }
    if (peak > 0.0001 && peak < 0.9) {
      const gain = 0.9 / peak;
      for (let i = 0; i < merged.length; i += 1) merged[i] = Math.max(-1, Math.min(1, merged[i] * gain));
    }

    voiceBusy = true;
    updateInteractiveState();
    showToast('正在本机识别…', 'info');
    try {
      const res = await fetchJSON('/v1/asr', {
        method: 'POST',
        body: { audio: encodeWavBase64(merged, rec.rate, 16000) },
        timeout: 30000
      });
      const text = String((res && res.text) || '').trim();
      if (!text) {
        showToast('没有识别到内容，请再说一次。', 'error');
        return;
      }
      const base = (dom.composerInput.value || '').trim();
      dom.composerInput.value = base ? base + ' ' + text : text;
      updateComposerMeta();
      dom.composerInput.focus();
      dom.composerInput.setSelectionRange(dom.composerInput.value.length, dom.composerInput.value.length);
      showToast('已识别：' + text, 'success');
    } catch (err) {
      showToast('离线识别失败：' + describeError(err), 'error');
    } finally {
      voiceBusy = false;
      updateInteractiveState();
    }
  }

  function setupOfflineVoice() {
    state.voiceReady = true;
    dom.voiceBtn.hidden = false;
    dom.voiceBtn.title = '离线语音输入（本机识别）';
    dom.voiceBtn.addEventListener('click', () => {
      if (state.sessionState === 'LOCKED' || state.busy.size > 0 || voiceBusy) return;
      if (state.voiceActive) stopRecording();
      else startRecording();
    });
  }

  function setupBrowserVoice() {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) { dom.voiceBtn.hidden = true; return; }
    state.voiceReady = true;
    const rec = new SR();
    rec.lang = 'zh-CN';
    rec.interimResults = true;
    rec.continuous = false;
    browserRec = rec;
    let baseText = '';
    rec.onstart = () => {
      state.voiceActive = true;
      baseText = (dom.composerInput.value || '').trim();
      dom.voiceBtn.classList.add('is-listening');
      dom.voiceBtn.setAttribute('aria-pressed', 'true');
    };
    rec.onresult = (event) => {
      let finalText = '';
      let interimText = '';
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const result = event.results[i];
        const piece = result[0] ? result[0].transcript : '';
        if (result.isFinal) finalText += piece;
        else interimText += piece;
      }
      const spoken = (finalText || interimText).trim();
      if (!spoken) return;
      dom.composerInput.value = baseText ? baseText + ' ' + spoken : spoken;
      updateComposerMeta();
    };
    rec.onend = () => {
      state.voiceActive = false;
      dom.voiceBtn.classList.remove('is-listening');
      dom.voiceBtn.setAttribute('aria-pressed', 'false');
      updateComposerMeta();
    };
    rec.onerror = (event) => {
      const code = event && event.error;
      if (code !== 'aborted') showToast('浏览器语音识别失败：' + code, 'error');
    };
    dom.voiceBtn.hidden = false;
    dom.voiceBtn.title = '语音输入（浏览器识别）';
    dom.voiceBtn.addEventListener('click', () => {
      if (state.sessionState === 'LOCKED' || state.busy.size > 0) return;
      if (state.voiceActive) { stopVoice(); return; }
      stopVoice();
      try { rec.start(); } catch (err) { showToast('麦克风启动失败。', 'error'); }
    });
  }

  async function initVoice() {
    if (!dom.voiceBtn) return;
    try {
      const health = await fetchJSON('/health', { timeout: 5000 });
      if (health && health.asr && health.asr.available) {
        setupOfflineVoice();
        return;
      }
    } catch (err) { /* 后端不可用时尝试浏览器识别 */ }
    setupBrowserVoice();
  }

  function resizeComposer() {
    if (!dom.composerInput) return;
    dom.composerInput.style.height = 'auto';
    const nextHeight = Math.min(dom.composerInput.scrollHeight, 138);
    dom.composerInput.style.height = Math.max(36, nextHeight) + 'px';
  }

  function updateComposerMeta() {
    const value = dom.composerInput ? dom.composerInput.value : '';
    if (dom.charCount) {
      dom.charCount.hidden = value.length < 400;
      dom.charCount.textContent = value.length + '/500';
    }
    updateInteractiveState();
    resizeComposer();
  }

  function clearComposer() {
    if (!dom.composerInput) return;
    dom.composerInput.value = '';
    dom.composerInput.style.height = 'auto';
    if (dom.charCount) dom.charCount.hidden = true;
    updateInteractiveState();
  }

  /* ============================================================
   * 9. 路由（对话 / 审计）
   * ============================================================ */

  function parseHashRoute() {
    const hash = String(window.location.hash || '');
    const match = /^#\/audit\/([^/?#]+)/.exec(hash);
    if (!match) return { name: 'chat' };
    let traceId = match[1];
    try {
      traceId = decodeURIComponent(traceId);
    } catch (err) {
      /* 保留原始值 */
    }
    return { name: 'audit', traceId: traceId };
  }

  function handleRoute() {
    const route = parseHashRoute();
    state.route = route.name;

    if (route.name === 'audit') {
      if (state.activeStepup) closeStepupModal({ keepContext: true, silent: true });
      dom.chatView.hidden = true;
      dom.auditView.hidden = false;
      dom.auditTraceId.textContent = route.traceId || '—';
      loadAudit(route.traceId);
      dom.auditView.scrollTop = 0;
    } else {
      dom.auditView.hidden = true;
      dom.chatView.hidden = false;
      document.title = APP_TITLE;
      if (state.auditAbort) {
        state.auditAbort.abort();
        state.auditAbort = null;
      }
    }
  }

  function openAudit(traceId) {
    const id = String(traceId || '').trim();
    if (!id) {
      showToast('暂无审计记录，请先完成一次对话。', 'info');
      return;
    }
    window.location.hash = '#/audit/' + encodeURIComponent(id);
  }

  function openLatestAudit() {
    const target =
      state.lastTraceId ||
      (state.recent.length ? state.recent[0].trace_id : '') ||
      '';
    if (!target) {
      showToast('暂无审计记录，请先完成一次对话。', 'info');
      return;
    }
    openAudit(target);
  }


  async function openAuditIndex() {
    if (!dom.auditIndex) return;
    dom.auditIndex.hidden = false;
    dom.auditIndexBackdrop.hidden = false;
    dom.auditIndexCount.textContent = '加载中…';
    dom.auditIndexList.replaceChildren(el('div', 'audit-index-empty', '正在读取审计记录…'));
    try {
      const data = await fetchJSON('/v1/audit/traces?limit=300', { timeout: 10000 });
      renderAuditIndex(data);
    } catch (err) {
      dom.auditIndexCount.textContent = '读取失败';
      dom.auditIndexList.replaceChildren(
        el('div', 'audit-index-empty', '无法读取审计记录：' + describeError(err))
      );
    }
  }

  function closeAuditIndex() {
    if (dom.auditIndex) dom.auditIndex.hidden = true;
    if (dom.auditIndexBackdrop) dom.auditIndexBackdrop.hidden = true;
  }

  function renderAuditIndex(data) {
    if (!dom.auditIndexList) return;
    const rows = (data && data.traces) || [];
    if (dom.auditIndexCount) {
      dom.auditIndexCount.textContent =
        '共 ' + (data.total || rows.length) + ' 条记录' +
        (rows.length < (data.total || 0) ? '，显示最近 ' + rows.length + ' 条' : '');
    }
    if (!rows.length) {
      dom.auditIndexList.replaceChildren(el('div', 'audit-index-empty', '还没有审计记录。'));
      return;
    }
    const nodes = rows.map((item) => {
      const row = el('button', 'audit-row');
      row.type = 'button';
      const main = el('div', 'audit-row-main');
      main.append(
        el('strong', null, item.summary || item.trace_id),
        el('span', null,
          String(item.started_at || '').replace('T', ' ').slice(0, 16) +
          ' · ' + item.trace_id)
      );
      row.append(main, el('span', 'audit-row-count', (item.events || 0) + ' 事件'));
      row.addEventListener('click', () => {
        closeAuditIndex();
        window.location.hash = '#/audit/' + encodeURIComponent(item.trace_id);
      });
      return row;
    });
    dom.auditIndexList.replaceChildren.apply(dom.auditIndexList, nodes);
    if (dom.auditIndexSearch) dom.auditIndexSearch.value = '';
  }

  /* ============================================================
   * 10. 事件绑定与启动
   * ============================================================ */

  function bindDrawer() {
    if (dom.mobileMenuBtn) dom.mobileMenuBtn.addEventListener('click', openSidebar);
    if (dom.sidebarClose) dom.sidebarClose.addEventListener('click', closeSidebar);
    if (dom.sidebarBackdrop) dom.sidebarBackdrop.addEventListener('click', closeSidebar);
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') closeSidebar();
    });
    window.addEventListener('resize', () => {
      if (!isMobileViewport()) closeSidebar();
    });
  }
  function bindCoreEvents() {
    dom.composerForm.addEventListener('submit', (event) => {
      event.preventDefault();
      if (state.busy.size > 0 || state.sessionState === 'LOCKED') return;
      sendText(dom.composerInput.value);
    });

    $$('.example-chip').forEach((button) => {
      button.addEventListener('click', () => {
        onQuickCommand(button.dataset.example || button.textContent || '');
      });
    });

    dom.composerInput.addEventListener('input', updateComposerMeta);
    dom.composerInput.addEventListener('keydown', (event) => {
      if (event.key !== 'Enter' || event.shiftKey) return;
      if (event.isComposing || event.keyCode === 229) return;
      const enterToSend = !state.settings || state.settings.enter !== false;
      if (!enterToSend && !(event.ctrlKey || event.metaKey)) return;
      event.preventDefault();
      if (state.busy.size > 0 || state.sessionState === 'LOCKED') return;
      if (!dom.composerInput.value.trim()) return;
      sendText(dom.composerInput.value);
    });

    dom.newSessionBtn.addEventListener('click', startNewSession);
    dom.sessionBannerAction.addEventListener('click', startNewSession);
    dom.copySessionBtn.addEventListener('click', () => copySessionId());
    dom.auditShortcut.addEventListener('click', openLatestAudit);
    dom.mobileAuditBtn.addEventListener('click', openLatestAudit);
    if (dom.auditAllBtn) dom.auditAllBtn.addEventListener('click', openAuditIndex);
    if (dom.auditAllSide) dom.auditAllSide.addEventListener('click', openAuditIndex);
    if (dom.auditIndexClose) dom.auditIndexClose.addEventListener('click', closeAuditIndex);
    if (dom.auditIndexBackdrop) dom.auditIndexBackdrop.addEventListener('click', closeAuditIndex);
    if (dom.auditIndexSearch) {
      dom.auditIndexSearch.addEventListener('input', () => {
        const q = dom.auditIndexSearch.value.trim().toLowerCase();
        dom.auditIndexList.querySelectorAll('.audit-row').forEach((row) => {
          row.hidden = q && !row.textContent.toLowerCase().includes(q);
        });
      });
    }
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && dom.auditIndex && !dom.auditIndex.hidden) closeAuditIndex();
    });
    bindDrawer();
    dom.auditBackBtn.addEventListener('click', () => {
      window.location.hash = '#/';
    });
    dom.auditCopyBtn.addEventListener('click', () => {
      if (!state.auditTraceId) return;
      copyText(state.auditTraceId).then((ok) => {
        showToast(ok ? 'trace_id 已复制' : '复制失败，请手动选择。', ok ? 'success' : 'error');
      });
    });
    dom.auditDownloadBtn.addEventListener('click', downloadAuditJson);
    dom.connectionRetry.addEventListener('click', retryConnection);

    dom.stepupClose.addEventListener('click', () => closeStepupModal());
    dom.stepupLater.addEventListener('click', () => closeStepupModal());
    dom.stepupBackdrop.addEventListener('click', () => closeStepupModal());
    dom.stepupForm.addEventListener('submit', (event) => {
      event.preventDefault();
      submitStepup();
    });

    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && !dom.stepupModal.hidden) {
        closeStepupModal();
      }
    });

    dom.stepupInputs.forEach((input, index) => {
      input.addEventListener('input', () => handleStepupInput(index));
      input.addEventListener('keydown', (event) => handleStepupKeydown(event, index));
      input.addEventListener('paste', handleStepupPaste);
      input.addEventListener('focus', () => input.select());
    });

    window.addEventListener('hashchange', handleRoute);
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState !== 'visible') return;
      if (state.connected) refreshSessionState({ quiet: true });
      else checkHealth();
    });
  }

  function startStatePolling() {
    if (state.statePollTimer) window.clearInterval(state.statePollTimer);
    state.statePollTimer = window.setInterval(() => {
      if (document.visibilityState !== 'visible' || state.route !== 'chat') return;
      if (state.connected) refreshSessionState({ quiet: true });
      else checkHealth();
    }, 30000);
  }

  function copySessionId() {
    copyText(state.sessionId).then((ok) => {
      showToast(ok ? '会话标识已复制' : '复制失败，请手动选择。', ok ? 'success' : 'error');
    });
  }

  async function retryConnection() {
    dom.connectionRetry.disabled = true;
    const original = dom.connectionRetry.textContent;
    dom.connectionRetry.textContent = '重试中…';
    const ok = await checkHealth();
    if (ok) {
      await refreshSessionState({ quiet: true });
      showToast('银行服务连接已恢复。', 'success');
    } else {
      showToast('仍无法连接银行服务，请检查后端是否已启动。', 'error');
    }
    dom.connectionRetry.disabled = false;
    dom.connectionRetry.textContent = original;
  }

  function startNewSession() {
    if (state.currentTurn) closeTurnStream(state.currentTurn, { intentional: true });
    closeStepupModal({ keepContext: false, silent: true, force: true });
    state.confirmContexts.forEach((ctx) => {
      if (ctx.timer) window.clearInterval(ctx.timer);
    });
    state.confirmContexts.clear();
    state.stepupContexts.clear();
    state.busy.clear();
    state.sessionId = createId('sess_');
    writeSessionId(state.sessionId);
    state.sessionPending = null;
    state.currentTurn = null;
    state.lastTraceId = null;
    state.localLock = false;

    if (dom.chatStream) {
      $$('.msg', dom.chatStream).forEach((node) => {
        if (node !== dom.welcome) node.remove();
      });
      $$('.chat-date', dom.chatStream).forEach((node) => node.remove());
      const date = el('div', 'chat-date');
      date.append(el('span', null, '今天 · 演示会话'));
      dom.chatStream.insertBefore(date, dom.welcome);
    }
    dom.welcome.hidden = false;
    dom.welcomeTime.textContent = clockNow();
    renderSessionId();
    renderHistory();
    setSessionState('NORMAL');
    clearComposer();
    updateComposerMeta();
    dom.connectionBanner.hidden = state.connected;
    if (state.route === 'audit') window.location.hash = '#/';
    showToast('已开启新会话', 'success');
    dom.chatScroll.scrollTop = 0;
    window.setTimeout(() => {
      if (window.innerWidth > 920) dom.composerInput.focus();
    }, 60);
  }



  /* ============================================================
   * 7.6 设置页
   * ============================================================ */
  function loadSettings() {
    try {
      const raw = window.localStorage.getItem(SETTINGS_KEY);
      const saved = raw ? JSON.parse(raw) : {};
      return Object.assign({}, DEFAULT_SETTINGS, saved && typeof saved === 'object' ? saved : {});
    } catch (err) {
      return Object.assign({}, DEFAULT_SETTINGS);
    }
  }

  function saveSettings() {
    try {
      window.localStorage.setItem(SETTINGS_KEY, JSON.stringify(state.settings));
    } catch (err) {
      /* 忽略存储失败 */
    }
  }

  function applySettings() {
    if (!state.settings) state.settings = loadSettings();
    const s = state.settings;
    document.body.classList.toggle('font-large', s.font === 'large');
    document.body.classList.toggle('density-compact', s.density === 'compact');
    if (dom.setFont) dom.setFont.value = s.font;
    if (dom.setDensity) dom.setDensity.value = s.density;
    if (dom.setDetails) dom.setDetails.checked = !!s.details;
    if (dom.setVoice) dom.setVoice.checked = s.voice !== false;
    if (dom.setEnter) dom.setEnter.checked = s.enter !== false;
    if (dom.setHistory) dom.setHistory.checked = s.history !== false;
    if (dom.voiceBtn) {
      if (!s.voice) dom.voiceBtn.hidden = true;
      else if (state.voiceReady) dom.voiceBtn.hidden = false;
    }
  }

  function showSettingsSection(name) {
    if (!dom.settingsView) return;
    $$('[data-panel]', dom.settingsView).forEach((panel) => {
      panel.hidden = panel.dataset.panel !== name;
    });
    $$('[data-section]', dom.settingsView).forEach((button) => {
      button.classList.toggle('is-active', button.dataset.section === name);
    });
    if (name === 'ops') { refreshOps(); refreshKnowledge(); refreshTickets(); refreshFlags(); }
    if (name === 'identity') refreshIdentity();
    if (name === 'chat') refreshBrainMode();
  }

  async function refreshVoiceStatus() {
    if (!dom.voiceEngine) return;
    try {
      const health = await fetchJSON('/health', { timeout: 6000 });
      const asr = (health && health.asr) || {};
      const engine = asr.available && asr.engine === 'vosk-small-cn' ? 'Vosk 离线模型'
        : asr.available ? '系统离线识别' : '未启用离线识别';
      dom.voiceEngine.textContent = engine;
      dom.voiceDetail.textContent = asr.available
        ? '本机运行，不联网、不上传音频'
        : '运行 scripts\\setup_asr.ps1 可启用，或回退浏览器识别';
      if (dom.aboutAsr) dom.aboutAsr.textContent = engine;
      if (dom.aboutVersion) dom.aboutVersion.textContent = 'v' + ((health && health.version) || '1.0.0');
      if (dom.aboutMode) {
        dom.aboutMode.textContent = health && health.brain && health.brain.enabled
          ? '大模型（可选）' : '规则模式（无需 Key）';
      }
      if (dom.aboutPort) dom.aboutPort.textContent = window.location.origin;
    } catch (err) {
      dom.voiceEngine.textContent = '服务未连接';
      dom.voiceDetail.textContent = '启动后端后可检测';
    }
  }

  function openSecurity() {
    if (!dom.settingsView) return;
    dom.settingsView.hidden = false;
    closeSidebar();
    showSettingsSection('security');
    if (dom.settingsView.scrollIntoView) dom.settingsView.scrollIntoView({ block: 'start' });
  }

  function openSettings() {
    if (!dom.settingsView) return;
    dom.settingsView.hidden = false;
    closeSidebar();
    showSettingsSection('general');
    refreshVoiceStatus();
  }

  function closeSettings() {
    if (dom.settingsView) dom.settingsView.hidden = true;
  }


  async function refreshOps() {
    if (!dom.opsMetrics) return;
    dom.opsMetrics.replaceChildren(el('div', 'ops-empty', '正在读取运营指标…'));
    try {
      const data = await fetchJSON('/v1/metrics', { timeout: 8000 });
      const cards = [
        ['会话数', data.sessions], ['审计链路', data.traces], ['审计事件', data.events],
        ['工具调用', data.tool_calls], ['好评', data.feedback_up], ['差评', data.feedback_down],
        ['知识有效', (data.knowledge || {}).active], ['知识过期', (data.knowledge || {}).expired]
      ];
      dom.opsMetrics.replaceChildren.apply(dom.opsMetrics, cards.map(([label, value]) => {
        const card = el('div', 'ops-card');
        card.append(el('strong', null, String(value == null ? 0 : value)), el('span', null, label));
        return card;
      }));
      if (!dom.opsBadcases) return;
      const badcases = data.badcases || [];
      if (!badcases.length) {
        dom.opsBadcases.replaceChildren(el('div', 'ops-empty', '暂无差评。点回复下方的「没帮上」即可归集。'));
        return;
      }
      dom.opsBadcases.replaceChildren.apply(dom.opsBadcases, badcases.map((item) => {
        const row = el('div', 'ops-badcase');
        row.append(el('span', null, (item.reason || '未填写原因') + ' · ' + (item.trace_id || '—')));
        const btn = el('button', null, '看审计');
        btn.type = 'button';
        btn.addEventListener('click', () => {
          if (item.trace_id) window.location.hash = '#/audit/' + encodeURIComponent(item.trace_id);
        });
        row.append(btn);
        return row;
      }));
    } catch (err) {
      dom.opsMetrics.replaceChildren(el('div', 'ops-empty', '读取失败：' + describeError(err)));
    }
  }


  const SCOPE_CN = { query: '仅查询', small: '查询+小额', all: '全部' };

  async function refreshIdentity() {
    if (!dom.identityGrid) return;
    try {
      const data = await fetchJSON('/v1/identity', { timeout: 8000 });
      const cards = [
        ['实名认证', data.realname_status === 'verified' ? '已认证' : '待认证'],
        ['KYC 等级', data.kyc_level || '—'],
        ['客户号', data.customer_no || '—'],
        ['设备绑定', data.device_bound ? '已绑定' : '未绑定']
      ];
      dom.identityGrid.replaceChildren.apply(dom.identityGrid, cards.map(([label, value]) => {
        const card = el('div', 'identity-card');
        card.append(el('strong', null, String(value)), el('span', null, label));
        return card;
      }));
      if (dom.identityDevice) {
        dom.identityDevice.textContent = data.device_bound
          ? '已绑定 ····' + (data.device_tail || '') + '（本机）'
          : '尚未绑定，发送一条消息或点右侧按钮完成绑定';
      }
      const auths = data.authorizations || [];
      if (dom.authList) {
        if (!auths.length) {
          dom.authList.replaceChildren(el('div', 'ops-empty', '还没有代办授权。'));
        } else {
          dom.authList.replaceChildren.apply(dom.authList, auths.map((item) => {
            const row = el('div', 'identity-auth');
            row.append(el('span', null,
              item.delegate_name + ' · ' + (item.relationship || '—') + ' · ' +
              (SCOPE_CN[item.scope] || item.scope) + ' · 至 ' +
              String(item.expires_at || '').slice(0, 10) + ' · ' + item.status));
            if (item.status === 'active') {
              const btn = el('button', null, '撤销');
              btn.type = 'button';
              btn.addEventListener('click', async () => {
                try {
                  await fetchJSON('/v1/identity/revoke', {
                    method: 'POST', body: { id: item.id }, timeout: 6000
                  });
                  showToast('已撤销授权。', 'success');
                  refreshIdentity();
                } catch (err) {
                  showToast('撤销失败：' + describeError(err), 'error');
                }
              });
              row.append(btn);
            }
            return row;
          }));
        }
      }
    } catch (err) {
      if (dom.identityGrid) {
        dom.identityGrid.replaceChildren(el('div', 'ops-empty', '读取失败：' + describeError(err)));
      }
    }
  }

  async function bindDevice() {
    try {
      await fetchJSON('/v1/identity/bind-device', {
        method: 'POST',
        body: { session_id: state.sessionId, device_id: getDeviceId() },
        timeout: 8000
      });
      showToast('本机设备已绑定。', 'success');
      refreshIdentity();
    } catch (err) {
      showToast('绑定失败：' + describeError(err), 'error');
    }
  }

  async function addAuthorization() {
    const name = (dom.authName && dom.authName.value || '').trim();
    if (!name) {
      showToast('请先填代办人姓名。', 'error');
      return;
    }
    try {
      await fetchJSON('/v1/identity/authorize', {
        method: 'POST',
        body: {
          delegate_name: name,
          relationship: dom.authRelation ? dom.authRelation.value : '其他',
          scope: dom.authScope ? dom.authScope.value : 'query',
          days: Number(dom.authDays ? dom.authDays.value : 30)
        },
        timeout: 8000
      });
      if (dom.authName) dom.authName.value = '';
      showToast('授权已创建。', 'success');
      refreshIdentity();
    } catch (err) {
      showToast('创建失败：' + describeError(err), 'error');
    }
  }


  const KB_STATUS_CN = {
    published: '已发布', draft: '草稿', approved: '已审批', retired: '已下架', expired: '已过期'
  };

  async function refreshKnowledge() {
    if (!dom.opsKnowledge) return;
    try {
      const data = await fetchJSON('/v1/knowledge', { timeout: 8000 });
      const rows = data.entries || [];
      const options = [];
      const seen = new Set();
      dom.opsKnowledge.replaceChildren.apply(dom.opsKnowledge, rows.map((item) => {
        if (!seen.has(item.entry_id) && item.id) {
          seen.add(item.entry_id);
          const option = document.createElement('option');
          option.value = item.entry_id;
          option.textContent = item.title;
          options.push(option);
        }
        const row = el('div', 'ops-badcase');
        row.append(el('span', null,
          item.title + ' · ' + item.version + ' · ' + (KB_STATUS_CN[item.status] || item.status)));
        const actions = el('span');
        if (item.status === 'draft') {
          const btn = el('button', null, '审批');
          btn.type = 'button';
          btn.addEventListener('click', () => kbAction('/v1/knowledge/approve', item.id));
          actions.append(btn);
        } else if (item.status === 'approved') {
          const btn = el('button', null, '发布');
          btn.type = 'button';
          btn.addEventListener('click', () => kbAction('/v1/knowledge/publish', item.id));
          actions.append(btn);
        }
        row.append(actions);
        return row;
      }));
      if (dom.kbEntry) dom.kbEntry.replaceChildren.apply(dom.kbEntry, options);
    } catch (err) {
      dom.opsKnowledge.replaceChildren(el('div', 'ops-empty', '读取失败：' + describeError(err)));
    }
  }

  async function kbAction(path, id) {
    try {
      await fetchJSON(path, { method: 'POST', body: { id: id }, timeout: 8000 });
      showToast('操作成功。', 'success');
      refreshKnowledge();
    } catch (err) {
      showToast('操作失败：' + describeError(err), 'error');
    }
  }

  async function createKbDraft() {
    const entryId = dom.kbEntry && dom.kbEntry.value;
    if (!entryId) {
      showToast('请先选择知识条目。', 'error');
      return;
    }
    try {
      await fetchJSON('/v1/knowledge/draft', {
        method: 'POST',
        body: { entry_id: entryId, content: (dom.kbContent && dom.kbContent.value) || '' },
        timeout: 8000
      });
      if (dom.kbContent) dom.kbContent.value = '';
      showToast('草稿已创建，等待审批。', 'success');
      refreshKnowledge();
    } catch (err) {
      showToast('创建失败：' + describeError(err), 'error');
    }
  }


  const FLAG_DEFAULTS = [
    ['semantic_reply', '语义理解灰度'], ['knowledge_vector', '知识向量检索灰度']
  ];

  async function refreshTickets() {
    if (!dom.opsTickets) return;
    try {
      const data = await fetchJSON('/v1/tickets', { timeout: 8000 });
      const rows = data.tickets || [];
      if (!rows.length) {
        dom.opsTickets.replaceChildren(el('div', 'ops-empty', '暂无工单。转人工后自动生成。'));
        return;
      }
      dom.opsTickets.replaceChildren.apply(dom.opsTickets, rows.map((item) => {
        const row = el('div', 'ops-badcase');
        row.append(el('span', null, item.ticket_no + ' · ' + item.status + ' · ' + (item.summary || '')));
        const actions = el('span');
        if (item.status === 'open') {
          const btn = el('button', null, '受理');
          btn.type = 'button';
          btn.addEventListener('click', () => updateTicket(item.ticket_no, 'in_progress'));
          actions.append(btn);
        }
        if (item.status !== 'resolved') {
          const btn = el('button', null, '解决');
          btn.type = 'button';
          btn.addEventListener('click', () => updateTicket(item.ticket_no, 'resolved'));
          actions.append(btn);
        }
        row.append(actions);
        return row;
      }));
    } catch (err) {
      dom.opsTickets.replaceChildren(el('div', 'ops-empty', '读取失败：' + describeError(err)));
    }
  }

  async function updateTicket(ticketNo, status) {
    try {
      await fetchJSON('/v1/tickets/update', {
        method: 'POST', body: { ticket_no: ticketNo, status: status }, timeout: 6000
      });
      showToast('工单状态已更新。', 'success');
      refreshTickets();
    } catch (err) {
      showToast('更新失败：' + describeError(err), 'error');
    }
  }

  async function refreshFlags() {
    if (!dom.opsFlags) return;
    try {
      const data = await fetchJSON('/v1/flags', { timeout: 8000 });
      const map = {};
      (data.flags || []).forEach((item) => { map[item.name] = item; });
      dom.opsFlags.replaceChildren.apply(dom.opsFlags, FLAG_DEFAULTS.map(([name, label]) => {
        const item = map[name] || { name: name, enabled: 0, rollout: 0 };
        const row = el('div', 'ops-badcase');
        row.append(el('span', null, label + ' · 灰度 ' + item.rollout + '%'));
        const btn = el('button', null, item.enabled ? '关闭' : '开启');
        btn.type = 'button';
        btn.addEventListener('click', () => toggleFlag(name, item));
        row.append(btn);
        return row;
      }));
      const ab = await fetchJSON('/v1/ab/assign?session_id=' + encodeURIComponent(state.sessionId), { timeout: 6000 });
      if (dom.opsAb) {
        dom.opsAb.textContent = 'A/B：' + ab.experiment + ' · 当前会话分桶 ' + ab.bucket +
          '（按 session_id 哈希，稳定分组）';
      }
    } catch (err) {
      if (dom.opsFlags) dom.opsFlags.replaceChildren(el('div', 'ops-empty', '读取失败：' + describeError(err)));
    }
  }

  async function toggleFlag(name, item) {
    try {
      await fetchJSON('/v1/flags', {
        method: 'POST',
        body: { name: name, enabled: !item.enabled, rollout: item.enabled ? 0 : 100 },
        timeout: 6000
      });
      refreshFlags();
    } catch (err) {
      showToast('灰度操作失败：' + describeError(err), 'error');
    }
  }


  async function runSecurityCheck(caseName) {
    if (!dom.securityResult) return;
    dom.securityResult.hidden = false;
    dom.securityResult.classList.remove('is-pass', 'is-fail');
    dom.securityResult.textContent = '正在隔离临时库运行…';
    try {
      const data = await fetchJSON('/v1/security/selftest', {
        method: 'POST', body: { case: caseName }, timeout: 20000
      });
      const lines = [];
      lines.push((data.passed ? 'PASS' : 'FAIL') + '  ' + (data.expected ? '期望 ' + data.expected : ''));
      if (data.actual) lines.push('实际：' + data.actual);
      (data.steps || []).forEach((step, index) => lines.push((index + 1) + '. ' + step));
      lines.push('临时库：' + (data.temp_db ? '已隔离，运行后删除' : '—'));
      dom.securityResult.textContent = lines.join('\n');
      dom.securityResult.classList.add(data.passed ? 'is-pass' : 'is-fail');
    } catch (err) {
      dom.securityResult.textContent = '运行失败：' + describeError(err);
      dom.securityResult.classList.add('is-fail');
    }
  }


  async function saveLlmKey() {
    const key = String((dom.llmKeyInput && dom.llmKeyInput.value) || '').trim();
    if (!key) {
      showToast('请先填写 API Key。', 'error');
      return;
    }
    try {
      const res = await fetchJSON('/v1/llm/config', {
        method: 'POST', body: { api_key: key, enabled: true }, timeout: 10000
      });
      if (dom.llmKeyInput) dom.llmKeyInput.value = '';
      await refreshBrainMode();
      showToast('Key 已保存，已切换到大模型模式；失败会自动回退规则。', 'success');
      return res;
    } catch (err) {
      showToast('保存失败：' + describeError(err), 'error');
    }
  }

  async function clearLlmKey() {
    try {
      await fetchJSON('/v1/llm/config', { method: 'POST', body: { clear: true }, timeout: 10000 });
      await refreshBrainMode();
      showToast('已清除本机 Key，回到规则模式。', 'info');
    } catch (err) {
      showToast('清除失败：' + describeError(err), 'error');
    }
  }

  async function setBrainMode(enabled) {
    try {
      await fetchJSON('/v1/llm/config', {
        method: 'POST', body: { enabled: Boolean(enabled) }, timeout: 10000
      });
      await refreshBrainMode();
      showToast('已切换。安全边界不变，仅理解层变化。', 'info');
    } catch (err) {
      showToast('切换失败：' + describeError(err), 'error');
      await refreshBrainMode();
    }
  }

  async function refreshBrainMode() {
    if (!dom.brainMode && !dom.brainPill) return;
    try {
      const health = await fetchJSON('/health', { timeout: 6000 });
      const brain = (health && health.brain) || {};
      const enabled = Boolean(brain.enabled);
      const hasKey = Boolean(brain.has_key);
      if (dom.brainPill) {
        dom.brainPill.classList.toggle('is-llm', enabled);
        dom.brainPill.disabled = !hasKey;
        dom.brainPill.title = hasKey
          ? '切换理解模式；安全边界不变'
          : '未配置模型 Key，当前只能用规则模式';
        if (dom.brainPillLabel) dom.brainPillLabel.textContent = enabled ? '大模型模式' : '规则模式';
      }
      state.brainMode = enabled ? 'llm' : 'rules';
      if (dom.llmKeyNote) {
        dom.llmKeyNote.textContent = hasKey
          ? '已配置（' + String(brain.masked || '****') + '）；Key 只存本机，不写入审计'
          : '未配置 Key；Key 只保存在本机，不回显、不写入审计';
      }
      if (!dom.brainMode) return;
      dom.brainMode.disabled = !hasKey;
      if (brain.enabled) {
        dom.brainMode.value = 'llm';
        if (dom.brainModeNote) dom.brainModeNote.textContent = '大模型模式（安全边界不变，仅替换理解层）';
      } else {
        dom.brainMode.value = 'rules';
        if (dom.brainModeNote) {
          dom.brainModeNote.textContent = '规则模式（默认，无需 Key）；大模型模式需在 backend\\.env 配置 Key 后重启';
        }
      }
    } catch (err) {
      /* 保持默认 */
    }
  }


  async function runDemoReel() {
    if (!dom.demoScenes) return;
    dom.demoScenes.replaceChildren(el('div', 'ops-empty', '正在隔离库生成五幕…'));
    try {
      const data = await fetchJSON('/v1/demo/reel', { timeout: 30000 });
      const scenes = data.scenes || [];
      dom.demoScenes.replaceChildren.apply(dom.demoScenes, scenes.map((item) => {
        const card = el('div', 'ops-badcase');
        const copy = el('div');
        copy.append(
          el('strong', null, item.scene),
          el('div', null, '你：' + item.user),
          el('div', null, 'AI：' + String(item.reply || '').slice(0, 160)),
          el('span', null, item.status + ' · ' + (item.trace_id || ''))
        );
        const link = el('button', null, '看审计');
        link.type = 'button';
        link.addEventListener('click', () => {
          if (item.trace_id) window.location.hash = '#/audit/' + encodeURIComponent(item.trace_id);
        });
        card.append(copy, link);
        return card;
      }));
    } catch (err) {
      dom.demoScenes.replaceChildren(el('div', 'ops-empty', '生成失败：' + describeError(err)));
    }
  }

  function bindSettings() {
    if (dom.sideSecurity) dom.sideSecurity.addEventListener('click', openSecurity);
    if (dom.welcomeSecurity) dom.welcomeSecurity.addEventListener('click', openSecurity);
    if (dom.brainPill) {
      dom.brainPill.addEventListener('click', () => {
        if (dom.brainPill.disabled) {
          showToast('未配置模型 Key，当前只能用规则模式。', 'info');
          return;
        }
        setBrainMode(!dom.brainPill.classList.contains('is-llm'));
      });
    }
    if (dom.llmKeySave) dom.llmKeySave.addEventListener('click', saveLlmKey);
    if (dom.llmKeyClear) dom.llmKeyClear.addEventListener('click', clearLlmKey);
    if (dom.brainMode) {
      dom.brainMode.addEventListener('change', () => {
        if (!dom.brainMode.value) return;
        setBrainMode(dom.brainMode.value === 'llm');
      });
    }
    if (dom.settingsShortcut) dom.settingsShortcut.addEventListener('click', openSettings);
    if (dom.mobileSettingsBtn) dom.mobileSettingsBtn.addEventListener('click', openSettings);
    if (dom.settingsClose) dom.settingsClose.addEventListener('click', closeSettings);
    if (dom.settingsNav) {
      dom.settingsNav.querySelectorAll('[data-section]').forEach((button) => {
        button.addEventListener('click', () => showSettingsSection(button.dataset.section));
      });
    }
    const update = (key, value) => {
      if (!state.settings) state.settings = loadSettings();
      state.settings[key] = value;
      saveSettings();
      applySettings();
    };
    if (dom.setFont) dom.setFont.addEventListener('change', () => update('font', dom.setFont.value));
    if (dom.setDensity) dom.setDensity.addEventListener('change', () => update('density', dom.setDensity.value));
    if (dom.setDetails) dom.setDetails.addEventListener('change', () => update('details', dom.setDetails.checked));
    if (dom.setEnter) dom.setEnter.addEventListener('change', () => update('enter', dom.setEnter.checked));
    if (dom.setHistory) dom.setHistory.addEventListener('change', () => update('history', dom.setHistory.checked));
    if (dom.setVoice) {
      dom.setVoice.addEventListener('change', () => {
        update('voice', dom.setVoice.checked);
        if (!dom.setVoice.checked) stopVoice();
      });
    }
    if (dom.voiceRetest) dom.voiceRetest.addEventListener('click', refreshVoiceStatus);
    if (dom.opsRefresh) dom.opsRefresh.addEventListener('click', () => { refreshOps(); refreshTickets(); refreshFlags(); });
    if (dom.kbDraft) dom.kbDraft.addEventListener('click', createKbDraft);
    if (dom.demoRun) dom.demoRun.addEventListener('click', runDemoReel);
    $$('[data-security]').forEach((button) => {
      button.addEventListener('click', () => runSecurityCheck(button.dataset.security));
    });
    if (dom.identityBind) dom.identityBind.addEventListener('click', bindDevice);
    if (dom.authAdd) dom.authAdd.addEventListener('click', addAuthorization);
    if (dom.exportTransactions) {
      dom.exportTransactions.addEventListener('click', () => openExportDialog());
    }
    if (dom.exportClose) dom.exportClose.addEventListener('click', closeExportDialog);
    if (dom.exportBackdrop) dom.exportBackdrop.addEventListener('click', closeExportDialog);
    if (dom.exportDownload) dom.exportDownload.addEventListener('click', downloadExport);
    if (dom.clearHistory) {
      dom.clearHistory.addEventListener('click', () => {
        if (!window.confirm('确定清除本机保存的所有历史会话？此操作不可恢复。')) return;
        try { window.localStorage.removeItem(HISTORY_KEY); } catch (err) { /* 忽略 */ }
        state.history = [];
        renderHistory();
        showToast('已清除本机会话历史。', 'success');
      });
    }
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && dom.settingsView && !dom.settingsView.hidden) closeSettings();
    });
  }


  function initialize() {
    state.sessionId = readSessionIdOrCreate();
    state.settings = loadSettings();
    state.history = loadHistory();
    state.recent = loadRecent();
    renderSessionId();
    renderRecent();
    renderHistory();
    buildQuickCommands();
    bindTools();
    loadOverview();
    dom.welcomeTime.textContent = clockNow();
    setSessionState('NORMAL');
    updateComposerMeta();
    bindCoreEvents();
    bindSettings();
    initVoice();
    applySettings();
    refreshBrainMode();
    handleRoute();
    startStatePolling();

    const restored = historyEntry(state.sessionId, false);
    if (restored && restored.messages && restored.messages.length) {
      renderHistoryMessages(restored);
    }

    checkHealth().then((ok) => {
      refreshSessionState({ quiet: true });
      if (!ok && state.route === 'chat') {
        showConnectionBanner('银行服务暂时无法连接', '界面可正常浏览；请启动后端后点击「重试连接」。');
      }
    });
  }

  /* ============================================================
   * 11. 发送消息 / 会话回合
   * ============================================================ */

  async function sendText(rawText) {
    const text = String(rawText || '').trim();
    if (!text) return;
    if (state.sessionState === 'LOCKED') {
      showToast('会话已锁定，请先开启新会话。', 'error');
      return;
    }
    if (state.busy.size > 0) {
      showToast('请等待当前操作完成后再发送。', 'info');
      return;
    }

    if (dom.welcome) dom.welcome.hidden = true;
    stopVoice();

    const userNode = buildUserMessage(text);
    dom.chatStream.append(userNode);
    recordMessage('user', text);
    scrollToBottom(true);
    clearComposer();

    const turn = createAssistantTurn();
    turn.userText = text;
    state.lastUserText = text;
    state.currentTurn = turn;
    showTyping(turn);
    markBusy('send');

    let response;
    try {
      response = await fetchJSON('/v1/chat', {
        method: 'POST',
        body: { session_id: state.sessionId, text: text, brain: state.brainMode || 'rules' },
        timeout: 120000
      });
    } catch (err) {
      clearBusy('send');
      removeTyping(turn);
      renderChatError(turn, err.code || 'E_INTERNAL', describeError(err));
      if ((err.code === 'E_NETWORK' || err.code === 'E_TIMEOUT') && !dom.composerInput.value.trim()) {
        dom.composerInput.value = text;
        updateComposerMeta();
      }
      reportTransportError(err);
      refreshSessionState({ quiet: true });
      return;
    }

    clearBusy('send');

    if (!response || !response.trace_id) {
      removeTyping(turn);
      renderChatError(turn, 'E_INTERNAL', '服务未返回 trace_id，无法建立事件流。');
      return;
    }

    turn.traceId = String(response.trace_id);
    turn.status = response.status || '';
    turn.postReply = response.reply || '';
    recordMessage('assistant', response.reply);
    turn.postStatus = response.status || '';
    turn.expected = Array.isArray(response.events) ? response.events.length : 0;
    turn.fallbackEvents = Array.isArray(response.events) ? response.events.slice() : [];
    state.lastTraceId = turn.traceId;

    addRecent(turn.traceId, text, response.status);
    openEventStream(turn);
    refreshSessionState({ quiet: true });
    updatePlanStatus(turn);
  }

  function buildUserMessage(text) {
    const row = el('article', 'msg user');
    const body = el('div', 'msg-body');
    const bubble = el('div', 'user-bubble', text);
    const meta = el('div', 'user-meta', clockNow());
    const avatar = el('span', 'avatar user-avatar', '我');
    avatar.setAttribute('aria-hidden', 'true');
    body.append(bubble, meta);
    row.append(body, avatar);
    return row;
  }

  function createAssistantTurn() {
    state.turnSeq += 1;
    const id = 'turn_' + state.turnSeq;
    const row = el('article', 'msg assistant');
    const avatar = el('span', 'avatar assistant-avatar');
    avatar.setAttribute('aria-hidden', 'true');
    avatar.append(iconNode('shieldCheck'));

    const body = el('div', 'msg-body');
    const meta = el('div', 'msg-meta');
    const author = el('span', 'msg-author', 'AI 助手');
    const sep = el('span', 'meta-sep');
    sep.setAttribute('aria-hidden', 'true');
    const time = document.createElement('time');
    time.textContent = clockNow();
    append(meta, author, sep, time);

    const blocks = el('div', 'assistant-blocks');
    body.append(meta, blocks);
    row.append(avatar, body);
    dom.chatStream.append(row);
    scrollToBottom(true);

    return {
      id: id,
      busyKey: 'stream:' + id,
      root: row,
      body: body,
      meta: meta,
      blocks: blocks,
      timeEl: time,
      traceId: null,
      resultTraceId: null,
      typing: null,
      internalBody: null,
      detailsBody: null,
      internalEventCount: 0,
      auditCount: 0,
      auditMetaEl: null,
      detailsPanel: null,
      detailToggle: null,
      resumeEl: null,
      queue: [],
      draining: false,
      seen: new Set(),
      received: 0,
      expected: 0,
      retries: 0,
      es: null,
      closeTimer: null,
      watchdog: null,
      retryTimer: null,
      streamNotice: null,
      terminal: false,
      awaiting: false,
      awaitingType: '',
      status: '',
      postReply: '',
      postStatus: '',
      fallbackEvents: [],
      planCard: null,
      planNodes: new Map(),
      confirmCards: new Map(),
      stepupCards: new Map(),
      errorRendered: false,
      resultRendered: false,
      cancelled: false,
      locked: false
    };
  }

  function showTyping(turn) {
    if (!turn || !turn.root || !turn.root.isConnected) return;
    if (turn.typing) {
      turn.typing.stage.textContent = TYPING_TEXT;
      return;
    }
    const bubble = el('div', 'typing-bubble');
    bubble.setAttribute('role', 'status');
    bubble.setAttribute('aria-label', TYPING_TEXT);
    const dots = el('span', 'typing-dots');
    dots.append(el('span'), el('span'), el('span'));
    dots.setAttribute('aria-hidden', 'true');
    const stage = el('span', 'typing-stage', TYPING_TEXT);
    bubble.append(dots, stage);
    turn.typing = { node: bubble, stage: stage };
    const stick = isNearBottom();
    turn.blocks.append(bubble);
    if (stick) scrollToBottom(false);
  }

  function removeTyping(turn) {
    if (!turn || !turn.typing) return;
    const node = turn.typing.node;
    turn.typing = null;
    if (node && node.parentNode) node.remove();
  }

  function appendBlock(turn, node, options) {
    const opts = options || {};
    if (!turn || !turn.root || !turn.root.isConnected) return;
    if (opts.removeTyping !== false) removeTyping(turn);
    const stick = opts.force || isNearBottom();
    turn.blocks.append(node);
    if (stick) scrollToBottom(false);
  }

  /* 内部执行轨迹不进入对话主线：先挂到游离节点（或已展开的详情里），
   * 等 result 到达后再随“执行详情”一并展示。 */
  function appendInternal(turn, node) {
    if (!turn || !node) return;
    if (turn.detailsBody) {
      turn.detailsBody.append(node);
      turn.internalEventCount += 1;
      return;
    }
    if (!turn.internalBody) {
      turn.internalBody = el('div', 'detail-timeline');
    }
    turn.internalBody.append(node);
    turn.internalEventCount += 1;
  }

  /* ============================================================
   * 12. SSE 事件流与断线重连
   * ============================================================ */

  function openEventStream(turn) {
    if (!turn || !turn.traceId || turn.terminal || !turn.root.isConnected) return;
    if (turn.es) closeTurnStream(turn, { intentional: false, keepBusy: true, silent: true });

    markBusy(turn.busyKey);
    turn.streamFailed = false;

    let es;
    try {
      es = new EventSource('/v1/stream/' + encodeURIComponent(turn.traceId));
    } catch (err) {
      showStreamFailure(turn, '浏览器无法建立事件流连接。');
      return;
    }

    turn.es = es;

    es.onopen = () => {
      if (turn.es !== es) return;
      turn.streamOpen = true;
      armStreamWatchdog(turn);
      if (turn.streamNotice) {
        const copy = turn.streamNotice.querySelector('.stream-note-copy');
        if (copy) copy.textContent = '事件流已重新连接，正在补齐事件…';
      }
    };

    es.onmessage = (event) => {
      if (turn.es !== es) return;
      handleStreamPayload(turn, event.data, null);
    };

    SSE_EVENT_TYPES.forEach((type) => {
      if (type === 'error') return;
      es.addEventListener(type, (event) => {
        if (turn.es !== es) return;
        handleStreamPayload(turn, event.data, type);
      });
    });

    es.addEventListener('error', (event) => {
      if (turn.es !== es) return;
      if (event && typeof event.data === 'string' && event.data) {
        handleStreamPayload(turn, event.data, 'error');
        return;
      }
      handleStreamFailure(turn, es);
    });

    ['end', 'done'].forEach((type) => {
      es.addEventListener(type, () => {
        if (turn.es !== es) return;
        closeTurnStream(turn, { intentional: true, silent: true });
      });
    });
  }

  function handleStreamPayload(turn, rawData, forcedType) {
    if (!turn) return;
    if (rawData === null || rawData === undefined || rawData === '') return;
    if (String(rawData) === '[DONE]') {
      closeTurnStream(turn, { intentional: true, silent: true });
      return;
    }

    let parsed;
    try {
      parsed = typeof rawData === 'string' ? JSON.parse(rawData) : rawData;
    } catch (err) {
      console.warn('[bankagent] 无法解析 SSE 事件', err);
      renderStreamNotice(turn, {
        tone: 'error',
        text: '收到一条无法解析的事件数据，已跳过。'
      });
      return;
    }

    const event = normalizeStreamEvent(parsed, forcedType);
    if (!event) return;
    acceptEvent(turn, event);
  }

  function normalizeStreamEvent(raw, forcedType, scope) {
    if (!raw || typeof raw !== 'object') return null;
    const rawType = raw.type || raw.event || raw.event_type || forcedType || '';
    const type = String(rawType || '').toLowerCase();
    if (!type) return null;

    let data = raw.data;
    if (typeof data === 'string') {
      try {
        data = JSON.parse(data);
      } catch (err) {
        data = null;
      }
    }
    if (!data || typeof data !== 'object') {
      data = {};
      Object.keys(raw).forEach((key) => {
        if (['type', 'event', 'event_type', 'data', 'seq'].indexOf(key) === -1) {
          data[key] = raw[key];
        }
      });
    }

    let seq = raw.seq;
    if (seq === undefined && data && data.seq !== undefined) seq = data.seq;
    return {
      type: type,
      data: data || {},
      seq: seq === undefined ? null : seq,
      scope: scope || null,
      raw: raw
    };
  }

  function acceptEvent(turn, event) {
    if (!turn || !event) return false;
    if (!turn.root || !turn.root.isConnected) return false;

    if (event.type === 'done' || event.type === 'end') {
      closeTurnStream(turn, { intentional: true, silent: true });
      return false;
    }
    if (event.type === 'ping' || event.type === 'heartbeat') return false;
    if (SSE_EVENT_TYPES.indexOf(event.type) === -1) {
      console.debug('[bankagent] 忽略未知事件类型', event.type);
      return false;
    }

    const seqPart =
      event.seq !== null && event.seq !== undefined
        ? 'seq:' + String(event.seq)
        : 'raw:' + event.type + ':' + JSON.stringify(event.data);
    const dedupeKey = event.scope ? 'scope:' + event.scope + ':' + seqPart : seqPart;
    if (turn.seen.has(dedupeKey)) return false;
    turn.seen.add(dedupeKey);
    turn.received += 1;
    turn.lastEventAt = Date.now();

    if (event.type === 'confirm') {
      turn.awaiting = true;
      turn.awaitingType = 'confirm';
    }
    if (event.type === 'stepup') {
      turn.awaiting = true;
      turn.awaitingType = 'stepup';
    }
    if (event.type === 'result' || event.type === 'error') {
      turn.terminal = true;
      turn.awaiting = false;
    }

    if (turn.watchdog) {
      window.clearTimeout(turn.watchdog);
      turn.watchdog = null;
    }
    removeStreamNotice(turn);
    enqueueEvent(turn, event);
    scheduleStreamClose(turn);
    return true;
  }

  function scheduleStreamClose(turn) {
    if (!turn || !turn.es) return;
    if (turn.closeTimer) window.clearTimeout(turn.closeTimer);
    const receivedAll = turn.expected > 0 && turn.received >= turn.expected;
    if (!receivedAll && !turn.terminal && !turn.awaiting) return;
    const delay = receivedAll ? 450 : 900;
    turn.closeTimer = window.setTimeout(() => {
      turn.closeTimer = null;
      if (!turn.es) return;
      if (turn.terminal || turn.awaiting || (turn.expected > 0 && turn.received >= turn.expected)) {
        closeTurnStream(turn, { intentional: true, silent: true });
      }
    }, delay);
  }

  function handleStreamFailure(turn, es) {
    if (!turn || turn.es !== es) return;
    const receivedAll = turn.expected > 0 && turn.received >= turn.expected;
    if (turn.terminal || turn.awaiting || receivedAll) {
      closeTurnStream(turn, { intentional: true, silent: true });
      return;
    }
    if (!turn.root.isConnected) {
      closeTurnStream(turn, { intentional: false, keepBusy: false, silent: true });
      return;
    }

    closeTurnStream(turn, { intentional: false, keepBusy: true, silent: true });

    if (turn.retries < 2) {
      turn.retries += 1;
      renderStreamNotice(turn, {
        tone: 'warn',
        text: '事件流连接中断，正在尝试第 ' + turn.retries + ' 次重连…',
        actions: [
          { label: '立即重试', onClick: () => retryStream(turn) }
        ]
      });
      turn.retryTimer = window.setTimeout(() => {
        turn.retryTimer = null;
        if (turn.root.isConnected && !turn.terminal) openEventStream(turn);
      }, 900 * turn.retries);
    } else {
      showStreamFailure(turn, '事件流多次连接失败，未收到完整事件。');
    }
  }

  function showStreamFailure(turn, message) {
    if (!turn) return;
    removeTyping(turn);
    const actions = [
      { label: '重新连接', onClick: () => retryStream(turn) }
    ];
    if (turn.fallbackEvents.length || turn.postReply) {
      actions.push({ label: '显示完整结果', onClick: () => useFallbackResult(turn) });
    }
    renderStreamNotice(turn, {
      tone: 'error',
      text: message || '事件流连接失败。',
      actions: actions
    });
    clearBusy(turn.busyKey);
    if (!turn.root.isConnected) closeTurnStream(turn, { intentional: true, silent: true });
  }

  function retryStream(turn) {
    if (!turn) return;
    closeTurnStream(turn, { intentional: false, keepBusy: true, silent: true });
    turn.retries = 0;
    openEventStream(turn);
    renderStreamNotice(turn, {
      tone: 'warn',
      text: '正在重新连接事件流…',
      actions: [
        { label: '显示完整结果', onClick: () => useFallbackResult(turn) }
      ]
    });
  }

  function armStreamWatchdog(turn) {
    if (!turn) return;
    if (turn.watchdog) window.clearTimeout(turn.watchdog);
    turn.watchdog = window.setTimeout(() => {
      turn.watchdog = null;
      if (!turn.es || turn.received > 0 || turn.terminal || turn.awaiting) return;
      const actions = [
        { label: '重新连接', onClick: () => retryStream(turn) }
      ];
      if (turn.fallbackEvents.length || turn.postReply) {
        actions.push({ label: '显示完整结果', onClick: () => useFallbackResult(turn) });
      }
      renderStreamNotice(turn, {
        tone: 'warn',
        text: '事件流已连接，但暂未收到事件。可以重试，或直接展示本次完整结果。',
        actions: actions
      });
    }, 10000);
  }

  function closeTurnStream(turn, options) {
    if (!turn) return;
    const opts = options || {};
    if (turn.closeTimer) {
      window.clearTimeout(turn.closeTimer);
      turn.closeTimer = null;
    }
    if (turn.watchdog) {
      window.clearTimeout(turn.watchdog);
      turn.watchdog = null;
    }
    if (turn.retryTimer) {
      window.clearTimeout(turn.retryTimer);
      turn.retryTimer = null;
    }
    const es = turn.es;
    turn.es = null;
    if (es) {
      try {
        es.onopen = null;
        es.onmessage = null;
        es.close();
      } catch (err) {
        /* noop */
      }
    }
    if (!opts.keepBusy) clearBusy(turn.busyKey);
  }

  function renderStreamNotice(turn, options) {
    if (!turn || !turn.root || !turn.root.isConnected) return;
    removeTyping(turn);
    const opts = options || {};
    let node = turn.streamNotice;
    if (!node) {
      node = el('div', 'stream-note');
      node.setAttribute('role', 'status');
      turn.streamNotice = node;
      appendBlock(turn, node, { removeTyping: false });
    } else {
      node.replaceChildren();
    }
    node.classList.toggle('is-error', opts.tone === 'error');
    if (opts.tone === 'warn') {
      node.append(el('span', 'spinner'));
    } else {
      node.append(iconNode('alert'));
    }
    node.append(el('span', 'stream-note-copy', opts.text || '事件流连接异常。'));
    if (opts.actions && opts.actions.length) {
      const actions = el('span', 'stream-actions');
      opts.actions.forEach((action) => {
        const button = el('button', 'small-button', action.label);
        button.type = 'button';
        button.addEventListener('click', action.onClick);
        actions.append(button);
      });
      node.append(actions);
    }
  }

  function removeStreamNotice(turn) {
    if (!turn || !turn.streamNotice) return;
    const node = turn.streamNotice;
    turn.streamNotice = null;
    if (node.parentNode) node.remove();
  }

  function useFallbackResult(turn) {
    if (!turn) return;
    closeTurnStream(turn, { intentional: true, silent: true });
    removeStreamNotice(turn);
    turn.expected = 0;
    const events = turn.fallbackEvents;
    if (Array.isArray(events) && events.length) {
      renderEventBatch(turn, events);
      renderSystemNote(turn, '已切换为完整结果展示（事件流不可用）。');
      showToast('已展示服务返回的完整结果。', 'info');
    } else if (turn.postReply) {
      renderResultCard(turn, {
        reply: turn.postReply,
        level: '',
        audit_url: ''
      });
      renderSystemNote(turn, '已切换为完整结果展示（事件流不可用）。');
    } else {
      renderChatError(turn, 'E_STREAM', '事件流不可用，且服务未返回可展示的结果。');
    }
    clearBusy(turn.busyKey);
  }

  /* ============================================================
   * 13. 事件渲染队列
   * ============================================================ */

  function enqueueEvent(turn, event) {
    turn.queue.push(event);
    if (!turn.draining) drainEventQueue(turn);
  }

  async function drainEventQueue(turn) {
    if (turn.draining) return;
    turn.draining = true;
    try {
      while (turn.queue.length) {
        if (!turn.root.isConnected) {
          turn.queue.length = 0;
          break;
        }
        const event = turn.queue.shift();
        try {
          processEvent(turn, event);
        } catch (err) {
          console.error('[bankagent] 渲染事件失败', event, err);
          renderChatError(turn, 'E_RENDER', '界面渲染该事件失败：' + (err.message || err));
        }
        if (event.type === 'thinking') {
          if (!REDUCED_MOTION) await sleep(560);
        } else if (turn.queue.length) {
          if (!REDUCED_MOTION) await sleep(130);
        }
      }
    } finally {
      turn.draining = false;
    }
  }

  function processEvent(turn, event) {
    if (!turn.root.isConnected) return;
    const data = event.data || {};
    switch (event.type) {
      case 'thinking':
        showTyping(turn);
        return;
      case 'plan':
        renderPlanCard(turn, data);
        break;
      case 'policy':
        renderPolicyCard(turn, data);
        break;
      case 'confirm':
        removeTyping(turn);
        renderConfirmCard(turn, data);
        break;
      case 'stepup':
        removeTyping(turn);
        renderStepupRequired(turn, data);
        break;
      case 'step':
        applyStep(turn, data);
        break;
      case 'result':
        removeTyping(turn);
        renderResultCard(turn, data);
        break;
      case 'error':
        removeTyping(turn);
        renderChatError(turn, data.code || 'E_INTERNAL', data.message || ERROR_HINTS.E_INTERNAL);
        break;
      case 'audit':
        turn.auditCount = Number(data.event_count) || turn.auditCount || 0;
        if (turn.auditMetaEl) {
          turn.auditMetaEl.textContent = '审计 ' + turn.auditCount + ' 条';
        }
        break;
      default:
        break;
    }
  }

  function renderEventBatch(turn, events, scope) {
    if (!turn || !Array.isArray(events)) return 0;
    let accepted = 0;
    events.forEach((raw) => {
      const event = normalizeStreamEvent(raw, null, scope || null);
      if (event && acceptEvent(turn, event)) accepted += 1;
    });
    return accepted;
  }

  /* ============================================================
   * 14. 计划 DAG
   * ============================================================ */

  function describeNode(node) {
    const tool = String(node.tool || '');
    const args = node.args && typeof node.args === 'object' ? node.args : {};
    const info = TOOL_INFO[tool] || null;
    const amount = fmtPlanAmount(
      args.amount !== undefined
        ? args.amount
        : args.amount_each !== undefined
          ? args.amount_each
          : args.budget !== undefined
            ? args.budget
            : args.new_limit
    );
    const contact = String(args.alias || args.name || args.payee || args.contact_name || '');
    const rangeKey = String(args.range || '');
    const range = RANGE_LABELS[rangeKey] || (rangeKey && rangeKey.length <= 4 ? rangeKey : '近期');
    let text = info ? info.label : '执行 ' + (tool || '步骤');

    switch (tool) {
      case 'get_contacts':
        text = '查询联系人' + (contact ? '「' + contact + '」' : '');
        break;
      case 'quote_transfer':
        text = '试算转账' + (amount ? ' ' + amount + ' 元' : '');
        break;
      case 'create_transfer':
        text = '发起转账' + (amount ? ' ' + amount + ' 元' : '') + (contact ? ' → ' + contact : '');
        break;
      case 'submit_transfer':
        text = '发起大额转账' + (amount ? ' ' + amount + ' 元' : '') + (contact ? ' → ' + contact : '');
        break;
      case 'analyze_spending':
        text = '分析' + range + '支出结构';
        break;
      case 'detect_anomaly':
        text = '识别' + range + '异常交易';
        break;
      case 'get_subscriptions':
        text = '列出订阅与下次扣费';
        break;
      case 'cancel_subscription':
        text =
          '取消订阅' +
          (args.merchant ? '「' + truncate(args.merchant, 14) + '」' : args.sub_id ? ' #' + args.sub_id : '') +
          '（不可逆）';
        break;
      case 'get_profile':
        text = '读取用户档案（生日与风险等级）';
        break;
      case 'freeze_amount':
        text = '锁定' + (amount ? ' ' + amount + ' 元' : '') + '活期资金';
        break;
      case 'create_standing_plan':
        text = '创建长期计划' + (args.goal ? '「' + truncate(args.goal, 16) + '」' : '');
        break;
      case 'report_card_lost':
        text = '卡片挂失（不可逆）';
        break;
      case 'get_accounts':
        text = '读取账户、卡列表与可用余额';
        break;
      case 'purchase_wealth':
        text = '申购理财' + (amount ? ' ' + amount + ' 元' : '');
        break;
      case 'set_reminder':
        text = '设置提醒' + (args.content ? '「' + truncate(args.content, 16) + '」' : '');
        break;
      case 'order_gift':
        text =
          '下单' +
          (args.item ? '「' + truncate(args.item, 14) + '」' : '礼品') +
          (amount ? ' · ' + amount + ' 元' : '');
        break;
      default:
        if (amount) text += ' · ' + amount + ' 元';
        break;
    }

    const hint = String(node.risk_hint || '').toLowerCase();
    const risk = hint === 'yellow' || hint === 'red' || hint === 'green'
      ? hint
      : info && info.risk
        ? info.risk
        : 'green';

    const deps = Array.isArray(node.depends_on)
      ? node.depends_on.map(String)
      : node.depends_on
        ? [String(node.depends_on)]
        : [];

    return { text: text, risk: risk, deps: deps, tool: tool, info: info };
  }

  function renderPlanCard(turn, data) {
    const plan = data.plan && typeof data.plan === 'object' ? data.plan : {};
    const nodes = Array.isArray(plan.nodes) ? plan.nodes : [];
    const planId = plan.plan_id || data.plan_id || '';
    const card = el('section', 'event-card plan-card');
    card.setAttribute('aria-label', '执行计划');

    const head = el('div', 'plan-head');
    const headMain = el('div', 'plan-head-main');
    const iconWrap = el('span', 'card-icon');
    iconWrap.append(iconNode('flow'));
    const copy = el('div');
    const rawSummary = String(data.summary || plan.goal || buildPlanSummary(nodes));
    const planSummary = nodes.length && rawSummary && !/^\d+\s*步/.test(rawSummary)
      ? nodes.length + ' 步 · ' + rawSummary
      : rawSummary || buildPlanSummary(nodes);
    copy.append(
      el('div', 'card-kicker', 'EXECUTION PLAN' + (planId ? ' · ' + planId : '')),
      el('div', 'plan-title', '执行计划'),
      el('div', 'plan-summary', planSummary)
    );
    headMain.append(iconWrap, copy);

    const side = el('div', 'plan-side');
    const chip = el('span', 'plan-chip', '待执行');
    const progress = el('span', 'plan-progress', '0/' + nodes.length);
    side.append(chip, progress);
    head.append(headMain, side);

    const list = el('ol', 'plan-nodes');
    const planNodes = new Map();
    const planRefs = [];

    nodes.forEach((node, index) => {
      const descriptor = describeNode(node);
      const key = String(node.id || 'n' + (index + 1));
      const item = el('li', 'plan-node');
      if (index >= 3) item.hidden = true;
      item.style.setProperty('--i', String(index));
      item.dataset.nodeId = key;

      const idBadge = el('span', 'node-index', key.length <= 4 ? key : String(index + 1));
      idBadge.title = '节点 ' + key;

      const body = el('div', 'node-body');
      const title = el('div', 'node-title');
      title.append(
        el('span', 'node-tool', descriptor.tool || 'tool'),
        el('span', 'node-summary', descriptor.text)
      );

      const meta = el('div', 'node-meta');
      const risk = el('span', 'node-risk');
      risk.append(
        el('span', 'risk-dot' + (descriptor.risk ? ' ' + descriptor.risk : '')),
        document.createTextNode(riskLabel(descriptor.risk))
      );
      const deps = el('span', 'node-deps', descriptor.deps.length ? '← ' + descriptor.deps.join(' / ') : '← 起点');
      meta.append(risk, deps);

      const status = el('span', 'node-status', '待执行');
      body.append(title, meta);
      item.append(idBadge, body, status);
      list.append(item);

      const ref = {
        key: key,
        node: node,
        descriptor: descriptor,
        el: item,
        statusEl: status,
        state: 'pending'
      };
      planNodes.set(key, ref);
      planRefs.push(ref);
      if (!turn.planNodes.has(key)) turn.planNodes.set(key, ref);
      else turn.planNodes.set(key, ref);
    });

    card.append(head, list);
    if (nodes.length > 3) {
      const more = el('button', 'plan-more', '查看完整计划（共 ' + nodes.length + ' 步）');
      more.type = 'button';
      let expanded = false;
      more.addEventListener('click', () => {
        expanded = !expanded;
        planRefs.forEach((ref, index) => { if (index >= 3) ref.el.hidden = !expanded; });
        more.textContent = expanded ? '收起计划' : '查看完整计划（共 ' + nodes.length + ' 步）';
      });
      card.append(more);
    }
    appendInternal(turn, card);

    turn.planCard = {
      root: card,
      chip: chip,
      progress: progress,
      nodes: planRefs,
      planId: planId,
      summary: planSummary
    };
    nodes.forEach((node, index) => {
      const key = String(node.id || 'n' + (index + 1));
      if (planNodes.has(key)) turn.planNodes.set(key, planNodes.get(key));
    });
    updatePlanStatus(turn);
  }

  function buildPlanSummary(nodes) {
    if (!Array.isArray(nodes) || !nodes.length) return '暂无执行步骤';
    const labels = nodes.slice(0, 3).map((node) => {
      const info = TOOL_INFO[node.tool];
      return info ? info.label : node.tool || '步骤';
    });
    return nodes.length + ' 步：' + labels.join(' → ') + (nodes.length > 3 ? ' → …' : '');
  }

  function updatePlanStatus(turn) {
    if (!turn || !turn.planCard) return;
    const refs = turn.planCard.nodes;
    const total = refs.length;
    const done = refs.filter((ref) => ref.state === 'done').length;
    const failed = refs.filter((ref) => ref.state === 'failed').length;

    let text = '待执行';
    let cls = '';
    if (turn.locked) {
      text = '已锁定';
      cls = 'is-error';
    } else if (turn.errorRendered) {
      text = '已中止';
      cls = 'is-error';
    } else if (turn.cancelled) {
      text = '已取消';
      cls = 'is-cancelled';
    } else if (turn.resultRendered) {
      text = '已完成';
      cls = 'is-done';
    } else if (turn.awaiting) {
      text = turn.awaitingType === 'stepup' ? '等待强验证' : '等待确认';
      cls = 'is-waiting';
    } else if (failed > 0) {
      text = '执行异常';
      cls = 'is-error';
    } else if (total > 0 && done >= total) {
      text = '已完成';
      cls = 'is-done';
    } else if (done > 0) {
      text = '执行中';
    }

    turn.planCard.chip.className = 'plan-chip' + (cls ? ' ' + cls : '');
    turn.planCard.chip.textContent = text;
    turn.planCard.progress.textContent = done + '/' + total;
  }

  function applyStep(turn, data) {
    const nodeId = data.node_id !== undefined && data.node_id !== null ? String(data.node_id) : '';
    if (
      nodeId === 'stepup' ||
      nodeId === 'cancel' ||
      nodeId === 'compensate' ||
      data.tool === 'StepUpAuth' ||
      data.tool === 'user.cancel' ||
      data.tool === 'saga.compensate'
    ) {
      /* 强验证、取消、冲正已经由专用卡片/结果卡呈现，避免重复步骤行 */
      return;
    }
    let ref = nodeId ? turn.planNodes.get(nodeId) : null;
    if (!ref) {
      ref = Array.from(turn.planNodes.values()).find(
        (candidate) => candidate.state === 'pending' && candidate.descriptor.tool === data.tool
      );
    }
    const ok = data.ok !== false;

    if (ref) {
      ref.state = ok ? 'done' : 'failed';
      ref.el.classList.remove('is-done', 'is-failed');
      ref.el.classList.add(ok ? 'is-done' : 'is-failed');

      const status = el('span', 'node-status');
      status.append(iconNode(ok ? 'check' : 'x'));
      const ms = data.ms !== undefined && data.ms !== null ? String(data.ms) + ' ms' : '';
      status.append(
        document.createTextNode(
          ' ' + (ok ? '完成' : '失败') + (ms ? ' · ' + ms : '')
        )
      );
      if (!ok) {
        const errorText =
          (data.error && (data.error.message || data.error.code)) || data.message || '工具调用失败';
        status.title = String(errorText);
      }
      ref.statusEl.replaceWith(status);
      ref.statusEl = status;
    }
    renderStepLine(turn, data, ok);
    updatePlanStatus(turn);
  }

  function renderStepLine(turn, data, ok) {
    const node = el('div', 'step-line');
    const check = el('span', 'step-check' + (ok ? '' : ' is-failed'));
    check.append(iconNode(ok ? 'check' : 'x'));
    const info = TOOL_INFO[data.tool];
    const label = info ? info.label : data.tool || '步骤';
    const text = el('span', null, label + (ok ? ' 已完成' : ' 执行失败'));
    node.append(check, text);
    if (data.tool) node.append(el('span', 'step-tool', String(data.tool)));
    if (data.ms !== undefined && data.ms !== null) {
      node.append(el('span', 'step-ms', String(data.ms) + ' ms'));
    }
    if (!ok) {
      const errorText =
        (data.error && (data.error.message || data.error.code)) || data.message || '';
      if (errorText) node.append(el('span', 'error-message', String(errorText)));
    }
    appendInternal(turn, node);
  }

  /* ============================================================
   * 15. 策略徽标
   * ============================================================ */

  function renderPolicyCard(turn, data) {
    const level = normalizeLevel(data.level);
    const card = el('section', 'policy-card' + (level ? ' level-' + level : ''));
    if (level === 'red' || level === 'deny') card.setAttribute('role', 'alert');

    const iconWrap = el('span', 'policy-icon');
    iconWrap.append(iconNode(level === 'green' ? 'shieldCheck' : level === 'yellow' ? 'shield' : 'shieldAlert'));
    const main = el('div', 'policy-main');
    const top = el('div', 'policy-top');
    top.append(el('span', 'policy-title', POLICY_TITLES[level] || '策略校验'));
    if (data.rule_id) top.append(el('span', 'policy-rule', String(data.rule_id)));
    top.append(
      el('span', 'policy-level', POLICY_TAGS[level] || '裁决')
    );
    main.append(top);

    const factors = normalizeFactors(data.required_factors);
    let reason = String(data.reason || '');
    if (factors.length) {
      reason += (reason ? ' · ' : '') + '需完成 ' + factors.join(' + ');
    }
    if (reason) main.append(el('div', 'policy-reason', reason));

    card.append(iconWrap, main);
    appendInternal(turn, card);
  }

  /* ============================================================
   * 16. 确认卡与倒计时
   * ============================================================ */

  function renderConfirmCard(turn, data) {
    const card = data.card && typeof data.card === 'object' ? data.card : {};
    /* 契约示例把参数放在 card.params；当前实现把 payee_name/amount 等
     * 直接平铺在 card 上。两种形状都兼容，避免确认卡出现空字段。 */
    const flatParams = {};
    [
      'amount', 'payee_name', 'payee_tail', 'arrival', 'memo',
      'merchant', 'item', 'deliver_at', 'card_tail', 'new_limit'
    ].forEach((key) => {
      if (card[key] !== undefined && card[key] !== null && card[key] !== '') {
        flatParams[key] = card[key];
      }
    });
    const nestedParams =
      card.params && typeof card.params === 'object'
        ? card.params
        : card.params_snapshot && typeof card.params_snapshot === 'object'
          ? card.params_snapshot
          : {};
    const rawParams = Object.assign({}, flatParams, nestedParams);
    const intentId = String(data.intent_id || card.intent_id || '');
    const action = String(card.action || data.action || '');
    const merged = mergeConfirmParams(rawParams, deriveConfirmFields(turn, action));
    const params = merged.params;
    const level = normalizeLevel(card.level || data.level) || 'yellow';
    const deadline = parseDateMs(data.expires_at || card.expires_at) || Date.now() + CONFIRM_TTL_MS;
    const tokenFromEvent = String(
      data.token || data.confirm_token || card.confirm_token || card.token || ''
    );

    const ctx = {
      intentId: intentId,
      card: card,
      data: data,
      turn: turn,
      token: tokenFromEvent,
      deadline: deadline,
      resolved: false,
      submitting: false,
      expired: false,
      level: level,
      tick: null
    };

    const root = el('section', 'event-card confirm-card');
    root.setAttribute('aria-label', '待确认业务卡片');

    const head = el('div', 'confirm-head');
    const headMain = el('div', 'confirm-head-main');
    const iconWrap = el('span', 'card-icon confirm-icon');
    iconWrap.append(iconNode('shieldAlert'));
    const titles = el('div');
    titles.append(
      el('div', 'confirm-kicker', 'SECURE CONFIRMATION · 待您确认'),
      el('div', 'confirm-title', confirmTitle(action))
    );
    headMain.append(iconWrap, titles);

    const side = el('div', 'confirm-side');
    const levelChip = el('span', 'confirm-level', level === 'red' ? '高风险 · 强验证' : '需确认');
    const countdown = el('span', 'confirm-countdown');
    countdown.append(iconNode('clock'), el('span', null, '剩余 05:00'));
    side.append(levelChip, countdown);
    head.append(headMain, side);

    const hasAmount = params && params.amount !== undefined && params.amount !== null && params.amount !== '';
    let amountWrap = null;
    let amountValue = null;
    if (hasAmount) {
      amountWrap = el('div', 'confirm-amount');
      amountWrap.append(el('div', 'confirm-amount-label', (params && params.amount_label) || '本次金额'));
      amountValue = el('div', 'confirm-amount-value');
      amountValue.append(
        el('span', 'currency', '¥'),
        el('span', null, fmtCardAmount(params.amount)),
        el('span', 'amount-unit', '元')
      );
      amountWrap.append(amountValue);
    }

    const progress = el('div', 'confirm-progress');
    const progressFill = el('span');
    progress.append(progressFill);

    const grid = el('div', 'confirm-grid');
    buildConfirmRows(grid, params, action, merged.used);

    if (card.params_hash) {
      const row = el('div', 'confirm-row is-wide');
      row.append(el('span', 'confirm-key', '参数指纹'));
      const value = el('span', 'confirm-value mono', shortHash(card.params_hash, 30));
      value.title = String(card.params_hash);
      row.append(value);
      grid.append(row);
    }

    const note = el('div', 'confirm-note');
    note.append(
      iconNode('lock'),
      el(
        'span',
        null,
        merged.used
          ? '部分字段由执行计划回填；确认后服务端仍会重新裁决并校验参数指纹，收款账户以执行时核验为准。'
          : '确认后服务端会重新裁决并校验参数指纹；金额与关键参数不会被前端改写。'
      )
    );

    const inlineError = el('div', 'inline-error');
    inlineError.hidden = true;
    inlineError.setAttribute('role', 'alert');

    const actions = el('div', 'confirm-actions');
    const confirmBtn = el('button', 'btn btn-primary');
    confirmBtn.type = 'button';
    confirmBtn.append(iconNode('shieldCheck'), el('span', null, '确认执行'));
    const cancelBtn = el('button', 'btn btn-secondary');
    cancelBtn.type = 'button';
    cancelBtn.append(el('span', null, '取消'));
    actions.append(confirmBtn, cancelBtn);

    const cardParts = [head];
    const riskNote = String(card.risk_note || '');
    if (riskNote) {
      const risk = el('div', 'confirm-risk-banner');
      risk.append(el('strong', null, '⚠ 风险提示'), el('span', null, riskNote));
      cardParts.push(risk);
    }
    if (amountWrap) cardParts.push(amountWrap);
    cardParts.push(progress, grid, note, inlineError, actions);
    root.append.apply(root, cardParts);
    if (!intentId) {
      showConfirmError(ctx, '确认卡缺少 intent_id，无法提交。', 'E_PARAM', inlineError);
      confirmBtn.disabled = true;
      cancelBtn.disabled = true;
    }

    ctx.inlineError = inlineError;
    ctx.confirmBtn = confirmBtn;
    ctx.cancelBtn = cancelBtn;
    ctx.actionsEl = actions;
    ctx.countdownEl = countdown;
    ctx.levelChip = levelChip;
    ctx.kickerEl = $('.confirm-kicker', titles);
    ctx.progressEl = progress;
    ctx.progressFill = progressFill;
    ctx.amountEl = amountValue;

    ctx.tick = () => {
      if (ctx.resolved) return;
      const remainingMs = ctx.deadline - Date.now();
      if (remainingMs <= 0) {
        expireConfirm(ctx);
        return;
      }
      const remaining = Math.ceil(remainingMs / 1000);
      const minutes = Math.floor(remaining / 60);
      const seconds = remaining % 60;
      const text = '剩余 ' + pad2(minutes) + ':' + pad2(seconds);
      const label = countdown.querySelector('span');
      if (label) label.textContent = text;
      const ratio = Math.max(0, Math.min(100, (remainingMs / CONFIRM_TTL_MS) * 100));
      progressFill.style.width = ratio + '%';
      const urgent = remaining <= 60;
      countdown.classList.toggle('is-urgent', urgent);
      progress.classList.toggle('is-urgent', urgent);
    };

    const confirmHandler = () => requestConfirm(ctx);
    const cancelHandler = () => cancelConfirm(ctx);
    confirmBtn.addEventListener('click', confirmHandler);
    cancelBtn.addEventListener('click', cancelHandler);

    ctx.root = root;
    turn.confirmCards.set(intentId || 'confirm_' + Date.now(), ctx);
    appendBlock(turn, root);
    updatePlanStatus(turn);

    if (ctx.deadline <= Date.now()) {
      expireConfirm(ctx);
    } else {
      state.confirmContexts.add(ctx);
      startCountdownTicker();
      ctx.tick();
    }
  }

  /* 后端在部分场景会在执行前就发确认卡，params 只有 action/goal。
   * 用计划节点的用户参数回填金额与收款人，避免确认卡出现空金额；
   * 真实姓名与尾号仍以服务端 card 为准（拿不到时显示“执行时核验”）。 */
  function deriveConfirmFields(turn, action) {
    const fields = {};
    const refs = turn && turn.planCard && Array.isArray(turn.planCard.nodes)
      ? turn.planCard.nodes
      : [];
    const nodes = refs.map((ref) => ref.node).filter(Boolean);
    if (!nodes.length) return fields;

    const actionTools = [
      'create_transfer', 'submit_transfer', 'order_gift', 'purchase_wealth',
      'freeze_amount', 'create_standing_plan', 'cancel_subscription',
      'report_card_lost', 'apply_virtual_card', 'adjust_card_limit'
    ];
    const candidate =
      nodes.slice().reverse().find((node) => node.tool === action) ||
      nodes.slice().reverse().find((node) => actionTools.indexOf(node.tool) !== -1);

    const argsList = [];
    if (candidate && candidate.args) argsList.push(candidate.args);
    nodes.forEach((node) => {
      if (node.args && (!candidate || node.args !== candidate.args)) argsList.push(node.args);
    });

    const amountKeys = ['amount', 'amount_each', 'budget', 'new_limit'];
    for (let k = 0; k < amountKeys.length && !fields.amount; k += 1) {
      for (let i = 0; i < argsList.length; i += 1) {
        const value = argsList[i][amountKeys[k]];
        if (value === undefined || value === null || value === '') continue;
        if (typeof value === 'string' && value.trim().charAt(0) === '$') continue;
        const formatted = fmtPlanAmount(value);
        if (formatted && formatted !== '0.00') {
          fields.amount = formatted;
          break;
        }
      }
    }

    let payee = '';
    for (let i = 0; i < argsList.length && !payee; i += 1) {
      payee = argsList[i].payee || argsList[i].payee_name || argsList[i].alias || argsList[i].name || '';
    }
    if (payee) fields.payee_name = String(payee);

    if (action === 'transfer' || action === 'create_transfer' || action === 'submit_transfer') {
      fields.arrival = '实时';
    }
    if (candidate && candidate.args && candidate.args.memo) {
      fields.memo = String(candidate.args.memo);
    }
    return fields;
  }

  function mergeConfirmParams(raw, derived) {
    if (raw && Array.isArray(raw.fields) && raw.fields.length) {
      return { params: raw, used: false };
    }
    const merged = {};
    let used = false;
    const keys = [
      'amount', 'payee_name', 'payee_tail', 'arrival', 'memo',
      'merchant', 'item', 'deliver_at', 'card_tail'
    ];
    keys.forEach((key) => {
      const rawValue = raw ? raw[key] : undefined;
      if (rawValue !== undefined && rawValue !== null && rawValue !== '') {
        merged[key] = rawValue;
      } else if (derived && derived[key] !== undefined && derived[key] !== '') {
        merged[key] = derived[key];
        used = true;
      }
    });
    Object.keys(raw || {}).forEach((key) => {
      if (merged[key] === undefined) merged[key] = raw[key];
    });
    return { params: merged, used: used };
  }

  function confirmTitle(action) {
    switch (action) {
      case 'transfer':
      case 'create_transfer':
      case 'submit_transfer':
        return '请确认转账信息';
      case 'cancel_subscription':
      case 'subscription_cancel':
        return '请确认取消订阅';
      case 'report_card_lost':
      case 'card_lost':
        return '请确认卡片挂失';
      case 'create_standing_plan':
        return '请确认长期计划';
      case 'purchase_wealth':
        return '请确认理财申购';
      default:
        return '请确认业务信息';
    }
  }

  function buildConfirmRows(grid, params, action) {
    const rows = [];
    const isTransfer =
      action === 'transfer' ||
      action === 'create_transfer' ||
      action === 'submit_transfer' ||
      (!action && (params.payee_name || params.payee_tail));

    if (Array.isArray(params.fields) && params.fields.length) {
      params.fields.forEach((entry) => {
        if (!Array.isArray(entry) || entry.length < 2) return;
        rows.push([String(entry[0]), String(entry[1])]);
      });
    } else if (isTransfer) {
      rows.push(['收款人', params.payee_name || params.payee || params.contact_name || '—']);
      rows.push(['收款账户', params.payee_tail ? '尾号 ' + params.payee_tail : '执行时核验']);
      rows.push(['到账时间', params.arrival || params.arrival_time || '实时']);
      if (params.memo) rows.push(['用途', truncate(params.memo, 30)]);
    } else {
      if (params.payee_name || params.payee) rows.push(['收款人', params.payee_name || params.payee]);
      if (params.payee_tail) rows.push(['收款账户', '尾号 ' + params.payee_tail]);
      if (params.merchant) rows.push(['商户', params.merchant]);
      if (params.item) rows.push(['礼品', params.item]);
      if (params.deliver_at) rows.push(['送达时间', params.deliver_at]);
      if (params.card_tail) rows.push(['卡片', '尾号 ' + params.card_tail]);
      if (params.arrival) rows.push(['到账时间', params.arrival]);
      if (params.memo) rows.push(['备注', truncate(params.memo, 30)]);
      if (!rows.length) rows.push(['业务类型', actionLabel(action)]);
    }

    rows.forEach((entry) => {
      const row = el('div', 'confirm-row');
      const value = el('span', 'confirm-value', String(entry[1]));
      value.title = String(entry[1]);
      row.append(el('span', 'confirm-key', entry[0]), value);
      grid.append(row);
    });
  }

  function startCountdownTicker() {
    if (state.countdownTimer) return;
    state.countdownTimer = window.setInterval(() => {
      state.confirmContexts.forEach((ctx) => {
        if (ctx.resolved) {
          state.confirmContexts.delete(ctx);
          return;
        }
        ctx.tick();
      });
      if (!state.confirmContexts.size) {
        window.clearInterval(state.countdownTimer);
        state.countdownTimer = null;
      }
    }, 1000);
  }

  function expireConfirm(ctx) {
    if (!ctx || ctx.resolved) return;
    if (ctx.submitting) return;
    ctx.expired = true;
    settleConfirm(ctx, 'expired');
  }

  function settleConfirm(ctx, kind) {
    if (!ctx) return;
    ctx.resolved = true;
    ctx.submitting = false;
    state.confirmContexts.delete(ctx);

    if (ctx.root) ctx.root.classList.add('is-resolved');
    if (ctx.root && kind === 'expired') ctx.root.classList.add('is-expired');
    if (ctx.countdownEl) {
      const label = ctx.countdownEl.querySelector('span');
      if (label) label.textContent = kind === 'expired' ? '已失效' : kind === 'cancelled' ? '已取消' : '已确认';
    }
    if (ctx.kickerEl) {
      ctx.kickerEl.textContent = 'SECURE CONFIRMATION · ' +
        (kind === 'expired' ? '已失效' : kind === 'cancelled' ? '已取消' : '已确认');
    }
    if (ctx.levelChip) {
      ctx.levelChip.textContent = kind === 'expired' ? '已失效' : kind === 'cancelled' ? '已关闭' : '已确认';
    }
    if (ctx.progressFill) ctx.progressFill.style.width = '0%';
    if (ctx.progressEl) ctx.progressEl.classList.remove('is-urgent');
    if (ctx.inlineError) ctx.inlineError.hidden = true;

    if (ctx.actionsEl) {
      const outcome = el(
        'span',
        'confirm-outcome ' + (kind === 'cancelled' ? 'is-cancelled' : kind === 'expired' ? 'is-cancelled' : 'is-success')
      );
      outcome.append(
        iconNode(kind === 'cancelled' ? 'x' : kind === 'expired' ? 'clock' : 'check'),
        el(
          'span',
          null,
          kind === 'cancelled' ? '已取消本次操作' : kind === 'expired' ? '确认已失效，请重新发起' : '已确认执行'
        )
      );
      ctx.actionsEl.replaceChildren(outcome);
    }
    recomputeTurnAwaiting(ctx.turn);
  }

  function showConfirmError(ctx, message, code, target) {
    const box = target || ctx.inlineError;
    if (!box) return;
    box.replaceChildren(
      iconNode('alert'),
      el('span', null, (code ? code + ' · ' : '') + message)
    );
    box.hidden = false;
  }

  function setConfirmBusy(ctx, busy, label) {
    if (!ctx.confirmBtn || !ctx.cancelBtn) return;
    ctx.confirmBtn.disabled = busy;
    ctx.cancelBtn.disabled = busy;
    if (busy) {
      ctx.confirmBtn.replaceChildren(
        el('span', 'spinner'),
        el('span', null, label || '处理中…')
      );
    } else {
      ctx.confirmBtn.replaceChildren(iconNode('shieldCheck'), el('span', null, '确认执行'));
    }
  }

  function pendingTokenFromState() {
    const pending = state.sessionPending;
    if (!pending || typeof pending !== 'object') return '';
    return String(
      pending.token ||
      pending.confirm_token ||
      (pending.card && (pending.card.confirm_token || pending.card.token)) ||
      ''
    );
  }

  /* 区分“前置校验失败”和“执行阶段失败”。
   * 后端 _fail 会带 error 对象；执行后的失败只有 status=error + 事件流，
   * 后者仍应视为确认/强验证已通过，交给事件渲染。 */
  function responseFailure(response) {
    if (!response || typeof response !== 'object') return null;
    if (response.error && (response.error.code || response.error.message)) {
      return {
        code: response.error.code || 'E_INTERNAL',
        message: response.error.message || response.message || '请求未通过校验。',
        locked: response.status === 'locked'
      };
    }
    if (response.status === 'locked') {
      const errorEvent = Array.isArray(response.events)
        ? response.events.find((event) => event && event.type === 'error')
        : null;
      const data = errorEvent && errorEvent.data ? errorEvent.data : {};
      return {
        code: data.code || 'E_STEPUP_LOCKED',
        message: data.message || response.reply || '会话已被安全锁定。',
        locked: true
      };
    }
    if (response.ok === false) {
      const errorEvent = Array.isArray(response.events)
        ? response.events.find((event) => event && event.type === 'error')
        : null;
      const data = errorEvent && errorEvent.data ? errorEvent.data : {};
      return {
        code: response.code || data.code || 'E_INTERNAL',
        message: data.message || response.message || response.reply || '请求未通过校验。',
        locked: false
      };
    }
    if (response.status === 'error') {
      const events = Array.isArray(response.events) ? response.events : [];
      const errorEvents = events.filter((event) => event && event.type === 'error');
      const hasNonErrorEvent = events.some((event) => event && event.type !== 'error');
      if (errorEvents.length && !hasNonErrorEvent) {
        const data = errorEvents[0].data || {};
        return {
          code: data.code || 'E_INTERNAL',
          message: data.message || response.reply || '请求未通过校验。',
          locked: false
        };
      }
    }
    return null;
  }

  async function requestConfirm(ctx) {
    if (!ctx || ctx.resolved || ctx.submitting) return;
    if (state.sessionState === 'LOCKED') {
      showConfirmError(ctx, '会话已锁定，请开启新会话。', 'E_LOCKED');
      return;
    }
    if (ctx.deadline <= Date.now()) {
      expireConfirm(ctx);
      return;
    }

    ctx.submitting = true;
    setConfirmBusy(ctx, true, '正在提交…');
    if (ctx.inlineError) ctx.inlineError.hidden = true;
    markBusy('confirm:' + (ctx.intentId || ctx.turn.id));
    showTyping(ctx.turn);

    closeTurnStream(ctx.turn, { intentional: true, keepBusy: false, silent: true });
    const token = ctx.token || pendingTokenFromState();

    try {
      const response = await fetchJSON('/v1/confirm', {
        method: 'POST',
        body: { session_id: state.sessionId, intent_id: ctx.intentId, token: token },
        timeout: 60000
      });

      const failure = responseFailure(response);
      if (failure) {
        showConfirmError(ctx, failure.message, failure.code);
        if (
          failure.locked ||
          failure.code === 'E_CONFIRM_EXPIRED' ||
          failure.code === 'E_CONFIRM_USED' ||
          failure.code === 'E_CONFIRM_TOKEN' ||
          failure.code === 'E_PARAM'
        ) {
          if (failure.locked) setSessionState('LOCKED');
          settleConfirm(ctx, 'expired');
        } else {
          ctx.submitting = false;
          setConfirmBusy(ctx, false);
        }
        removeTyping(ctx.turn);
        refreshSessionState({ quiet: true });
        return;
      }

      settleConfirm(ctx, 'confirmed');
      recordMessage('assistant', response && response.reply);
      const events = response && Array.isArray(response.events) ? response.events : [];
      if (events.length) {
        renderEventBatch(ctx.turn, events, 'confirm:' + (ctx.intentId || ctx.turn.id));
        const settlesTyping = events.some((event) =>
          event && ['result', 'stepup', 'confirm', 'error'].indexOf(event.type) !== -1
        );
        if (!settlesTyping) removeTyping(ctx.turn);
      } else if (response && response.status === 'awaiting_stepup') {
        removeTyping(ctx.turn);
        showConfirmError(ctx, '服务要求强验证，但未返回验证挑战，请重试。', 'E_PARAM');
      } else if (response && response.reply) {
        renderResultCard(ctx.turn, {
          reply: response.reply,
          level: ctx.level,
          audit_url: '',
          txn_no: ''
        });
      } else if (response && response.status === 'error') {
        renderChatError(ctx.turn, response.code || 'E_INTERNAL', response.message || '确认后执行失败。');
      } else {
        removeTyping(ctx.turn);
        renderSystemNote(ctx.turn, '已提交确认，等待银行服务返回执行结果。');
      }
      refreshSessionState({ quiet: true });
    } catch (err) {
      removeTyping(ctx.turn);
      const code = err.code || 'E_INTERNAL';
      showConfirmError(ctx, describeError(err), code);
      if (
        code === 'E_CONFIRM_EXPIRED' ||
        code === 'E_CONFIRM_USED' ||
        code === 'E_CONFIRM_TOKEN' ||
        code === 'E_PARAM' ||
        code === 'E_NOT_FOUND'
      ) {
        settleConfirm(ctx, 'expired');
      } else {
        ctx.submitting = false;
        setConfirmBusy(ctx, false);
      }
      reportTransportError(err);
      refreshSessionState({ quiet: true });
    } finally {
      clearBusy('confirm:' + (ctx.intentId || ctx.turn.id));
    }
  }

  async function cancelConfirm(ctx) {
    if (!ctx || ctx.resolved || ctx.submitting) return;
    ctx.submitting = true;
    setConfirmBusy(ctx, true, '正在取消…');
    if (ctx.inlineError) ctx.inlineError.hidden = true;
    markBusy('cancel:' + (ctx.intentId || ctx.turn.id));
    showTyping(ctx.turn);
    closeTurnStream(ctx.turn, { intentional: true, keepBusy: false, silent: true });

    try {
      const response = await fetchJSON('/v1/cancel', {
        method: 'POST',
        body: { session_id: state.sessionId, intent_id: ctx.intentId },
        timeout: 30000
      });
      const failure = responseFailure(response);
      if (failure) {
        removeTyping(ctx.turn);
        showConfirmError(ctx, failure.message, failure.code);
        ctx.submitting = false;
        setConfirmBusy(ctx, false);
        refreshSessionState({ quiet: true });
        return;
      }
      ctx.turn.cancelled = true;
      settleConfirm(ctx, 'cancelled');
      recordMessage('assistant', response && response.reply);
      updatePlanStatus(ctx.turn);
      const events = response && Array.isArray(response.events) ? response.events : [];
      if (events.length) {
        renderEventBatch(ctx.turn, events, 'cancel:' + (ctx.intentId || ctx.turn.id));
        const settlesTyping = events.some((event) =>
          event && ['result', 'stepup', 'confirm', 'error'].indexOf(event.type) !== -1
        );
        if (!settlesTyping) removeTyping(ctx.turn);
      } else {
        removeTyping(ctx.turn);
        renderSystemNote(ctx.turn, '已取消本次操作，未执行任何资金变动。');
      }
      refreshSessionState({ quiet: true });
    } catch (err) {
      removeTyping(ctx.turn);
      ctx.submitting = false;
      setConfirmBusy(ctx, false);
      showConfirmError(ctx, describeError(err), err.code || 'E_INTERNAL');
      reportTransportError(err);
      refreshSessionState({ quiet: true });
    } finally {
      clearBusy('cancel:' + (ctx.intentId || ctx.turn.id));
    }
  }

  function recomputeTurnAwaiting(turn) {
    if (!turn) return;
    let hasConfirm = false;
    state.confirmContexts.forEach((ctx) => {
      if (ctx.turn === turn && !ctx.resolved) hasConfirm = true;
    });
    let hasStepup = false;
    state.stepupContexts.forEach((ctx) => {
      if (ctx.turn === turn && !ctx.resolved) hasStepup = true;
    });
    turn.awaiting = hasConfirm || hasStepup;
    turn.awaitingType = hasStepup ? 'stepup' : hasConfirm ? 'confirm' : '';
    updatePlanStatus(turn);
  }

  /* ============================================================
   * 17. 结果、错误与系统提示
   * ============================================================ */


  function speakReply(text) {
    if (!('speechSynthesis' in window)) {
      showToast('当前浏览器不支持语音朗读。', 'info');
      return;
    }
    const clean = String(text || '').replace(/[*#`>]/g, '').slice(0, 500);
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(clean);
    utterance.lang = 'zh-CN';
    utterance.rate = 1.02;
    window.speechSynthesis.speak(utterance);
  }

  async function sendFeedback(traceId, rating, button) {
    try {
      await fetchJSON('/v1/feedback', {
        method: 'POST',
        body: { trace_id: traceId || '', session_id: state.sessionId, rating: rating,
                reason: rating === 'down' ? '用户点了没帮上' : '' },
        timeout: 6000
      });
      if (button) {
        button.classList.add('is-open');
        button.textContent = rating === 'up' ? '已赞' : '已记录';
      }
      showToast(rating === 'up' ? '感谢反馈。' : '已归集为 badcase，可在设置-运营查看。', 'success');
    } catch (err) {
      showToast('反馈提交失败：' + describeError(err), 'error');
    }
  }

  function renderResultCard(turn, data) {
    if (!turn || !turn.root || !turn.root.isConnected) return;
    const reply = String(data.reply || (data.ok === false ? '本次操作未完成。' : '操作已完成。')).trim() || '操作已完成。';
    const traceId = traceIdFromAuditUrl(data.audit_url, turn.traceId) || '';

    if (/已取消/.test(reply)) {
      resolveCancelledPending(turn);
    }

    const wrapper = el('div', 'assistant-result');
    const bubble = el('div', 'reply-bubble', reply);

    if (Array.isArray(data.quick_replies) && data.quick_replies.length) {
      const chips = el('div', 'quick-replies');
      data.quick_replies.forEach((label) => {
        const chip = el('button', 'quick-chip', String(label));
        chip.type = 'button';
        chip.addEventListener('click', () => {
          if (state.busy.size === 0) sendText(String(label));
        });
        chips.append(chip);
      });
      wrapper.append(bubble, chips);
    } else {
      wrapper.append(bubble);
    }

    if (turn.status === 'denied' || turn.status === 'error' || /没太听清|没找到|未完成/.test(reply)) {
      const errorActions = el('div', 'reply-error-actions');
      const retry = el('button', 'reply-action', '重试');
      retry.type = 'button';
      retry.addEventListener('click', () => {
        if (state.busy.size === 0 && state.lastUserText) sendText(state.lastUserText);
      });
      const rephrase = el('button', 'reply-action', '换个说法');
      rephrase.type = 'button';
      rephrase.addEventListener('click', () => {
        dom.composerInput.value = '';
        dom.composerInput.focus();
        updateComposerMeta();
      });
      errorActions.append(retry, rephrase);
      wrapper.append(errorActions);
    }

    const actions = el('div', 'reply-actions');
    const detailId = 'exec-details-' + turn.id;
    const detailBtn = el('button', 'reply-action');
    detailBtn.type = 'button';
    detailBtn.setAttribute('aria-expanded', 'false');
    detailBtn.setAttribute('aria-controls', detailId);
    detailBtn.title = '展开本次请求的内部执行轨迹';
    detailBtn.append(el('span', null, '执行详情'), iconNode('chevronDown', 'detail-chevron'));

    const sep = el('span', 'reply-action-sep');
    sep.setAttribute('aria-hidden', 'true');

    const auditBtn = el('button', 'reply-action');
    auditBtn.type = 'button';
    auditBtn.append(iconNode('history'), el('span', null, '查看审计'));
    if (traceId) {
      auditBtn.addEventListener('click', () => openAudit(traceId));
    } else {
      auditBtn.disabled = true;
      auditBtn.title = '本次结果暂无可关联的审计链路';
    }
    const suggestion = /回复「([^」]{1,12})」/.exec(String(reply || ''));
    if (suggestion) {
      const suggestBtn = el('button', 'reply-action suggestion-action');
      suggestBtn.type = 'button';
      suggestBtn.append(el('span', null, suggestion[1]));
      suggestBtn.addEventListener('click', () => {
        if (state.busy.size === 0) sendText(suggestion[1]);
      });
      actions.append(suggestBtn);
    }
    const speakBtn = el('button', 'reply-action');
    speakBtn.type = 'button';
    speakBtn.append(el('span', null, '朗读'));
    speakBtn.title = '用本机语音朗读这条回复';
    speakBtn.addEventListener('click', () => speakReply(reply));

    const upBtn = el('button', 'reply-action');
    upBtn.type = 'button';
    upBtn.append(el('span', null, '有用'));
    upBtn.addEventListener('click', () => sendFeedback(traceId, 'up', upBtn));

    const downBtn = el('button', 'reply-action');
    downBtn.type = 'button';
    downBtn.append(el('span', null, '没帮上'));
    downBtn.addEventListener('click', () => sendFeedback(traceId, 'down', downBtn));

    actions.append(detailBtn, sep, auditBtn, el('span', 'reply-action-sep'), speakBtn, upBtn, downBtn);

    const panel = el('div', 'execution-details');
    panel.id = detailId;
    panel.hidden = true;
    panel.setAttribute('role', 'region');
    panel.setAttribute('aria-label', '内部执行轨迹');

    const head = el('div', 'exec-details-head');
    const headCopy = el('div');
    headCopy.append(
      el('div', 'exec-details-kicker', 'EXECUTION DETAILS · 执行轨迹'),
      el(
        'div',
        'exec-details-sub',
        turn.internalEventCount
          ? '仅展开可见 · 计划、策略与步骤留痕'
          : '仅展开可见 · 内部执行留痕'
      )
    );

    const meta = el('div', 'exec-details-meta');
    if (traceId) {
      const trace = el('span', 'exec-trace', traceId);
      trace.title = traceId;
      meta.append(trace);
    }
    const auditMeta = el('span', 'exec-audit-count');
    auditMeta.textContent = turn.auditCount ? '审计 ' + turn.auditCount + ' 条' : '审计可回放';
    meta.append(auditMeta);
    turn.auditMetaEl = auditMeta;
    head.append(headCopy, meta);
    panel.append(head);

    if (turn.internalBody && turn.internalBody.childElementCount) {
      const body = turn.internalBody;
      turn.internalBody = null;
      turn.detailsBody = body;
      panel.append(body);
    } else {
      turn.detailsBody = null;
      panel.append(el('div', 'exec-empty', '本次为直接回复，未触发执行计划。'));
    }

    detailBtn.addEventListener('click', () => {
      const open = panel.hidden;
      panel.hidden = !open;
      detailBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
      detailBtn.classList.toggle('is-open', open);
      if (open && isNearBottom()) scrollToBottom(false);
    });
    if (state.settings && state.settings.details) {
      panel.hidden = false;
      detailBtn.setAttribute('aria-expanded', 'true');
      detailBtn.classList.add('is-open');
    }

    wrapper.append(actions, panel);
    appendBlock(turn, wrapper);

    turn.resultRendered = true;
    turn.resultTraceId = traceId;
    turn.awaiting = false;
    recomputeTurnAwaiting(turn);
    updatePlanStatus(turn);
    refreshSessionState({ quiet: true });
  }

  function resolveCancelledPending(turn) {
    /* “算了”由后端在另一个 trace/回合里返回结果；它取消的是上一条待确认业务，
     * 所以要跨回合结算页面上尚未处理的确认卡/强验证卡。 */
    const confirmContexts = Array.from(state.confirmContexts);
    confirmContexts.forEach((ctx) => {
      if (ctx.resolved) return;
      if (ctx.turn) ctx.turn.cancelled = true;
      settleConfirm(ctx, 'cancelled');
    });
    const stepupContexts = Array.from(state.stepupContexts.values());
    stepupContexts.forEach((ctx) => {
      if (ctx.resolved) return;
      if (ctx.turn) ctx.turn.cancelled = true;
      markStepupCancelled(ctx);
    });
    if (state.activeStepup) {
      closeStepupModal({ silent: true, force: true });
    }
    if (turn) {
      turn.cancelled = true;
      updatePlanStatus(turn);
    }
  }

  function markStepupCancelled(ctx) {
    ctx.resolved = true;
    ctx.submitting = false;
    removeStepupResume(ctx);
    state.stepupContexts.delete(ctx.challengeId);
    if (ctx.cardEl) {
      ctx.cardEl.classList.add('is-cancelled');
      const kicker = ctx.cardEl.querySelector('.stepup-inline-kicker');
      const title = ctx.cardEl.querySelector('.stepup-inline-title');
      const desc = ctx.cardEl.querySelector('.stepup-inline-desc');
      if (kicker) kicker.textContent = 'STEP-UP AUTHENTICATION · 已取消';
      if (title) title.textContent = '强验证已取消';
      if (desc) desc.textContent = '本次业务已取消，无需继续验证。';
      if (ctx.actionsEl) ctx.actionsEl.hidden = true;
      const copy = ctx.cardEl.querySelector('.stepup-inline-copy');
      if (copy) {
        const outcome = el('span', 'stepup-inline-outcome is-cancelled');
        outcome.append(iconNode('x'), el('span', null, '已取消'));
        copy.append(outcome);
      }
    }
    recomputeTurnAwaiting(ctx.turn);
  }

  function renderChatError(turn, code, message) {
    if (!turn || !turn.root || !turn.root.isConnected) return;
    removeTyping(turn);
    removeStreamNotice(turn);
    const actualCode = String(code || 'E_INTERNAL');
    const actualMessage = String(message || ERROR_HINTS[actualCode] || '操作未完成，请稍后重试。');
    const card = el('div', 'error-card');
    card.setAttribute('role', 'alert');
    const main = el('div', 'error-main');
    const title = el('div', 'error-title');
    title.append(
      el('span', null, '操作未完成'),
      el('span', 'error-code', actualCode)
    );
    main.append(title, el('div', 'error-message', actualMessage));
    card.append(iconNode('alert'), main);
    appendBlock(turn, card);
    turn.errorRendered = true;
    turn.terminal = true;
    turn.awaiting = false;
    updatePlanStatus(turn);
    refreshSessionState({ quiet: true });
    closeTurnStream(turn, { intentional: true, silent: true });
  }

  function renderSystemNote(turn, text) {
    if (!turn || !turn.root || !turn.root.isConnected) return;
    const node = el('div', 'system-note');
    node.append(iconNode('info'), el('span', null, String(text)));
    appendBlock(turn, node);
  }

  function renderFallbackResult(turn, reply) {
    renderResultCard(turn, {
      reply: reply,
      level: '',
      audit_url: ''
    });
  }

  function traceIdFromAuditUrl(url, fallback) {
    if (!url) return fallback || '';
    const match = String(url).match(/\/audit\/([^/?#]+)/);
    if (!match) return fallback || '';
    try {
      return decodeURIComponent(match[1]);
    } catch (err) {
      return match[1];
    }
  }

  /* ============================================================
   * 18. 强验证（Step-up）
   * ============================================================ */

  function renderStepupRequired(turn, data) {
    const challengeId = String(data.challenge_id || '');
    const factors = normalizeFactors(data.factors);
    const hint = String(data.hint || '演示验证码 123456');
    const summary = String(data.summary || '');
    const card = el('section', 'stepup-inline');
    card.setAttribute('aria-label', '强验证要求');

    const iconWrap = el('span', 'stepup-inline-icon');
    iconWrap.append(iconNode('shieldAlert'));
    const copy = el('div', 'stepup-inline-copy');
    copy.append(
      el('div', 'stepup-inline-kicker', 'STEP-UP AUTHENTICATION · 强验证'),
      el('div', 'stepup-inline-title', '为保护您的资金安全，请完成身份验证'),
      el(
        'div',
        'stepup-inline-desc',
        (summary ? '操作摘要：' + summary + ' · ' : '') +
          (factors.length ? '验证方式：' + factors.join(' + ') : '验证方式：短信验证码') +
          ' · ' +
          hint
      )
    );
    const actions = el('div', 'stepup-inline-actions');
    const hintChip = el('span', 'stepup-inline-hint', hint);
    const button = el('button', 'btn btn-primary');
    button.type = 'button';
    button.append(iconNode('shieldCheck'), el('span', null, '开始验证'));
    actions.append(hintChip, button);

    card.append(iconWrap, copy, actions);

    const ctx = {
      data: data,
      turn: turn,
      challengeId: challengeId,
      factors: factors,
      hint: hint,
      cardEl: card,
      actionsEl: actions,
      button: button,
      resolved: false,
      submitting: false,
      outcome: ''
    };

    if (challengeId) {
      state.stepupContexts.set(challengeId, ctx);
      turn.stepupCards.set(challengeId, ctx);
    }
    button.addEventListener('click', () => showStepupModal(ctx));

    appendInternal(turn, card);
    turn.awaiting = true;
    turn.awaitingType = 'stepup';
    updatePlanStatus(turn);

    if (state.route === 'chat') {
      showStepupModal(ctx);
    }
  }

  function showStepupResume(ctx) {
    if (!ctx || ctx.resolved || !ctx.turn || !ctx.turn.root.isConnected) return;
    if (ctx.resumeEl && ctx.resumeEl.isConnected) return;
    const strip = el('div', 'stepup-resume');
    strip.setAttribute('role', 'status');
    const copy = el('div', 'stepup-resume-copy');
    copy.append(
      el('div', 'stepup-resume-title', '需要完成强验证'),
      el('div', 'stepup-resume-text', '验证通过后才会继续执行本次业务。')
    );
    const button = el('button', 'btn btn-primary');
    button.type = 'button';
    button.append(iconNode('shieldCheck'), el('span', null, '继续验证'));
    button.addEventListener('click', () => showStepupModal(ctx));
    strip.append(iconNode('shieldAlert'), copy, button);
    ctx.resumeEl = strip;
    appendBlock(ctx.turn, strip);
  }

  function removeStepupResume(ctx) {
    if (!ctx || !ctx.resumeEl) return;
    if (ctx.resumeEl.parentNode) ctx.resumeEl.remove();
    ctx.resumeEl = null;
  }

  function showStepupModal(ctx) {
    if (!ctx || ctx.resolved) return;
    if (state.route !== 'chat') return;

    removeStepupResume(ctx);
    state.activeStepup = ctx;
    if (!state.modalTrigger || !state.modalTrigger.isConnected) {
      state.modalTrigger = ctx.button || document.activeElement;
    }

    const factors = ctx.factors && ctx.factors.length ? ctx.factors : ['短信验证码'];
    const summaryText = String((ctx.data && ctx.data.summary) || '');
    dom.stepupDesc.textContent = (summaryText ? '操作：' + summaryText + '。' : '') +
      (factors.join(' + ') === '短信验证码'
        ? '为保护您的资金安全，请输入短信验证码。'
        : '请完成『' + factors.join(' + ') + '』身份验证。演示环境使用短信验证码。');

    dom.stepupHint.replaceChildren();
    const rawHint = String(ctx.hint || '演示验证码 123456');
    const codeMatch = rawHint.match(/(\d{4,8})/);
    if (codeMatch) {
      const before = rawHint.slice(0, codeMatch.index);
      const after = rawHint.slice(codeMatch.index + codeMatch[0].length);
      if (before) dom.stepupHint.append(document.createTextNode(before));
      dom.stepupHint.append(el('strong', null, codeMatch[0]));
      if (after) dom.stepupHint.append(document.createTextNode(after));
    } else {
      dom.stepupHint.textContent = rawHint;
    }

    resetStepupInputs(false);
    dom.stepupError.hidden = true;
    dom.stepupModal.hidden = false;
    window.requestAnimationFrame(() => {
      if (dom.stepupInputs[0]) dom.stepupInputs[0].focus();
    });
  }

  function closeStepupModal(options) {
    const opts = options || {};
    const ctx = state.activeStepup;
    if (ctx && ctx.submitting && !opts.force) return;
    if (state.autoSubmitTimer) {
      window.clearTimeout(state.autoSubmitTimer);
      state.autoSubmitTimer = null;
    }
    dom.stepupModal.hidden = true;
    dom.stepupError.hidden = true;
    state.activeStepup = null;
    resetStepupInputs(false);
    if (opts.keepContext === false && ctx) {
      ctx.resolved = true;
      if (ctx.cardEl) ctx.cardEl.classList.add('is-resolved');
    }
    if (ctx && !ctx.resolved && state.route === 'chat' && state.sessionState !== 'LOCKED') {
      showStepupResume(ctx);
    }
    if (!opts.silent) {
      if (state.sessionState === 'LOCKED') {
        showToast('会话已锁定，请开启新会话。', 'error');
      } else if (!ctx || !ctx.resolved) {
        showToast('已暂缓强验证，可在对话中点击「继续验证」。', 'info');
      }
    }
    if (!opts.keepFocus && state.modalTrigger && state.modalTrigger.isConnected && !dom.chatView.hidden) {
      try {
        state.modalTrigger.focus();
      } catch (err) {
        /* noop */
      }
    }
    state.modalTrigger = null;
  }

  function resetStepupInputs(focusFirst) {
    dom.stepupInputs.forEach((input) => {
      input.value = '';
      input.disabled = false;
      input.classList.remove('is-filled');
    });
    dom.stepupSubmit.disabled = true;
    dom.stepupSubmit.replaceChildren(el('span', null, '验证并继续'));
    dom.stepupError.hidden = true;
    dom.stepupError.textContent = '';
    if (focusFirst === true && dom.stepupInputs[0]) dom.stepupInputs[0].focus();
  }

  function getStepupCode() {
    return dom.stepupInputs.map((input) => input.value).join('');
  }

  function updateStepupSubmitState() {
    dom.stepupSubmit.disabled = getStepupCode().length !== 6;
  }

  function handleStepupInput(index) {
    const input = dom.stepupInputs[index];
    const digits = String(input.value || '').replace(/\D/g, '');
    input.value = digits ? digits.slice(-1) : '';
    input.classList.toggle('is-filled', !!input.value);
    dom.stepupError.hidden = true;

    if (input.value && index < dom.stepupInputs.length - 1) {
      dom.stepupInputs[index + 1].focus();
    }
    updateStepupSubmitState();
    scheduleStepupAutoSubmit();
  }

  function handleStepupKeydown(event, index) {
    if (event.key === 'Backspace' && !dom.stepupInputs[index].value && index > 0) {
      event.preventDefault();
      const previous = dom.stepupInputs[index - 1];
      previous.value = '';
      previous.classList.remove('is-filled');
      previous.focus();
      updateStepupSubmitState();
      return;
    }
    if (event.key === 'ArrowLeft' && index > 0) {
      event.preventDefault();
      dom.stepupInputs[index - 1].focus();
      return;
    }
    if (event.key === 'ArrowRight' && index < dom.stepupInputs.length - 1) {
      event.preventDefault();
      dom.stepupInputs[index + 1].focus();
      return;
    }
    if (event.key === 'Enter') {
      event.preventDefault();
      if (getStepupCode().length === 6) submitStepup();
    }
  }

  function handleStepupPaste(event) {
    event.preventDefault();
    const text = event.clipboardData ? event.clipboardData.getData('text') : '';
    const digits = String(text || '').replace(/\D/g, '').slice(0, 6);
    if (!digits) return;
    dom.stepupInputs.forEach((input, index) => {
      input.value = digits.charAt(index) || '';
      input.classList.toggle('is-filled', !!input.value);
    });
    const next = Math.min(digits.length, dom.stepupInputs.length - 1);
    dom.stepupInputs[next].focus();
    updateStepupSubmitState();
    scheduleStepupAutoSubmit();
  }

  function scheduleStepupAutoSubmit() {
    if (state.autoSubmitTimer) window.clearTimeout(state.autoSubmitTimer);
    state.autoSubmitTimer = null;
    if (getStepupCode().length !== 6) return;
    state.autoSubmitTimer = window.setTimeout(() => {
      state.autoSubmitTimer = null;
      if (state.activeStepup && !state.activeStepup.submitting) submitStepup();
    }, 380);
  }

  function showStepupError(message, code) {
    dom.stepupError.textContent = (code ? code + ' · ' : '') + message;
    dom.stepupError.hidden = false;
    const wrap = dom.stepupInputs[0] ? dom.stepupInputs[0].parentElement : null;
    if (wrap) {
      wrap.classList.remove('is-shaking');
      void wrap.offsetWidth;
      wrap.classList.add('is-shaking');
      window.setTimeout(() => wrap.classList.remove('is-shaking'), 360);
    }
  }

  function setStepupBusy(busy) {
    dom.stepupInputs.forEach((input) => {
      input.disabled = busy;
    });
    dom.stepupSubmit.disabled = busy || getStepupCode().length !== 6;
    if (busy) {
      dom.stepupSubmit.replaceChildren(el('span', 'spinner'), el('span', null, '验证中…'));
    } else {
      dom.stepupSubmit.replaceChildren(el('span', null, '验证并继续'));
    }
  }

  async function submitStepup() {
    const ctx = state.activeStepup;
    if (!ctx || ctx.resolved || ctx.submitting) return;
    if (state.autoSubmitTimer) {
      window.clearTimeout(state.autoSubmitTimer);
      state.autoSubmitTimer = null;
    }
    const code = getStepupCode();
    if (!/^\d{6}$/.test(code)) {
      showStepupError('请输入完整的 6 位验证码。', 'E_PARAM');
      return;
    }

    ctx.submitting = true;
    setStepupBusy(true);
    dom.stepupError.hidden = true;
    markBusy('stepup:' + ctx.challengeId);
    closeTurnStream(ctx.turn, { intentional: true, keepBusy: false, silent: true });

    try {
      const response = await fetchJSON('/v1/stepup/verify', {
        method: 'POST',
        body: {
          session_id: state.sessionId,
          challenge_id: ctx.challengeId,
          code: code
        },
        timeout: 60000
      });

      const failure = responseFailure(response);
      if (failure) {
        showStepupError(failure.message, failure.code);
        if (failure.locked) {
          setSessionState('LOCKED');
          markStepupLocked(ctx);
        } else {
          handleStepupFailure(ctx, failure.code);
        }
        refreshSessionState({ quiet: true });
        return;
      }

      markStepupResolved(ctx);
      recordMessage('assistant', response && response.reply);
      closeStepupModal({ silent: true, keepFocus: false });
      showToast('强验证已通过。', 'success');

      const events = response && Array.isArray(response.events) ? response.events : [];
      if (events.length) {
        renderEventBatch(ctx.turn, events, 'stepup:' + ctx.challengeId);
      } else if (response && response.reply) {
        renderResultCard(ctx.turn, {
          reply: response.reply,
          level: '',
          audit_url: ''
        });
      } else if (response && response.status === 'awaiting_stepup') {
        renderChatError(ctx.turn, 'E_PARAM', '服务要求新的强验证挑战，但未返回验证信息。');
      } else {
        renderSystemNote(ctx.turn, '强验证已通过，等待银行服务返回执行结果。');
      }
      refreshSessionState({ quiet: true });
    } catch (err) {
      const code = err.code || 'E_STEPUP_FAIL';
      showStepupError(describeError(err), code);
      handleStepupFailure(ctx, code);
      reportTransportError(err);
      refreshSessionState({ quiet: true });
    } finally {
      clearBusy('stepup:' + ctx.challengeId);
      if (!ctx.resolved) {
        ctx.submitting = false;
        setStepupBusy(false);
      }
    }
  }

  function handleStepupFailure(ctx, code) {
    ctx.submitting = false;
    if (state.autoSubmitTimer) {
      window.clearTimeout(state.autoSubmitTimer);
      state.autoSubmitTimer = null;
    }
    setStepupBusy(false);
    if (code === 'E_STEPUP_LOCK' || code === 'E_STEPUP_LOCKED') {
      setSessionState('LOCKED');
      markStepupLocked(ctx);
      return;
    }
    if (code === 'E_STEPUP_EXPIRED') {
      ctx.resolved = true;
      state.stepupContexts.delete(ctx.challengeId);
      if (ctx.cardEl) {
        ctx.cardEl.classList.add('is-resolved');
        ctx.actionsEl.replaceChildren(el('span', 'stepup-inline-outcome', '验证已过期，请重新发起'));
      }
      recomputeTurnAwaiting(ctx.turn);
      return;
    }
    dom.stepupInputs.forEach((input) => {
      input.value = '';
      input.classList.remove('is-filled');
    });
    dom.stepupSubmit.disabled = true;
    if (dom.stepupInputs[0]) dom.stepupInputs[0].focus();
  }

  function markStepupResolved(ctx) {
    ctx.resolved = true;
    ctx.submitting = false;
    removeStepupResume(ctx);
    state.stepupContexts.delete(ctx.challengeId);
    if (ctx.cardEl) {
      ctx.cardEl.classList.add('is-resolved');
      const kicker = ctx.cardEl.querySelector('.stepup-inline-kicker');
      const title = ctx.cardEl.querySelector('.stepup-inline-title');
      const desc = ctx.cardEl.querySelector('.stepup-inline-desc');
      if (kicker) kicker.textContent = 'STEP-UP AUTHENTICATION · 验证完成';
      if (title) title.textContent = '强验证已通过';
      if (desc) desc.textContent = '短信验证码校验通过，业务将继续执行。';
      if (ctx.actionsEl) ctx.actionsEl.hidden = true;
      const copy = ctx.cardEl.querySelector('.stepup-inline-copy');
      if (copy) {
        const outcome = el('span', 'stepup-inline-outcome');
        outcome.append(iconNode('check'), el('span', null, '已验证通过'));
        copy.append(outcome);
      }
    }
    recomputeTurnAwaiting(ctx.turn);
  }

  function markStepupLocked(ctx) {
    ctx.resolved = true;
    ctx.submitting = false;
    removeStepupResume(ctx);
    state.stepupContexts.delete(ctx.challengeId);
    if (ctx.turn) {
      ctx.turn.locked = true;
      updatePlanStatus(ctx.turn);
    }
    if (ctx.cardEl) {
      ctx.cardEl.classList.add('is-locked');
      const kicker = ctx.cardEl.querySelector('.stepup-inline-kicker');
      const title = ctx.cardEl.querySelector('.stepup-inline-title');
      const desc = ctx.cardEl.querySelector('.stepup-inline-desc');
      if (kicker) kicker.textContent = 'STEP-UP AUTHENTICATION · 安全锁定';
      if (title) title.textContent = '会话已被安全锁定';
      if (desc) desc.textContent = '强验证失败次数过多，请开启新会话后继续。';
      if (ctx.actionsEl) ctx.actionsEl.hidden = true;
      const copy = ctx.cardEl.querySelector('.stepup-inline-copy');
      if (copy) {
        const outcome = el('span', 'stepup-inline-outcome is-locked');
        outcome.append(iconNode('lock'), el('span', null, '会话已锁定，请开启新会话'));
        copy.append(outcome);
      }
    }
    dom.stepupInputs.forEach((input) => {
      input.value = '';
      input.disabled = true;
      input.classList.remove('is-filled');
    });
    dom.stepupSubmit.disabled = true;
    dom.stepupSubmit.replaceChildren(iconNode('lock'), el('span', null, '会话已锁定'));
    showStepupError('强验证失败次数过多，会话已被安全锁定。', 'E_STEPUP_LOCK');
    if (ctx.turn) {
      renderChatError(ctx.turn, 'E_STEPUP_LOCKED', '强验证失败次数过多，会话已锁定。请开启新会话后继续。');
    }
    recomputeTurnAwaiting(ctx.turn);
  }

  function handleModalKeydown(event) {
    if (event.key !== 'Tab' || dom.stepupModal.hidden) return;
    const focusables = $$(
      'button:not([disabled]), input:not([disabled]), [href], [tabindex]:not([tabindex="-1"])',
      dom.stepupModal
    ).filter((node) => node.offsetParent !== null);
    if (!focusables.length) return;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    } else if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    }
  }

  dom.modalPanel.addEventListener('keydown', handleModalKeydown);

  /* ============================================================
   * 19. 审计回放页
   * ============================================================ */

  async function loadAudit(traceId) {
    const id = String(traceId || '').trim();
    state.auditTraceId = id;
    state.auditData = null;
    dom.auditTraceId.textContent = id || '—';
    dom.auditTraceId.title = id || '';
    dom.auditDownloadBtn.disabled = true;
    document.title = id ? '审计回放 · ' + id + ' · 盾语' : '审计回放 · 盾语';

    if (state.auditAbort) {
      state.auditAbort.abort();
      state.auditAbort = null;
    }

    if (!id || !/^[A-Za-z0-9_-]{1,80}$/.test(id)) {
      renderAuditError(id, new ApiError('E_PARAM', '缺少或非法的 trace_id，无法加载审计记录。', 400));
      return;
    }

    showAuditLoading();
    const controller = new AbortController();
    state.auditAbort = controller;

    try {
      const data = await fetchJSON('/v1/audit/' + encodeURIComponent(id), {
        signal: controller.signal,
        timeout: 20000
      });
      if (controller.signal.aborted) return;
      state.auditData = data || {};
      renderAuditReport(id, data || {});
      dom.auditDownloadBtn.disabled = false;
    } catch (err) {
      if (err && (err.code === 'E_ABORTED' || controller.signal.aborted)) return;
      renderAuditError(id, err);
    } finally {
      if (state.auditAbort === controller) state.auditAbort = null;
    }
  }

  function showAuditLoading() {
    const loading = el('div', 'audit-loading');
    loading.setAttribute('aria-label', '审计记录加载中');
    loading.append(
      el('div', 'skeleton skeleton-summary'),
      el('div', 'skeleton skeleton-line'),
      el('div', 'skeleton skeleton-line short'),
      el('div', 'skeleton skeleton-line')
    );
    dom.auditContent.replaceChildren(loading);
  }

  function renderAuditReport(traceId, data) {
    const events = Array.isArray(data.events) ? data.events.slice() : [];
    events.sort((a, b) => {
      const seqA = Number(a.seq);
      const seqB = Number(b.seq);
      if (Number.isFinite(seqA) && Number.isFinite(seqB)) return seqA - seqB;
      return 0;
    });

    const chainOk = data.chain_ok === true ? true : data.chain_ok === false ? false : null;
    const summary = buildChainSummary(events, chainOk);
    const panel = buildAuditPanel(events);
    dom.auditContent.replaceChildren(summary, panel);
    document.title = '审计回放 · ' + traceId + ' · 盾语';
  }

  function buildChainSummary(events, chainOk) {
    const card = el(
      'section',
      'chain-card ' + (chainOk === true ? 'is-ok' : chainOk === false ? 'is-bad' : 'is-unknown')
    );
    card.setAttribute('role', 'status');

    const iconWrap = el('span', 'chain-icon');
    iconWrap.append(iconNode(chainOk === true ? 'shieldCheck' : chainOk === false ? 'alert' : 'info'));

    const copy = el('div', 'chain-copy');
    copy.append(
      el('div', 'chain-kicker', 'HASH CHAIN VERIFICATION · 哈希链校验'),
      el(
        'div',
        'chain-title',
        chainOk === true
          ? '哈希链校验通过'
          : chainOk === false
            ? '哈希链校验失败'
            : '哈希链状态未返回'
      ),
      el(
        'div',
        'chain-desc',
        chainOk === true
          ? 'prev_hash 逐条串联，任何一条记录被改写都会导致后续哈希校验失败。'
          : chainOk === false
            ? '检测到哈希链断裂或记录缺失，审计数据可能被篡改或写入异常，请立即核查。'
            : '后端未返回 chain_ok 字段，无法确认链完整性。'
      )
    );

    const actors = new Set();
    events.forEach((event) => {
      if (event.actor) actors.add(String(event.actor));
    });
    const span = auditTimeSpan(events);

    const stats = el('div', 'chain-stats');
    [
      ['事件数', String(events.length)],
      ['参与角色', String(actors.size)],
      ['时间跨度', span]
    ].forEach((pair) => {
      const stat = el('div', 'stat');
      stat.append(el('span', 'stat-value', pair[1]), el('span', 'stat-label', pair[0]));
      stats.append(stat);
    });

    const lastEvent = events.length ? events[events.length - 1] : null;
    const lastHash = lastEvent && lastEvent.hash ? String(lastEvent.hash) : '';
    const proof = el('div', 'chain-proof');
    proof.append(
      el('span', 'proof-node', 'GENESIS'),
      el('span', 'proof-arrow', '→'),
      el('span', 'proof-node', 'SEQ 1…' + (events.length || 0)),
      el('span', 'proof-arrow', '→')
    );
    const head = el(
      'span',
      'proof-node is-head',
      lastHash ? 'HEAD ' + shortHash(lastHash, 16) : 'HEAD —'
    );
    if (lastHash) head.title = lastHash;
    proof.append(head);

    card.append(iconWrap, copy, stats, proof);
    return card;
  }

  function auditTimeSpan(events) {
    const times = events
      .map((event) => parseDateMs(event.ts))
      .filter((value) => value !== null)
      .sort((a, b) => a - b);
    if (times.length < 2) return '—';
    const seconds = Math.max(0, Math.round((times[times.length - 1] - times[0]) / 1000));
    if (seconds < 60) return seconds + ' 秒';
    const minutes = Math.floor(seconds / 60);
    const remain = seconds % 60;
    if (minutes < 60) return remain ? minutes + ' 分 ' + remain + ' 秒' : minutes + ' 分';
    const hours = Math.floor(minutes / 60);
    return hours + ' 小时 ' + (minutes % 60) + ' 分';
  }

  function buildAuditPanel(events) {
    const panel = el('section', 'audit-panel');
    const head = el('div', 'audit-panel-head');
    head.append(
      el('div', 'audit-panel-title', '决策链路 · ' + events.length + ' 条事件'),
      el('div', 'audit-panel-sub', '按 seq 升序 · 点击 payload 展开原始记录')
    );
    panel.append(head);

    if (!events.length) {
      const empty = el('div', 'audit-empty');
      const icon = el('div', 'audit-empty-icon');
      icon.append(iconNode('history'));
      empty.append(
        icon,
        el('h3', null, '该 trace 暂无审计事件'),
        el('p', null, '会话可能尚未产生执行记录，或审计写入仍在进行中。')
      );
      panel.append(empty);
      return panel;
    }

    const list = el('ol', 'audit-list');
    events.forEach((event, index) => {
      list.append(buildAuditItem(event, index, events.length));
    });
    panel.append(list);
    return panel;
  }

  function buildAuditItem(event, index, total) {
    const actor = String(event.actor || 'system').toLowerCase();
    const actorMeta = ACTOR_META[actor] || { cn: '系统', tone: 'blue', cls: 'actor-system' };
    const decision = String(event.decision || '').toLowerCase();
    const decisionMeta = DECISION_META[decision] || null;
    const isErrorDecision = decision === 'deny' || decision === 'error' || decision === 'fail';
    const tone = isErrorDecision ? 'red' : actorMeta.tone;

    const item = el('li', 'audit-item');
    item.dataset.tone = tone;
    item.style.animationDelay = Math.min(index * 20, 320) + 'ms';

    const parts = timestampParts(event.ts);
    const time = el('div', 'audit-time');
    time.title = event.ts ? String(event.ts) : '';
    time.append(document.createTextNode(parts.time), el('small', null, parts.date));

    const rail = el('div', 'audit-rail');
    rail.append(el('span', 'audit-dot'));

    const card = el('div', 'audit-card');
    const head = el('div', 'audit-card-head');
    head.append(el('span', 'audit-seq', '#' + String(event.seq === undefined ? index + 1 : event.seq).padStart(2, '0')));
    const actorChip = el('span', 'actor-chip ' + actorMeta.cls, actor);
    actorChip.title = actorMeta.cn;
    head.append(actorChip);
    head.append(el('span', 'audit-action', String(event.action || event.type || 'event')));
    if (decision) {
      const chip = el(
        'span',
        'decision-chip ' + (decisionMeta ? decisionMeta.cls : ''),
        decisionMeta ? decisionMeta.cn : decision
      );
      chip.title = 'decision: ' + decision;
      head.append(chip);
    }

    const sub = el('div', 'audit-card-sub');
    sub.append(el('span', null, actorMeta.cn));
    sub.append(el('span', 'audit-time-inline', parts.full));
    if (event.node_id) sub.append(el('span', 'audit-tag', 'node ' + event.node_id));

    card.append(head, sub);

    if (event.payload !== undefined && event.payload !== null) {
      const details = document.createElement('details');
      details.className = 'payload-details';
      const summary = document.createElement('summary');
      summary.textContent = 'payload · 展开 JSON';
      const pre = document.createElement('pre');
      pre.className = 'payload-json';
      pre.textContent = safeJson(event.payload);
      details.append(summary, pre);
      card.append(details);
    }

    if (event.hash || event.prev_hash) {
      const hashLine = el('div', 'hash-line');
      if (event.prev_hash) {
        const prev = el('span', 'hash-pill', 'prev ' + shortHash(event.prev_hash, 18));
        prev.title = String(event.prev_hash);
        hashLine.append(prev);
      }
      if (event.hash) {
        const hash = el(
          'span',
          'hash-pill' + (index === total - 1 ? ' is-head' : ''),
          'hash ' + shortHash(event.hash, 22)
        );
        hash.title = String(event.hash);
        hashLine.append(hash);
      }
      card.append(hashLine);
    }

    item.append(time, rail, card);
    return item;
  }

  function renderAuditError(traceId, err) {
    const code = err && err.code ? err.code : 'E_INTERNAL';
    let message = err ? describeError(err) : '审计记录加载失败。';
    if (err && err.status === 404) {
      message = '未找到该 trace 的审计记录，可能尚未产生事件或服务已重置数据库。';
    }
    const box = el('div', 'audit-error');
    box.setAttribute('role', 'alert');
    const icon = el('div', 'audit-error-icon');
    icon.append(iconNode('alert'));
    const title = el('h3');
    title.append(document.createTextNode('审计记录无法加载'));
    title.append(el('span', 'error-code', code));
    box.append(icon, title, el('p', null, message));

    const actions = el('div', 'audit-actions');
    const retry = el('button', 'btn btn-primary', '重试');
    retry.type = 'button';
    retry.addEventListener('click', () => loadAudit(traceId));
    const back = el('button', 'btn btn-secondary', '返回对话');
    back.type = 'button';
    back.addEventListener('click', () => {
      window.location.hash = '#/';
    });
    actions.append(retry, back);
    box.append(actions);
    dom.auditContent.replaceChildren(box);
    dom.auditDownloadBtn.disabled = true;
  }

  function downloadAuditJson() {
    if (!state.auditData || !state.auditTraceId) {
      showToast('暂无可下载的审计数据。', 'error');
      return;
    }
    try {
      const payload = JSON.stringify(state.auditData, null, 2);
      const blob = new Blob([payload], { type: 'application/json;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = 'audit_' + state.auditTraceId + '.json';
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1200);
      showToast('审计 JSON 已下载。', 'success');
    } catch (err) {
      showToast('下载失败：' + (err.message || err), 'error');
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initialize);
  } else {
    initialize();
  }
})();
