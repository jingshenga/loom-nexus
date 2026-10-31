<script setup>
import AdminTabs from "../components/AdminTabs.vue";
import { computed, onMounted, ref } from "vue";
import { api, mediaUrl } from "../api/client";
import { Check, RotateCw, X, ImageIcon, CheckSquare, Square, X as CloseIcon } from "lucide-vue-next";

const items = ref([]);
const current = ref(null);
const modalOpen = ref(false);
const status = ref("pending");
const offset = ref(0);
const total = ref(0);
const pageSize = 40;
const fieldsText = ref("{}");
const normalizedText = ref("");
const flash = ref("");
const busy = ref(false);
const selectedIds = ref([]);
const excludedIds = ref([]);
const allMatching = ref(false);

const selectedCount = computed(() =>
  allMatching.value ? Math.max(0, total.value - excludedIds.value.length) : selectedIds.value.length,
);
const pageFullySelected = computed(() =>
  items.value.length > 0 && items.value.every((item) => isSelected(item.id)),
);

function statusLabel(item) {
  if (item.status === "failed") return "识别失败";
  if (item.review_status === "confirmed") return "已确认";
  if (item.review_status === "not_card") return "非资料卡";
  return "待核对";
}

function notify(message) {
  flash.value = message;
  setTimeout(() => (flash.value = ""), 3000);
}

function clearSelection() {
  selectedIds.value = [];
  excludedIds.value = [];
  allMatching.value = false;
}

function isSelected(id) {
  return allMatching.value
    ? !excludedIds.value.includes(id)
    : selectedIds.value.includes(id);
}

function toggleItem(id) {
  if (allMatching.value) {
    excludedIds.value = excludedIds.value.includes(id)
      ? excludedIds.value.filter((value) => value !== id)
      : [...excludedIds.value, id];
    return;
  }
  selectedIds.value = selectedIds.value.includes(id)
    ? selectedIds.value.filter((value) => value !== id)
    : [...selectedIds.value, id];
}

function togglePage() {
  if (allMatching.value) {
    clearSelection();
    return;
  }
  const pageIds = items.value.map((item) => item.id);
  selectedIds.value = pageFullySelected.value
    ? selectedIds.value.filter((id) => !pageIds.includes(id))
    : [...new Set([...selectedIds.value, ...pageIds])];
}

function toggleAllMatching() {
  allMatching.value = !allMatching.value;
  selectedIds.value = [];
  excludedIds.value = [];
}

function select(item) {
  current.value = item;
  fieldsText.value = JSON.stringify(item.fields || {}, null, 2);
  normalizedText.value = item.normalized_text || "";
  modalOpen.value = true;
}

function closeModal() {
  if (current.value) {
    const fieldsDirty = fieldsText.value !== JSON.stringify(current.value.fields || {}, null, 2);
    const textDirty = normalizedText.value !== (current.value.normalized_text || "");
    if ((fieldsDirty || textDirty) && !confirm("修改尚未保存，确定关闭并放弃吗？")) return;
  }
  modalOpen.value = false;
}

async function load() {
  const data = await api(`/api/admin/ocr/queue?status=${status.value}&limit=${pageSize}&offset=${offset.value}`);
  total.value = data.total;
  if (!data.items.length && offset.value > 0 && total.value > 0) {
    offset.value = Math.floor((total.value - 1) / pageSize) * pageSize;
    return load();
  }
  items.value = data.items;
  if (current.value && !data.items.some((item) => item.id === current.value.id)) {
    current.value = null;
    modalOpen.value = false;
  }
}

function changeStatus() {
  offset.value = 0;
  clearSelection();
  current.value = null;
  modalOpen.value = false;
  load();
}

function changePage(delta) {
  const lastOffset = Math.max(0, Math.floor((total.value - 1) / pageSize) * pageSize);
  offset.value = Math.min(lastOffset, Math.max(0, offset.value + delta * pageSize));
  load();
}

function parseFields() {
  let fields;
  try {
    fields = JSON.parse(fieldsText.value || "{}");
  } catch {
    notify("字段内容不是有效 JSON，请修正后再保存");
    return null;
  }
  if (!fields || Array.isArray(fields) || typeof fields !== "object") {
    notify("字段内容需要是 JSON 对象");
    return null;
  }
  return fields;
}

async function save() {
  if (!current.value) return false;
  const fields = parseFields();
  if (fields === null) return false;
  await api(`/api/admin/ocr/${current.value.id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ fields, normalized_text: normalizedText.value }),
  });
  current.value.fields = fields;
  current.value.normalized_text = normalizedText.value;
  return true;
}

async function saveOnly() {
  if (busy.value) return;
  busy.value = true;
  try {
    if (await save()) notify("修改已保存");
  } catch (error) {
    notify(`保存失败：${error.message}`);
  } finally {
    busy.value = false;
  }
}

async function decide(decision) {
  if (!current.value || busy.value) return;
  const fields = parseFields();
  if (fields === null) return;
  busy.value = true;
  try {
    await api(`/api/admin/ocr/${current.value.id}/decision`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision, fields, normalized_text: normalizedText.value }),
    });
    notify(decision === "confirm" ? "已保存并确认资料卡" : "已保存并标记为非资料卡");
    modalOpen.value = false;
    current.value = null;
    await load();
  } catch (error) {
    notify(`操作失败：${error.message}`);
  } finally {
    busy.value = false;
  }
}

async function retry() {
  if (!current.value || busy.value) return;
  busy.value = true;
  try {
    await api(`/api/admin/ocr/${current.value.id}/decision`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision: "retry" }),
    });
    notify("已重新加入本地识别队列");
    modalOpen.value = false;
    current.value = null;
    await load();
  } catch (error) {
    notify(`操作失败：${error.message}`);
  } finally {
    busy.value = false;
  }
}

async function bulkDecision(decision) {
  if (!selectedCount.value || busy.value) return;
  const label = decision === "confirm" ? "确认资料卡" : "非资料卡";
  const scope = allMatching.value ? `当前筛选的 ${selectedCount.value} 张图片` : `选中的 ${selectedCount.value} 张图片`;
  if (!confirm(`将${scope}统一改为“${label}”？这会更新最终审核状态。`)) return;
  busy.value = true;
  try {
    const data = await api("/api/admin/ocr/bulk-decision", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        decision,
        ids: allMatching.value ? [] : selectedIds.value,
        all_matching: allMatching.value,
        status: status.value,
        exclude_ids: allMatching.value ? excludedIds.value : [],
      }),
    });
    clearSelection();
    modalOpen.value = false;
    current.value = null;
    notify(`已批量更新 ${data.updated} 张图片`);
    await load();
  } catch (error) {
    notify(`批量操作失败：${error.message}`);
  } finally {
    busy.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div class="space-y-3">
    <AdminTabs />
    <div class="flex flex-wrap items-center gap-2">
      <h2 class="mr-2 text-lg font-semibold">OCR 资料核对</h2>
      <select v-model="status" @change="changeStatus" class="rounded-lg border border-slate-700 bg-slate-900 px-2 py-1 text-sm">
        <option value="pending">待核对</option>
        <option value="failed">识别失败</option>
        <option value="confirmed">已确认</option>
        <option value="not_card">非资料卡</option>
        <option value="all">全部</option>
      </select>
      <span class="text-xs text-slate-500">{{ total ? offset + 1 : 0 }}–{{ Math.min(offset + items.length, total) }} / {{ total }} 张</span>
      <button @click="togglePage" :disabled="!items.length || busy" class="flex items-center gap-1 rounded-lg border border-slate-700 px-2.5 py-1 text-xs text-slate-300 disabled:opacity-40">
        <component :is="pageFullySelected ? CheckSquare : Square" class="size-3.5" />
        {{ allMatching ? "取消全部选择" : pageFullySelected ? "取消本页选择" : "本页全选" }}
      </button>
      <button v-if="total" @click="toggleAllMatching" :disabled="busy" class="rounded-lg border border-sky-800 px-2.5 py-1 text-xs text-sky-300 disabled:opacity-40">
        {{ allMatching ? "取消筛选结果全选" : `全选当前筛选 ${total} 张` }}
      </button>
      <div class="ml-auto flex items-center gap-1">
        <button :disabled="offset === 0 || busy" @click="changePage(-1)" class="rounded border border-slate-700 px-2 py-1 text-xs text-slate-300 disabled:opacity-40">上一页</button>
        <button :disabled="offset + pageSize >= total || busy" @click="changePage(1)" class="rounded border border-slate-700 px-2 py-1 text-xs text-slate-300 disabled:opacity-40">下一页</button>
      </div>
      <span v-if="flash" class="w-full rounded bg-emerald-600/20 px-3 py-1 text-xs text-emerald-300">{{ flash }}</span>
    </div>

    <div v-if="items.length" class="grid grid-cols-3 gap-2 sm:grid-cols-4 lg:grid-cols-6 2xl:grid-cols-8">
      <article v-for="item in items" :key="item.id" class="relative min-w-0 overflow-hidden rounded-lg border border-slate-800 bg-slate-900/70 hover:border-slate-600">
        <input type="checkbox" :checked="isSelected(item.id)" :disabled="busy" @change="toggleItem(item.id)"
          class="absolute left-2 top-2 z-10 size-5 cursor-pointer accent-sky-500 disabled:opacity-50" :aria-label="`选择图片 ${item.id}`" />
        <button @click="select(item)" class="block w-full text-left">
          <div class="aspect-[3/4] overflow-hidden bg-black">
            <img :src="mediaUrl(item.media_path)" class="h-full w-full object-contain" :alt="item.nickname || 'OCR 图片'" loading="lazy" />
          </div>
          <div class="space-y-1 p-2">
            <div class="truncate text-xs font-medium text-slate-200">{{ item.nickname || item.source_title || `消息 #${item.raw_id || item.id}` }}</div>
            <div class="flex items-center justify-between gap-1 text-[10px] text-slate-400">
              <span class="truncate">{{ item.classification }}</span>
              <span class="shrink-0">{{ Math.round(item.confidence * 100) }}%</span>
            </div>
            <div class="truncate text-[10px]" :class="item.status === 'failed' ? 'text-rose-300' : item.review_status === 'confirmed' ? 'text-emerald-300' : item.review_status === 'not_card' ? 'text-slate-400' : 'text-amber-300'">
              {{ statusLabel(item) }}<span v-if="item.error"> · {{ item.error }}</span>
            </div>
          </div>
        </button>
      </article>
    </div>
    <div v-else class="rounded-xl border border-slate-800 bg-slate-900/40 py-16 text-center text-sm text-slate-500">当前筛选没有图片</div>

    <div v-if="selectedCount" class="sticky bottom-2 z-20 flex flex-wrap items-center gap-2 rounded-xl border border-sky-800 bg-slate-950/95 p-3 shadow-xl backdrop-blur">
      <span class="mr-auto text-sm text-slate-200">已选 {{ selectedCount }} 张</span>
      <button :disabled="busy" @click="bulkDecision('confirm')" class="flex items-center gap-1 rounded-lg bg-emerald-600 px-3 py-2 text-xs font-medium text-white disabled:opacity-50">
        <Check class="size-4" />批量确认资料卡
      </button>
      <button :disabled="busy" @click="bulkDecision('not_card')" class="flex items-center gap-1 rounded-lg border border-slate-700 px-3 py-2 text-xs text-slate-200 disabled:opacity-50">
        <X class="size-4" />批量标记非资料卡
      </button>
      <button :disabled="busy" @click="clearSelection" class="rounded-lg px-2 py-2 text-xs text-slate-400 hover:text-white">清空选择</button>
    </div>

    <div v-if="modalOpen && current" class="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-2 sm:p-5" @click.self="closeModal">
      <div class="flex max-h-[96dvh] w-full max-w-6xl flex-col overflow-hidden rounded-2xl border border-slate-700 bg-slate-950 shadow-2xl">
        <header class="flex items-center gap-3 border-b border-slate-800 px-4 py-3">
          <ImageIcon class="size-4 shrink-0 text-sky-400" />
          <div class="min-w-0 flex-1">
            <div class="truncate text-sm font-medium text-white">{{ current.nickname || current.source_title || `图片 #${current.id}` }}</div>
            <div class="text-xs text-slate-500">{{ current.source_title || "未知来源" }}<span v-if="current.posted_at"> · {{ new Date(current.posted_at).toLocaleString("zh-CN") }}</span> · {{ statusLabel(current) }}</div>
          </div>
          <button @click="closeModal" class="rounded-lg p-2 text-slate-400 hover:bg-slate-800 hover:text-white" aria-label="关闭"><CloseIcon class="size-4" /></button>
        </header>
        <div class="grid min-h-0 flex-1 grid-cols-1 overflow-y-auto lg:grid-cols-2 lg:overflow-hidden">
          <section class="flex min-h-64 items-center justify-center bg-black p-2 lg:min-h-0 lg:overflow-auto">
            <img :src="mediaUrl(current.media_path)" class="max-h-[55dvh] max-w-full object-contain lg:max-h-full" alt="OCR 图片详情" />
          </section>
          <section class="space-y-3 overflow-y-auto p-4">
            <div v-if="current.error" class="rounded-lg border border-rose-900/70 bg-rose-950/40 p-3 text-xs text-rose-200">识别失败：{{ current.error }}</div>
            <p class="whitespace-pre-wrap rounded-lg bg-slate-900 p-3 text-xs leading-relaxed text-slate-400">来源消息：{{ current.message_text || "（无正文）" }}</p>
            <label class="block text-xs text-slate-400">OCR 原文（只读）
              <textarea :value="current.raw_text" readonly rows="5" class="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 p-3 text-sm leading-relaxed text-slate-200" />
            </label>
            <label class="block text-xs text-slate-400">可搜索内容（可修改）
              <textarea v-model="normalizedText" rows="4" class="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 p-3 text-sm leading-relaxed text-slate-200" />
            </label>
            <label class="block text-xs text-slate-400">结构化字段 JSON（可修改）
              <textarea v-model="fieldsText" rows="8" spellcheck="false" class="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 p-3 font-mono text-xs leading-relaxed text-slate-200" />
            </label>
            <div v-if="Object.keys(current.evidence || {}).length" class="space-y-1">
              <h3 class="text-xs font-medium text-slate-400">字段依据</h3>
              <div v-for="(ev, key) in current.evidence" :key="key" class="rounded bg-slate-900 px-2 py-1.5 text-xs">
                <span class="text-sky-300">{{ key }}</span><span class="ml-2 text-slate-400">{{ ev.line }}</span>
              </div>
            </div>
            <div class="sticky bottom-0 flex flex-wrap gap-2 border-t border-slate-800 bg-slate-950 py-3">
              <button :disabled="busy" @click="saveOnly" class="rounded-lg border border-slate-700 px-3 py-2 text-xs text-slate-200 disabled:opacity-50">保存修改</button>
              <button :disabled="busy" @click="decide('confirm')" class="flex items-center gap-1 rounded-lg bg-emerald-600 px-3 py-2 text-xs font-medium text-white disabled:opacity-50"><Check class="size-4" />保存并确认资料卡</button>
              <button :disabled="busy" @click="decide('not_card')" class="flex items-center gap-1 rounded-lg border border-slate-700 px-3 py-2 text-xs text-slate-200 disabled:opacity-50"><X class="size-4" />保存并标记非资料卡</button>
              <button v-if="current.status === 'failed'" :disabled="busy" @click="retry" class="flex items-center gap-1 rounded-lg border border-amber-700/60 px-3 py-2 text-xs text-amber-300 disabled:opacity-50"><RotateCw class="size-4" />重试识别</button>
            </div>
          </section>
        </div>
      </div>
    </div>
  </div>
</template>
