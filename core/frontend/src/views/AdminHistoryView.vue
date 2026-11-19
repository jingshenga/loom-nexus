<script setup>
import AdminMediaGrid from "../components/AdminMediaGrid.vue";
import { computed, onMounted, ref } from "vue";
import { api, mediaUrl } from "../api/client";

const sources = ref([]);
const items = ref([]);
const selected = ref(null);
const sourceId = ref("");
const searchText = ref("");
const appliedSearch = ref("");
const total = ref(0);
const offset = ref(0);
const pageSize = 50;
const loading = ref(false);
const error = ref("");

const activeSource = computed(() => sources.value.find((item) => String(item.source_id) === sourceId.value));

async function loadSources() {
  const data = await api("/api/admin/review/history/sources");
  sources.value = data.items || [];
}

async function load(reset = false) {
  if (reset) offset.value = 0;
  loading.value = true;
  error.value = "";
  try {
    const params = new URLSearchParams({ limit: String(pageSize), offset: String(offset.value) });
    if (sourceId.value) params.set("source_id", sourceId.value);
    if (appliedSearch.value) params.set("q", appliedSearch.value);
    const data = await api(`/api/admin/review/history?${params}`);
    items.value = data.items || [];
    total.value = data.total || 0;
    if (selected.value) {
      selected.value = items.value.find((item) => item.id === selected.value.id) || null;
    }
  } catch (err) {
    error.value = err.message || "历史资料加载失败";
  } finally {
    loading.value = false;
  }
}

function search() {
  appliedSearch.value = searchText.value.trim();
  selected.value = null;
  load(true);
}

function sourceChanged() {
  selected.value = null;
  load(true);
}

function telegramUrl(item) {
  const username = (item.source_username || "").replace(/^@/, "");
  return username && item.platform_msg_id
    ? `https://t.me/${username}/${item.platform_msg_id}` : "";
}

function mediaCount(item) {
  return (item.media || []).length;
}

onMounted(async () => {
  await loadSources();
  await load();
});
</script>

<template>
  <div class="space-y-4">
      <div class="flex flex-wrap items-center gap-3">
      <div class="mr-auto">
        <h1 class="text-xl font-semibold">历史签到资料库</h1>
        <p class="mt-1 text-xs text-slate-400">浏览已保存的天一和小木屋历史消息，不会触发 Telegram 采集。</p>
      </div>
      <RouterLink to="/admin/review" class="rounded-lg border border-slate-700 px-3 py-2 text-sm text-slate-300 hover:bg-slate-800">返回审核</RouterLink>
    </div>

    <div class="flex flex-wrap gap-2 rounded-xl border border-slate-800 bg-slate-900/60 p-3">
      <select v-model="sourceId" @change="sourceChanged" class="min-w-48 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm">
        <option value="">全部来源</option>
        <option v-for="source in sources" :key="source.source_id" :value="String(source.source_id)">{{ source.title }}{{ source.username ? ` · @${source.username}` : "" }}</option>
      </select>
      <input v-model="searchText" @keyup.enter="search" placeholder="搜索 TG 号、昵称或消息文字" class="min-w-48 flex-1 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm" />
      <button @click="search" class="rounded-lg bg-sky-600 px-4 py-2 text-sm font-medium text-white hover:bg-sky-500">搜索</button>
      <button v-if="appliedSearch" @click="searchText = ''; search()" class="rounded-lg px-3 py-2 text-sm text-slate-400 hover:text-white">清除</button>
    </div>

    <div v-if="activeSource" class="text-xs text-slate-400">
      扫描状态：{{ activeSource.status }} · 已扫描 {{ activeSource.scanned_count }} 条 · 已保存 {{ activeSource.stored_count }} 条 · 当前结果 {{ total }} 条
    </div>
    <p v-if="error" class="rounded-lg border border-rose-800 bg-rose-950/30 p-3 text-sm text-rose-300">{{ error }}</p>

    <div class="flex items-center justify-between text-xs text-slate-400">
      <span>当前 {{ total ? offset + 1 : 0 }}–{{ Math.min(offset + items.length, total) }} / {{ total }} 条</span>
      <div class="flex gap-2">
        <button @click="offset = Math.max(0, offset - pageSize); load()" :disabled="offset === 0 || loading" class="rounded border border-slate-700 px-3 py-1.5 disabled:opacity-40">上一页</button>
        <button @click="offset += pageSize; load()" :disabled="offset + pageSize >= total || loading" class="rounded border border-slate-700 px-3 py-1.5 disabled:opacity-40">下一页</button>
      </div>
    </div>

    <div class="grid grid-cols-3 gap-2 sm:grid-cols-4 lg:grid-cols-6 2xl:grid-cols-8">
      <button v-for="item in items" :key="item.id" @click="selected = item"
        class="min-w-0 overflow-hidden rounded-lg border text-left"
        :class="selected?.id === item.id ? 'border-sky-500 bg-sky-950/30' : 'border-slate-800 bg-slate-900/70 hover:border-slate-600'">
        <div class="relative aspect-[3/4] overflow-hidden bg-black">
          <img v-if="item.media?.length && (item.media.find((m) => m.kind === 'photo')?.path || item.media[0].poster)"
            :src="mediaUrl(item.media.find((m) => m.kind === 'photo')?.path || item.media[0].poster)" class="size-full object-contain" loading="lazy" />
          <span v-else class="flex size-full items-center justify-center text-xs text-slate-500">无图片</span>
          <span class="absolute right-2 top-2 rounded bg-black/70 px-2 py-1 text-[10px] text-slate-200">{{ mediaCount(item) }} 媒体</span>
        </div>
        <div class="space-y-1 p-2">
          <div class="truncate text-xs font-medium">{{ item.nickname || item.contact_username ? (item.nickname || `@${item.contact_username}`) : `消息 #${item.platform_msg_id}` }}</div>
          <div class="mt-1 truncate text-xs text-sky-300">{{ item.source_title }}{{ item.contact_username ? ` · @${item.contact_username}` : "" }}</div>
          <div class="line-clamp-2 text-[10px] text-slate-500">{{ item.posted_at ? new Date(item.posted_at).toLocaleString('zh-CN') : '时间未知' }} · {{ item.listing_id ? `卡片 #${item.listing_id} · ${item.listing_status}` : `尚未生成卡片 · ${item.extract_state}` }}</div>
          <p class="line-clamp-2 whitespace-pre-wrap text-[10px] leading-relaxed text-slate-400">{{ item.raw_text || "（无文字）" }}</p>
        </div>
      </button>
      <div v-if="!items.length && !loading" class="col-span-full rounded-xl border border-dashed border-slate-700 py-16 text-center text-sm text-slate-500">没有找到符合条件的已保存消息</div>
      <div v-if="loading" class="col-span-full py-12 text-center text-sm text-slate-500">正在读取资料…</div>
    </div>

    <div v-if="selected" class="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto bg-black/80 p-2 sm:p-5" @click.self="selected = null">
    <section class="my-auto flex min-h-0 max-h-[96dvh] w-full max-w-5xl flex-col gap-3 touch-pan-y overflow-y-auto overscroll-contain rounded-xl border border-slate-700 bg-slate-950 p-4 shadow-2xl [-webkit-overflow-scrolling:touch]">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 class="font-semibold">{{ selected.nickname || (selected.contact_username ? `@${selected.contact_username}` : `消息 #${selected.platform_msg_id}`) }}</h2>
          <p class="mt-1 text-xs text-slate-400">{{ selected.source_title }} · {{ selected.posted_at ? new Date(selected.posted_at).toLocaleString('zh-CN') : '时间未知' }} · 消息 ID {{ selected.platform_msg_id }}</p>
          <p v-if="selected.contact_username" class="mt-1 text-xs text-slate-300">TG：@{{ selected.contact_username }}</p>
        </div>
        <div class="flex items-center gap-2"><a v-if="telegramUrl(selected)" :href="telegramUrl(selected)" target="_blank" rel="noopener noreferrer" class="rounded-lg border border-sky-700 px-3 py-2 text-xs text-sky-300 hover:bg-sky-950/40">打开 Telegram 原消息 ↗</a><button @click="selected = null" class="rounded-lg border border-slate-700 px-3 py-2 text-xs text-slate-400">关闭</button></div>
      </div>
      <div class="text-xs text-slate-400">{{ selected.listing_id ? `已关联卡片 #${selected.listing_id}（${selected.listing_status}）` : `尚未生成卡片 · 提取状态：${selected.extract_state}` }}</div>
      <AdminMediaGrid :media="selected.media" />
      <details open class="rounded-lg border border-slate-800 bg-slate-950/60 p-3">
        <summary class="cursor-pointer text-sm text-slate-300">原始消息内容</summary>
        <p class="mt-3 whitespace-pre-wrap break-words text-sm leading-relaxed text-slate-300">{{ selected.raw_text || "（无文字）" }}</p>
      </details>
    </section>
    </div>
  </div>
</template>
