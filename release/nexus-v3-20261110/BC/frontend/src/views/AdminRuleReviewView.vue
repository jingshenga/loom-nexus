<script setup>
import { computed, onMounted, ref } from "vue";
import { Check, CircleOff, RefreshCw, ShieldAlert } from "lucide-vue-next";
import AdminTabs from "../components/AdminTabs.vue";
import { api, mediaUrl } from "../api/client";

const items = ref([]);
const selected = ref(null);
const confirmedFieldsJson = ref("");
const status = ref("open");
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

async function load() {
  loading.value = true;
  error.value = "";
  try {
    const query = new URLSearchParams({ status: status.value, limit: "200" });
    if (sourceId.value) query.set("source_id", sourceId.value);
    if (ruleId.value) query.set("rule_id", ruleId.value);
    if (reason.value) query.set("reason", reason.value);
    if (createdFrom.value) query.set("since", `${createdFrom.value}T00:00:00+08:00`);
    if (createdTo.value) query.set("until", `${createdTo.value}T00:00:00+08:00`);
    const result = await api(`/api/admin/rules/active-review-queue?${query}`);
    items.value = result.items || [];
    if (selected.value) await openItem(selected.value.id);
    else if (items.value.length) await openItem(items.value[0].id);
    else selected.value = null;
  } catch (e) {
    error.value = e.message || "读取规则待审队列失败";
  } finally {
    loading.value = false;
  }
}

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
      <span class="self-center text-xs text-slate-400">{{ items.length }} 条</span>
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
          <p class="mt-2 text-xs text-slate-500">规则 {{ item.candidate_rule_ids.join(", ") || "无命中" }} · {{ item.created_at }}</p>
        </button>
      </section>
      <section v-if="selected" class="min-w-0 space-y-4 rounded-xl border border-slate-800 bg-slate-900/60 p-4">
        <header class="flex flex-wrap items-start justify-between gap-2">
          <div><h2 class="font-semibold">{{ selected.source_title }} · 消息 {{ selected.platform_msg_id }}</h2><p class="mt-1 text-xs text-slate-400">候选 #{{ selected.id }} · {{ selected.reason }} · {{ selected.status }}</p></div>
          <p v-if="username" class="text-sm text-sky-300">TG：@{{ String(username).replace(/^@/, "") }}</p>
        </header>
        <pre class="max-h-60 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-slate-950 p-3 text-sm text-slate-200">{{ selected.raw_text || "（原消息无文字）" }}</pre>
        <div v-if="selected.media?.length" class="flex flex-wrap gap-2">
          <a v-for="(media,index) in selected.media" :key="index" :href="mediaUrl(media.path || media.poster)" target="_blank" class="block w-24 rounded-lg border border-slate-700 p-1 text-xs text-slate-300">
            <img v-if="media.kind === 'photo' || media.poster" :src="mediaUrl(media.kind === 'photo' ? media.path : media.poster, { thumbnail: true })" class="aspect-square w-full rounded object-cover" loading="lazy" />
            <span class="block truncate p-1">{{ media.kind || "媒体" }}</span>
          </a>
        </div>
        <section v-if="selected.media?.some(media => media.ocr_text)" class="rounded-lg border border-slate-800 p-3"><h3 class="mb-2 text-sm font-medium">已有 OCR 证据</h3><div v-for="(media,index) in selected.media.filter(item => item.ocr_text)" :key="index" class="border-t border-slate-800 py-2 text-xs text-slate-300"><span class="text-slate-500">媒体 {{ index + 1 }}：</span>{{ media.ocr_text }}</div></section>
        <div class="grid gap-3 md:grid-cols-2">
          <div class="rounded-lg border border-slate-800 p-3"><h3 class="mb-2 text-sm font-medium">候选字段（可人工修正）</h3><textarea v-model="confirmedFieldsJson" :disabled="selected.status !== 'open'" rows="9" spellcheck="false" class="w-full rounded-lg border border-slate-700 bg-slate-950 p-2 font-mono text-xs text-slate-300" /></div>
          <div class="rounded-lg border border-slate-800 p-3"><h3 class="mb-2 text-sm font-medium">规则命中与冲突</h3><pre class="whitespace-pre-wrap break-words text-xs text-slate-300">{{ JSON.stringify(selected.diagnostics_json || {}, null, 2) }}</pre></div>
        </div>
        <section class="rounded-lg border border-slate-800 p-3"><h3 class="mb-2 text-sm font-medium">操作历史</h3><p v-if="!selected.events?.length" class="text-xs text-slate-500">暂无处理记录</p><div v-for="event in selected.events" :key="event.id" class="border-t border-slate-800 py-2 text-xs text-slate-400">{{ event.action }} · 管理员 {{ event.actor_user_id }} · {{ event.created_at }}{{ event.listing_id ? ` · Listing #${event.listing_id}` : "" }}</div></section>
        <div v-if="selected.status === 'open'" class="flex flex-wrap gap-2 border-t border-slate-800 pt-3">
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
