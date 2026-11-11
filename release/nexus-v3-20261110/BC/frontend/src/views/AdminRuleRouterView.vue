<script setup>
import { computed, onMounted, ref } from "vue";
import { GitBranch, RefreshCw, RotateCcw, Search } from "lucide-vue-next";
import AdminTabs from "../components/AdminTabs.vue";
import { api } from "../api/client";

const sources = ref([]);
const templates = ref([]);
const diagnostics = ref(null);
const selectedSourceId = ref("");
const selectedRuleId = ref("");
const selectedVersionId = ref("");
const selectedRawId = ref("");
const purpose = ref("listing");
const search = ref("");
const versions = ref([]);
const samples = ref([]);
const dryRun = ref(null);
const loading = ref(false);
const busy = ref(false);
const error = ref("");
const notice = ref("");

const selectedSource = computed(() => sources.value.find((item) => String(item.source_id) === String(selectedSourceId.value)) || null);
const visibleSources = computed(() => sources.value.filter((item) =>
  !search.value || (item.title + " " + item.peer_id + " " + item.username + " "
    + item.bindings.map((rule) => rule.rule_name).join(" ")).toLowerCase().includes(search.value.toLowerCase())));
const bindings = computed(() => selectedSource.value?.bindings || []);
const selectedRule = computed(() => bindings.value.find((rule) => String(rule.rule_id) === String(selectedRuleId.value)) || bindings.value[0] || null);
const selectedVersion = computed(() => versions.value.find((version) => String(version.id) === String(selectedVersionId.value)) || null);
const selectedTemplateVersion = computed(() => {
  if (!selectedVersion.value?.template_version_id) return null;
  for (const template of templates.value) {
    const version = (template.versions || []).find((item) => item.id === selectedVersion.value.template_version_id);
    if (version) return { ...version, template_name: template.template_name };
  }
  return null;
});
const differences = computed(() => {
  if (!selectedRule.value || !selectedVersion.value) return [];
  const current = selectedRule.value.config || {};
  const target = selectedVersion.value.config_json || {};
  return [...new Set([...Object.keys(current), ...Object.keys(target)])].sort()
    .filter((key) => JSON.stringify(current[key]) !== JSON.stringify(target[key]))
    .map((key) => ({ key, current: current[key], target: target[key] }));
});

function json(value) {
  return JSON.stringify(value ?? null, null, 2);
}

async function loadVersions() {
  versions.value = [];
  const rule = selectedRule.value;
  if (!rule) return;
  selectedRuleId.value = String(rule.rule_id);
  const data = await api("/api/admin/rules/" + rule.rule_id + "/versions");
  versions.value = data.items || [];
  const old = versions.value.find((version) => version.version < rule.version);
  selectedVersionId.value = String((old || versions.value[0] || {}).id || "");
}

async function load() {
  loading.value = true;
  error.value = "";
  try {
    const [routerData, templateData, diagnosticsData] = await Promise.all([
      api("/api/admin/rules/router-management"),
      api("/api/admin/rules/templates"),
      api("/api/admin/rules/shadow-diagnostics"),
    ]);
    sources.value = routerData.items || [];
    templates.value = templateData.items || [];
    diagnostics.value = diagnosticsData;
    if (!sources.value.some((item) => String(item.source_id) === String(selectedSourceId.value))) {
      selectedSourceId.value = String(sources.value[0]?.source_id || "");
    }
    await Promise.all([loadVersions(), loadSamples()]);
  } catch (e) {
    error.value = e.message || "读取规则管理状态失败";
  } finally {
    loading.value = false;
  }
}

async function changeSource(source) {
  selectedSourceId.value = String(source.source_id);
  selectedRuleId.value = String(source.bindings[0]?.rule_id || "");
  selectedRawId.value = "";
  dryRun.value = null;
  await Promise.all([loadVersions(), loadSamples()]);
}

async function loadSamples() {
  samples.value = [];
  if (!selectedSource.value) return;
  try {
    const data = await api("/api/admin/rules/sources/" + selectedSource.value.source_id + "/raw-samples?limit=50");
    samples.value = data.items || [];
  } catch (e) {
    error.value = e.message || "读取历史消息样本失败";
  }
}

async function runDryRun() {
  if (!selectedSource.value) return;
  busy.value = true;
  error.value = "";
  try {
    const ids = selectedRawId.value ? [Number(selectedRawId.value)] : [];
    dryRun.value = await api("/api/admin/rules/router-dryrun", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source_id: selectedSource.value.source_id,
        purpose: purpose.value,
        raw_message_ids: ids,
        sample_limit: ids.length ? 1 : 20,
        rule_version_id: selectedVersionId.value ? Number(selectedVersionId.value) : null,
      }),
    });
  } catch (e) {
    error.value = e.message || "Dry-run 失败";
  } finally {
    busy.value = false;
  }
}

async function setMode(mode) {
  const source = selectedSource.value;
  if (!source || busy.value || source.router_mode === mode) return;
  const modeName = mode === "shadow" ? "Shadow" : "Legacy";
  const prompt = "确定只对来源「" + source.title + "」(ID " + source.source_id + ") 切换为 " + modeName + "？\n\n"
    + (mode === "shadow"
      ? "Shadow 只记录预测，Legacy 继续处理实际数据；启用前需已部署相应数据库结构。"
      : "后续消息会回到 Legacy 路径。")
    + "\n操作会记录管理员身份和变更时间。";
  if (!window.confirm(prompt)) return;
  busy.value = true;
  error.value = "";
  try {
    await api("/api/admin/rules/sources/" + source.source_id + "/router-mode", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    });
    notice.value = "来源 " + source.title + " 已切换为 " + modeName + "。";
    await load();
  } catch (e) {
    error.value = e.message || "来源模式更新失败";
  } finally {
    busy.value = false;
  }
}

async function rollback() {
  if (!selectedRule.value || !selectedVersion.value) return;
  const affected = sources.value.filter((source) => source.bindings.some((item) => item.rule_id === selectedRule.value.rule_id));
  const names = affected.map((source) => source.title + " (" + source.source_id + ")").join("、") || "无来源";
  if (!window.confirm("将规则「" + selectedRule.value.rule_name + "」生成回滚版本 v"
      + selectedVersion.value.version + "？\n关联来源：" + names
      + "\n仅影响后续处理，不重写历史消息、卡片或审核状态。")) return;
  busy.value = true;
  try {
    await api("/api/admin/rules/" + selectedRule.value.rule_id + "/rollback", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target_version: selectedVersion.value.version, change_summary: "规则工坊管理员回滚" }),
    });
    notice.value = "已生成回滚版本；既有解析和卡片未修改。";
    await load();
  } catch (e) {
    error.value = e.message || "回滚失败";
  } finally {
    busy.value = false;
  }
}

async function prune() {
  const counts = diagnostics.value?.counts || {};
  const expiring = Number(counts.expired_runs || 0) + Number(counts.expired_comparisons || 0)
    + Number(counts.expired_error_buckets || 0);
  if (!expiring) return;
  if (!window.confirm("当前估算 " + expiring + " 条超过 30 天的 Shadow 诊断。每批最多 250 条，本次最多 4 批。规则版本、人工审核、Lifecycle 事件和 Listing 不会删除。继续？")) return;
  busy.value = true;
  try {
    const result = await api("/api/admin/rules/shadow-diagnostics/prune", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ older_than_days: 30, batch_size: 250, max_batches: 4 }),
    });
    notice.value = "清理 #" + result.run_id + " 完成：比较 " + result.deleted.comparisons
      + "、解析 " + result.deleted.shadow_runs + "、错误汇总 " + result.deleted.error_buckets + " 条。";
    await load();
  } catch (e) {
    error.value = e.message || "Shadow 清理失败";
    await load();
  } finally {
    busy.value = false;
  }
}

onMounted(load);
</script>

<template>
  <AdminTabs />
  <section class="space-y-4">
    <header class="flex flex-wrap items-center justify-between gap-3">
      <div>
        <h1 class="flex items-center gap-2 text-xl font-semibold"><GitBranch class="size-5" />规则版本与 Shadow</h1>
        <p class="mt-1 text-xs text-slate-400">逐来源查看和操作。Active 当前不可用；模式切换仅作用于单个来源。</p>
      </div>
      <button class="flex items-center gap-2 rounded-lg border border-slate-700 px-3 py-2 text-sm" @click="load"><RefreshCw class="size-4" />刷新</button>
    </header>
    <p v-if="notice" role="status" class="rounded-lg border border-emerald-800 bg-emerald-950/50 px-3 py-2 text-sm text-emerald-200">{{ notice }}</p>
    <p v-if="error" role="alert" class="rounded-lg border border-rose-800 bg-rose-950/50 px-3 py-2 text-sm text-rose-200">{{ error }}</p>
    <div v-if="loading" class="rounded-xl border border-slate-800 p-8 text-center text-sm text-slate-400">读取规则与来源状态…</div>
    <div v-else class="grid gap-4 xl:grid-cols-[minmax(17rem,0.7fr)_minmax(0,1.7fr)]">
      <aside class="space-y-3">
        <label class="relative block"><Search class="absolute left-3 top-2.5 size-4 text-slate-500" /><input v-model="search" placeholder="搜索来源或规则" class="w-full rounded-lg border border-slate-700 bg-slate-950 py-2 pl-9 pr-3 text-sm" /></label>
        <div class="max-h-[70vh] space-y-2 overflow-y-auto pr-1">
          <button v-for="source in visibleSources" :key="source.source_id" class="w-full min-w-0 rounded-xl border p-3 text-left" :class="String(source.source_id) === String(selectedSourceId) ? 'border-sky-600 bg-sky-950/30' : 'border-slate-800 bg-slate-900/70'" @click="changeSource(source)">
            <div class="flex min-w-0 items-start justify-between gap-2"><span class="min-w-0 break-words text-sm font-medium">{{ source.title || "未命名来源" }}</span><span class="shrink-0 rounded px-2 py-0.5 text-[10px]" :class="source.router_mode === 'shadow' ? 'bg-violet-900 text-violet-200' : 'bg-slate-800 text-slate-300'">{{ source.router_mode === "shadow" ? "Shadow" : "Legacy" }}</span></div>
            <p class="mt-1 break-all text-[11px] text-slate-500">Source {{ source.source_id }} · peer {{ source.peer_id }}</p>
            <p class="mt-1 text-[11px] text-slate-500">{{ source.bindings.length }} 条绑定 · {{ source.last_shadow ? "最近 Shadow " + new Date(source.last_shadow.observed_at).toLocaleString() : "无 Shadow 记录" }}</p>
          </button>
        </div>
      </aside>

      <div v-if="selectedSource" class="min-w-0 space-y-4">
        <section class="space-y-3 rounded-xl border border-slate-800 bg-slate-900/70 p-4">
          <div class="flex flex-wrap items-start justify-between gap-3">
            <div class="min-w-0">
              <h2 class="break-words text-lg font-semibold">{{ selectedSource.title || "未命名来源" }}</h2>
              <p class="mt-1 break-all text-xs text-slate-400">Source {{ selectedSource.source_id }} · peer {{ selectedSource.peer_id }} · {{ selectedSource.username ? "@" + selectedSource.username : "无公开用户名" }} · {{ selectedSource.router_mode }}</p>
              <p class="mt-1 text-[11px] text-amber-300">模式切换依赖 2A–2D 数据库迁移已完成；本地页面不会代替生产迁移或发布审批。</p>
              <p v-if="selectedSource.last_mode_event" class="mt-1 text-[11px] text-slate-500">最近模式：{{ selectedSource.last_mode_event.previous_mode }} → {{ selectedSource.last_mode_event.new_mode }} · 管理员 #{{ selectedSource.last_mode_event.actor_user_id }} · {{ new Date(selectedSource.last_mode_event.changed_at).toLocaleString() }}</p>
            </div>
            <div class="flex flex-wrap gap-2">
              <button v-if="selectedSource.router_mode === 'legacy'" :disabled="busy" class="rounded-lg bg-violet-700 px-3 py-2 text-sm text-white disabled:opacity-50" @click="setMode('shadow')">此来源启用 Shadow</button>
              <button v-else :disabled="busy" class="rounded-lg bg-slate-700 px-3 py-2 text-sm text-white disabled:opacity-50" @click="setMode('legacy')">切回 Legacy</button>
              <button disabled title="Active 模式本阶段不可用" class="cursor-not-allowed rounded-lg border border-slate-800 px-3 py-2 text-sm text-slate-600">Active（禁用）</button>
            </div>
          </div>
          <div class="grid gap-2 md:grid-cols-2">
            <article v-for="binding in bindings" :key="binding.rule_id" class="min-w-0 rounded-lg border border-slate-800 bg-slate-950/70 p-3">
              <div class="flex items-start justify-between gap-2"><h3 class="break-words text-sm font-medium">{{ binding.rule_name }}</h3><span class="shrink-0 text-xs text-slate-500">{{ binding.rule_type }}</span></div>
              <p class="mt-1 break-all text-[11px] text-slate-400">规则 #{{ binding.rule_id }} · priority {{ binding.priority }} · v{{ binding.version ?? "?" }}</p>
              <p class="break-all text-[11px] text-slate-500">{{ binding.template_name || "独立实例" }} · 模板版本 {{ binding.template_version_id || "无" }} · 绑定版本 {{ binding.rule_version_id || "未固定" }}</p>
            </article>
          </div>
          <p v-if="selectedSource.last_shadow" class="rounded-lg border border-slate-800 bg-slate-950/60 p-3 text-xs text-slate-400">最近比较：{{ selectedSource.last_shadow.changed ? "存在差异" : "结果一致" }} · {{ new Date(selectedSource.last_shadow.observed_at).toLocaleString() }} · Raw #{{ selectedSource.last_shadow.raw_id }}</p>
          <p v-else class="text-xs text-slate-500">该来源没有已记录 Shadow 比较。</p>
          <div v-if="selectedSource.recent_errors?.length" class="rounded-lg border border-amber-900/60 bg-amber-950/20 p-3 text-xs text-amber-200">近 24 小时错误：
            <span v-for="item in selectedSource.recent_errors" :key="item.error_class + item.last_occurred_at" class="ml-2">{{ item.error_class }} × {{ item.occurrences }}</span>
          </div>
        </section>

        <section class="space-y-3 rounded-xl border border-slate-800 bg-slate-900/70 p-4">
          <h2 class="text-base font-semibold">规则历史与回滚预览</h2>
          <div class="grid gap-2 sm:grid-cols-2">
            <label class="text-xs text-slate-400">规则
              <select v-model="selectedRuleId" class="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm" @change="loadVersions">
                <option v-for="binding in bindings" :key="binding.rule_id" :value="String(binding.rule_id)">{{ binding.rule_name }} · v{{ binding.version }}</option>
              </select>
            </label>
            <label class="text-xs text-slate-400">对比目标版本
              <select v-model="selectedVersionId" class="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm">
                <option v-for="version in versions" :key="version.id" :value="String(version.id)">v{{ version.version }} · {{ version.snapshot_origin }} · {{ new Date(version.created_at).toLocaleString() }}</option>
              </select>
            </label>
          </div>
          <p v-if="selectedVersion" class="text-xs text-slate-400">创建人 {{ selectedVersion.created_by }} · {{ new Date(selectedVersion.created_at).toLocaleString() }} · 模板 {{ selectedTemplateVersion ? selectedTemplateVersion.template_name + ' v' + selectedTemplateVersion.version : '无' }} · hash {{ selectedVersion.config_hash?.slice(0, 12) }}</p>
          <p v-if="selectedVersion && !differences.length" class="rounded-lg border border-emerald-900/50 p-3 text-xs text-emerald-200">配置没有差异。</p>
          <div v-for="item in differences" :key="item.key" class="grid gap-2 rounded-lg border border-slate-800 bg-slate-950/60 p-3 sm:grid-cols-2">
            <div class="min-w-0"><p class="mb-1 break-all text-xs">{{ item.key }} · 当前</p><pre class="max-h-40 overflow-auto whitespace-pre-wrap break-words text-[11px] text-rose-200">{{ json(item.current) }}</pre></div>
            <div class="min-w-0"><p class="mb-1 break-all text-xs">目标版本</p><pre class="max-h-40 overflow-auto whitespace-pre-wrap break-words text-[11px] text-emerald-200">{{ json(item.target) }}</pre></div>
          </div>
          <button v-if="selectedVersion && selectedRule && selectedVersion.version < selectedRule.version" :disabled="busy" class="flex items-center gap-2 rounded-lg border border-amber-700 px-3 py-2 text-sm text-amber-200 disabled:opacity-50" @click="rollback"><RotateCcw class="size-4" />生成回滚版本</button>
        </section>

        <section class="space-y-3 rounded-xl border border-slate-800 bg-slate-900/70 p-4">
          <h2 class="text-base font-semibold">指定来源 Dry-run</h2>
          <p class="text-xs text-slate-400">只读历史 raw_messages，不访问 Telegram、不调用 OCR、不建任务或改 Listing。</p>
          <div class="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
            <label class="text-xs text-slate-400">用途<select v-model="purpose" class="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-2 py-2 text-sm"><option value="listing">名片</option><option value="on_duty">在岗</option><option value="checkin_reverse">签到反查</option></select></label>
            <label class="text-xs text-slate-400">历史消息样本
              <select v-model="selectedRawId" class="mt-1 w-full min-w-0 rounded-lg border border-slate-700 bg-slate-950 px-2 py-2 text-sm"><option value="">最近 20 条</option><option v-for="sample in samples" :key="sample.raw_id" :value="String(sample.raw_id)">Raw #{{ sample.raw_id }} · 消息 #{{ sample.platform_msg_id }} · {{ sample.text_preview?.slice(0, 45) || "无文字" }}</option></select>
            </label>
            <label class="text-xs text-slate-400">测试版本<select v-model="selectedVersionId" class="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-2 py-2 text-sm"><option value="">当前绑定</option><option v-for="version in versions" :key="version.id" :value="String(version.id)">v{{ version.version }}</option></select></label>
            <button :disabled="busy || !selectedSource" class="mt-auto flex items-center justify-center gap-2 rounded-lg bg-sky-700 px-3 py-2 text-sm text-white disabled:opacity-50" @click="runDryRun"><Search class="size-4" />运行 Dry-run</button>
          </div>
          <div v-if="dryRun" class="space-y-3">
            <p class="rounded-lg border border-emerald-900/50 bg-emerald-950/20 p-3 text-xs text-emerald-200">只读 · {{ dryRun.count }} 条 · Telegram {{ dryRun.telegram_requests }} 次 · OCR {{ dryRun.ocr_runs }} 次</p>
            <article v-for="result in dryRun.results" :key="result.raw_id" class="min-w-0 space-y-2 rounded-lg border border-slate-800 bg-slate-950/60 p-3">
              <h3 class="break-all text-sm font-medium">Raw #{{ result.raw_id }} · 消息 #{{ result.platform_msg_id }} · {{ result.changed ? "有差异" : "一致" }}</h3>
              <p class="break-words text-xs text-slate-400">身份 {{ result.identity_diff.legacy || "未识别" }} → {{ result.identity_diff.router || "未识别" }} · 冲突 {{ result.conflicts.length }}</p>
              <p v-if="Object.keys(result.field_diff || {}).length" class="break-words text-xs text-amber-300">字段差异：<span v-for="(diff, key) in result.field_diff" :key="key" class="mr-2">{{ key }}（{{ diff.legacy ?? '空' }} → {{ diff.router ?? '空' }}）</span></p>
              <div class="grid gap-2 md:grid-cols-2">
                <pre class="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded bg-black/20 p-2 text-[11px]">Legacy
{{ json(result.legacy) }}</pre>
                <pre class="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded bg-black/20 p-2 text-[11px]">Shadow
{{ json(result.router) }}</pre>
              </div>
              <p class="break-words text-[11px] text-amber-300">预计动作 {{ result.estimated_action }} · 阻断 {{ result.publication_blockers.join("、") }}</p>
            </article>
          </div>
        </section>
      </div>
      <p v-else class="rounded-xl border border-slate-800 p-8 text-sm text-slate-500">没有来源。</p>
    </div>

    <section v-if="diagnostics" class="space-y-3 rounded-xl border border-slate-800 bg-slate-900/70 p-4">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <div><h2 class="text-base font-semibold">Shadow 日志治理</h2><p class="mt-1 text-xs text-slate-400">只清理过期 Shadow 诊断，不清理规则、人工审核、Lifecycle 或 Listing。</p></div>
        <button :disabled="busy || (!diagnostics.counts.expired_runs && !diagnostics.counts.expired_comparisons && !diagnostics.counts.expired_error_buckets)" class="rounded-lg border border-amber-800 px-3 py-2 text-sm text-amber-200 disabled:opacity-40" @click="prune">预览并分批清理</button>
      </div>
      <div class="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        <p class="rounded-lg border border-slate-800 p-3 text-xs">比较 {{ diagnostics.counts.comparisons }} · {{ (diagnostics.counts.comparison_bytes / 1048576).toFixed(2) }} MiB</p>
        <p class="rounded-lg border border-slate-800 p-3 text-xs">解析 {{ diagnostics.counts.shadow_runs }} · {{ (diagnostics.counts.parse_run_bytes / 1048576).toFixed(2) }} MiB</p>
        <p class="rounded-lg border border-slate-800 p-3 text-xs">24 小时错误 {{ diagnostics.counts.recent_errors }} · 桶 {{ diagnostics.counts.error_buckets }}</p>
        <p class="rounded-lg border border-slate-800 p-3 text-xs">预计过期 {{ diagnostics.counts.expired_comparisons + diagnostics.counts.expired_runs + diagnostics.counts.expired_error_buckets }}</p>
      </div>
      <div v-if="diagnostics.warnings?.length" role="alert" class="rounded-lg border border-rose-800 bg-rose-950/30 p-3 text-sm text-rose-200">
        <p class="font-medium">Shadow 治理告警</p>
        <p class="mt-1">{{ diagnostics.warnings.map((item) => item === "shadow_storage_capacity" ? "存储容量达到阈值" : "近 24 小时 Shadow 错误达到阈值").join("；") }}</p>
        <p class="mt-1 text-xs text-rose-300/80">阈值：{{ (diagnostics.thresholds.storage_bytes / 1048576).toFixed(0) }} MiB · {{ diagnostics.thresholds.recent_errors }} 条错误</p>
      </div>
      <p v-if="diagnostics.last_cleanup" class="text-xs text-slate-500">上次清理 #{{ diagnostics.last_cleanup.id }} · {{ diagnostics.last_cleanup.status }} · {{ diagnostics.last_cleanup.actor_username || "未知管理员" }} · {{ new Date(diagnostics.last_cleanup.started_at).toLocaleString() }}</p>
    </section>
  </section>
</template>
