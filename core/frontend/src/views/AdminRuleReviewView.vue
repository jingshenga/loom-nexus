<script setup>
import { computed, onMounted, ref } from "vue";
import { Check, CircleOff, RefreshCw, ShieldAlert } from "lucide-vue-next";
import AdminTabs from "../components/AdminTabs.vue";
import { api, mediaUrl } from "../api/client";

const items = ref([]);
const selected = ref(null);
const confirmedFieldsJson = ref("");
const status = ref("open");
const category = ref("review");
const sourceId = ref("");
const ruleId = ref("");
const createdFrom = ref("");
const createdTo = ref("");
const reason = ref("");
const loading = ref(false);
const busy = ref(false);
const error = ref("");
const notice = ref("");
const confirmedFields = computed(() => {
  try { return JSON.parse(confirmedFieldsJson.value || "{}"); } catch { return {}; }
});
const username = computed(() => confirmedFields.value.username || "");
const fieldRows = computed(() => [
  ["TG 身份", "username"], ["昵称", "nickname"], ["价格", "price_amount"],
  ["区域", "district"], ["描述", "description"],
].map(([label, key]) => {
  const evidence = selected.value?.candidate_json?.evidence?.[key];
  const conflict = (selected.value?.diagnostics_json?.conflicts || [])
    .some((item) => item.kind === "field" && item.field === key);
  return {
    label, value: confirmedFields.value[key] ?? "未提取",
    source: evidence?.src || "无证据", conflict,
  };
}));

async function load() {
  loading.value = true;
  error.value = "";
  try {
    const query = new URLSearchParams({ status: status.value, category: category.value, limit: "200" });
    if (sourceId.value) query.set("source_id", sourceId.value);
    if (ruleId.value) query.set("rule_id", ruleId.value);
    if (reason.value) query.set("reason", reason.value);
    if (createdFrom.value) query.set("since", `${createdFrom.value}T00:00:00+08:00`);
    if (createdTo.value) query.set("until", `${createdTo.value}T00:00:00+08:00`);
    const result = await api(`/api/admin/rules/active-review-queue?${query}`);
    items.value = result.items || [];
    counts.value = result.counts || { review: 0, unmatched: 0 };
    if (selected.value && items.value.some(item => item.id === selected.value.id))
      await openItem(selected.value.id);
    else if (items.value.length) await openItem(items.value[0].id);
    else selected.value = null;
  } catch (e) {
    error.value = e.message || "读取规则待审队列失败";
  } finally {
    loading.value = false;
  }
}

const counts = ref({ review: 0, unmatched: 0 });

async function openItem(id) {
  try {
    selected.value = await api(`/api/admin/rules/active-review-queue/${id}`);
    confirmedFieldsJson.value = JSON.stringify(selected.value.candidate_json?.fields || {}, null, 2);
  } catch (e) {
    error.value = e.message || "读取候选详情失败";
  }
}

async function resolve(action) {
  if (!selected.value || busy.value) return;
  if (action === "create_pending_listing") {
    try { JSON.parse(confirmedFieldsJson.value); }
    catch { error.value = "候选字段不是有效 JSON"; return; }
  }
  const labels = { dismiss: "驳回候选", no_action: "标记无需处理", create_pending_listing: "送入 Listing 待审核" };
  if (!window.confirm(`确认${labels[action]}？此操作会记录管理员和时间。送入 Listing 只创建待审核卡片，不会发布或修改已有卡片。`)) return;
  busy.value = true;
  error.value = "";
  notice.value = "";
  try {
    const result = await api(`/api/admin/rules/active-review-queue/${selected.value.id}/resolve`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, confirmed_fields: action === "create_pending_listing" ? confirmedFields.value : null }),
    });
    notice.value = result.listing_id
      ? `已创建待审核卡片 #${result.listing_id}。` : `已记录：${labels[action]}。`;
    selected.value = null;
    await load();
  } catch (e) {
    error.value = e.message || "审核操作失败";
  } finally {
    busy.value = false;
  }
}

async function requeue() {
  if (!selected.value || busy.value) return;
  busy.value = true;
  error.value = "";
  notice.value = "";
  try {
    await api(`/api/admin/rules/active-review-queue/${selected.value.id}/requeue`, { method: "POST" });
    notice.value = "已将该未命中记录送入人工审核，操作已记录。";
    category.value = "review";
    await load();
  } catch (e) {
    error.value = e.message || "送入人工审核失败";
  } finally {
    busy.value = false;
  }
}

onMounted(load);
</script>

<template>
  <AdminTabs />
  <main class="space-y-4">
    <header class="flex flex-wrap items-center justify-between gap-3">
      <div>
        <h1 class="flex items-center gap-2 text-xl font-semibold"><ShieldAlert class="size-5" />规则待审</h1>
        <p class="mt-1 text-xs text-slate-400">身份不明、规则冲突及 Active 解析异常集中核对。送入 Listing 只生成待审核卡片。</p>
      </div>
      <button class="flex items-center gap-2 rounded-lg border border-slate-700 px-3 py-2 text-sm" @click="load"><RefreshCw class="size-4" />刷新</button>
    </header>
    <p v-if="notice" role="status" class="rounded-lg border border-emerald-800 bg-emerald-950/50 px-3 py-2 text-sm text-emerald-200">{{ notice }}</p>
    <p v-if="error" role="alert" class="rounded-lg border border-rose-800 bg-rose-950/50 px-3 py-2 text-sm text-rose-200">{{ error }}</p>
    <section class="flex flex-wrap gap-2 rounded-xl border border-slate-800 bg-slate-900/60 p-3">
      <select v-model="status" class="rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm" @change="load">
        <option value="open">待处理</option><option value="resolved">已处理</option><option value="dismissed">已驳回</option>
      </select>
      <input v-model="sourceId" inputmode="numeric" placeholder="来源 ID" class="w-28 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm" @change="load" />
      <input v-model="ruleId" inputmode="numeric" placeholder="规则 ID" class="w-28 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm" @change="load" />
      <input v-model="reason" placeholder="原因代码（可选）" class="min-w-40 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm" @change="load" />
      <label class="flex items-center gap-2 text-xs text-slate-400">从<input v-model="createdFrom" type="date" class="rounded-lg border border-slate-700 bg-slate-950 px-2 py-2 text-sm text-slate-200" @change="load" /></label>
      <label class="flex items-center gap-2 text-xs text-slate-400">至<input v-model="createdTo" type="date" class="rounded-lg border border-slate-700 bg-slate-950 px-2 py-2 text-sm text-slate-200" @change="load" /></label>
      <div class="flex flex-wrap gap-2" role="tablist" aria-label="审核队列分类">
        <button v-for="tab in [{id:'review',label:'需处理'},{id:'unmatched',label:'普通未命中'},{id:'all',label:'全部'}]" :key="tab.id"
          role="tab" :aria-selected="category === tab.id" class="rounded-lg border px-3 py-2 text-sm"
          :class="category === tab.id ? 'border-sky-600 bg-sky-950/50 text-sky-200' : 'border-slate-700 text-slate-300'"
          @click="category = tab.id; load()">
          {{ tab.label }} <span v-if="tab.id !== 'all'" class="ml-1 text-xs text-slate-400">{{ counts[tab.id] }}</span>
        </button>
      </div>
      <span class="self-center text-xs text-slate-400">{{ items.length }} 条当前页</span>
    </section>
    <div class="grid gap-4 xl:grid-cols-[minmax(18rem,0.7fr)_minmax(0,1.5fr)]">
      <section class="max-h-[75vh] space-y-2 overflow-y-auto">
        <p v-if="loading" class="rounded-xl border border-slate-800 p-6 text-center text-sm text-slate-400">读取中…</p>
        <p v-else-if="!items.length" class="rounded-xl border border-slate-800 p-6 text-center text-sm text-slate-400">当前筛选没有候选。</p>
        <button v-for="item in items" :key="item.id" class="w-full rounded-xl border p-3 text-left"
          :class="selected?.id === item.id ? 'border-sky-600 bg-sky-950/30' : 'border-slate-800 bg-slate-900/70'"
          @click="openItem(item.id)">
          <div class="flex items-start justify-between gap-2"><span class="font-medium">{{ item.source_title }} · 消息 {{ item.platform_msg_id }}</span><span class="shrink-0 text-xs text-amber-300">{{ item.reason }}</span></div>
          <p class="mt-1 line-clamp-3 text-sm text-slate-300">{{ item.message_preview || "（无文字，查看媒体）" }}</p>
          <p class="mt-2 text-xs text-slate-500">{{ item.category === 'unmatched' ? '普通未命中' : '需人工核验' }} · 规则 {{ item.candidate_rule_ids.join(", ") || "无命中" }} · {{ item.created_at }}</p>
        </button>
      </section>
      <section v-if="selected" class="min-w-0 space-y-4 rounded-xl border border-slate-800 bg-slate-900/60 p-4">
        <header class="flex flex-wrap items-start justify-between gap-2">
          <div><h2 class="font-semibold">{{ selected.source_title }} · 消息 {{ selected.platform_msg_id }}</h2><p class="mt-1 text-xs text-slate-400">来源 {{ selected.source_id }} · {{ selected.posted_at || '发布时间未知' }} · 候选 #{{ selected.id }} · {{ selected.reason }} · {{ selected.status }}</p><p v-if="selected.candidate_json?.ocr_assist?.length" class="mt-1 text-xs text-sky-300">OCR 辅助候选 · {{ selected.candidate_json.ocr_assist.length }} 张图片证据</p></div>
          <p v-if="username" class="text-sm text-sky-300">TG：@{{ String(username).replace(/^@/, "") }}</p>
        </header>
        <pre class="max-h-60 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-slate-950 p-3 text-sm text-slate-200">{{ selected.raw_text || "（原消息无文字）" }}</pre>
        <div v-if="selected.media?.length" class="grid grid-cols-2 gap-3 sm:grid-cols-3">
          <div v-for="(media,index) in selected.media" :key="index" class="min-w-0 rounded-lg border border-slate-700 p-2 text-xs text-slate-300">
            <a :href="mediaUrl(media.path || media.poster)" target="_blank" rel="noreferrer" class="block">
            <img v-if="media.kind === 'photo' || media.poster" :src="mediaUrl(media.kind === 'photo' ? media.path : media.poster, { thumbnail: true })" class="aspect-square w-full rounded object-cover" loading="lazy" />
            <span class="block truncate p-1">{{ media.kind || "媒体" }}</span>
            </a>
            <section v-if="media.ocr_result" class="mt-2 space-y-1 border-t border-slate-800 pt-2">
              <p class="font-medium text-sky-200">已有 OCR · {{ media.ocr_result.status }} · {{ media.ocr_result.review_status }} · 分类 {{ media.ocr_result.classification }} · 版本 {{ media.ocr_result.engine_version || '未知' }} · 置信度 {{ media.ocr_result.confidence }}</p>
              <p v-if="media.ocr_result.classifier_version_id || media.ocr_result.human_classification" class="text-slate-400">图片分类 {{ media.ocr_result.predicted_classification || media.ocr_result.classification }} · 模型版本 #{{ media.ocr_result.classifier_version_id || '未知' }} · 人工标签 {{ media.ocr_result.human_classification || '无' }} · 预测置信度 {{ media.ocr_result.predicted_confidence ?? '未知' }}</p>
              <p class="whitespace-pre-wrap break-words text-slate-300">{{ media.ocr_result.raw_text || media.ocr_result.normalized_text || '已有 OCR 记录，无文本结果' }}</p>
              <pre v-if="media.ocr_result.fields_json && Object.keys(media.ocr_result.fields_json).length" class="whitespace-pre-wrap break-words text-slate-400">{{ JSON.stringify(media.ocr_result.fields_json, null, 2) }}</pre>
            </section>
          </div>
        </div>
        <div class="grid gap-3 md:grid-cols-2">
          <div class="rounded-lg border border-slate-800 p-3"><h3 class="mb-2 text-sm font-medium">候选字段与缺失项</h3><dl class="mb-3 grid grid-cols-[5rem_minmax(0,1fr)] gap-x-2 gap-y-1 text-xs"><template v-for="row in fieldRows" :key="row.label"><dt class="text-slate-500">{{ row.label }}</dt><dd class="break-words text-slate-300">{{ row.value }} <span class="text-slate-500">· {{ row.source }}</span><span v-if="row.conflict" class="ml-1 text-amber-300">· 冲突</span></dd></template></dl><textarea v-model="confirmedFieldsJson" :disabled="selected.status !== 'open' || selected.reason === 'no_rule_match'" rows="7" spellcheck="false" class="w-full rounded-lg border border-slate-700 bg-slate-950 p-2 font-mono text-xs text-slate-300" /><p v-if="selected.reason === 'no_rule_match'" class="mt-2 text-xs text-slate-400">当前没有规则提取字段；送入审核后仍需人工核验身份与字段。</p></div>
          <div class="rounded-lg border border-slate-800 p-3"><h3 class="mb-2 text-sm font-medium">规则命中与冲突</h3><p class="mb-2 text-xs text-slate-400">{{ selected.reason }} · 命中规则 {{ selected.candidate_rule_ids.join(', ') || '无' }}</p><div v-for="(trace,index) in selected.diagnostics_json?.trace || []" :key="index" class="mb-2 rounded bg-slate-950 p-2 text-xs text-slate-300">{{ selected.rule_versions?.find(rule => rule.rule_version_id === trace.rule_version_id)?.rule_name || `规则 ${trace.rule_id}` }} · 版本 {{ selected.rule_versions?.find(rule => rule.rule_version_id === trace.rule_version_id)?.version || trace.rule_version_id || '未知' }} · {{ trace.matched ? '命中' : trace.reason || '未命中' }}</div><pre class="whitespace-pre-wrap break-words text-xs text-slate-400">{{ JSON.stringify(selected.diagnostics_json || {}, null, 2) }}</pre></div>
        </div>
        <section class="rounded-lg border border-slate-800 p-3"><h3 class="mb-2 text-sm font-medium">操作历史</h3><p v-if="!selected.events?.length" class="text-xs text-slate-500">暂无处理记录</p><div v-for="event in selected.events" :key="event.id" class="border-t border-slate-800 py-2 text-xs text-slate-400">{{ event.action }} · 管理员 {{ event.actor_user_id }} · {{ event.created_at }}{{ event.listing_id ? ` · Listing #${event.listing_id}` : "" }}</div></section>
        <div v-if="selected.status === 'open'" class="flex flex-wrap gap-2 border-t border-slate-800 pt-3">
          <button v-if="selected.reason === 'no_rule_match'" :disabled="busy" class="rounded-lg border border-sky-700 px-3 py-2 text-sm text-sky-200 disabled:opacity-50" @click="requeue">送入人工审核</button>
          <button :disabled="busy" class="flex items-center gap-1 rounded-lg border border-rose-800 px-3 py-2 text-sm text-rose-200 disabled:opacity-50" @click="resolve('dismiss')"><CircleOff class="size-4" />驳回无效候选</button>
          <button :disabled="busy" class="rounded-lg border border-slate-700 px-3 py-2 text-sm disabled:opacity-50" @click="resolve('no_action')">无需处理</button>
          <button :disabled="busy || !username" class="flex items-center gap-1 rounded-lg bg-sky-700 px-3 py-2 text-sm text-white disabled:opacity-50" @click="resolve('create_pending_listing')"><Check class="size-4" />送入 Listing 待审核</button>
        </div>
        <p v-if="selected.status === 'open' && !username" class="text-xs text-amber-300">缺少明确 TG 身份，不能创建 Listing；请驳回或标记无需处理。</p>
      </section>
      <section v-else-if="!loading" class="rounded-xl border border-slate-800 p-8 text-center text-sm text-slate-500">选择一条候选查看证据和处理记录。</section>
    </div>
  </main>
</template>
