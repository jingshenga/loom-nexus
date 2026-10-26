<script setup>
import AdminTabs from "../components/AdminTabs.vue";
import { onMounted, onUnmounted, ref } from "vue";
import { CheckCircle2, XCircle, Save, Inbox, ArchiveRestore, Trash2 } from "lucide-vue-next";
import { api } from "../api/client";
import MediaGallery from "../components/MediaGallery.vue";

const queue = ref([]);
const current = ref(null);
const form = ref({ nickname: "", district: "", price_amount: null, price_currency: "", description: "" });
const flash = ref("");
const statusTab = ref("pending");
const selectedIds = ref([]);
let timer = null;

function isSelected(id) {
  return selectedIds.value.includes(id);
}

function toggleSelect(id) {
  selectedIds.value = isSelected(id)
    ? selectedIds.value.filter((x) => x !== id)
    : [...selectedIds.value, id];
}

const allSelected = () =>
  queue.value.length > 0 && queue.value.every((x) => selectedIds.value.includes(x.id));

function toggleSelectAll() {
  selectedIds.value = allSelected() ? [] : queue.value.map((x) => x.id);
}

async function batchRemove() {
  if (!selectedIds.value.length) return;
  const r = await api("/api/admin/review/batch-delete", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ids: selectedIds.value }),
  });
  notify(`已删除 ${r.deleted} 条卡片（原始消息保留）`);
  selectedIds.value = [];
  await loadQueue();
}

function select(item) {
  current.value = item;
  form.value = {
    nickname: item.nickname || "",
    district: item.district || "",
    price_amount: item.price_amount,
    price_currency: item.price_currency || "",
    description: item.description || "",
  };
}

async function loadQueue() {
  const data = await api(`/api/admin/review/queue?status=${statusTab.value}`);
  queue.value = data.items;
  if (!data.items.some((x) => x.id === (current.value && current.value.id))) {
    select(data.items[0] || null);
  }
}

function notify(msg) {
  flash.value = msg;
  setTimeout(() => (flash.value = ""), 2500);
}

async function saveEdits() {
  if (!current.value) return;
  const body = { ...form.value };
  if (body.price_amount === "") body.price_amount = null;
  await api(`/api/admin/review/${current.value.id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  notify("字段已保存");
}

async function decide(action, target) {
  const card = target || current.value;
  if (!card) return;
  try {
    await api(`/api/admin/review/${card.id}/${action}`, { method: "POST" });
    notify(
      action === "publish" ? "已发布到广场" :
      action === "withdraw" ? "已撤回下架" : "已驳回",
    );
    await loadQueue();
  } catch (e) {
    notify(`操作失败: ${e.message}`);
  }
}

function onKey(e) {
  if ((e.ctrlKey || e.metaKey) && e.key === "Enter") decide("publish");
}

onMounted(async () => {
  await loadQueue();
  window.addEventListener("keydown", onKey);
  timer = setInterval(loadQueue, 15000);
});
onUnmounted(() => {
  window.removeEventListener("keydown", onKey);
  clearInterval(timer);
});
</script>

<template>
  <div class="space-y-3">
  <AdminTabs />

    <div class="flex items-center gap-3">
      <h2 class="text-lg font-semibold">审核工作台</h2>
      <select v-model="statusTab" @change="selectedIds = []; loadQueue()"
        class="rounded-lg border border-slate-700 bg-slate-900 px-2 py-1 text-sm">
        <option value="pending">待审核</option>
        <option value="published">已发布</option>
        <option value="rejected">已驳回</option>
        <option value="archived">已下架</option>
      </select>
      <label class="flex items-center gap-1.5 text-xs text-slate-400">
        <input type="checkbox" :checked="allSelected()" @change="toggleSelectAll" class="size-4 accent-sky-500" />
        全选
      </label>
      <button v-if="selectedIds.length" @click="batchRemove"
        class="flex items-center gap-1 rounded-lg border border-rose-700/60 px-3 py-1 text-xs text-rose-300 hover:bg-rose-900/30">
        <Trash2 class="size-3.5" />批量删除（{{ selectedIds.length }}）
      </button>
      <span class="text-xs text-slate-500">Ctrl+Enter 快捷发布</span>
      <span v-if="flash" class="ml-auto rounded bg-emerald-600/20 px-3 py-1 text-xs text-emerald-300">{{ flash }}</span>
    </div>

    <div class="grid grid-cols-1 gap-4 lg:h-[calc(100dvh-190px)] lg:grid-cols-12">
      <!-- left: queue -->
      <aside class="lg:col-span-3 lg:overflow-y-auto lg:pr-1 space-y-2 max-h-48 overflow-y-auto lg:max-h-none">
        <button
          v-for="item in queue" :key="item.id" @click="select(item)"
          class="w-full rounded-lg border p-3 text-left text-sm"
          :class="current && current.id === item.id
            ? 'border-sky-500 bg-sky-600/10'
            : 'border-slate-800 bg-slate-900 hover:border-slate-600'"
        >
          <div class="flex items-center justify-between gap-2">
            <span class="flex min-w-0 items-center gap-2">
              <input type="checkbox" :checked="isSelected(item.id)"
                @click.stop="toggleSelect(item.id)" class="size-4 shrink-0 accent-sky-500" />
              <span class="truncate font-medium">{{ item.nickname || `#${item.id}` }}</span>
            </span>
            <span class="shrink-0 text-sky-400">{{ item.price_amount ?? "?" }}</span>
          </div>
          <div class="mt-1 flex items-center gap-2 text-xs text-slate-500">
            <span v-if="item.district" class="rounded bg-slate-800 px-1.5 py-0.5">{{ item.district }}</span>
            <span
              class="rounded px-1.5 py-0.5"
              :class="item.confidence < 1 ? 'bg-amber-600/20 text-amber-400' : 'bg-slate-800'">
              {{ Math.round((item.confidence ?? 1) * 100) }}%
            </span>
          </div>
          <!-- 手机快捷审批：卡片上直接上架/驳回 -->
          <div v-if="statusTab === 'pending'" class="mt-2 flex gap-2 lg:hidden">
            <button @click.stop="decide('publish', item)"
              class="flex flex-1 items-center justify-center gap-1 rounded-lg border border-emerald-600/40 bg-emerald-600/10 py-1.5 text-xs text-emerald-300">
              <CheckCircle2 class="size-3.5" />上架
            </button>
            <button @click.stop="decide('reject', item)"
              class="flex flex-1 items-center justify-center gap-1 rounded-lg border border-rose-600/40 bg-rose-600/10 py-1.5 text-xs text-rose-300">
              <XCircle class="size-3.5" />驳回
            </button>
          </div>
        </button>
        <div v-if="!queue.length" class="flex flex-col items-center gap-2 py-16 text-slate-600">
          <Inbox class="size-8" />队列已清空
        </div>
      </aside>

      <!-- middle: source message -->
      <section v-if="current" class="lg:col-span-5 space-y-3 lg:overflow-y-auto rounded-xl border border-slate-800 bg-slate-900/50 p-4">
        <div class="text-xs text-slate-500">
          来源：{{ current.source_title || "未知" }}
          <span v-if="current.source_posted_at"> · {{ new Date(current.source_posted_at).toLocaleString("zh-CN") }}</span>
        </div>
        <p class="whitespace-pre-wrap rounded-lg bg-slate-950 p-3 text-sm leading-relaxed text-slate-200">
          {{ current.source_text || "（无正文）" }}
        </p>
        <MediaGallery :media="current.media" />
      </section>
      <section v-else class="lg:col-span-5 flex items-center justify-center rounded-xl border border-slate-800 text-slate-600">
        选择左侧任务
      </section>

      <!-- right: fields + actions -->
      <section v-if="current" class="lg:col-span-4 space-y-3 lg:overflow-y-auto rounded-xl border border-slate-800 bg-slate-900/50 p-4">
        <div class="flex flex-wrap gap-1.5">
          <span v-for="(ev, field) in current.evidence" :key="field"
            class="rounded px-2 py-0.5 text-xs"
            :class="ev.src === 'ocr' ? 'bg-amber-600/20 text-amber-300' : 'bg-slate-800 text-slate-400'">
            {{ field }} · {{ ev.src }}
          </span>
        </div>
        <div class="space-y-2 text-sm">
          <label class="block">昵称
            <input v-model="form.nickname" class="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2" />
          </label>
          <label class="block">城区
            <input v-model="form.district" class="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2" />
          </label>
          <div class="flex gap-2">
            <label class="flex-1">价格
              <input v-model.number="form.price_amount" type="number" class="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2" />
            </label>
            <label class="w-24">币种
              <input v-model="form.price_currency" class="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2" />
            </label>
          </div>
          <label class="block">描述
            <textarea v-model="form.description" rows="4" class="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2" />
          </label>
        </div>
        <div class="flex gap-2 pt-1">
          <button @click="saveEdits"
            class="flex flex-1 items-center justify-center gap-1.5 rounded-lg border border-slate-600 py-2 text-sm hover:bg-slate-800">
            <Save class="size-4" />保存修订
          </button>
          <template v-if="statusTab === 'pending'">
            <button @click="decide('reject')"
              class="flex flex-1 items-center justify-center gap-1.5 rounded-lg border border-rose-700/60 py-2 text-sm text-rose-300 hover:bg-rose-900/30">
              <XCircle class="size-4" />驳回
            </button>
            <button @click="decide('publish')"
              class="flex flex-1 items-center justify-center gap-1.5 rounded-lg bg-emerald-600 py-2 text-sm font-medium hover:bg-emerald-500">
              <CheckCircle2 class="size-4" />发布
            </button>
          </template>
          <button v-else-if="statusTab === 'published'" @click="decide('withdraw')"
            class="flex flex-1 items-center justify-center gap-1.5 rounded-lg border border-amber-600/60 py-2 text-sm text-amber-300 hover:bg-amber-900/30">
            <ArchiveRestore class="size-4" />撤回下架
          </button>
        </div>
      </section>
    </div>
  </div>
</template>
