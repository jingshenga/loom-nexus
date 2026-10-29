<script setup>
import AdminTabs from "../components/AdminTabs.vue";
import { onMounted, ref } from "vue";
import { api, mediaUrl } from "../api/client";
import { Check, RotateCw, X, ImageIcon } from "lucide-vue-next";

const items = ref([]);
const current = ref(null);
const status = ref("pending");
const offset = ref(0);
const total = ref(0);
const pageSize = 40;
const fieldsText = ref("{}");
const normalizedText = ref("");
const flash = ref("");
const busy = ref(false);

function select(item) {
  current.value = item;
  fieldsText.value = JSON.stringify(item.fields || {}, null, 2);
  normalizedText.value = item.normalized_text || "";
}

function notify(message) {
  flash.value = message;
  setTimeout(() => (flash.value = ""), 2600);
}

async function load() {
  const data = await api(`/api/admin/ocr/queue?status=${status.value}&limit=${pageSize}&offset=${offset.value}`);
  total.value = data.total;
  if (!data.items.length && offset.value > 0 && total.value > 0) {
    offset.value = Math.floor((total.value - 1) / pageSize) * pageSize;
    return load();
  }
  items.value = data.items;
  if (!data.items.some((item) => item.id === current.value?.id)) {
    select(data.items[0] || null);
  }
}

function changeStatus() {
  offset.value = 0;
  load();
}

function changePage(delta) {
  const lastOffset = Math.max(0, Math.floor((total.value - 1) / pageSize) * pageSize);
  offset.value = Math.min(lastOffset, Math.max(0, offset.value + delta * pageSize));
  load();
}

async function save() {
  if (!current.value) return null;
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
  await api(`/api/admin/ocr/${current.value.id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ fields, normalized_text: normalizedText.value }),
  });
  return true;
}

async function decide(decision) {
  if (!current.value || busy.value) return;
  busy.value = true;
  try {
    if (decision === "confirm" && !(await save())) return;
    await api(`/api/admin/ocr/${current.value.id}/decision`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision }),
    });
    notify(decision === "confirm" ? "已确认 OCR 资料" : "已标记为非资料卡");
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
    await load();
  } catch (error) {
    notify(`操作失败：${error.message}`);
  } finally {
    busy.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div class="space-y-3">
    <AdminTabs />
    <div class="flex flex-wrap items-center gap-3">
      <h2 class="text-lg font-semibold">OCR 资料核对</h2>
      <select v-model="status" @change="changeStatus" class="rounded-lg border border-slate-700 bg-slate-900 px-2 py-1 text-sm">
        <option value="pending">待核对</option>
        <option value="failed">识别失败</option>
        <option value="confirmed">已确认</option>
        <option value="not_card">非资料卡</option>
      </select>
      <span class="text-xs text-slate-500">{{ total ? offset + 1 : 0 }}–{{ Math.min(offset + items.length, total) }} / {{ total }} 张</span>
      <div class="flex items-center gap-1">
        <button :disabled="offset === 0" @click="changePage(-1)" class="rounded border border-slate-700 px-2 py-1 text-xs text-slate-300 disabled:opacity-40">上一页</button>
        <button :disabled="offset + pageSize >= total" @click="changePage(1)" class="rounded border border-slate-700 px-2 py-1 text-xs text-slate-300 disabled:opacity-40">下一页</button>
      </div>
      <span v-if="flash" class="ml-auto rounded bg-emerald-600/20 px-3 py-1 text-xs text-emerald-300">{{ flash }}</span>
    </div>

    <div class="grid grid-cols-1 gap-4 lg:h-[calc(100dvh-190px)] lg:grid-cols-12">
      <aside class="max-h-48 space-y-2 overflow-y-auto lg:col-span-3 lg:max-h-none lg:overflow-y-auto">
        <button v-for="item in items" :key="item.id" @click="select(item)"
          class="w-full rounded-lg border p-3 text-left"
          :class="current?.id === item.id ? 'border-sky-500 bg-sky-600/10' : 'border-slate-800 bg-slate-900 hover:border-slate-600'">
          <div class="flex items-center gap-2 text-sm font-medium">
            <ImageIcon class="size-4 shrink-0 text-sky-400" />
            <span class="truncate">{{ item.nickname || item.source_title || `消息 #${item.raw_id}` }}</span>
          </div>
          <div class="mt-1 flex flex-wrap gap-2 text-xs text-slate-500">
            <span>{{ item.classification }}</span>
            <span>{{ Math.round(item.confidence * 100) }}%</span>
            <span v-if="item.status === 'failed'" class="text-rose-400">识别失败</span>
          </div>
          <p v-if="item.error" class="mt-1 line-clamp-2 text-xs text-rose-300">{{ item.error }}</p>
        </button>
        <div v-if="!items.length" class="py-12 text-center text-sm text-slate-500">队列为空</div>
      </aside>

      <template v-if="current">
        <section class="space-y-3 rounded-xl border border-slate-800 bg-slate-900/50 p-3 lg:col-span-4 lg:overflow-y-auto">
          <div class="text-xs text-slate-500">
            来源：{{ current.source_title || "未知" }}
            <span v-if="current.posted_at"> · {{ new Date(current.posted_at).toLocaleString("zh-CN") }}</span>
          </div>
          <div class="overflow-hidden rounded-lg border border-slate-800 bg-black">
            <img :src="mediaUrl(current.media_path)" class="max-h-[68vh] w-full object-contain" alt="待核对的资料卡" />
          </div>
          <p class="whitespace-pre-wrap rounded-lg bg-slate-950 p-3 text-xs leading-relaxed text-slate-400">
            来源消息：{{ current.message_text || "（无正文）" }}
          </p>
        </section>

        <section class="space-y-3 rounded-xl border border-slate-800 bg-slate-900/50 p-3 lg:col-span-5 lg:overflow-y-auto">
          <label class="block text-xs text-slate-400">OCR 原文
            <textarea :value="current.raw_text" readonly rows="7" class="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 p-3 text-sm leading-relaxed text-slate-200" />
          </label>
          <label class="block text-xs text-slate-400">清理后的可搜索文本
            <textarea v-model="normalizedText" rows="5" class="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 p-3 text-sm leading-relaxed text-slate-200" />
          </label>
          <label class="block text-xs text-slate-400">结构化候选字段（JSON，可修改）
            <textarea v-model="fieldsText" rows="10" spellcheck="false" class="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 p-3 font-mono text-xs leading-relaxed text-slate-200" />
          </label>
          <div v-if="Object.keys(current.evidence || {}).length" class="space-y-1">
            <h3 class="text-xs font-medium text-slate-400">字段依据</h3>
            <div v-for="(ev, key) in current.evidence" :key="key" class="rounded bg-slate-950 px-2 py-1.5 text-xs">
              <span class="text-sky-300">{{ key }}</span>
              <span class="ml-2 text-slate-400">{{ ev.line }}</span>
            </div>
          </div>
          <div class="flex flex-wrap gap-2 border-t border-slate-800 pt-3">
            <button :disabled="busy" @click="decide('confirm')" class="flex items-center gap-1 rounded-lg bg-emerald-600 px-3 py-2 text-xs font-medium text-white disabled:opacity-50">
              <Check class="size-4" />保存并确认
            </button>
            <button :disabled="busy" @click="decide('not_card')" class="flex items-center gap-1 rounded-lg border border-slate-700 px-3 py-2 text-xs text-slate-300 disabled:opacity-50">
              <X class="size-4" />不是资料卡
            </button>
            <button v-if="current.status === 'failed'" :disabled="busy" @click="retry" class="flex items-center gap-1 rounded-lg border border-amber-700/60 px-3 py-2 text-xs text-amber-300 disabled:opacity-50">
              <RotateCw class="size-4" />重试识别
            </button>
          </div>
        </section>
      </template>
      <section v-else class="lg:col-span-9 flex items-center justify-center rounded-xl border border-slate-800 text-slate-600">
        选择一张图片查看 OCR 结果
      </section>
    </div>
  </div>
</template>
