<script setup>
import { computed, onMounted, ref } from "vue";
import { AlertTriangle, Check, ChevronLeft, ChevronRight, Filter, RefreshCw, X } from "lucide-vue-next";
import AdminTabs from "../components/AdminTabs.vue";
import { api, mediaUrl } from "../api/client";

const items = ref([]);
const sources = ref([]);
const rules = ref([]);
const status = ref("pending");
const sourceId = ref("");
const ruleId = ref("");
const after = ref("");
const before = ref("");
const offset = ref(0);
const hasMore = ref(false);
const loading = ref(false);
const error = ref("");
const notice = ref("");
const selected = ref({});
const reasonText = ref({});
const busy = ref({});

const query = computed(() => {
  const params = new URLSearchParams({ status: status.value, limit: "25", offset: String(offset.value) });
  if (sourceId.value) params.set("source_id", sourceId.value);
  if (ruleId.value) params.set("rule_id", ruleId.value);
  if (after.value) params.set("created_after", new Date(after.value).toISOString());
  if (before.value) params.set("created_before", new Date(before.value).toISOString());
  return params.toString();
});

function date(value) {
  if (!value) return "—";
  return new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" });
}

async function load() {
  loading.value = true;
  error.value = "";
  try {
    const [data, sourceData, ruleData] = await Promise.all([
      api(`/api/admin/rules/lifecycle-review?${query.value}`),
      sources.value.length ? Promise.resolve({ items: sources.value }) : api("/api/admin/sources"),
      rules.value.length ? Promise.resolve({ items: rules.value }) : api("/api/admin/rules"),
    ]);
    items.value = data.items || [];
    hasMore.value = Boolean(data.has_more);
    sources.value = sourceData.items || [];
    rules.value = ruleData.items || [];
    for (const event of items.value) {
      const published = (event.candidates || []).find((item) => item.status === "published");
      if (selected.value[event.id] === undefined && published) {
        selected.value[event.id] = published.id;
      }
    }
  } catch (e) {
    error.value = e.message || "加载生命周期审核记录失败";
  } finally {
    loading.value = false;
  }
}

function applyFilters() {
  offset.value = 0;
  selected.value = {};
  load();
}

function setPage(next) {
  offset.value = Math.max(0, next);
  load();
}

async function resolve(event, action) {
  if (busy.value[event.id]) return;
  const listingId = action === "approve" ? Number(selected.value[event.id]) : null;
  if (action === "approve" && !listingId) {
    error.value = "批准归档前，必须选中一张 TG 号完全一致且仍在上架的卡片。";
    return;
  }
  const actionLabel = action === "approve" ? "归档所选卡片" : "拒绝该生命周期事件";
  if (!window.confirm(`确定${actionLabel}？系统只处理当前选中的单张卡片，并记录管理员和时间。`)) return;
  busy.value[event.id] = true;
  error.value = "";
  notice.value = "";
  try {
    await api(`/api/admin/rules/lifecycle-review/${event.id}/resolve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, listing_id: listingId, reason: reasonText.value[event.id] || "" }),
    });
    notice.value = action === "approve" ? `事件 #${event.id} 已批准，所选卡片已归档。` : `事件 #${event.id} 已拒绝。`;
    await load();
  } catch (e) {
    error.value = `${e.message || "处理失败"}。列表已刷新；请确认事件是否已被其他管理员处理。`;
    await load();
  } finally {
    busy.value[event.id] = false;
  }
}

onMounted(load);
</script>

<template>
  <AdminTabs />
  <section class="space-y-4">
    <header class="flex flex-wrap items-center justify-between gap-3">
      <div>
        <h1 class="text-xl font-semibold">生命周期审核</h1>
        <p class="mt-1 text-xs text-slate-400">事件只进入人工审核；未验证发送者授权和转发来源，不会自动归档。</p>
      </div>
      <button class="flex items-center gap-2 rounded-lg border border-slate-700 px-3 py-2 text-sm hover:bg-slate-800" @click="load">
        <RefreshCw class="size-4" />刷新
      </button>
    </header>

    <form class="grid gap-2 rounded-xl border border-slate-800 bg-slate-900/70 p-3 sm:grid-cols-2 lg:grid-cols-6" @submit.prevent="applyFilters">
      <label class="text-xs text-slate-400">状态
        <select v-model="status" class="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-2 py-2 text-sm text-slate-100">
          <option value="pending">待审核</option><option value="approved">已批准</option><option value="rejected">已拒绝</option><option value="all">全部</option>
        </select>
      </label>
      <label class="text-xs text-slate-400">来源
        <select v-model="sourceId" class="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-2 py-2 text-sm text-slate-100">
          <option value="">全部来源</option><option v-for="source in sources" :key="source.id" :value="String(source.id)">{{ source.title }} · {{ source.id }}</option>
        </select>
      </label>
      <label class="text-xs text-slate-400">规则
        <select v-model="ruleId" class="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-2 py-2 text-sm text-slate-100">
          <option value="">全部规则</option><option v-for="rule in rules" :key="rule.id" :value="String(rule.id)">{{ rule.rule_name }}</option>
        </select>
      </label>
      <label class="text-xs text-slate-400">从时间
        <input v-model="after" type="datetime-local" class="mt-1 w-full min-w-0 rounded-lg border border-slate-700 bg-slate-950 px-2 py-2 text-sm text-slate-100" />
      </label>
      <label class="text-xs text-slate-400">到时间
        <input v-model="before" type="datetime-local" class="mt-1 w-full min-w-0 rounded-lg border border-slate-700 bg-slate-950 px-2 py-2 text-sm text-slate-100" />
      </label>
      <button class="mt-auto flex items-center justify-center gap-2 rounded-lg bg-sky-600 px-3 py-2 text-sm font-medium text-white hover:bg-sky-500">
        <Filter class="size-4" />筛选
      </button>
    </form>

    <p v-if="notice" role="status" class="rounded-lg border border-emerald-800 bg-emerald-950/60 px-3 py-2 text-sm text-emerald-200">{{ notice }}</p>
    <p v-if="error" role="alert" class="rounded-lg border border-rose-800 bg-rose-950/60 px-3 py-2 text-sm text-rose-200">{{ error }}</p>
    <div v-if="loading" class="rounded-xl border border-slate-800 p-8 text-center text-sm text-slate-400">正在加载审核事件…</div>
    <div v-else-if="!items.length" class="rounded-xl border border-slate-800 p-8 text-center text-sm text-slate-400">没有符合条件的生命周期事件。</div>

    <article v-for="event in items" :key="event.id" class="space-y-4 rounded-xl border border-slate-800 bg-slate-900/70 p-3 sm:p-5">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div class="min-w-0">
          <h2 class="break-words font-semibold">#{{ event.id }} · {{ event.lifecycle_term }} · {{ event.source_title || `来源 ${event.source_id}` }}</h2>
          <p class="mt-1 break-all text-xs text-slate-400">
            来源 {{ event.source_id }} · 消息 {{ event.platform_msg_id }} · {{ event.rule_name || "规则版本未知" }} v{{ event.rule_version ?? "?" }}
          </p>
          <p class="mt-1 text-xs text-slate-500">原消息 {{ date(event.posted_at) }} · 事件 {{ date(event.created_at) }} · 发送者 peer ID {{ event.sender_peer_id ?? "未知" }}</p>
        </div>
        <span class="shrink-0 rounded-full border px-2.5 py-1 text-xs" :class="event.status === 'pending' ? 'border-amber-700 text-amber-300' : event.status === 'approved' ? 'border-emerald-700 text-emerald-300' : 'border-slate-700 text-slate-400'">
          {{ event.status === "pending" ? "待审核" : event.status === "approved" ? "已批准" : "已拒绝" }}
        </span>
      </div>

      <div class="grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(18rem,0.8fr)]">
        <section class="min-w-0 space-y-2 rounded-lg border border-slate-800 bg-slate-950/70 p-3">
          <h3 class="text-sm font-medium text-slate-200">原始消息证据</h3>
          <pre class="max-h-64 overflow-auto whitespace-pre-wrap break-words text-sm leading-relaxed text-slate-300">{{ event.raw_text || "（无文字正文）" }}</pre>
          <div class="flex flex-wrap gap-2">
            <a v-for="media in (Array.isArray(event.media_json) ? event.media_json : [])" :key="media.path" :href="mediaUrl(media.path)" target="_blank" class="block">
              <img v-if="media.kind === 'photo'" :src="mediaUrl(media.path, { thumbnail: true })" alt="原消息媒体" class="size-20 rounded-md border border-slate-700 object-cover sm:size-24" />
              <span v-else class="rounded border border-slate-700 px-2 py-1 text-xs text-slate-300">{{ media.kind || "媒体" }} · 打开</span>
            </a>
          </div>
          <a v-if="event.source_username" :href="`https://t.me/${event.source_username}/${event.platform_msg_id}`" target="_blank" rel="noreferrer" class="inline-block text-xs text-sky-400 hover:underline">打开 Telegram 原消息</a>
        </section>
        <section class="space-y-2 rounded-lg border border-amber-900/60 bg-amber-950/20 p-3">
          <h3 class="flex items-center gap-2 text-sm font-medium text-amber-200"><AlertTriangle class="size-4" />身份与自动处理阻断</h3>
          <p class="break-all text-sm text-slate-200">目标 TG：<span class="font-mono">{{ event.candidate_username ? `@${event.candidate_username}` : "未能精确识别" }}</span></p>
          <p class="text-xs text-slate-400">触发原因：{{ event.review_reason }} · 身份依据：{{ event.identity_evidence?.identity_basis || "旧记录未保存" }}</p>
          <p class="text-xs text-slate-400">发送者授权：{{ event.identity_evidence?.sender_authority_verified ? "已验证" : "未验证" }} · 转发来源字段：{{ event.identity_evidence?.forward_metadata_available ? "可用" : "原始数据没有该字段" }}</p>
          <p class="text-xs text-slate-400">自动处理阻断：{{ event.identity_evidence?.automatic_action_blocked_reason || event.review_reason }}</p>
        </section>
      </div>

      <section class="space-y-2">
        <h3 class="text-sm font-medium">匹配卡片（仅列出 TG 号完全相同的记录）</h3>
        <p v-if="!event.candidates?.length" class="rounded-lg border border-slate-800 p-3 text-sm text-slate-500">没有精确 TG 匹配卡片。此事件不能批准归档，可选择拒绝。</p>
        <label v-for="candidate in event.candidates" :key="candidate.id" class="flex min-w-0 items-start gap-3 rounded-lg border p-3" :class="candidate.status === 'published' ? 'border-slate-700 bg-slate-950/50' : 'border-slate-800 bg-slate-950/20 opacity-70'">
          <input v-if="candidate.status === 'published' && event.status === 'pending'" v-model="selected[event.id]" type="radio" :name="`listing-${event.id}`" :value="candidate.id" class="mt-1 accent-sky-500" />
          <div class="min-w-0 flex-1">
            <p class="break-words text-sm font-medium">#{{ candidate.id }} · {{ candidate.nickname || "未命名" }} · <span class="font-mono">@{{ candidate.contact_username }}</span></p>
            <p class="text-xs text-slate-400">状态 {{ candidate.status }} · 价格 {{ candidate.price_amount ?? "—" }}</p>
          </div>
          <span v-if="candidate.status !== 'published'" class="shrink-0 text-xs text-slate-500">仅展示，不可归档</span>
        </label>
      </section>

      <div v-if="event.status === 'pending'" class="grid gap-2 border-t border-slate-800 pt-3 sm:grid-cols-[1fr_auto_auto]">
        <input v-model="reasonText[event.id]" maxlength="500" aria-label="审核备注" placeholder="审核备注（选填，会进入审计记录）" class="min-w-0 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100" />
        <button :disabled="busy[event.id] || !selected[event.id]" class="flex items-center justify-center gap-2 rounded-lg bg-rose-700 px-3 py-2 text-sm text-white enabled:hover:bg-rose-600 disabled:cursor-not-allowed disabled:opacity-50" @click="resolve(event, 'approve')">
          <Check class="size-4" />批准归档所选卡片
        </button>
        <button :disabled="busy[event.id]" class="flex items-center justify-center gap-2 rounded-lg border border-slate-700 px-3 py-2 text-sm text-slate-200 hover:bg-slate-800 disabled:opacity-50" @click="resolve(event, 'reject')">
          <X class="size-4" />拒绝
        </button>
      </div>
      <p v-else class="text-xs text-slate-500">处理管理员：{{ event.reviewer_display_name || event.reviewer_username || event.reviewed_by || "未知" }} · {{ date(event.reviewed_at) }} · {{ event.review_reason_text || "无备注" }}</p>
    </article>

    <nav class="flex items-center justify-between" aria-label="审核事件分页">
      <button :disabled="offset === 0 || loading" class="flex items-center gap-1 rounded-lg border border-slate-700 px-3 py-2 text-sm disabled:opacity-40" @click="setPage(offset - 25)"><ChevronLeft class="size-4" />上一页</button>
      <span class="text-xs text-slate-500">第 {{ Math.floor(offset / 25) + 1 }} 页</span>
      <button :disabled="!hasMore || loading" class="flex items-center gap-1 rounded-lg border border-slate-700 px-3 py-2 text-sm disabled:opacity-40" @click="setPage(offset + 25)">下一页<ChevronRight class="size-4" /></button>
    </nav>
  </section>
</template>
