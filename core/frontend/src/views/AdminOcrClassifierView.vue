<script setup>
import { computed, onMounted, ref } from "vue";
import { RouterLink } from "vue-router";
import AdminTabs from "../components/AdminTabs.vue";
import { api } from "../api/client";

const categories = ref([]);
const versions = ref([]);
const selectedId = ref(null);
const busy = ref(false);
const message = ref("");
const draft = ref({ code: "", name: "", review_required: true });
const policy = ref({
  confidence_threshold: 0.65,
  review_classes: ["info_card", "uncertain"],
  min_samples_per_class: 5,
  min_macro_f1: 0.7,
});
const shadow = ref(null);
const shadowSummary = ref(null);
const selected = computed(() => versions.value.find((item) => item.id === selectedId.value) || null);
const active = computed(() => versions.value.find((item) => item.status === "active") || null);
const canPrepare = computed(() => {
  if (!selected.value || selected.value.status !== "tested") return false;
  const metrics = selected.value.metrics || {};
  return Number(metrics.macro_f1 || 0) >= Number(selected.value.policy?.min_macro_f1 || 0.7)
    && Object.values(metrics.class_counts || {}).every((count) => count >= Number(selected.value.policy?.min_samples_per_class || 5))
    && Object.values(metrics.excluded_categories || {}).every((count) => count >= Number(selected.value.policy?.min_samples_per_class || 5))
    && Number(selected.value.shadow_count || 0) > 0;
});

function notify(text) {
  message.value = text;
  setTimeout(() => { if (message.value === text) message.value = ""; }, 5000);
}

async function load() {
  const [categoryData, versionData] = await Promise.all([
    api("/api/admin/ocr-classifier/categories"),
    api("/api/admin/ocr-classifier/versions"),
  ]);
  categories.value = categoryData.items;
  versions.value = versionData.items;
  if (!selectedId.value) selectedId.value = versions.value.find((item) => item.status === "active")?.id || versions.value[0]?.id || null;
  if (versions.value.find((item) => item.id === selectedId.value)?.shadow_count) {
    shadowSummary.value = await api(`/api/admin/ocr-classifier/versions/${selectedId.value}/shadow`);
  } else {
    shadowSummary.value = null;
  }
}

async function addCategory() {
  if (!draft.value.code.trim() || !draft.value.name.trim() || busy.value) return;
  busy.value = true;
  try {
    await api("/api/admin/ocr-classifier/categories", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(draft.value),
    });
    if (draft.value.review_required) policy.value.review_classes = [...new Set([...policy.value.review_classes, draft.value.code.trim().toLowerCase().replaceAll("-", "_")])];
    draft.value = { code: "", name: "", review_required: true };
    await load();
    notify("已增加类别；样本达到要求后会参与训练");
  } catch (error) {
    notify(`增加类别失败：${error.message}`);
  } finally { busy.value = false; }
}

async function updateCategory(category, patch) {
  try {
    await api(`/api/admin/ocr-classifier/categories/${category.code}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    });
    if (patch.review_required !== undefined) {
      policy.value.review_classes = patch.review_required
        ? [...new Set([...policy.value.review_classes, category.code])]
        : policy.value.review_classes.filter((code) => code !== category.code);
    }
    await load();
  } catch (error) { notify(`保存类别失败：${error.message}`); }
}

async function train() {
  if (busy.value) return;
  busy.value = true;
  try {
    const version = await api("/api/admin/ocr-classifier/train", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(policy.value),
    });
    await load();
    selectedId.value = version.id;
    shadow.value = null;
    shadowSummary.value = null;
    notify(`测试版本 V${version.id} 已生成；尚未影响线上分类`);
  } catch (error) { notify(`训练失败：${error.message}`); }
  finally { busy.value = false; }
}

async function prepare() {
  if (!selected.value || busy.value) return;
  busy.value = true;
  try {
    await api(`/api/admin/ocr-classifier/versions/${selected.value.id}/prepare`, { method: "POST" });
    await load();
    notify(`V${selected.value?.id || selectedId.value} 已准备上线；线上版本未改变`);
  } catch (error) { notify(`暂不能准备上线：${error.message}`); }
  finally { busy.value = false; }
}

async function publish() {
  if (!selected.value || busy.value) return;
  const id = selected.value.id;
  if (!window.confirm(`发布 V${id} 为当前图片分类规则？之后新识别图片会使用此版本。`)) return;
  busy.value = true;
  try {
    await api(`/api/admin/ocr-classifier/versions/${id}/publish`, { method: "POST" });
    await load();
    selectedId.value = id;
    notify(`V${id} 已上线`);
  } catch (error) { notify(`发布失败：${error.message}`); }
  finally { busy.value = false; }
}

async function rollback(version) {
  if (busy.value || !window.confirm(`切回 V${version.id}？`)) return;
  busy.value = true;
  try {
    await api(`/api/admin/ocr-classifier/versions/${version.id}/publish`, { method: "POST" });
    await load();
    selectedId.value = version.id;
    notify(`已切回 V${version.id}`);
  } catch (error) { notify(`回退失败：${error.message}`); }
  finally { busy.value = false; }
}

async function runShadow() {
  if (!selected.value || busy.value) return;
  busy.value = true;
  try {
    const result = await api(`/api/admin/ocr-classifier/versions/${selected.value.id}/shadow?limit=100`, { method: "POST" });
    shadow.value = result;
    await load();
    shadowSummary.value = await api(`/api/admin/ocr-classifier/versions/${selected.value.id}/shadow`);
    notify(`影子测试完成：${result.evaluated} 张；线上规则未改变`);
  } catch (error) { notify(`影子测试失败：${error.message}`); }
  finally { busy.value = false; }
}

onMounted(load);
</script>

<template>
  <div class="space-y-4">
    <AdminTabs />
    <div class="flex flex-wrap items-center gap-3">
      <h1 class="text-lg font-semibold">图片分类训练与规则</h1>
      <RouterLink to="/admin/ocr" class="text-xs text-sky-300 hover:text-sky-200">返回 OCR 核对 ↗</RouterLink>
      <span class="ml-auto text-xs text-slate-400">当前线上：{{ active ? `V${active.id}` : "尚未发布训练模型，使用旧规则" }}</span>
    </div>

    <div class="grid gap-4 xl:grid-cols-2">
      <section class="space-y-3 rounded-xl border border-slate-800 bg-slate-900/60 p-4">
        <div>
          <h2 class="font-medium">类别</h2>
          <p class="mt-1 text-xs text-slate-500">旧版“已确认/非资料卡”映射为“资料卡/其他”；可在 OCR 页改成更细类别。停用会保留标签和模型记录。</p>
        </div>
        <div class="max-h-64 space-y-2 overflow-y-auto">
          <div v-for="category in categories" :key="category.code" class="flex flex-wrap items-center gap-2 rounded-lg border border-slate-800 bg-slate-950/60 px-3 py-2">
            <span class="min-w-20 text-sm">{{ category.name }}</span>
            <span class="text-[10px] text-slate-500">{{ category.code }} · {{ category.labeled_count }} 张（人工 {{ category.manual_label_count }} / 旧审核 {{ category.legacy_label_count }}）</span>
            <label class="ml-auto flex items-center gap-1 text-[10px] text-slate-400">
              <input type="checkbox" :checked="category.review_required" @change="updateCategory(category, { review_required: $event.target.checked })" />低置信度进入核对
            </label>
            <button v-if="!category.is_builtin" @click="updateCategory(category, { is_active: !category.is_active })" class="rounded border border-slate-700 px-2 py-1 text-[10px]" :class="category.is_active ? 'text-amber-300' : 'text-emerald-300'">
              {{ category.is_active ? "停用" : "启用" }}
            </button>
            <span v-else class="text-[10px] text-slate-500">内置</span>
          </div>
        </div>
        <form class="grid gap-2 sm:grid-cols-[1fr_1fr_auto]" @submit.prevent="addCategory">
          <input v-model="draft.code" placeholder="代码，如 sale_ad" class="min-w-0 rounded border border-slate-700 bg-slate-950 px-2 py-2 text-xs" />
          <input v-model="draft.name" placeholder="显示名称，如促销广告" class="min-w-0 rounded border border-slate-700 bg-slate-950 px-2 py-2 text-xs" />
          <button :disabled="busy" class="rounded bg-sky-700 px-3 py-2 text-xs text-white disabled:opacity-40">增加类别</button>
        </form>
      </section>

      <section class="space-y-3 rounded-xl border border-slate-800 bg-slate-900/60 p-4">
        <div>
          <h2 class="font-medium">训练与测试设置</h2>
          <p class="mt-1 text-xs text-slate-500">使用 OCR 文字特征及人像、文字密度等图片信号，在本机训练；不提取资料字段，不上传第三方。</p>
        </div>
        <div class="grid gap-3 sm:grid-cols-2">
          <label class="text-xs text-slate-400">置信度门槛（%）
            <input :value="Math.round(policy.confidence_threshold * 100)" type="number" min="0" max="100" step="1" class="mt-1 w-full rounded border border-slate-700 bg-slate-950 px-2 py-2" @input="policy.confidence_threshold = Math.max(0, Math.min(1, Number($event.target.value) / 100))" />
          </label>
          <label class="text-xs text-slate-400">每类上线最低标注数
            <input v-model.number="policy.min_samples_per_class" type="number" min="2" max="1000" class="mt-1 w-full rounded border border-slate-700 bg-slate-950 px-2 py-2" />
          </label>
          <label class="text-xs text-slate-400">最低宏平均 F1（%）
            <input :value="Math.round(policy.min_macro_f1 * 100)" type="number" min="0" max="100" class="mt-1 w-full rounded border border-slate-700 bg-slate-950 px-2 py-2" @input="policy.min_macro_f1 = Number($event.target.value) / 100" />
          </label>
        </div>
        <p class="text-xs text-slate-400">人工核对类别由左侧“低置信度进入核对”控制；低于门槛的预测也会进入核对。</p>
        <div class="flex flex-wrap gap-2 border-t border-slate-800 pt-3">
          <button :disabled="busy" @click="train" class="rounded-lg bg-sky-700 px-3 py-2 text-xs font-medium text-white disabled:opacity-40">{{ busy ? "处理中…" : "开始训练并验证" }}</button>
          <button :disabled="busy || !selected || !['tested','ready'].includes(selected.status)" @click="runShadow" class="rounded-lg border border-slate-700 px-3 py-2 text-xs text-slate-200 disabled:opacity-40">影子测试 100 张</button>
          <button :disabled="busy || !canPrepare" @click="prepare" class="rounded-lg border border-amber-700 px-3 py-2 text-xs text-amber-200 disabled:opacity-40">准备上线</button>
          <button :disabled="busy || selected?.status !== 'ready'" @click="publish" class="rounded-lg bg-emerald-700 px-3 py-2 text-xs font-medium text-white disabled:opacity-40">发布到线上</button>
        </div>
        <p class="text-[10px] leading-relaxed text-slate-500">训练只生成测试版本。测试集样本数或宏平均 F1 未达到上方门槛时，“准备上线”不可用；发布需要管理员再次确认。新版本不会自动覆盖人工标签。</p>
      </section>
    </div>

    <section class="space-y-3 rounded-xl border border-slate-800 bg-slate-900/60 p-4">
      <div class="flex flex-wrap items-center gap-2">
        <h2 class="mr-auto font-medium">版本记录</h2>
        <select v-model.number="selectedId" class="rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs">
          <option v-for="version in versions" :key="version.id" :value="version.id">V{{ version.id }} · {{ version.status }}</option>
        </select>
        <button v-if="selected && ['ready','retired'].includes(selected.status)" @click="rollback(selected)" :disabled="busy" class="rounded border border-slate-700 px-2.5 py-1.5 text-xs text-amber-300 disabled:opacity-40">切回此版本</button>
      </div>
      <div v-if="selected" class="grid gap-4 lg:grid-cols-3">
        <div class="rounded-lg bg-slate-950/70 p-3">
          <div class="text-xs text-slate-400">验证集概况</div>
          <div class="mt-2 grid grid-cols-2 gap-2 text-sm">
            <div>准确率 <b>{{ Math.round((selected.metrics?.accuracy || 0) * 100) }}%</b></div>
            <div>宏平均 F1 <b>{{ Math.round((selected.metrics?.macro_f1 || 0) * 100) }}%</b></div>
            <div>训练图片 <b>{{ selected.metrics?.training_count || 0 }}</b></div>
            <div>验证图片 <b>{{ selected.metrics?.validation_count || 0 }}</b></div>
          </div>
          <div class="mt-3 space-y-1 text-[10px] text-slate-400">
            <div v-for="(metric, code) in selected.metrics?.per_class || {}" :key="code">{{ categories.find((item) => item.code === code)?.name || code }}：样本 {{ metric.support }} · Precision {{ Math.round(metric.precision * 100) }}% · Recall {{ Math.round(metric.recall * 100) }}% · F1 {{ Math.round(metric.f1 * 100) }}%</div>
          </div>
          <p class="mt-3 text-[10px] text-slate-500">验证集留出自人工标签，百分比是校准后的模型置信度估值，不代表保证准确率。</p>
        </div>
        <div class="rounded-lg bg-slate-950/70 p-3 lg:col-span-2">
          <div class="mb-2 text-xs text-slate-400">混淆矩阵（行=人工类别，列=模型预测）</div>
          <div class="overflow-x-auto">
            <table class="w-full border-collapse text-center text-[10px]">
              <thead><tr><th class="p-1 text-left">真实 \ 预测</th><th v-for="code in Object.keys(selected.metrics?.confusion_matrix || {})" :key="code" class="p-1">{{ categories.find((item) => item.code === code)?.name || code }}</th></tr></thead>
              <tbody><tr v-for="(row, actual) in selected.metrics?.confusion_matrix || {}" :key="actual"><th class="p-1 text-left">{{ categories.find((item) => item.code === actual)?.name || actual }}</th><td v-for="code in Object.keys(selected.metrics?.confusion_matrix || {})" :key="code" class="border border-slate-800 p-1">{{ row[code] || 0 }}</td></tr></tbody>
            </table>
          </div>
          <div v-if="shadowSummary?.version_id === selected.id" class="mt-3 rounded border border-sky-900/70 bg-sky-950/30 p-2 text-xs text-sky-200">
            影子测试：候选预测 {{ shadowSummary.predicted_count }} 张 · 已人工核对 {{ shadowSummary.reviewed_count }} 张<span v-if="shadowSummary.accuracy !== null"> · 当前匹配率 {{ Math.round(shadowSummary.accuracy * 100) }}%</span>。只记录预测，不改变线上规则。
            <div class="mt-1 flex flex-wrap gap-x-3">{{ shadowSummary.items.map((item) => `${categories.find((category) => category.code === item.predicted_class)?.name || item.predicted_class} ${item.predicted_count}张（已核 ${item.reviewed_count}）`).join(" · ") }}</div>
          </div>
          <div v-else-if="selected.metrics?.excluded_categories && Object.keys(selected.metrics.excluded_categories).length" class="mt-3 rounded border border-amber-900/70 bg-amber-950/30 p-2 text-xs text-amber-200">以下类别样本不足，未参与此版本训练：{{ Object.entries(selected.metrics.excluded_categories).map(([code, count]) => `${categories.find((item) => item.code === code)?.name || code} ${count}张`).join("、") }}</div>
        </div>
      </div>
      <div v-else class="py-6 text-center text-sm text-slate-500">尚无训练版本。先在 OCR 核对页标注图片类别。</div>
    </section>
    <div v-if="message" class="fixed bottom-4 right-4 z-50 max-w-lg rounded-lg border border-slate-700 bg-slate-950 px-4 py-3 text-sm text-slate-100 shadow-xl">{{ message }}</div>
  </div>
</template>
