<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref } from "vue";
import { onBeforeRouteLeave } from "vue-router";
import { ArrowDown, ArrowLeft, ArrowRight, Check, ChevronDown, ImageIcon, Save, X } from "lucide-vue-next";
import { api, mediaUrl } from "../api/client";
import AdminTabs from "../components/AdminTabs.vue";

const endpoint = "/api/admin/moderation/workbench";
const pageSize = 30;
const filters = [
  ["all", "全部"], ["low_confidence", "置信度 < 0.79"],
  ["missing_price", "缺核心资费"], ["has_ocr", "含 OCR 图片"],
];
const profileFields = [
  { key: "age_years", label: "年龄 / 岁", type: "number", min: 0, max: 120 },
  { key: "height_cm", label: "身高 / cm", type: "number", min: 50, max: 250 },
  { key: "weight_kg", label: "体重 / kg", type: "number", min: 0.01, max: 500, step: "any" },
  { key: "bust_size", label: "胸围", type: "text", maxlength: 24 },
];
const textFields = [
  ["price_1p_text", "1P 资费"], ["price_2p_text", "2P 资费"],
  ["service_text", "服务项目"], ["features_text", "特色"],
  ["restriction_text", "禁忌"], ["fixed_intro_terms", "固定规范标签"],
];
const baseFields = [
  { key: "nickname", label: "艺名", maxlength: 128 },
  { key: "district", label: "城区", maxlength: 64 },
  { key: "price_amount", label: "核心资费", type: "number", min: 0, max: 99999999.99, step: "0.01" },
  { key: "price_currency", label: "货币", maxlength: 16 },
  { key: "contact_username", label: "TG 号", maxlength: 128 },
];
const inputStyle = "w-full min-w-0 rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-2 text-sm text-slate-100 focus:border-sky-500 focus:outline-none";
const secondaryButton = "inline-flex min-h-9 items-center justify-center gap-1.5 rounded-lg border border-slate-700 px-2.5 py-1.5 text-sm text-slate-200 hover:bg-slate-800 disabled:opacity-45";

const filter = ref("all");
const offset = ref(0);
const hasMore = ref(false);
const queue = ref([]);
const card = ref(null);
const draft = ref(null);
const baseline = ref(null);
const loading = ref(false);
const working = ref(false);
const error = ref("");
const notice = ref("");
const evidenceOpen = ref(true);
const evidencePane = ref(null);
const fieldPane = ref(null);
const preview = ref(null);
const previewDialog = ref(null);
const form = ref(null);
const busy = computed(() => loading.value || working.value);
const selectedIndex = computed(() => queue.value.findIndex((item) => item.id === card.value?.id));
const canAdvance = computed(() => selectedIndex.value < queue.value.length - 1 || hasMore.value);
const dirty = computed(() => JSON.stringify(draft.value) !== JSON.stringify(baseline.value));

function asForm(nextCard) {
  if (!nextCard) return null;
  return {
    fields: Object.fromEntries(Object.entries(nextCard.fields).map(([key, value]) => [key, value ?? ""])),
    attributes: Object.fromEntries([
      ...profileFields.map(({ key }) => [key, nextCard.attributes[key] ?? ""]),
      ...textFields.map(([key]) => {
        const value = nextCard.attributes[key];
        return [key, Array.isArray(value) ? value.join("\n") : value ?? ""];
      }),
    ]),
    required: Object.fromEntries(nextCard.required_fields.map(({ field_key, value }) => [
      field_key, Array.isArray(value) ? value.join("\n") : value ?? "",
    ])),
  };
}

function errorMessage(err) {
  try {
    const { detail } = JSON.parse(err.message);
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) return "请检查输入格式和数值范围。";
  } catch { /* plain text error */ }
  return err.message || "操作失败，请重试。";
}

function confirmLeave() {
  return !dirty.value || window.confirm("当前卡片有未保存的修改，确定离开？");
}

onBeforeRouteLeave(() => !working.value && confirmLeave());

async function load({ id, page = offset.value, mode = filter.value, reveal = false } = {}) {
  if (loading.value) return;
  loading.value = true;
  error.value = "";
  try {
    let data;
    // Resolving the last item on a page can make that page disappear.
    for (;;) {
      const params = new URLSearchParams({ filter: mode, limit: pageSize, offset: page });
      if (id != null) params.set("listing_id", id);
      data = await api(`${endpoint}/next?${params}`);
      if (data.queue.length || page === 0 || id != null) break;
      page = Math.max(0, page - pageSize);
    }
    filter.value = mode;
    offset.value = page;
    queue.value = data.queue;
    hasMore.value = data.has_more;
    const changedCard = card.value?.id !== data.card?.id;
    card.value = data.card;
    const values = asForm(data.card);
    draft.value = values;
    baseline.value = values ? JSON.parse(JSON.stringify(values)) : null;
    if (changedCard || reveal) evidenceOpen.value = true;
    if (reveal && data.card && !window.matchMedia("(min-width: 1280px)").matches) {
      await nextTick();
      evidencePane.value?.scrollIntoView({ block: "start" });
    }
  } catch (err) {
    error.value = errorMessage(err);
  } finally {
    loading.value = false;
  }
}

function choose(options) {
  if (busy.value || !confirmLeave()) return;
  notice.value = "";
  load(options);
}

function move(delta) {
  const index = selectedIndex.value + delta;
  if (queue.value[index]) {
    choose({ id: queue.value[index].id, reveal: true });
  } else if (delta > 0 && hasMore.value) {
    choose({ page: offset.value + pageSize, reveal: true });
  } else if (delta < 0 && offset.value > 0) {
    choose({ page: offset.value - pageSize, reveal: true });
  }
}

function onKey(event) {
  if (busy.value || previewDialog.value?.open || event.altKey || event.metaKey || event.ctrlKey
      || event.target instanceof Element && event.target.closest("input,textarea,select,[contenteditable]")) return;
  if (event.key === "ArrowUp" || event.key === "ArrowDown") {
    event.preventDefault();
    move(event.key === "ArrowUp" ? -1 : 1);
  } else if (event.code === "Space" && !(event.target instanceof Element && event.target.closest("button,a,summary"))) {
    if (!card.value) return;
    event.preventDefault();
    evidenceOpen.value = !evidenceOpen.value;
    if (evidenceOpen.value) evidencePane.value?.scrollIntoView({ block: "nearest" });
  }
}

function changes(group) {
  return Object.fromEntries(Object.entries(draft.value[group]).filter(([key, value]) => value !== baseline.value[group][key]));
}

async function submit(action) {
  if (busy.value || !card.value) return;
  if ((action !== "reject" || dirty.value) && !form.value.reportValidity()) return;
  if (action === "reject" && !window.confirm(dirty.value
    ? "保存当前修改，驳回卡片并移出待审队列？" : "驳回这张卡片并移出待审队列？")) return;
  working.value = true;
  notice.value = "";
  error.value = "";
  const id = card.value.id;
  const payload = { action, expected_updated_at: card.value.updated_at };
  if (dirty.value || action === "approve") {
    Object.assign(payload, changes("fields"));
    if (payload.price_amount === "") payload.price_amount = null;
    const numeric = new Set(profileFields.filter((field) => field.type === "number").map((field) => field.key));
    const multiline = new Set(textFields.map(([key]) => key));
    const changedAttributes = changes("attributes");
    if (action === "approve") {
      for (const key of card.value.required_attribute_keys || []) {
        changedAttributes[key] = draft.value.attributes[key];
      }
    }
    payload.attributes = Object.fromEntries(Object.entries(changedAttributes).map(([key, value]) => [
      key, multiline.has(key) ? String(value).split(/\r?\n/).map((line) => line.trim()).filter(Boolean)
        : numeric.has(key) && value === "" ? null : value,
    ]));
    payload.required_fields = changes("required");
  }
  try {
    const result = await api(`${endpoint}/${id}/decision`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    notice.value = { approve: "卡片已发布", save: "修改已保存", reject: "卡片已驳回" }[action];
    if (action === "save") {
      card.value.updated_at = result.updated_at;
      baseline.value = JSON.parse(JSON.stringify(draft.value));
      await load({ id });
    } else {
      card.value = null;
      draft.value = baseline.value = null;
      await load();
    }
  } catch (err) {
    error.value = errorMessage(err);
  } finally {
    working.value = false;
  }
}

const messageParts = computed(() => {
  const original = card.value?.original_text || "";
  const fields = card.value?.fields || {};
  const words = [...new Set([
    fields.nickname, fields.district, fields.contact_username, fields.price_amount,
    ...Object.values(card.value?.attributes || {}).flat(),
  ].filter((value) => value != null).map((value) => String(value).trim())
    .filter((value) => value.length >= 2 && value.length <= 120))]
    .sort((a, b) => b.length - a.length);
  if (!words.length) return [{ text: original, highlight: false }];
  const pattern = new RegExp(`(${words.map((word) => word.replace(/[.*+?^$${}()|[\]\\]/g, "\\$&")).join("|")})`, "gi");
  return original.split(pattern).map((text, index) => ({ text, highlight: index % 2 === 1 }));
});

const dateTime = (value) => value ? new Date(value).toLocaleString("zh-CN") : "时间未记录";
const percent = (value) => value == null ? "—" : `${(Number(value) * 100).toFixed(1)}%`;

async function openPreview(asset) {
  if (!asset) return;
  preview.value = asset;
  await nextTick();
  previewDialog.value?.showModal();
}

function mediaForOcr(path) {
  const original = card.value?.media.find((asset) => asset.path === path);
  if (original) return original;
  if (card.value?.media.some((asset) => asset.poster === path)) return { kind: "photo", path };
  return null;
}

onMounted(() => {
  window.addEventListener("keydown", onKey);
  load();
});
onUnmounted(() => window.removeEventListener("keydown", onKey));
</script>

<template>
  <AdminTabs />
  <header class="mb-4 flex flex-wrap items-center justify-between gap-3">
    <h1 class="text-2xl font-semibold text-slate-100">审核工作台</h1>
    <RouterLink to="/admin/review" class="text-sm text-sky-300 hover:underline">批量审核与重复卡片 <ArrowRight class="inline size-3.5" /></RouterLink>
  </header>

  <div v-if="error || notice" class="mb-4 flex flex-wrap items-center gap-3 rounded-lg border px-3 py-2 text-sm"
    :class="error ? 'border-rose-700/60 bg-rose-950/50 text-rose-200' : 'border-emerald-800 bg-emerald-950/40 text-emerald-200'"
    :role="error ? 'alert' : 'status'">
    {{ error || notice }}
    <button v-if="error" type="button" :disabled="busy" class="underline" @click="choose({})">重新加载队列</button>
  </div>

  <div class="grid min-w-0 items-start gap-4 xl:grid-cols-[15rem_minmax(0,1fr)_23rem]">
    <aside class="min-w-0 rounded-xl border border-slate-800 bg-slate-900" aria-label="待审队列" :aria-busy="loading">
      <div class="space-y-3 border-b border-slate-800 p-3">
        <div class="flex items-center justify-between gap-2">
          <h2 class="font-semibold text-slate-100">待审队列</h2>
          <p class="hidden whitespace-nowrap text-xs text-slate-400 xl:block"><kbd>↑</kbd>/<kbd>↓</kbd> 切换 · <kbd>Space</kbd> 展开</p>
        </div>
        <div class="grid grid-cols-2 gap-1.5" aria-label="队列筛选">
          <button v-for="[value, label] in filters" :key="value" type="button" :disabled="busy"
            :aria-pressed="filter === value" class="rounded-lg border px-2 py-1.5 text-xs disabled:opacity-50"
            :class="filter === value ? 'border-sky-500/60 bg-sky-500/15 text-sky-200' : 'border-slate-700 text-slate-300 hover:bg-slate-800'"
            @click="choose({ mode: value, page: 0 })">{{ label }}</button>
        </div>
      </div>

      <div class="max-h-[19rem] overflow-y-auto xl:max-h-[calc(100dvh-19rem)]">
        <p v-if="!queue.length" class="p-4 text-sm text-slate-400">{{ loading ? "加载中…" : "没有符合条件的待审卡片" }}</p>
        <ul v-else class="divide-y divide-slate-800">
          <li v-for="item in queue" :key="item.id">
            <button type="button" :disabled="busy" :aria-current="item.id === card?.id ? 'true' : undefined"
              class="block w-full min-w-0 border-l-2 px-3 py-3 text-left hover:bg-slate-800/75"
              :class="item.id === card?.id ? 'border-sky-400 bg-sky-500/10' : 'border-transparent'"
              @click="choose({ id: item.id, reveal: true })">
              <span class="flex min-w-0 items-center justify-between gap-2">
                <span class="truncate text-sm font-semibold text-slate-100">{{ item.nickname || "未命名卡片" }}</span>
                <span class="shrink-0 text-xs tabular-nums text-slate-400">{{ percent(item.confidence) }}</span>
              </span>
              <span class="mt-1 block truncate text-xs text-slate-300">{{ [item.district, item.source_title].filter(Boolean).join(" · ") || "来源未记录" }}</span>
              <span class="mt-1 block text-xs text-slate-400">{{ dateTime(item.created_at) }}</span>
              <span v-if="item.issues.length" class="mt-2 flex flex-wrap gap-1">
                <span v-for="issue in item.issues" :key="issue" class="rounded bg-amber-400/10 px-1.5 py-0.5 text-xs text-amber-200">{{ issue }}</span>
              </span>
            </button>
          </li>
        </ul>
      </div>
      <div v-if="offset || hasMore" class="flex justify-between gap-2 border-t border-slate-800 p-3">
        <button type="button" :class="secondaryButton" :disabled="busy || offset === 0"
          @click="choose({ page: offset - pageSize })"><ArrowLeft class="size-3.5" />上一页</button>
        <button type="button" :class="secondaryButton" :disabled="busy || !hasMore"
          @click="choose({ page: offset + pageSize })">下一页<ArrowRight class="size-3.5" /></button>
      </div>
    </aside>

    <section ref="evidencePane" class="min-w-0 scroll-mt-20 rounded-xl border border-slate-800 bg-slate-900" aria-label="原始证据">
      <div class="flex items-center justify-between gap-2 border-b border-slate-800 p-3">
        <button type="button" class="flex items-center gap-2 font-semibold text-slate-100" :aria-expanded="evidenceOpen"
          aria-controls="workbench-evidence" @click="evidenceOpen = !evidenceOpen">
          <ChevronDown class="size-4 transition-transform" :class="evidenceOpen ? '' : '-rotate-90'" />
          原证比对
        </button>
        <button v-if="card" type="button" class="text-xs text-sky-300 xl:hidden" @click="fieldPane?.scrollIntoView({ block: 'start' })">
          纠偏与处置 <ArrowDown class="inline size-3.5" />
        </button>
      </div>
      <p v-if="!card" class="p-4 text-sm text-slate-400">{{ loading ? "正在读取证据…" : "选择一张待审卡片查看证据。" }}</p>
      <div v-else-if="evidenceOpen" id="workbench-evidence" class="space-y-5 p-3 xl:max-h-[calc(100dvh-13.5rem)] xl:overflow-y-auto">
        <section>
          <h3 class="mb-1 text-sm font-semibold text-slate-100">
            {{ card.original_text_source === 'listing' ? '卡片介绍（未保留原文）'
              : card.original_text_source === 'profile' ? '资料采集原文' : 'Telegram 原文' }}
          </h3>
          <p class="mb-2 break-words text-xs text-slate-400">{{ [card.source_title, card.original_posted_at ? dateTime(card.original_posted_at) : ""].filter(Boolean).join(" · ") }}</p>
          <p v-if="card.original_text" class="whitespace-pre-wrap rounded-lg bg-slate-950 p-3 text-sm leading-6 text-slate-200 [overflow-wrap:anywhere]">
            <template v-for="(part, index) in messageParts" :key="index"><mark v-if="part.highlight" class="rounded bg-amber-300/20 text-amber-200">{{ part.text }}</mark><template v-else>{{ part.text }}</template></template>
          </p>
          <p v-else class="text-sm text-slate-400">没有保留原文</p>
        </section>
        <section>
          <h3 class="mb-2 text-sm font-semibold text-slate-100">随附媒体</h3>
          <div v-if="card.media.length" class="grid grid-cols-3 gap-2 sm:grid-cols-4">
            <button v-for="(asset, i) in card.media" :key="asset.path || asset.poster" type="button"
              :aria-label="`放大${asset.kind === 'video' ? '视频' : '图片'} ${i + 1}`"
              class="flex aspect-square min-w-0 items-center justify-center overflow-hidden rounded-lg border border-slate-700 bg-slate-950 hover:border-sky-400"
              @click="openPreview(asset)">
              <img v-if="asset.kind === 'photo' || asset.poster" :src="mediaUrl(asset.kind === 'photo' ? asset.path : asset.poster, { thumbnail: true })"
                alt="" loading="lazy" class="size-full object-cover" />
              <ImageIcon v-else class="size-6 text-slate-400" />
            </button>
          </div>
          <p v-else class="text-sm text-slate-400">没有随附媒体</p>
        </section>
        <section>
          <h3 class="mb-2 text-sm font-semibold text-slate-100">OCR 识别结果</h3>
          <p v-if="!card.ocr.length" class="text-sm text-slate-400">没有 OCR 记录</p>
          <div v-else class="space-y-2">
            <details v-for="(asset, i) in card.ocr" :key="asset.id" :open="i === 0" class="rounded-lg border border-slate-800 bg-slate-950 p-2.5">
              <summary class="cursor-pointer text-sm text-slate-200">
                图片 {{ i + 1 }} · 图像置信度 {{ percent(asset.confidence) }}
                <span v-if="asset.review_status === 'pending'" class="text-amber-200"> · 待人工复核</span>
              </summary>
              <button v-if="mediaForOcr(asset.media_path)" type="button" class="mt-1 text-xs text-sky-300 hover:underline"
                @click="openPreview(mediaForOcr(asset.media_path))">查看对应原图</button>
              <p class="mt-1 text-xs text-slate-400 [overflow-wrap:anywhere]">{{ asset.media_path }}</p>
              <ul v-if="asset.lines.length" class="mt-2 divide-y divide-slate-800">
                <li v-for="line in asset.lines" :key="line.text" class="flex items-start justify-between gap-2 py-1.5 text-sm text-slate-200">
                  <span class="min-w-0 [overflow-wrap:anywhere]">{{ line.text }}</span><span class="shrink-0 tabular-nums text-slate-400">{{ percent(line.score) }}</span>
                </li>
              </ul>
              <p v-else class="mt-2 text-sm text-slate-400">尚无识别文本（{{ asset.status }}）</p>
            </details>
          </div>
        </section>
      </div>
    </section>

    <section ref="fieldPane" class="min-w-0 scroll-mt-20 rounded-xl border border-slate-800 bg-slate-900" aria-label="结构化字段与审核操作">
      <h2 class="border-b border-slate-800 p-3 font-semibold text-slate-100">纠偏与处置 <span v-if="card" class="ml-2 text-xs font-normal text-slate-400">#{{ card.id }}</span></h2>
      <p v-if="!draft" class="p-4 text-sm text-slate-400">待审卡片的可编辑字段会显示在这里。</p>
      <form v-else ref="form" @submit.prevent="submit('save')">
        <fieldset :disabled="busy" class="min-w-0 space-y-4 p-3 xl:max-h-[calc(100dvh-20rem)] xl:overflow-y-auto">
          <p v-if="card.missing_publish_fields.length" class="rounded-lg bg-amber-400/10 p-2 text-xs text-amber-200">上架前需补齐 {{ card.missing_publish_fields.join("、") }}</p>
          <div class="grid grid-cols-2 gap-2">
            <label v-for="field in baseFields" :key="field.key" class="min-w-0 space-y-1 text-xs text-slate-300"
              :class="field.key === 'contact_username' ? 'col-span-2' : ''">
              <span>{{ field.label }}</span>
              <input v-model="draft.fields[field.key]" :type="field.type || 'text'" :min="field.min" :max="field.max"
                :step="field.step" :maxlength="field.maxlength" :class="inputStyle" />
            </label>
            <label class="col-span-2 min-w-0 space-y-1 text-xs text-slate-300">
              <span>介绍</span><textarea v-model="draft.fields.description" rows="3" maxlength="30000" :class="inputStyle" />
            </label>
            <label v-for="field in card.required_fields" :key="field.field_key" class="col-span-2 min-w-0 space-y-1 text-xs text-slate-300">
              <span>{{ field.label }}</span><input v-model="draft.required[field.field_key]" :class="inputStyle" />
            </label>
          </div>
          <div class="grid grid-cols-2 gap-2 border-t border-slate-800 pt-3">
            <label v-for="field in profileFields" :key="field.key" class="min-w-0 space-y-1 text-xs text-slate-300">
              <span>{{ field.label }}</span>
              <input v-model="draft.attributes[field.key]" :type="field.type" :min="field.min" :max="field.max"
                :step="field.step" :maxlength="field.maxlength" :class="inputStyle" />
            </label>
            <label v-for="[key, label] in textFields" :key="key" class="min-w-0 space-y-1 text-xs text-slate-300">
              <span>{{ label }}</span><textarea v-model="draft.attributes[key]" rows="2" :class="inputStyle" placeholder="每行一项" />
            </label>
          </div>
        </fieldset>
        <div class="grid grid-cols-2 gap-2 border-t border-slate-800 p-3">
          <button type="button" :disabled="busy" class="nx-grad inline-flex min-h-9 items-center justify-center gap-1.5 rounded-lg px-2 text-sm font-semibold text-white disabled:opacity-45"
            @click="submit('approve')"><Check class="size-4 shrink-0" />通过并发布</button>
          <button type="submit" :disabled="busy || !dirty" :class="secondaryButton"><Save class="size-4 shrink-0" />修正并保存</button>
          <button type="button" :disabled="busy" :class="secondaryButton" class="!border-rose-700/60 !text-rose-200" @click="submit('reject')">
            <X class="size-4 shrink-0" />驳回并归档
          </button>
          <button type="button" :disabled="busy || !canAdvance" :class="secondaryButton" @click="move(1)">下一条 <ArrowRight class="size-4 shrink-0" /></button>
        </div>
      </form>
    </section>
  </div>

  <dialog ref="previewDialog" class="m-auto max-h-[95dvh] w-[min(94vw,60rem)] rounded-xl border border-slate-700 bg-slate-950 p-3 text-white backdrop:bg-black/80"
    aria-label="媒体预览" @close="preview = null">
    <div class="mb-2 flex justify-end">
      <button type="button" :class="secondaryButton" @click="previewDialog.close()"><X class="size-4" />关闭预览</button>
    </div>
    <img v-if="preview?.kind === 'photo'" :src="mediaUrl(preview.path)" alt="原图预览" class="mx-auto max-h-[80dvh] max-w-full object-contain" />
    <video v-else-if="preview" :src="mediaUrl(preview.path)" :poster="preview.poster ? mediaUrl(preview.poster) : undefined"
      class="mx-auto max-h-[80dvh] max-w-full" controls playsinline />
  </dialog>
</template>
