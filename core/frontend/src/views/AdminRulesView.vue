<script setup>
import { computed, onMounted, onUnmounted, ref } from "vue";
import { Save, FlaskConical, Sparkles, Trash2, Link2, Settings2, PlugZap, RefreshCw, Copy } from "lucide-vue-next";
import { api, mediaUrl } from "../api/client";

const rules = ref([]);
const current = ref(null);
const configText = ref("");
const sampleText = ref("");
const dryrun = ref(null);
const llm = ref({ available: false, model: null });
const flash = ref("");
const sources = ref([]);
const collectionSources = ref([]);
const bindSourceId = ref(null);
const sampleSourceId = ref(null);
const sampleLimit = ref(20);
const showSettings = ref(false);
const llmForm = ref({ base_url: "", model: "", api_key: "", allow_private: false });
const llmTestResult = ref("");
const apForm = ref({ autopublish_enabled: true, autopublish_threshold: 0.8 });
const activeTab = ref("listing");
const dialogs = ref([]);
const dutyDialogKey = ref("");
const dutyForm = ref({});
const profileForm = ref({});
const profileResults = ref([]);
const profileSummary = ref(null);
const historyScan = ref(null);
const profileUpdatedAt = ref(null);
const profileUsernames = ref("");
const profileSourceId = ref(null);
let profileRefreshTimer;
const filteredRules = computed(() => rules.value.filter((r) => (r.rule_type || "listing") === activeTab.value));

const EMPTY_DUTY_CONFIG = {
  sender_username: "",
  sender_peer_id: null,
  match_prefix: "",
  search_text: "",
  update_mode: "replace_snapshot",
  identity_mode: "direct_username_links",
  target_channel_username: "",
  auto_collect_enabled: true,
  collection_source_id: null,
  profile_query_required: false,
  exclude_usernames: [],
  business_cutoff_hour: 4,
};

const EMPTY_PROFILE_CONFIG = {
  recent_window: 10,
  max_scan_messages: 100,
  max_photos: 6,
  max_videos: 2,
  intro_min_chars: 30,
  intro_min_lines: 3,
  channel_any_sender_matches: true,
  skip_video_when_photo_found: true,
  exclude_avatar: true,
  pinned_followup_messages: 5,
};

const EMPTY_CHECKIN_CONFIG = { collection_mode: "checkin_source_reverse", source_username: "" };
const checkinForm = ref({ ...EMPTY_CHECKIN_CONFIG });

const EMPTY_CONFIG = {
  gate: { require_any_keywords: [], blocked_sender_peer_ids: [] },
  extract: { nickname: [], username: [], price: [], district: [], currency_map: {} },
  ocr: { enabled: true, trigger_when_missing: ["price"] },
  lifecycle_terms: ["已售", "已出", "离开", "休息", "已结"],
  display: { exclude_patterns: [] },
};

const FIELD_HELP = [
  ["_说明（任意层级）",
    "注释键，引擎自动忽略，可在 JSON 任何位置写给自己看。如 \"_说明\": \"价格取第一个数字，乘100变成元\""],
  ["gate.require_any_keywords",
    "门禁开关：正文含任一关键词才处理，空数组=不过滤。写法 [\"老师编号\"]"],
  ["gate.blocked_sender_peer_ids",
    "发送者黑名单：这些频道号发的消息直接丢弃。写法 [-1001942025496]"],
  ["extract.nickname / username / district",
    "正则数组（Python re 语法），按顺序试，首个命中生效；正则必须恰好带 1 个捕获组 (…) 括住要的值。username 存储时自动去掉前导@，捕获组里带不带 @ 都不会出现双@@"],
  ["extract.price",
    "价格正则数组，同上带 1 个捕获组。例：【修车水费】：#6P → \"【修车水费】[#：:\\\\s]*([0-9]+(?:\\\\.[0-9]{1,2})?)\" 抓到 6；多条消息里有多个价格时，取正则匹配到的第一个数字"],
  ["extract.price_multiply",
    "价格倍率（数字）：提取后自动相乘。填 100 表示 6P→600 元；不填或 0 = 不乘。小木屋 800P 本身就是元，不要填"],
  ["extract.currency_map",
    "币种判定：{\"正则\": \"币种代码\"}，正文命中正则就用该币种。写法 {\"P\": \"CNY\"}"],
  ["ocr.enabled / trigger_when_missing",
    "图片兜底：缺这些字段时对照片 OCR 再抽一遍（OCR 结果置信度封顶 0.79，会进人工审核）。写法 {\"enabled\": true, \"trigger_when_missing\": [\"price\"]}"],
  ["lifecycle_terms",
    "下架词：新消息命中任一词，自动下架该联系人已上架卡片。写法 [\"已售\", \"已出\", \"离开\"]"],
  ["display.exclude_patterns",
    "展示剔除：正则数组，命中的整行不对外显示（原始数据不动）。写法 [\"宁波天一生水\", \"频道[:：]\"]"],
  ["改完规则怎么生效",
    "①保存 ②在「绑定来源」处选群组点「重跑提取」→ 存量卡片按新规则重算，结果有变化时原卡自动刷新（发布时间保持消息时间，不重复建卡）"],
];

async function reextractSource() {
  if (!bindSourceId.value) {
    notify("先选择要重跑的来源");
    return;
  }
  const data = await api("/api/admin/rules/reextract", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source_id: bindSourceId.value }),
  });
  notify(`已重置 ${data.requeued} 条消息，正在按当前规则重新提取…`);
}

function notify(msg) {
  flash.value = msg;
  setTimeout(() => (flash.value = ""), 3000);
}

// ---- 动态黑话词表与纠偏建议（热更新闭环） ----

const slangItems = ref([]);
const insightMinHits = 2;
const insights = ref([]);
const learnedCandidates = ref([]);
const candidateResults = ref({});
const preview = ref(null);
const previewBusy = ref(false);
const pendingAdopt = ref(null);
const slangForm = ref({ category: "tariff_alias", source_term: "", target_action: "", factor: null });

async function loadSlang() {
  slangItems.value = (await api("/api/admin/rules/slang-registry")).items;
}
async function loadInsights() {
  insights.value = (
    await api(`/api/admin/rules/correction-insights?days=30&min_hits=${insightMinHits}`)
  ).items;
}
async function loadCandidates() {
  learnedCandidates.value = (await api("/api/admin/rules/candidates")).items || [];
  candidateResults.value = {};
}
async function backtestCandidate(candidate) {
  candidateResults.value = { ...candidateResults.value, [candidate.candidate_key]: { loading: true } };
  try {
    const result = await api("/api/admin/rules/candidates/backtest", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ candidate_key: candidate.candidate_key }),
    });
    candidateResults.value = { ...candidateResults.value, [candidate.candidate_key]: result };
  } catch (e) {
    candidateResults.value = { ...candidateResults.value, [candidate.candidate_key]: { error: String(e.message) } };
  }
}
async function adoptCandidate(candidate) {
  try {
    await api("/api/admin/rules/candidates/adopt", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ candidate_key: candidate.candidate_key }),
    });
    notify(`「${candidate.raw_pattern}」已采纳并热生效`);
    await Promise.all([loadCandidates(), loadSlang()]);
  } catch (e) {
    notify(`采纳失败: ${String(e.message).slice(0, 100)}`);
  }
}
async function dismissCandidate(candidate) {
  await api("/api/admin/rules/candidates/dismiss", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ candidate_key: candidate.candidate_key }),
  });
  learnedCandidates.value = learnedCandidates.value.filter((item) => item.candidate_key !== candidate.candidate_key);
}
async function reloadSlang() {
  const data = await api("/api/admin/rules/slang-registry/reload", { method: "POST" });
  notify(`词表已重载：${data.applied_terms} 条动态词条生效`);
  await loadSlang();
}
async function upsertSlang() {
  const body = {
    category: slangForm.value.category,
    source_term: slangForm.value.source_term.trim(),
    target_action: slangForm.value.target_action.trim() || null,
    factor: slangForm.value.factor || null,
    enabled: true,
  };
  try {
    const data = await api("/api/admin/rules/slang-registry", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    notify(`词条「${data.item.source_term}」已热生效（动态词表 ${data.applied_terms} 条）`);
    slangForm.value = { category: slangForm.value.category, source_term: "", target_action: "", factor: null };
    await loadSlang();
  } catch (e) {
    notify(`保存失败: ${String(e.message).slice(0, 80)}`);
  }
}
async function toggleSlang(item) {
  await api(`/api/admin/rules/slang-registry/${item.id}/toggle`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled: !item.enabled }),
  });
  await loadSlang();
}
async function deleteSlang(item) {
  await api(`/api/admin/rules/slang-registry/${item.id}`, { method: "DELETE" });
  await loadSlang();
}
async function adoptSuggestion(suggestion) {
  pendingAdopt.value = suggestion;
  previewBusy.value = true;
  try {
    // 先热应用候选跑回测（服务端跑完会恢复基线，未入库不残留）
    let config = {};
    try { config = JSON.parse(configText.value || "{}"); } catch { config = {}; }
    preview.value = await api("/api/admin/rules/slang-registry/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        category: suggestion.category,
        source_term: suggestion.source_term,
        target_action: suggestion.target_action,
        config,
        sample_set: "golden",
        sample_limit: 30,
      }),
    });
  } catch (e) {
    notify(`回测失败: ${String(e.message).slice(0, 80)}`);
    pendingAdopt.value = null;
  } finally {
    previewBusy.value = false;
  }
}
async function confirmAdopt() {
  if (!pendingAdopt.value) return;
  previewBusy.value = true;
  try {
    const data = await api("/api/admin/rules/slang-registry", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...pendingAdopt.value, enabled: true }),
    });
    notify(`词条「${data.item.source_term}」已入库并热生效（动态词表 ${data.applied_terms} 条）`);
    preview.value = null;
    pendingAdopt.value = null;
    await loadSlang();
  } catch (e) {
    notify(`入库失败: ${String(e.message).slice(0, 80)}`);
  } finally {
    previewBusy.value = false;
  }
}

const boundGroups = () => (current.value && current.value.bindings) || [];

function select(rule) {
  current.value = rule;
  configText.value = JSON.stringify(rule.config, null, 2);
  dryrun.value = null;
  dutyForm.value = { ...EMPTY_DUTY_CONFIG, ...(rule.config || {}) };
  profileForm.value = { ...EMPTY_PROFILE_CONFIG, ...(rule.config || {}) };
  checkinForm.value = { ...EMPTY_CHECKIN_CONFIG, ...(rule.config || {}) };
  // default the sample drawer to the first group this rule is bound to
  const first = (rule.bindings || [])[0];
  if (first) sampleSourceId.value = first.source_id;
  if (["profile_collection", "bot_collection", "checkin_reverse"].includes(activeTab.value) && rule.id) {
    loadProfileResults(rule.id);
    if (activeTab.value === "checkin_reverse") loadHistoryScan(rule.id);
    else historyScan.value = null;
  } else {
    profileResults.value = [];
    historyScan.value = null;
  }
  profileSourceId.value = rule.bindings?.[0]?.source_id || collectionSources.value[0]?.id || null;
}

async function loadProfileResults(ruleId = current.value?.id) {
  if (!ruleId) return;
  const data = await api(`/api/admin/rules/${ruleId}/profile-collection?limit=100`);
  profileResults.value = data.items || [];
  profileSummary.value = data.summary || null;
  profileUpdatedAt.value = new Date();
}

async function loadHistoryScan(ruleId = current.value?.id) {
  if (!ruleId) return;
  const data = await api(`/api/admin/rules/${ruleId}/history-scan`);
  historyScan.value = data.scan || null;
}

function profileProgress() {
  if (!profileSummary.value?.total) return 0;
  return Math.round(profileSummary.value.completed * 100 / profileSummary.value.total);
}

function profileCooldownText(value) {
  if (!value) return "";
  const seconds = Math.max(0, Math.floor((Date.parse(value) - Date.now()) / 1000));
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (days) return `${days}天${hours}小时`;
  if (hours) return `${hours}小时${minutes}分钟`;
  return `${minutes}分钟`;
}

function profileCooldownDeadline(value) {
  return value ? new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" }) : "";
}

async function enqueueProfileUsername() {
  if (!current.value?.id || !profileUsernames.value.trim()) return notify("请输入 Telegram 用户名");
  if (!profileSourceId.value) return notify("请选择采集账号来源");
  const data = await api(`/api/admin/rules/${current.value.id}/profile-collection/enqueue`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ telegram_usernames: profileUsernames.value.split(/[\n\r,，;；\s]+/).filter(Boolean), source_id: profileSourceId.value }),
  });
  profileUsernames.value = "";
  await loadProfileResults();
  notify(`已提交 ${data.queued} 个 TG 号，${data.skipped} 个未入队`);
}

async function requeueIneffectiveProfiles() {
  if (!current.value?.id) return;
  const data = await api(`/api/admin/rules/${current.value.id}/profile-collection/requeue-ineffective`, {
    method: "POST",
  });
  await loadProfileResults();
  notify(`已将 ${data.requeued} 个无有效资料任务重新排队`);
}

function newRule() {
  current.value = { rule_name: "", rule_type: activeTab.value, is_active: true };
  const config = activeTab.value === "on_duty" ? EMPTY_DUTY_CONFIG
    : ["profile_collection", "bot_collection"].includes(activeTab.value) ? EMPTY_PROFILE_CONFIG
      : activeTab.value === "checkin_reverse" ? EMPTY_CHECKIN_CONFIG : EMPTY_CONFIG;
  configText.value = JSON.stringify(config, null, 2);
  dutyForm.value = { ...EMPTY_DUTY_CONFIG };
  profileForm.value = { ...EMPTY_PROFILE_CONFIG };
  checkinForm.value = { ...EMPTY_CHECKIN_CONFIG };
  profileResults.value = [];
  profileSummary.value = null;
  historyScan.value = null;
  dryrun.value = null;
}

async function loadRules() {
  const data = await api("/api/admin/rules");
  rules.value = data.items;
  collectionSources.value = data.collection_sources || [];
  const visible = rules.value.filter((r) => (r.rule_type || "listing") === activeTab.value);
  if (!current.value) {
    if (visible.length) select(visible[0]);
    else newRule();
  }
}

function switchTab(tab) {
  activeTab.value = tab;
  current.value = null;
  const first = rules.value.find((r) => (r.rule_type || "listing") === tab);
  if (first) select(first);
  else newRule();
}

async function save() {
  let config;
  if (activeTab.value === "on_duty") config = { ...dutyForm.value };
  else if (["profile_collection", "bot_collection"].includes(activeTab.value)) config = { ...profileForm.value };
  else if (activeTab.value === "checkin_reverse") config = { ...checkinForm.value };
  else {
    try {
      config = JSON.parse(configText.value);
    } catch {
      notify("JSON 语法错误");
      return;
    }
  }
  if (!current.value.rule_name.trim()) {
    notify("规则名不能为空");
    return;
  }
  const data = await api("/api/admin/rules", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ rule_name: current.value.rule_name, rule_type: activeTab.value, config, is_active: current.value.is_active !== false }),
  });
  notify(`已保存 v${data.version}`);
  await loadRules();
  const fresh = rules.value.find((r) => r.id === data.rule_id);
  if (fresh) select(fresh);
}

async function copyRule() {
  if (!current.value?.id) return;
  const name = `${current.value.rule_name}-副本`;
  const data = await api(`/api/admin/rules/${current.value.id}/copy`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ rule_name: name }),
  });
  current.value = null;
  await loadRules();
  const copied = rules.value.find((r) => r.id === data.rule_id);
  if (copied) select(copied);
  notify("已复制，来源未绑定");
}

async function bindDutyDialog() {
  if (!current.value?.id || !dutyDialogKey.value) return notify("先选择群组");
  const ruleId = current.value.id;
  const dialog = dialogs.value.find((d) => `${d.account_id}:${d.peer_id}` === dutyDialogKey.value);
  if (!dialog) return;
  const source = await api("/api/admin/sources", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(dialog),
  });
  await api(`/api/admin/rules/${current.value.id}/bind`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source_id: source.source_id, priority: 100 }),
  });
  notify(`已绑定 ${dialog.title}`);
  current.value = null;
  await loadRules();
  const fresh = rules.value.find((r) => r.id === ruleId);
  if (fresh) select(fresh);
}

async function runDryRun(sampleSet = "recent") {
  let config;
  try {
    config = JSON.parse(configText.value);
  } catch {
    notify("JSON 语法错误");
    return;
  }
  try {
    dryrun.value = await api("/api/admin/rules/dryrun", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ config, sample_limit: 50, sample_set: sampleSet }),
    });
  } catch (err) {
    notify(err.message || "回测失败");
  }
}

async function remove() {
  if (!current.value || !current.value.id) return;
  await api(`/api/admin/rules/${current.value.id}`, { method: "DELETE" });
  current.value = null;
  await loadRules();
  notify("已删除");
}

async function bindToSource() {
  if (!current.value || !current.value.id || !bindSourceId.value) {
    notify("先选择要绑定的来源");
    return;
  }
  await api(`/api/admin/rules/${current.value.id}/bind`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source_id: bindSourceId.value, priority: 100 }),
  });
  notify(`已绑定来源#${bindSourceId.value}（优先级 100，覆盖默认规则）`);
  if (["profile_collection", "bot_collection", "checkin_reverse"].includes(activeTab.value)) {
    const ruleId = current.value.id;
    await loadRules();
    const fresh = rules.value.find((rule) => rule.id === ruleId);
    if (fresh) select(fresh);
  }
}

async function loadRecentSamples() {
  const params = new URLSearchParams({ limit: String(sampleLimit.value) });
  if (sampleSourceId.value) params.set("source_id", String(sampleSourceId.value));
  const data = await api(`/api/admin/rules/samples?${params}`);
  if (data.samples.length) {
    sampleText.value = data.samples.map((s) => s.text).join("\n---\n");
    const bySource = {};
    for (const s of data.samples) bySource[s.source] = (bySource[s.source] || 0) + 1;
    const breakdown = Object.entries(bySource)
      .map(([name, n]) => `${name}×${n}`)
      .join("，");
    notify(`已载入 ${data.samples.length} 条（${breakdown}）`);
  } else {
    notify(sampleSourceId.value ? "该来源暂无消息样本" : "暂无历史消息样本");
  }
}

async function generateByAI() {
  const samples = sampleText.value.split("\n---\n").map((s) => s.trim()).filter(Boolean);
  if (!samples.length) {
    notify("先粘贴几条样本消息");
    return;
  }
  try {
    const data = await api("/api/admin/rules/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ samples }),
    });
    configText.value = JSON.stringify(data.config, null, 2);
    notify("AI 已生成规则草稿（请回测后保存）");
  } catch (e) {
    notify(`生成失败: ${String(e.message).slice(0, 80)}`);
  }
}

const llmHasKey = ref(false);

async function openSettings() {
  const data = await api("/api/admin/settings/llm");
  llmForm.value = {
    base_url: data.base_url || "",
    model: data.model || "",
    api_key: "",
    allow_private: data.allow_private || false,
  };
  llmHasKey.value = Boolean(data.has_key);
  apForm.value = {
    autopublish_enabled: data.autopublish_enabled,
    autopublish_threshold: data.autopublish_threshold,
  };
  llmTestResult.value = "";
  showSettings.value = true;
}

async function saveSettings() {
  await api("/api/admin/settings/llm", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...llmForm.value, ...apForm.value }),
  });
  llm.value = await api("/api/admin/rules/llm-status");
  notify("设置已保存，立即生效");
}

async function testSettings() {
  try {
    llmTestResult.value = "测试中…";
    await api("/api/admin/settings/llm", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(llmForm.value),
    });
    const r = await api("/api/admin/settings/llm/test", { method: "POST" });
    llmTestResult.value = `连通成功：${r.reply || "OK"}`;
    llm.value = await api("/api/admin/rules/llm-status");
  } catch (e) {
    llmTestResult.value = `失败：${String(e.message).slice(0, 90)}`;
  }
}

async function backfillSamples() {
  if (!sampleSourceId.value) {
    notify("先选择要拉取的来源");
    return;
  }
  try {
    notify("已发起历史拉取，等待网关执行…");
    await api(`/api/admin/sources/${sampleSourceId.value}/backfill`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ limit: sampleLimit.value }),
    });
    for (let i = 0; i < 30; i++) {
      await new Promise((r) => setTimeout(r, 2000));
      const st = await api(`/api/admin/sources/${sampleSourceId.value}/backfill/status`);
      if (st.status === "done") {
        notify(`历史拉取完成：${st.fetched} 条新样本`);
        await loadRecentSamples();
        return;
      }
      if (st.status === "failed") {
        notify(`拉取失败：${(st.error || "").slice(0, 60)}`);
        return;
      }
    }
    notify("拉取仍在进行，稍后手动点『载入样本』");
  } catch (e) {
    notify(`发起失败: ${String(e.message).slice(0, 60)}`);
  }
}

onMounted(async () => {
  await loadRules();
  llm.value = await api("/api/admin/rules/llm-status");
  sources.value = (await api("/api/admin/sources")).items;
  dialogs.value = (await api("/api/admin/sources/dialogs")).items;
  loadSlang();
  loadInsights();
  loadCandidates();
  profileRefreshTimer = setInterval(() => {
    if (["profile_collection", "bot_collection", "checkin_reverse"].includes(activeTab.value) && current.value?.id) {
      loadProfileResults(current.value.id);
      if (activeTab.value === "checkin_reverse") loadHistoryScan(current.value.id);
    }
  }, 30000);
});

onUnmounted(() => clearInterval(profileRefreshTimer));
</script>

<template>
  <div class="space-y-3">

    <div class="flex items-center gap-3">
      <h2 class="text-lg font-semibold">AI 规则工坊</h2>
      <span v-if="flash" class="rounded bg-emerald-600/20 px-3 py-1 text-xs text-emerald-300">{{ flash }}</span>
      <span class="ml-auto text-xs"
        :class="llm.available ? 'text-emerald-400' : 'text-slate-500'">
        {{ llm.available ? `AI 可用 (${llm.model})` : "AI 未配置" }}
      </span>
      <button @click="openSettings"
        class="flex items-center gap-1 rounded-lg border border-slate-700 px-2.5 py-1 text-xs text-slate-300 hover:bg-slate-800">
        <Settings2 class="size-3.5" />AI 设置
      </button>
    </div>

    <!-- 动态黑话词表 + 纠偏建议：审核纠偏 → 差分沉淀 → 一键采纳回测 → 热生效 -->
    <details class="space-y-3 rounded-xl border border-emerald-800/50 bg-emerald-950/10 p-4 text-sm" open>
      <summary class="cursor-pointer select-none text-sm font-semibold text-emerald-300">
        动态黑话库与纠偏建议（热更新，无需发版）
        <span class="ml-2 text-xs font-normal text-slate-500">
          已启用 {{ slangItems.filter(i => i.enabled).length }} 条
        </span>
      </summary>
      <div class="grid gap-4 lg:grid-cols-2">
        <div class="space-y-2 rounded-xl border border-indigo-800/50 bg-indigo-950/20 p-3">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h3 class="text-sm font-semibold text-indigo-200">自学习推荐候选</h3>
              <p class="text-[11px] text-slate-500">来自重复人工纠偏；采纳前会在黄金样本上比较改善与误伤。</p>
            </div>
            <button @click="loadCandidates"
              class="rounded-lg border border-slate-700 px-2 py-1 text-xs text-slate-300 hover:bg-slate-800">刷新候选</button>
          </div>
          <div v-if="learnedCandidates.length" class="max-h-80 space-y-2 overflow-y-auto">
            <article v-for="candidate in learnedCandidates" :key="candidate.candidate_key"
              class="rounded-lg border border-slate-800 bg-slate-950/60 p-2.5">
              <div class="flex flex-wrap items-center gap-2 text-xs">
                <span class="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-indigo-200">{{ candidate.field_name }}</span>
                <span class="font-mono text-emerald-300">{{ candidate.raw_pattern }}</span>
                <span class="text-slate-400">→ {{ candidate.target_action }}</span>
                <span class="ml-auto rounded bg-amber-900/40 px-1.5 py-0.5 text-amber-300">纠偏 {{ candidate.frequency }} 次</span>
                <span class="rounded bg-indigo-900/40 px-1.5 py-0.5 text-indigo-300">置信 {{ candidate.confidence }}</span>
              </div>
              <div v-for="(example, i) in candidate.examples" :key="i" class="mt-1 truncate text-[11px] text-slate-500">
                {{ example.nickname || `卡片 #${example.listing_id}` }}：{{ example.raw_snippet }}（{{ example.before }} → {{ example.after }}）
              </div>
              <div class="mt-2 flex flex-wrap items-center gap-2">
                <button @click="backtestCandidate(candidate)"
                  class="rounded-lg border border-sky-800 px-2 py-1 text-[11px] text-sky-300 hover:bg-sky-950/40">一键回测</button>
                <button @click="adoptCandidate(candidate)"
                  :disabled="!candidateResults[candidate.candidate_key] || candidateResults[candidate.candidate_key].loading || candidateResults[candidate.candidate_key].total === 0 || candidateResults[candidate.candidate_key].broken > 0"
                  class="rounded-lg bg-emerald-800 px-2 py-1 text-[11px] text-emerald-100 hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-40">采纳生效</button>
                <button @click="dismissCandidate(candidate)"
                  class="rounded-lg border border-slate-700 px-2 py-1 text-[11px] text-slate-400 hover:bg-slate-800">忽略</button>
                <span v-if="candidateResults[candidate.candidate_key]?.loading" class="text-[11px] text-slate-400">正在回测…</span>
                <span v-else-if="candidateResults[candidate.candidate_key]?.total != null" class="text-[11px]">
                  样本 {{ candidateResults[candidate.candidate_key].total }} ·
                  <span class="text-emerald-400">改善 {{ candidateResults[candidate.candidate_key].improved }}</span> ·
                  <span class="text-rose-400">破坏 {{ candidateResults[candidate.candidate_key].broken }}</span> ·
                  <span class="text-slate-400">不变 {{ candidateResults[candidate.candidate_key].unchanged }}</span>
                </span>
                <span v-else-if="candidateResults[candidate.candidate_key]?.error" class="text-[11px] text-rose-400">回测失败</span>
              </div>
            </article>
          </div>
          <div v-else class="rounded-lg border border-slate-800 bg-slate-900/40 p-3 text-xs text-slate-500">
            当前纠偏数据均已覆盖，暂无新推荐。
          </div>
        </div>
        <div class="space-y-2">
          <div class="flex items-center justify-between">
            <span class="text-xs text-slate-400">黑话词表（金额单位 / 资费别名 / 引导词）</span>
            <button @click="reloadSlang"
              class="flex items-center gap-1 rounded-lg border border-slate-700 px-2 py-0.5 text-xs text-slate-300 hover:bg-slate-800">
              <RefreshCw class="size-3" />重载
            </button>
          </div>
          <div class="max-h-56 space-y-1.5 overflow-y-auto pr-1">
            <div v-for="item in slangItems" :key="item.id"
              class="flex items-center gap-2 rounded-lg border border-slate-800 bg-slate-900/60 px-2.5 py-1.5 text-xs">
              <span class="rounded px-1.5 py-0.5 text-[10px]"
                :class="item.category === 'money_slang' ? 'bg-amber-900/40 text-amber-300' : 'bg-sky-900/40 text-sky-300'">
                {{ item.category }}
              </span>
              <span class="font-mono text-slate-200">{{ item.source_term }}</span>
              <span class="text-slate-500">→</span>
              <span class="text-slate-300">{{ item.target_action || (item.factor ? `×${item.factor}` : "?") }}</span>
              <button @click="toggleSlang(item)"
                class="ml-auto rounded px-2 py-0.5 text-[10px]"
                :class="item.enabled ? 'bg-emerald-900/40 text-emerald-300' : 'bg-slate-800 text-slate-500'">
                {{ item.enabled ? "启用中" : "已禁用" }}
              </button>
              <button @click="deleteSlang(item)" aria-label="删除词条"
                class="rounded p-0.5 text-slate-600 hover:text-rose-400">
                <Trash2 class="size-3" />
              </button>
            </div>
            <div v-if="!slangItems.length" class="rounded-lg border border-slate-800 bg-slate-900/40 p-3 text-xs text-slate-500">
              词表为空：内置黑话（张/米/章/1P…）仍生效；在此添加新词即可热更新，无需发版。
            </div>
          </div>
          <div class="flex flex-wrap items-center gap-1.5 border-t border-slate-800 pt-2">
            <select v-model="slangForm.category"
              class="rounded border border-slate-700 bg-slate-950 px-1.5 py-1 text-xs">
              <option value="money_slang">金额单位</option>
              <option value="tariff_alias">资费别名</option>
              <option value="bust_alias">胸围引导</option>
              <option value="age_alias">年龄引导</option>
              <option value="weight_alias">体重别名</option>
              <option value="restriction_alias">禁忌词</option>
              <option value="service_alias">服务标签</option>
            </select>
            <input v-model="slangForm.source_term" placeholder="词（如 双次）" maxlength="64"
              class="w-24 rounded border border-slate-700 bg-slate-950 px-1.5 py-1 text-xs" />
            <input v-model="slangForm.target_action" placeholder="→ 目标（如 2p）" maxlength="64"
              class="w-24 rounded border border-slate-700 bg-slate-950 px-1.5 py-1 text-xs" />
            <input v-model.number="slangForm.factor" type="number" placeholder="倍数" min="1"
              class="w-16 rounded border border-slate-700 bg-slate-950 px-1.5 py-1 text-xs" />
            <button @click="upsertSlang"
              class="rounded-lg bg-emerald-700 px-2.5 py-1 text-xs font-medium text-white hover:bg-emerald-600">
              添加并热生效
            </button>
          </div>
        </div>
        <div class="space-y-2">
          <div class="flex items-center justify-between">
            <span class="text-xs text-slate-400">高频纠偏建议（近 30 天，≥{{ insightMinHits }} 次）</span>
            <button @click="loadInsights"
              class="flex items-center gap-1 rounded-lg border border-slate-700 px-2 py-0.5 text-xs text-slate-300 hover:bg-slate-800">
              <FlaskConical class="size-3" />刷新分析
            </button>
          </div>
          <div class="max-h-64 space-y-1.5 overflow-y-auto pr-1">
            <div v-for="(insight, index) in insights" :key="index"
              class="rounded-lg border border-slate-800 bg-slate-900/60 p-2.5 text-xs">
              <div class="flex items-center gap-2">
                <span class="font-mono text-slate-400">{{ insight.field_name }}</span>
                <span class="truncate text-slate-300">{{ insight.old_value }} → {{ insight.new_value }}</span>
                <span class="ml-auto shrink-0 rounded bg-rose-900/40 px-1.5 py-0.5 text-[10px] text-rose-300">
                  ×{{ insight.hits }}
                </span>
              </div>
              <div v-for="(suggestion, sIndex) in insight.suggestions" :key="sIndex"
                class="mt-1.5 flex items-center gap-2">
                <span class="text-slate-400">
                  建议词条 <span class="font-mono text-emerald-300">{{ suggestion.source_term }}</span>
                  → {{ suggestion.target_action }}
                </span>
                <button @click="adoptSuggestion(suggestion)"
                  :disabled="previewBusy"
                  class="ml-auto rounded-lg border border-emerald-700/60 px-2 py-0.5 text-[11px] text-emerald-300 hover:bg-emerald-900/30 disabled:opacity-40">
                  一键采纳回测
                </button>
              </div>
            </div>
            <div v-if="!insights.length" class="rounded-lg border border-slate-800 bg-slate-900/40 p-3 text-xs text-slate-500">
              暂无高频纠偏。管理员在审核工作台的每次就地修改都会被记录为差分，反复出现的修正会汇总到这里。
            </div>
          </div>
          <div v-if="preview" class="space-y-2 rounded-lg border border-emerald-700/50 bg-emerald-900/10 p-2.5">
            <p class="text-xs text-emerald-300">
              回测完成（{{ preview.sample_set }} 样本 {{ preview.baseline.total }} 条）：
              命中率 {{ preview.baseline.hit_rate }} → {{ preview.with_candidate.hit_rate }}
              <template v-if="preview.baseline.passed != null">
                ，通过 {{ preview.baseline.passed }} → {{ preview.with_candidate.passed }}
              </template>
            </p>
            <p class="text-[11px] text-slate-400">
              候选词条 {{ preview.entry.source_term }} → {{ preview.entry.target_action || `×${preview.entry.factor}` }}
              尚未入库；确认后才会正式生效。
            </p>
            <div class="flex gap-2">
              <button @click="confirmAdopt" :disabled="previewBusy"
                class="rounded-lg bg-emerald-700 px-3 py-1 text-xs font-medium text-white hover:bg-emerald-600 disabled:opacity-40">
                确认入库生效
              </button>
              <button @click="preview = null; pendingAdopt = null"
                class="rounded-lg border border-slate-700 px-3 py-1 text-xs text-slate-400 hover:bg-slate-800">
                放弃
              </button>
            </div>
          </div>
        </div>
      </div>
    </details>

    <div v-if="showSettings"
      class="space-y-3 rounded-xl border border-violet-800/50 bg-violet-950/20 p-4 text-sm">
      <div class="text-xs text-slate-400">
        OpenAI 兼容端点（如 GLM / DeepSeek / 本地 Ollama）。保存后立即生效；API Key 只存不显。
      </div>
      <div class="grid gap-3 md:grid-cols-3">
        <label class="block text-xs">Base URL
          <input v-model="llmForm.base_url" placeholder="https://api.example.com/v1"
            class="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm" />
        </label>
        <label class="block text-xs">模型名
          <input v-model="llmForm.model" placeholder="glm-4-flash / deepseek-chat …"
            class="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm" />
        </label>
        <label class="block text-xs">API Key（留空=保留已存）
          <input v-model="llmForm.api_key" type="password" :placeholder="llmHasKey ? '已设置（留空保持不变）' : 'sk-…'"
            class="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm" />
          <span v-if="llmHasKey" class="text-emerald-400">✓ 已保存（数据库每日自动备份，不会丢失）</span>
        </label>
      </div>
      <div class="flex flex-wrap items-center gap-3">
        <label class="flex items-center gap-2 text-xs text-slate-400">
          <input v-model="llmForm.allow_private" type="checkbox" class="size-4 accent-violet-500" />
          允许私网端点（局域网大模型，如 Ollama）
        </label>
        <button @click="saveSettings"
          class="rounded-lg border border-slate-600 px-4 py-1.5 text-xs hover:bg-slate-800">保存</button>
        <button @click="testSettings"
          class="flex items-center gap-1.5 rounded-lg bg-violet-600 px-4 py-1.5 text-xs font-medium hover:bg-violet-500">
          <PlugZap class="size-3.5" />保存并测试
        </button>
        <span v-if="llmTestResult" class="text-xs"
          :class="llmTestResult.startsWith('失败') ? 'text-rose-400' : 'text-emerald-400'">
          {{ llmTestResult }}
        </span>
      </div>
      <div class="space-y-2 border-t border-slate-800 pt-3">
        <div class="text-xs font-medium text-slate-300">采集发布策略</div>
        <label class="flex items-center gap-2 text-xs text-slate-400">
          <input v-model="apForm.autopublish_enabled" type="checkbox" class="size-4 accent-emerald-500" />
          高置信自动上线（关闭则全部进待审核）
        </label>
        <div class="flex items-center gap-3 text-xs text-slate-400">
          自动上线置信度阈值
          <input v-model.number="apForm.autopublish_threshold" type="range" min="0.3" max="1" step="0.05"
            class="w-48 accent-emerald-500" />
          <span class="w-10 font-mono text-emerald-400">{{ apForm.autopublish_threshold.toFixed(2) }}</span>
          <span class="text-slate-500">≥阈值直接上线；OCR 参与或缺字段的一律进待审核</span>
        </div>
      </div>
    </div>

    <div class="flex gap-2 border-b border-slate-800 pb-2">
      <button v-for="tab in [
        { value: 'listing', label: '名片抓取' },
        { value: 'on_duty', label: '在岗规则 V1' },
        { value: 'profile_collection', label: 'TG号采集V2' },
        { value: 'bot_collection', label: 'Bot地址采集' },
        { value: 'checkin_reverse', label: '签到来源反查 V2' },
      ]"
        :key="tab.value" @click="switchTab(tab.value)"
        class="rounded-lg px-4 py-2 text-sm"
        :class="activeTab === tab.value ? 'bg-sky-600 font-semibold text-white' : 'bg-slate-900 text-slate-400'">
        {{ tab.label }}
      </button>
    </div>

    <div class="grid grid-cols-1 gap-4 lg:h-[calc(100dvh-190px)] lg:grid-cols-12">
      <aside class="flex gap-2 overflow-x-auto pb-1 lg:col-span-2 lg:block lg:space-y-2 lg:overflow-x-visible">
        <button @click="newRule"
          class="w-full rounded-lg bg-sky-600 py-2 text-sm font-medium hover:bg-sky-500">+ 新规则</button>
        <button v-for="r in filteredRules" :key="r.id" @click="select(r)"
          class="w-full rounded-lg border p-2.5 text-left text-sm"
          :class="current && current.id === r.id ? 'border-sky-500 bg-sky-600/10' : 'border-slate-800 bg-slate-900'">
          <div class="truncate font-medium">{{ r.rule_name }}</div>
          <div class="text-xs text-slate-500">v{{ r.version }}</div>
        </button>
      </aside>

      <section v-if="activeTab === 'listing'" class="lg:col-span-5 space-y-3 lg:overflow-y-auto">
        <div v-if="boundGroups().length" class="flex flex-wrap items-center gap-2 text-xs">
          <span class="text-slate-500">已绑定群组：</span>
          <span v-for="b in boundGroups()" :key="b.source_id"
            class="flex items-center gap-1 rounded-full border border-sky-800/60 bg-sky-950/40 px-2.5 py-1 text-sky-300">
            {{ b.title || `来源#${b.source_id}` }}
            <span class="text-slate-500">优先级{{ b.priority }}</span>
          </span>
          <span class="text-slate-600">（改完规则记得选对应群组「重跑提取」刷新存量卡片）</span>
        </div>
        <div class="flex gap-2">
          <input v-model="current.rule_name" placeholder="规则名" :disabled="current.rule_name === 'default-rule'"
            class="flex-1 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm" />
          <button @click="save" :disabled="current.rule_name === 'default-rule'"
            class="flex items-center gap-1.5 rounded-lg border border-slate-600 px-3 py-2 text-sm hover:bg-slate-800 disabled:opacity-40">
            <Save class="size-4" />保存
          </button>
          <button v-if="current.id && current.rule_name !== 'default-rule'" @click="remove"
            class="rounded-lg border border-rose-700/60 p-2 text-rose-300 hover:bg-rose-900/30"><Trash2 class="size-4" /></button>
          <button v-if="current.id && current.rule_name !== 'default-rule'" @click="copyRule"
            class="rounded-lg border border-slate-600 p-2 text-slate-300 hover:bg-slate-800" title="复制规则"><Copy class="size-4" /></button>
          <button v-if="current.id" @click="bindToSource"
            class="flex items-center gap-1.5 rounded-lg border border-slate-600 px-3 py-2 text-sm hover:bg-slate-800">
            <Link2 class="size-4" />绑定来源
          </button>
          <select v-model="bindSourceId"
            class="rounded-lg border border-slate-700 bg-slate-950 px-2 py-2 text-xs">
            <option :value="null" disabled>选择来源…</option>
            <option v-for="s in sources" :key="s.id" :value="s.id">
              {{ s.title || `来源#${s.id}` }}{{ s.is_active ? "" : "（已暂停）" }}
            </option>
          </select>
          <button @click="reextractSource"
            class="flex items-center gap-1.5 whitespace-nowrap rounded-lg border border-amber-700/60 px-2.5 py-2 text-xs text-amber-300 hover:bg-amber-900/30">
            <RefreshCw class="size-3.5" />重跑提取
          </button>
        </div>
        <textarea v-model="configText" rows="20" spellcheck="false"
          class="w-full rounded-lg border border-slate-700 bg-slate-950 p-3 font-mono text-xs leading-relaxed"></textarea>
        <details class="rounded-xl border border-slate-800 bg-slate-900/50 p-3 text-xs">
          <summary class="cursor-pointer text-slate-300">配置字段说明（点开）</summary>
          <div class="mt-2 space-y-1.5">
            <div v-for="[key, desc] in FIELD_HELP" :key="key" class="flex gap-2">
              <code class="shrink-0 text-sky-300">{{ key }}</code>
              <span class="text-slate-400">{{ desc }}</span>
            </div>
            <div class="mt-2 border-t border-slate-800 pt-2 text-slate-500">
              改完规则保存后，选来源点『重跑提取』即可让存量卡片按新规则刷新（新提取分数更高时更新原卡，不会重复建卡）。
            </div>
          </div>
        </details>
      </section>

      <section v-else-if="activeTab === 'on_duty'" class="space-y-3 lg:col-span-5 lg:overflow-y-auto">
        <div v-if="boundGroups().length" class="flex flex-wrap items-center gap-2 text-xs">
          <span class="text-slate-500">已绑定群组：</span>
          <span v-for="b in boundGroups()" :key="b.source_id"
            class="rounded-full border border-emerald-800/60 bg-emerald-950/40 px-2.5 py-1 text-emerald-300">
            {{ b.title || `来源#${b.source_id}` }}
          </span>
        </div>
        <div class="flex gap-2">
          <input v-model="current.rule_name" placeholder="签到规则名称"
            class="min-w-0 flex-1 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm" />
          <button @click="save" class="flex items-center gap-1 rounded-lg bg-sky-600 px-3 text-sm"><Save class="size-4" />保存</button>
          <button v-if="current.id" @click="copyRule" class="rounded-lg border border-slate-600 p-2" title="复制"><Copy class="size-4" /></button>
          <button v-if="current.id" @click="remove" class="rounded-lg border border-rose-700/60 p-2 text-rose-300" title="删除"><Trash2 class="size-4" /></button>
        </div>
        <label class="flex items-center gap-2 text-xs text-slate-400">
          <input v-model="current.is_active" type="checkbox" class="size-4 accent-emerald-500" />启用该规则
        </label>
        <div class="grid gap-3 rounded-xl border border-slate-800 bg-slate-900/50 p-4 sm:grid-cols-2">
          <label class="text-xs text-slate-400">发送 Bot 用户名
            <input v-model="dutyForm.sender_username" placeholder="XMWGJR_BOT"
              class="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm" />
          </label>
          <label class="text-xs text-slate-400">发送者 Telegram ID
            <input v-model.number="dutyForm.sender_peer_id" type="number"
              class="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm" />
          </label>
          <label class="text-xs text-slate-400 sm:col-span-2">消息首行特征
            <input v-model="dutyForm.match_prefix" placeholder="🔶 在线老师，按地区展示"
              class="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm" />
          </label>
          <label class="text-xs text-slate-400">更新方式
            <select v-model="dutyForm.update_mode" class="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm">
              <option value="edited_snapshot">主榜持续编辑</option>
              <option value="replace_snapshot">新榜单完整替换</option>
            </select>
          </label>
          <label class="text-xs text-slate-400">身份关联
            <select v-model="dutyForm.identity_mode" class="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm">
              <option value="direct_username_links">个人 TG 链接</option>
              <option value="linked_listing_messages">频道消息链接</option>
            </select>
          </label>
          <label v-if="dutyForm.identity_mode === 'linked_listing_messages'" class="text-xs text-slate-400 sm:col-span-2">目标频道用户名
            <input v-model="dutyForm.target_channel_username" placeholder="NBXMWBGJ"
              class="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm" />
          </label>
          <label class="flex items-center gap-2 text-xs text-slate-300 sm:col-span-2">
            <input v-model="dutyForm.auto_collect_enabled" type="checkbox" class="size-4 accent-emerald-500" />
            自动补采今日签到但尚无卡片的 TG 账号
          </label>
          <label v-if="dutyForm.auto_collect_enabled" class="text-xs text-slate-400 sm:col-span-2">名片采集来源
            <select v-model.number="dutyForm.collection_source_id"
              class="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm">
              <option :value="null" disabled>选择绑定了名片规则的来源…</option>
              <option v-for="s in collectionSources" :key="s.id" :value="s.id">
                {{ s.title || s.username || `来源#${s.id}` }}
              </option>
            </select>
            <span class="mt-1 block text-slate-500">只查询日报关联的消息或精确 TG 用户名，使用该来源现有规则；不扫描群历史，也不按昵称匹配。</span>
          </label>
          <label class="flex items-center gap-2 text-xs text-slate-400 sm:col-span-2">
            <input v-model="dutyForm.profile_query_required" type="checkbox" class="size-4 accent-emerald-500" />
            仅把带 profile 参数的个人链接识别为老师
          </label>
          <div class="text-xs text-slate-500 sm:col-span-2">业务日固定在北京时间 04:00 切换；00:00–03:59 延续前一天记录。</div>
        </div>
        <div class="flex gap-2">
          <select v-model="dutyDialogKey" class="min-w-0 flex-1 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm">
            <option value="" disabled>选择账号可见的群组或频道…</option>
            <option v-for="d in dialogs" :key="`${d.account_id}:${d.peer_id}`" :value="`${d.account_id}:${d.peer_id}`">
              {{ d.title }} · {{ d.username ? '@' + d.username : d.peer_id }}
            </option>
          </select>
          <button @click="bindDutyDialog" :disabled="!current.id"
            class="flex items-center gap-1 rounded-lg border border-emerald-700/60 px-3 text-sm text-emerald-300 disabled:opacity-40">
            <Link2 class="size-4" />绑定群组
          </button>
        </div>
        <details class="rounded-xl border border-slate-800 bg-slate-900/50 p-3 text-xs">
          <summary class="cursor-pointer text-slate-300">高级配置</summary>
          <pre class="mt-2 overflow-x-auto whitespace-pre-wrap text-slate-500">{{ JSON.stringify(dutyForm, null, 2) }}</pre>
        </details>
      </section>

      <section v-else class="space-y-3 lg:col-span-5 lg:overflow-y-auto">
        <div class="flex items-center gap-2">
          <input v-model="current.rule_name" placeholder="规则名称"
            class="min-w-0 flex-1 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm" />
          <button @click="save" class="flex items-center gap-1 rounded-lg bg-violet-600 px-3 py-2 text-sm">
            <Save class="size-4" />保存
          </button>
          <button v-if="current.id" @click="copyRule" class="rounded-lg border border-slate-600 p-2" title="复制规则">
            <Copy class="size-4" />
          </button>
          <button v-if="current.id" @click="remove" class="rounded-lg border border-rose-700/60 p-2 text-rose-300" title="删除规则">
            <Trash2 class="size-4" />
          </button>
        </div>
        <template v-if="['profile_collection', 'bot_collection'].includes(activeTab)">
        <label class="flex items-center gap-2 text-xs text-slate-400">
          <input v-model="current.is_active" type="checkbox" class="size-4 accent-violet-500" />启用 {{ activeTab === 'bot_collection' ? 'Bot地址采集' : 'TG号采集V2' }}
        </label>
        <div v-if="activeTab === 'profile_collection'" class="rounded-xl border border-violet-800/50 bg-violet-950/20 p-4 text-xs leading-relaxed text-slate-300">
          TG号采集V2 独立运行。输入一个或多个 TG 号提交后，按个人简介和关联公开频道采集；结果进入专属审核页，不覆盖已有卡片。
        </div>
        <div v-else class="rounded-xl border border-violet-800/50 bg-violet-950/20 p-4 text-xs leading-relaxed text-slate-300">
          只读取 TG 个人名片中的 Bot 联系地址并回填相同 TG 号的卡片；不扫描关联频道。提交支持单个或多个 TG 号，任务遵守账号冷却。
        </div>
        <div class="grid gap-3 rounded-xl border border-slate-800 bg-slate-900/50 p-4 sm:grid-cols-2">
          <label class="text-xs text-slate-400">先检查最近消息数
            <input v-model.number="profileForm.recent_window" type="number" min="1" max="10"
              class="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm" />
          </label>
          <label class="text-xs text-slate-400">总扫描上限
            <input v-model.number="profileForm.max_scan_messages" type="number" min="10" max="100"
              class="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm" />
          </label>
          <label class="text-xs text-slate-400">最多照片
            <input v-model.number="profileForm.max_photos" type="number" min="1" max="6"
              class="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm" />
          </label>
          <label class="text-xs text-slate-400">无照片时最多视频
            <input v-model.number="profileForm.max_videos" type="number" min="0" max="2"
              class="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm" />
          </label>
          <label class="text-xs text-slate-400">介绍图最少识别字数
            <input v-model.number="profileForm.intro_min_chars" type="number" min="1" max="500"
              class="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm" />
          </label>
          <label class="text-xs text-slate-400">介绍图最少文字行数
            <input v-model.number="profileForm.intro_min_lines" type="number" min="1" max="30"
              class="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm" />
          </label>
          <label class="flex items-center gap-2 text-xs text-slate-300">
            <input v-model="profileForm.channel_any_sender_matches" type="checkbox" disabled class="size-4 accent-violet-500" />
            关联频道内任意发送者均可作为候选
          </label>
          <label class="flex items-center gap-2 text-xs text-slate-300">
            <input v-model="profileForm.skip_video_when_photo_found" type="checkbox" disabled class="size-4 accent-violet-500" />
            找到照片后不采视频
          </label>
          <label class="flex items-center gap-2 text-xs text-slate-300 sm:col-span-2">
            <input v-model="profileForm.exclude_avatar" type="checkbox" disabled class="size-4 accent-violet-500" />
            排除 Telegram 头像
          </label>
        </div>
        </template>
        <template v-else>
          <label class="flex items-center gap-2 text-xs text-slate-400">
            <input v-model="current.is_active" type="checkbox" class="size-4 accent-sky-500" />启用签到来源反查规则
          </label>
          <div class="rounded-xl border border-sky-800/50 bg-sky-950/20 p-4 text-xs leading-relaxed text-slate-300">
            每日签到命中 TG 号后，自动在绑定的签到来源中搜索精确账号。命中消息及相册媒体进入「签到补采审核」；识别到重复身份时进入「疑似重复」。
          </div>
          <label class="block text-xs text-slate-400">签到来源频道/群组用户名
            <input v-model="checkinForm.source_username" placeholder="例如 nbtgzb 或 NBXMWBGJ"
              class="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm" />
          </label>
        </template>
      </section>

      <section v-if="activeTab === 'listing'" class="lg:col-span-5 space-y-3 lg:overflow-y-auto">
        <div class="space-y-2 rounded-xl border border-slate-800 bg-slate-900/50 p-3">
          <div class="flex items-center gap-2 text-sm font-medium"><Sparkles class="size-4 text-sky-400" />样本输入</div>
          <textarea v-model="sampleText" rows="5" placeholder="粘贴样本消息，多条用 --- 分隔"
            class="w-full rounded-lg border border-slate-700 bg-slate-950 p-2.5 text-xs"></textarea>
          <div class="flex flex-wrap items-center gap-2 text-xs">
            <select v-model="sampleSourceId"
              class="rounded-lg border border-slate-700 bg-slate-950 px-2 py-1.5">
              <option :value="null">全部来源</option>
              <option v-for="s in sources" :key="s.id" :value="s.id">
                {{ s.title || `来源#${s.id}` }}
              </option>
            </select>
            <input v-model.number="sampleLimit" type="number" min="1" max="200"
              title="要往前获取的消息条数"
              class="w-20 rounded-lg border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs" />
            <span class="text-slate-500">条</span>
            <button @click="loadRecentSamples"
              class="rounded-lg border border-slate-600 py-1.5 text-slate-300 hover:bg-slate-800">
              载入样本
            </button>
            <button v-if="sampleSourceId" @click="backfillSamples"
              class="rounded-lg border border-sky-700/60 py-1.5 text-sky-300 hover:bg-sky-900/30">
              拉取历史样本
            </button>
            <button @click="generateByAI" :disabled="!llm.available"
              class="flex-1 rounded-lg bg-violet-600 py-1.5 font-medium hover:bg-violet-500 disabled:opacity-40">
              AI 生成规则草稿
            </button>
          </div>
        </div>

        <div class="rounded-xl border border-slate-800 bg-slate-900/50 p-3">
          <div class="mb-2 flex items-center gap-2 text-sm font-medium">
            <FlaskConical class="size-4 text-emerald-400" />沙盒回测
            <span v-if="dryrun" class="ml-auto text-xs text-slate-400">
              {{ dryrun.sample_set === "golden" ? `通过 ${dryrun.passed}/${dryrun.total}` : `命中 ${dryrun.hits}/${dryrun.total}` }}
            </span>
          </div>
          <button @click="runDryRun()"
            class="mb-2 w-full rounded-lg border border-emerald-700/60 py-2 text-sm text-emerald-300 hover:bg-emerald-900/30">
            对最近 50 条真实消息回测
          </button>
          <button @click="runDryRun('golden')"
            class="mb-2 w-full rounded-lg border border-sky-700/60 py-2 text-sm text-sky-300 hover:bg-sky-900/30">
            回测标准样本
          </button>
          <p v-if="dryrun?.sample_set === 'golden' && !dryrun.total" class="text-xs text-slate-400">尚未保存标准样本。</p>
          <div v-if="dryrun" class="max-h-80 space-y-1.5 overflow-y-auto">
            <div v-for="r in dryrun.results" :key="r.raw_id"
              class="rounded-lg border p-2 text-xs"
              :class="r.matched ? 'border-emerald-800/60 bg-emerald-950/20' : 'border-slate-800'">
              <div class="flex items-center gap-2">
                <span class="font-mono text-slate-500">#{{ r.raw_id }}</span>
                <span v-if="r.gate !== 'pass'" class="rounded bg-rose-900/40 px-1.5 py-0.5 text-rose-300">{{ r.gate }}</span>
                <span v-if="r.lifecycle" class="rounded bg-amber-900/40 px-1.5 py-0.5 text-amber-300">生命周期:{{ r.lifecycle }}</span>
                <span v-for="(v, k) in r.fields" :key="k"
                  class="rounded bg-slate-800 px-1.5 py-0.5">{{ k }}={{ v }}</span>
              </div>
              <div class="mt-1 truncate text-slate-500">{{ r.text }}</div>
              <div v-if="r.media?.length" class="mt-2 flex flex-wrap gap-2">
                <a v-for="asset in r.media.filter(item => item.kind === 'photo')" :key="asset.path"
                  :href="mediaUrl(asset.path)" target="_blank" rel="noopener noreferrer">
                  <img :src="mediaUrl(asset.path, { thumbnail: true })" alt="标准样本原图"
                    loading="lazy" class="h-12 w-12 rounded object-cover" />
                </a>
              </div>
              <p v-for="(diff, field) in r.differences" :key="field" class="mt-1 break-words text-amber-300">
                {{ field }}：预期 {{ diff.expected ?? "空" }}，实际 {{ diff.actual ?? "空" }}
              </p>
            </div>
          </div>
        </div>
      </section>
      <section v-else-if="activeTab === 'on_duty'" class="space-y-3 lg:col-span-5 lg:overflow-y-auto">
        <div class="rounded-xl border border-slate-800 bg-slate-900/50 p-4">
          <h3 class="text-sm font-semibold">当前同步状态</h3>
          <div class="mt-3 grid grid-cols-2 gap-2 text-sm sm:grid-cols-3">
            <div class="rounded-lg bg-slate-950 p-3"><div class="text-2xl font-bold text-sky-300">{{ current.duty_status?.matched || 0 }}</div><div class="text-xs text-slate-500">日报身份（可采集）</div></div>
            <div class="rounded-lg bg-slate-950 p-3"><div class="text-2xl font-bold text-cyan-300">{{ current.duty_status?.green_blue || 0 }}</div><div class="text-xs text-slate-500">🟢💙并列</div></div>
            <div class="rounded-lg bg-slate-950 p-3"><div class="text-2xl font-bold text-lime-300">{{ current.duty_status?.green_only || 0 }}</div><div class="text-xs text-slate-500">仅🟢，跳过采集</div></div>
            <div class="rounded-lg bg-slate-950 p-3"><div class="text-2xl font-bold text-emerald-400">{{ current.duty_status?.with_card || 0 }}</div><div class="text-xs text-slate-500">已有卡片</div></div>
            <div class="rounded-lg bg-slate-950 p-3"><div class="text-2xl font-bold text-amber-300">{{ current.duty_status?.queued || 0 }}</div><div class="text-xs text-slate-500">排队补采</div></div>
            <div class="rounded-lg bg-slate-950 p-3"><div class="text-2xl font-bold text-violet-300">{{ current.duty_status?.fetched || 0 }}</div><div class="text-xs text-slate-500">已取回，待提取</div></div>
            <div class="rounded-lg bg-slate-950 p-3"><div class="text-2xl font-bold text-rose-300">{{ current.duty_status?.failed || 0 }}</div><div class="text-xs text-slate-500">失败 / 未找到</div></div>
            <div class="rounded-lg bg-slate-950 p-3"><div class="text-2xl font-bold text-slate-300">{{ current.duty_status?.unqueued || 0 }}</div><div class="text-xs text-slate-500">尚未处理</div></div>
            <div class="rounded-lg bg-slate-950 p-3"><div class="text-2xl font-bold text-amber-400">{{ current.duty_status?.unresolved || 0 }}</div><div class="text-xs text-slate-500">未解析日报链接</div></div>
          </div>
          <div class="mt-3 text-xs text-slate-500">最近同步：{{ current.duty_status?.observed_at ? new Date(current.duty_status.observed_at).toLocaleString('zh-CN') : '尚未同步' }}</div>
        </div>
        <div class="rounded-xl border border-slate-800 bg-slate-900/50 p-4 text-sm text-slate-400">
          网关只接收绑定群内指定 Bot 的匹配榜单。其他聊天、抽奖、积分、相册不会下载媒体，也不会写入原始消息表。
        </div>
      </section>
      <section v-else class="space-y-3 lg:col-span-5 lg:overflow-y-auto">
        <div v-if="activeTab === 'checkin_reverse'" class="rounded-xl border border-sky-800/50 bg-sky-950/20 p-4">
          <h3 class="text-sm font-semibold">签到来源反查 V2</h3>
          <p class="mt-2 text-xs leading-relaxed text-slate-400">从绑定的今日签到规则自动接收 TG 号，在指定公开来源中搜索精确账号。命中消息及相册媒体进入「签到补采审核」；重复身份进入「疑似重复」。</p>
          <div v-if="historyScan" class="mt-3 space-y-2 rounded-lg border border-sky-900/70 bg-slate-950/50 p-3 text-xs">
            <div class="flex flex-wrap items-center justify-between gap-2">
              <span class="font-medium text-sky-200">历史资料池 @{{ historyScan.source_username }} · {{ historyScan.status }}</span>
              <button @click="loadHistoryScan()" class="rounded border border-slate-700 px-2 py-1 text-slate-300">刷新</button>
            </div>
            <div class="grid grid-cols-2 gap-2 sm:grid-cols-3">
              <span>已扫描 {{ historyScan.scanned_count }}</span>
              <span>候选 {{ historyScan.candidate_count }}</span>
              <span>入库 {{ historyScan.stored_count }}</span>
              <span>自动上架 {{ historyScan.published_count }}</span>
              <span>待审核 {{ historyScan.review_count }}</span>
              <span v-if="historyScan.cursor_msg_id">断点消息 #{{ historyScan.cursor_msg_id }}</span>
            </div>
            <p class="text-slate-400">{{ historyScan.progress_note }}<span v-if="historyScan.last_scanned_at"> · 最近扫描 {{ new Date(historyScan.last_scanned_at).toLocaleString('zh-CN') }}</span></p>
            <p v-if="historyScan.next_attempt_at" class="text-amber-300">账号冷却/队列等待至 {{ new Date(historyScan.next_attempt_at).toLocaleString('zh-CN') }}</p>
            <p v-if="historyScan.error" class="text-rose-300">{{ historyScan.error }}</p>
          </div>
          <p v-else class="mt-3 text-xs text-amber-300">此规则尚未绑定启用的签到来源扫描队列。</p>
        </div>
        <div v-if="activeTab === 'profile_collection'" class="rounded-xl border border-violet-800/50 bg-violet-950/20 p-4">
          <h3 class="text-sm font-semibold">TG号采集V2 策略</h3>
          <p class="mt-2 text-xs leading-relaxed text-slate-400">
            先读个人简介关联消息和置顶，并补读置顶后的 5 条；不限制关联频道的更新时间。资料不足时扫描频道最近消息，最多 100 条、最多保存 6 张照片；照片 OCR 按文字量识别简介。有照片不取视频。结果进入专属审核页，不覆盖已上架卡片。
          </p>
        </div>
        <div v-if="current.id" class="flex flex-wrap gap-2 rounded-xl border border-slate-800 bg-slate-900/50 p-3">
          <select v-model="bindSourceId" class="min-w-0 flex-1 rounded-lg border border-slate-700 bg-slate-950 px-2 py-2 text-xs">
            <option :value="null" disabled>选择资料来源…</option>
            <option v-for="s in sources" :key="s.id" :value="s.id">{{ s.title || `来源#${s.id}` }}{{ s.is_active ? "" : "（已暂停）" }}</option>
          </select>
          <button @click="bindToSource" class="flex items-center gap-1 rounded-lg border border-slate-600 px-3 py-2 text-xs">
            <Link2 class="size-3.5" />绑定来源
          </button>
          <button @click="loadProfileResults()" class="flex items-center gap-1 rounded-lg border border-slate-600 px-3 py-2 text-xs">
            <RefreshCw class="size-3.5" />刷新试采结果
          </button>
        </div>
        <div v-if="current.id && ['profile_collection', 'bot_collection'].includes(activeTab)" class="space-y-2 rounded-xl border border-slate-800 bg-slate-900/50 p-3">
          <select v-model="profileSourceId" class="w-full rounded-lg border border-slate-700 bg-slate-950 px-2 py-2 text-xs">
            <option :value="null" disabled>选择 TG 查询账号来源…</option>
            <option v-for="s in collectionSources" :key="s.id" :value="s.id">{{ s.title || `来源#${s.id}` }}</option>
          </select>
          <textarea v-model="profileUsernames" rows="3" placeholder="输入一个或多个 TG 号；每行一个，也可用逗号分隔"
            class="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs"></textarea>
          <div class="flex flex-wrap gap-2">
            <button @click="enqueueProfileUsername" class="rounded-lg bg-sky-600 px-3 py-2 text-xs font-medium hover:bg-sky-500">提交采集</button>
            <button v-if="activeTab === 'profile_collection'" @click="requeueIneffectiveProfiles" class="rounded-lg border border-amber-700/60 px-3 py-2 text-xs text-amber-300 hover:bg-amber-900/30">重采无有效资料</button>
          </div>
        </div>
        <div v-if="current.id && profileSummary" class="space-y-3 rounded-xl border border-violet-800/50 bg-violet-950/20 p-3">
          <div class="flex flex-wrap items-center justify-between gap-2 text-xs">
            <span class="font-semibold text-violet-200">V2 处理进度：{{ profileSummary.completed }} / {{ profileSummary.total }}（{{ profileProgress() }}%）</span>
            <span class="text-slate-500">每 30 秒自动刷新<span v-if="profileUpdatedAt"> · 更新于 {{ profileUpdatedAt.toLocaleTimeString("zh-CN") }}</span></span>
          </div>
          <div class="h-2 overflow-hidden rounded-full bg-slate-800">
            <div class="h-full rounded-full bg-violet-500 transition-all" :style="{ width: `${profileProgress()}%` }"></div>
          </div>
          <div class="flex flex-wrap gap-2 text-[11px]">
            <span class="rounded bg-slate-800 px-2 py-1 text-slate-300">待采 {{ profileSummary.pending }}</span>
            <span class="rounded bg-sky-950 px-2 py-1 text-sky-300">采集中 {{ profileSummary.processing }}</span>
            <span class="rounded bg-amber-950 px-2 py-1 text-amber-300">等待重试 {{ profileSummary.deferred }}</span>
            <span class="rounded bg-emerald-950 px-2 py-1 text-emerald-300">已采集 {{ profileSummary.fetched }}</span>
            <span class="rounded bg-slate-800 px-2 py-1 text-slate-300">无可用资料 {{ profileSummary.not_found + profileSummary.skipped }}</span>
            <span class="rounded bg-rose-950 px-2 py-1 text-rose-300">失败 {{ profileSummary.failed }}</span>
          </div>
          <p v-if="profileSummary.cooldown_until" class="text-xs text-amber-200">
            Telegram 账号冷却中：约 {{ profileCooldownText(profileSummary.cooldown_until) }} 后自动恢复采集（{{ profileCooldownDeadline(profileSummary.cooldown_until) }}）
          </p>
          <p v-else class="text-xs text-emerald-300">账号当前可采集；待采任务由 V2 worker 自动处理。</p>
        </div>
        <div v-if="boundGroups().length" class="flex flex-wrap gap-2 text-xs text-slate-400">
          <span>已绑定：</span><span v-for="b in boundGroups()" :key="b.source_id" class="rounded-full border border-slate-700 px-2 py-1">{{ b.title || `来源#${b.source_id}` }}</span>
        </div>
        <div class="space-y-3">
          <article v-for="item in profileResults" :key="item.job_id" class="rounded-xl border border-slate-800 bg-slate-900/50 p-3">
            <div class="flex flex-wrap items-center justify-between gap-2 text-xs">
              <div class="font-medium text-slate-200"><a :href="item.profile_url || `https://t.me/${item.contact_username}`" target="_blank" class="hover:text-sky-300">@{{ item.contact_username }}</a> <span class="ml-1 text-slate-500">{{ item.duty_date }}</span></div>
              <div class="flex gap-2 text-slate-400"><span>{{ item.status }}</span><span v-if="item.ocr_pending">OCR {{ item.ocr_pending }} 项待跑</span><span v-if="item.ocr_review">{{ item.ocr_review }} 张待看</span></div>
            </div>
            <div v-if="item.error" class="mt-2 text-xs text-rose-300">{{ item.error }}</div>
            <div v-if="item.result_id" class="mt-2 space-y-2">
              <div class="flex flex-wrap gap-2 text-xs">
                <a v-if="item.profile_url" :href="item.profile_url" target="_blank" class="text-sky-300">Telegram 个人简介</a>
                <a v-if="item.channel_url" :href="item.channel_url" target="_blank" class="text-sky-300">关联频道</a>
                <a v-if="item.bot_url" :href="item.bot_url" target="_blank" class="text-violet-300">联系 Bot</a>
              </div>
              <p v-if="item.description" class="max-h-32 overflow-auto whitespace-pre-wrap text-xs leading-relaxed text-slate-300">{{ item.description }}</p>
              <div v-if="item.media?.length" class="grid grid-cols-3 gap-2 sm:grid-cols-6">
                <div v-for="(media, index) in item.media" :key="media.path || index" class="overflow-hidden rounded-lg border border-slate-800">
                  <img v-if="media.kind === 'photo'" :src="mediaUrl(media.path)" class="aspect-square w-full object-cover" loading="lazy" />
                  <video v-else-if="media.poster" :src="mediaUrl(media.path)" :poster="mediaUrl(media.poster)" controls class="aspect-square w-full object-cover" />
                  <div class="p-1 text-[10px] text-slate-400">{{ media.kind }}<span v-if="media.ocr_classification"> · {{ media.ocr_classification }}</span></div>
                  <p v-if="media.ocr_text" class="max-h-12 overflow-auto px-1 pb-1 text-[10px] text-slate-500">{{ media.ocr_text }}</p>
                </div>
              </div>
              <details class="text-xs text-slate-500"><summary class="cursor-pointer">采集证据</summary><pre class="mt-2 max-h-40 overflow-auto whitespace-pre-wrap">{{ JSON.stringify(item.evidence, null, 2) }}</pre></details>
            </div>
          </article>
          <div v-if="!profileResults.length" class="rounded-xl border border-slate-800 bg-slate-900/50 p-4 text-xs text-slate-500">
            暂无试采任务。可输入 Telegram 用户名手动入队，系统会从已绑定来源选择当前可用账号；worker 按账号冷却自动执行。结果先独立保存，不会覆盖旧卡片。
          </div>
        </div>
      </section>
    </div>
  </div>
</template>
