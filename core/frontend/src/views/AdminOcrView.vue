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
const pageSize = 60;
const categories = ref([]);
const versions = ref([]);
const categoryFilter = ref("");
const confidenceRange = ref("");
const confidenceSort = ref("confidence_asc");
const labelClass = ref("info_card");
const bulkClass = ref("info_card");
const activeVersion = computed(() => versions.value.find((item) => item.status === "active") || null);
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

function categoryName(code) {
  return categories.value.find((item) => item.code === code)?.name || ({
    info_card: "资料卡", profile_photo: "人像", advertisement: "广告",
    other: "其他", uncertain: "不确定",
  }[code] || code || "未分类");
}

function predictionCategoryName(item) {
  const version = versions.value.find((entry) => entry.id === item.classifier_version_id);
  const code = item.predicted_classification || item.classification;
  if (version?.policy?.classification_mode === "card_binary" && code === "other") return "非资料卡";
  return categoryName(code);
}

function confidenceText(item) {
  return item.confidence !== null && item.confidence !== undefined && Number.isFinite(Number(item.confidence))
    ? `${Math.round(Number(item.confidence) * 100)}%` : "";
}

function categoryDecision(code) {
  return code === "info_card" ? "confirm" : "not_card";
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
  labelClass.value = item.human_classification || item.predicted_classification || "info_card";
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
  const params = new URLSearchParams({
    status: status.value, limit: String(pageSize), offset: String(offset.value),
    sort: confidenceSort.value,
  });
  if (categoryFilter.value) params.set("category", categoryFilter.value);
  if (confidenceRange.value) {
    const [low, high] = confidenceRange.value.split(":");
    params.set("confidence_min", low);
    params.set("confidence_max", high);
  }
  const data = await api(`/api/admin/ocr/queue?${params}`);
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

function changeFilter() {
  offset.value = 0;
  clearSelection();
  load();
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

function stageNameCandidate() {
  try {
    const fields = JSON.parse(fieldsText.value || "{}");
    return String(fields.stage_name || fields.stage_name_candidate || "").trim();
  } catch {
    return "";
  }
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

async function applyStageName() {
  if (!current.value || busy.value) return;
  const candidate = stageNameCandidate();
  if (!candidate || !current.value.listing_id) return;
  if (!confirm(`将 OCR 昵称候选“${candidate}”应用到关联名片？这会标记为人工确认。`)) return;
  busy.value = true;
  try {
    if (!(await save())) return;
    const result = await api(`/api/admin/ocr/${current.value.id}/apply-stage-name`, { method: "POST" });
    notify(`已将“${result.nickname}”写入卡片 #${result.listing_id}`);
    modalOpen.value = false;
    current.value = null;
    await load();
  } catch (error) {
    notify(`应用昵称失败：${error.message}`);
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
      body: JSON.stringify({ decision, class_code: labelClass.value, fields, normalized_text: normalizedText.value }),
    });
    notify(`已保存并标记为${categoryName(labelClass.value)}`);
    modalOpen.value = false;
    current.value = null;
    await load();
  } catch (error) {
    notify(`操作失败：${error.message}`);
  } finally {
    busy.value = false;
  }
}

async function decideByClass() {
  return decide(categoryDecision(labelClass.value));
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
  decision = categoryDecision(bulkClass.value);
  const label = categoryName(bulkClass.value);
  const scope = allMatching.value ? `当前筛选的 ${selectedCount.value} 张图片` : `选中的 ${selectedCount.value} 张图片`;
  if (!confirm(`将${scope}统一改为“${label}”？这会更新最终审核状态。`)) return;
  busy.value = true;
  try {
    const data = await api("/api/admin/ocr/bulk-decision", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        decision,
        class_code: bulkClass.value,
        ids: allMatching.value ? [] : selectedIds.value,
        all_matching: allMatching.value,
        status: status.value,
        exclude_ids: allMatching.value ? excludedIds.value : [],
        category: allMatching.value ? categoryFilter.value || null : null,
        confidence_min: allMatching.value && confidenceRange.value ? Number(confidenceRange.value.split(":")[0]) : null,
        confidence_max: allMatching.value && confidenceRange.value ? Number(confidenceRange.value.split(":")[1]) : null,
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

onMounted(async () => {
  const [data, versionData] = await Promise.all([
    api("/api/admin/ocr-classifier/categories"),
    api("/api/admin/ocr-classifier/versions"),
  ]);
  categories.value = data.items.filter((item) => item.is_active && item.is_trainable);
  versions.value = versionData.items;
  if (!categories.value.some((item) => item.code === labelClass.value)) labelClass.value = categories.value[0]?.code || "";
  bulkClass.value = labelClass.value;
  await load();
});

async function reclassifySelected() {
  if (!activeVersion.value || !selectedIds.value.length || allMatching.value || busy.value) return;
  busy.value = true;
  try {
    const result = await api(`/api/admin/ocr-classifier/versions/${activeVersion.value.id}/reclassify`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids: selectedIds.value }),
    });
    notify(`已按 V${result.version_id} 重新分类 ${result.updated} 张；人工标签保留`);
    clearSelection();
    await load();
  } catch (error) { notify(`重新分类失败：${error.message}`); }
  finally { busy.value = false; }
}
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
      <select v-model="categoryFilter" @change="changeFilter" class="rounded-lg border border-slate-700 bg-slate-900 px-2 py-1 text-sm">
        <option value="">全部分类</option>
        <option v-for="item in categories" :key="item.code" :value="item.code">{{ item.name }}</option>
      </select>
      <select v-model="confidenceRange" @change="changeFilter" class="rounded-lg border border-slate-700 bg-slate-900 px-2 py-1 text-sm">
        <option value="">全部置信度</option><option value="0:0.5">0–50%</option>
        <option value="0.5:0.8">50–80%</option><option value="0.8:1">80–100%</option>
      </select>
      <select v-model="confidenceSort" @change="changeFilter" class="rounded-lg border border-slate-700 bg-slate-900 px-2 py-1 text-sm">
        <option value="confidence_asc">置信度低→高</option><option value="confidence_desc">置信度高→低</option>
        <option value="newest">最新优先</option><option value="oldest">最早优先</option>
      </select>
      <span class="text-xs text-slate-500">{{ total ? offset + 1 : 0 }}–{{ Math.min(offset + items.length, total) }}/{{ total }}张</span>
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
          <div class="relative aspect-[3/4] overflow-hidden bg-black">
            <img :src="mediaUrl(item.media_path)" class="h-full w-full object-contain" :alt="item.nickname || 'OCR 图片'" loading="lazy" />
            <span class="absolute right-1 top-1 max-w-[75%] truncate rounded bg-black/75 px-1.5 py-1 text-[10px] font-medium text-white">{{ predictionCategoryName(item) }}</span>
            <span v-if="confidenceText(item)" class="absolute bottom-1 right-1 rounded bg-emerald-600/90 px-2 py-1 text-xs font-semibold text-white shadow" :title="item.confidence_source === 'model' ? `模型置信度 · V${item.classifier_version_id}` : '旧规则估值，尚未由训练模型校准'">{{ confidenceText(item) }}</span>
          </div>
          <div class="space-y-1 p-2">
            <div class="truncate text-xs font-medium text-slate-200">{{ item.nickname || item.source_title || `消息 #${item.raw_id || item.id}` }}</div>
            <div class="flex items-center justify-between gap-1 text-[10px] text-slate-400">
              <span class="truncate">{{ item.human_classification ? `人工：${categoryName(item.human_classification)}` : statusLabel(item) }}</span>
              <span v-if="item.confidence_source === 'model'" class="shrink-0">V{{ item.classifier_version_id }}</span>
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
      <button v-if="activeVersion" :disabled="busy || allMatching || !selectedIds.length" @click="reclassifySelected" class="rounded-lg border border-sky-700 px-3 py-2 text-xs text-sky-200 disabled:opacity-40">按 V{{ activeVersion.id }} 重新分类</button>
      <select v-model="bulkClass" class="rounded-lg border border-slate-700 bg-slate-900 px-2 py-2 text-xs text-slate-200">
        <option v-for="item in categories" :key="item.code" :value="item.code">{{ item.name }}</option>
      </select>
      <button :disabled="busy" @click="bulkDecision(categoryDecision(bulkClass))" class="flex items-center gap-1 rounded-lg bg-emerald-600 px-3 py-2 text-xs font-medium text-white disabled:opacity-50">
        <Check class="size-4" />批量标注类别
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
            <label class="block text-xs text-slate-400">人工确认图片类别
              <select v-model="labelClass" class="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-100">
                <option v-for="item in categories" :key="item.code" :value="item.code">{{ item.name }}</option>
              </select>
              <span class="mt-1 block text-[10px] text-slate-500">保存后作为训练标签；会保留模型原预测和版本记录。</span>
            </label>
            <p class="whitespace-pre-wrap rounded-lg bg-slate-900 p-3 text-xs leading-relaxed text-slate-400">来源消息：{{ current.message_text || "（无正文）" }}</p>
            <p v-if="current.listing_id" class="rounded-lg bg-slate-900 p-3 text-xs text-slate-300">关联名片 #{{ current.listing_id }} · 当前昵称：{{ current.nickname || "未识别" }}</p>
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
            <div v-if="current.review_status === 'confirmed' && current.listing_id && stageNameCandidate()" class="rounded-lg border border-emerald-900/70 bg-emerald-950/20 p-3">
              <p class="text-xs text-emerald-200">昵称候选：{{ stageNameCandidate() }}。请核对后单独应用到关联名片。</p>
              <button :disabled="busy" @click="applyStageName" class="mt-2 rounded-lg bg-emerald-700 px-3 py-2 text-xs font-medium text-white disabled:opacity-50">确认并应用昵称</button>
            </div>
            <div class="sticky bottom-0 flex flex-wrap gap-2 border-t border-slate-800 bg-slate-950 py-3">
              <button :disabled="busy" @click="saveOnly" class="rounded-lg border border-slate-700 px-3 py-2 text-xs text-slate-200 disabled:opacity-50">保存修改</button>
              <button :disabled="busy || !labelClass" @click="decideByClass" class="flex items-center gap-1 rounded-lg bg-emerald-600 px-3 py-2 text-xs font-medium text-white disabled:opacity-50"><Check class="size-4" />保存并标记为{{ categoryName(labelClass) }}</button>
              <button v-if="current.status === 'failed'" :disabled="busy" @click="retry" class="flex items-center gap-1 rounded-lg border border-amber-700/60 px-3 py-2 text-xs text-amber-300 disabled:opacity-50"><RotateCw class="size-4" />重试识别</button>
            </div>
          </section>
        </div>
      </div>
    </div>
  </div>
</template>
