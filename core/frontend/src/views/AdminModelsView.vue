<script setup>
import { computed, onMounted, ref } from "vue";
import { Activity, ArrowDownToLine, Check, CircleAlert, Cpu, FileUp, ScanText } from "lucide-vue-next";
import { RouterLink } from "vue-router";
import { api } from "../api/client";

const inventory = ref(null);
const classifierVersions = ref([]);
const busy = ref(false);
const error = ref("");
const notice = ref("");
const selectedFile = ref(null);
const testFiles = ref([]);
const testVersionId = ref("");
const testResults = ref([]);
const readyVersions = computed(() => inventory.value?.vision4?.installed?.filter((x) => x.status === "ready" || x.status === "rollback") || []);

async function load() {
  error.value = "";
  try {
    const [models, versions] = await Promise.all([
      api("/api/admin/models"), api("/api/admin/ocr-classifier/versions"),
    ]);
    inventory.value = models;
    classifierVersions.value = versions.items || [];
    if (!testVersionId.value) {
      const active = models.vision4.installed?.find((item) => item.status === "active");
      testVersionId.value = String(active?.id || models.vision4.installed?.[0]?.id || "");
    }
  } catch (e) { error.value = e.message || "模型状态读取失败"; }
}
onMounted(load);

function selectFile(event) { selectedFile.value = event.target.files?.[0] || null; }
function selectTestFiles(event) { testFiles.value = Array.from(event.target.files || []); testResults.value = []; }
async function testImages(compare = false) {
  if (!testFiles.value.length || !testVersionId.value || busy.value) return;
  busy.value = true; error.value = ""; testResults.value = [];
  const active = inventory.value?.vision4?.installed?.find((item) => item.status === "active");
  const versions = [Number(testVersionId.value)];
  if (compare && active && active.id !== versions[0]) versions.push(active.id);
  try {
    for (const file of testFiles.value) {
      const row = { name: file.name, results: [] };
      for (const id of versions) {
        const result = await api(`/api/admin/models/${id}/predict`, {
          method: "POST", headers: { "Content-Type": file.type || "application/octet-stream" }, body: file,
        });
        row.results.push(result);
      }
      testResults.value.push(row);
    }
  } catch (e) { error.value = e.message || "模型测试失败"; }
  finally { busy.value = false; }
}
async function upload() {
  if (!selectedFile.value || busy.value) return;
  busy.value = true; error.value = ""; notice.value = "";
  try {
    const result = await api("/api/admin/models/vision4/upload", {
      method: "POST", headers: { "Content-Type": "application/zip", "X-Model-Filename": selectedFile.value.name },
      body: selectedFile.value,
    });
    notice.value = `版本 ${result.version_key} 已安装并通过测试推理，尚未启用。`;
    selectedFile.value = null;
    await load();
  } catch (e) { error.value = e.message || "模型上传失败"; }
  finally { busy.value = false; }
}
async function activate(item, rollback = false) {
  if (busy.value) return;
  busy.value = true; error.value = ""; notice.value = "";
  try {
    await api(`/api/admin/models/${item.id}/${rollback ? "rollback" : "activate"}`, { method: "POST" });
    notice.value = `模型 ${item.version_key} 已完成加载与切换。`;
    await load();
  } catch (e) { error.value = e.message || "模型切换失败，当前版本保持不变。"; }
  finally { busy.value = false; }
}
function pretty(value) { return value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "暂无记录"; }
</script>

<template>
  <div class="space-y-6">
    <div class="flex flex-wrap items-end justify-between gap-3">
      <div><p class="text-xs font-semibold uppercase tracking-[.18em] text-sky-400">MODEL CENTER</p><h1 class="mt-1 text-2xl font-bold">模型中心</h1><p class="mt-1 text-sm text-slate-400">三类引擎分别管理；上传、验证和启用是独立步骤。</p></div>
      <button class="rounded-xl border border-slate-700 px-3 py-2 text-sm hover:bg-slate-800" :disabled="busy" @click="load">刷新</button>
    </div>
    <div v-if="error" class="rounded-xl border border-rose-500/40 bg-rose-500/10 p-4 text-sm text-rose-300">{{ error }}</div>
    <div v-if="notice" class="rounded-xl border border-emerald-500/40 bg-emerald-500/10 p-4 text-sm text-emerald-300">{{ notice }}</div>

    <section v-if="inventory" class="grid gap-4 xl:grid-cols-3">
      <article class="min-w-0 rounded-2xl border border-slate-800 bg-slate-900 p-5">
        <div class="flex items-start justify-between gap-3"><div><div class="text-xs font-semibold uppercase tracking-wide text-sky-400">A · Vision4 ONNX</div><h2 class="mt-2 text-lg font-semibold">图片分类推理</h2></div><Cpu class="size-5 text-sky-400" /></div>
        <dl class="mt-4 space-y-2 text-sm"><div class="flex justify-between gap-3"><dt class="text-slate-400">当前版本</dt><dd class="break-all text-right">{{ inventory.vision4.version || "未知" }}</dd></div><div class="flex justify-between gap-3"><dt class="text-slate-400">运行状态</dt><dd>{{ inventory.vision4.runtime_status }}<span v-if="inventory.vision4.runtime_error" class="ml-2 break-all text-rose-300">{{ inventory.vision4.runtime_error }}</span></dd></div><div class="flex justify-between gap-3"><dt class="text-slate-400">模式</dt><dd>{{ inventory.vision4.mode }}</dd></div><div class="flex justify-between gap-3"><dt class="text-slate-400">加载耗时</dt><dd>{{ inventory.vision4.load_ms ?? "—" }} ms</dd></div><div class="flex justify-between gap-3"><dt class="text-slate-400">最近启用</dt><dd class="text-right">{{ pretty(inventory.vision4.activated_at) }}</dd></div><div class="flex justify-between gap-3"><dt class="text-slate-400">模型 SHA-256</dt><dd class="max-w-[62%] break-all text-right font-mono text-xs">{{ inventory.vision4.sha256 || "未加载" }}</dd></div></dl>
        <div class="mt-3 break-all text-xs text-slate-500">模型路径：{{ inventory.vision4.model_path }}</div>
        <div class="mt-3 flex flex-wrap gap-1.5"><span v-for="category in inventory.vision4.categories" :key="category.category" class="rounded-full bg-slate-800 px-2 py-1 text-xs">{{ category.category_zh || category.category }}</span></div>
        <details class="mt-3 text-xs text-slate-500"><summary class="cursor-pointer">查看预处理配置</summary><pre class="mt-2 max-h-40 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-slate-950 p-3">{{ JSON.stringify(inventory.vision4.preprocessing, null, 2) }}</pre></details>
        <div class="mt-5 rounded-xl border border-slate-800 bg-slate-950/50 p-3">
          <div class="mb-2 flex items-center gap-2 text-sm font-medium"><FileUp class="size-4" />安全安装模型包</div>
          <p class="mb-3 text-xs leading-5 text-slate-400">ZIP 仅接受 ONNX、manifest、labels、preprocess 与 SHA256SUMS 等白名单文件，最大 100 MiB。系统验证签名清单、维度和合成图推理；不会执行包内脚本，也不会自动启用。</p>
          <input class="block w-full min-w-0 text-xs text-slate-300 file:mr-2 file:rounded-lg file:border-0 file:bg-slate-800 file:px-3 file:py-2 file:text-xs" type="file" accept=".zip,application/zip" @change="selectFile" />
          <button class="mt-3 w-full rounded-xl bg-sky-600 px-3 py-2 text-sm font-semibold text-white disabled:opacity-50" :disabled="!selectedFile || busy" @click="upload">{{ busy ? "处理中…" : "上传并验证（保持未启用）" }}</button>
        </div>
        <div class="mt-4 space-y-2">
          <div v-if="!readyVersions.length" class="text-xs text-slate-500">暂无待启用模型版本</div>
          <div v-for="item in readyVersions" :key="item.id" class="flex min-w-0 items-center justify-between gap-2 rounded-xl border border-slate-800 p-3">
            <div class="min-w-0"><div class="truncate text-sm">{{ item.version_key }} <span class="text-xs text-slate-500">{{ item.status }}</span></div><div class="truncate font-mono text-[10px] text-slate-500">{{ item.model_sha256 }}</div></div>
            <button class="shrink-0 rounded-lg border border-slate-700 px-2.5 py-1.5 text-xs hover:bg-slate-800 disabled:opacity-50" :disabled="busy || item.status === 'active'" @click="activate(item, item.status === 'rollback')">{{ item.status === "rollback" ? "回滚到此版" : "启用" }}</button>
          </div>
        </div>
        <div class="mt-4 rounded-xl border border-slate-800 bg-slate-950/50 p-3">
          <h3 class="text-sm font-medium">图片测试与版本对比</h3>
          <select v-model="testVersionId" class="mt-2 w-full min-w-0 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm">
            <option v-for="item in inventory.vision4.installed" :key="item.id" :value="String(item.id)">{{ item.version_key }} · {{ item.status }}</option>
          </select>
          <input class="mt-2 block w-full min-w-0 text-xs text-slate-300" type="file" accept="image/*" multiple @change="selectTestFiles" />
          <div class="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2">
            <button class="rounded-lg border border-slate-700 px-3 py-2 text-sm disabled:opacity-50" :disabled="busy || !testFiles.length" @click="testImages(false)">测试所选模型</button>
            <button class="rounded-lg bg-violet-700 px-3 py-2 text-sm text-white disabled:opacity-50" :disabled="busy || !testFiles.length" @click="testImages(true)">与当前模型对比</button>
          </div>
          <div v-for="item in testResults" :key="item.name" class="mt-3 min-w-0 rounded-lg bg-slate-900 p-3 text-xs">
            <div class="break-all font-medium">{{ item.name }}</div>
            <div v-for="result in item.results" :key="result.version" class="mt-2 break-all text-slate-300">
              {{ result.version }}：{{ result.category_zh || result.category }} · {{ result.inference_ms }} ms
              <span class="block text-slate-500">{{ JSON.stringify(result.scores) }}</span>
            </div>
          </div>
        </div>
      </article>

      <article class="min-w-0 rounded-2xl border border-slate-800 bg-slate-900 p-5">
        <div class="flex items-start justify-between gap-3"><div><div class="text-xs font-semibold uppercase tracking-wide text-violet-400">B · 站内分类器</div><h2 class="mt-2 text-lg font-semibold">人工标签训练模型</h2></div><Activity class="size-5 text-violet-400" /></div>
        <p class="mt-3 text-sm leading-6 text-slate-400">此模型来自站内人工标签训练，与 Vision4 ONNX 推理模型相互独立。保留原有训练、影子评估、发布及回滚流程。</p>
        <div class="mt-4 space-y-2">
          <div v-for="item in classifierVersions.slice(0,5)" :key="item.id" class="flex items-center justify-between gap-3 rounded-xl border border-slate-800 p-3"><div class="min-w-0"><div class="text-sm">版本 #{{ item.id }}</div><div class="text-xs text-slate-500">{{ pretty(item.created_at) }}</div><div v-if="item.metrics?.macro_f1 !== undefined" class="mt-1 text-xs text-slate-400">Macro F1 {{ Number(item.metrics.macro_f1).toFixed(3) }}</div></div><span class="shrink-0 rounded-full bg-slate-800 px-2 py-1 text-xs">{{ item.status }}</span></div>
          <div v-if="!classifierVersions.length" class="text-xs text-slate-500">没有已登记的训练版本</div>
        </div>
        <RouterLink to="/admin/ocr-classifier" class="mt-4 inline-flex items-center gap-2 rounded-xl border border-slate-700 px-3 py-2 text-sm hover:bg-slate-800"><ArrowDownToLine class="size-4" />打开训练与发布</RouterLink>
      </article>

      <article class="min-w-0 rounded-2xl border border-slate-800 bg-slate-900 p-5">
        <div class="flex items-start justify-between gap-3"><div><div class="text-xs font-semibold uppercase tracking-wide text-emerald-400">C · RapidOCR</div><h2 class="mt-2 text-lg font-semibold">文字识别引擎</h2></div><ScanText class="size-5 text-emerald-400" /></div>
        <dl class="mt-4 space-y-3 text-sm"><div class="flex justify-between gap-3"><dt class="text-slate-400">引擎版本</dt><dd class="font-mono text-xs">{{ inventory.rapidocr.engine_version }}</dd></div><div class="flex justify-between gap-3"><dt class="text-slate-400">状态</dt><dd>{{ inventory.rapidocr.runtime_status }}</dd></div><div class="flex justify-between gap-3"><dt class="text-slate-400">成功记录</dt><dd>{{ inventory.rapidocr.done_count }}</dd></div><div class="flex justify-between gap-3"><dt class="text-slate-400">待处理</dt><dd>{{ inventory.rapidocr.pending_count }}</dd></div><div class="flex justify-between gap-3"><dt class="text-slate-400">失败记录</dt><dd class="flex items-center gap-1"><CircleAlert v-if="inventory.rapidocr.failed_count" class="size-3 text-amber-400" />{{ inventory.rapidocr.failed_count }}</dd></div><div class="flex justify-between gap-3"><dt class="text-slate-400">最近处理</dt><dd class="text-right">{{ pretty(inventory.rapidocr.last_processed_at) }}</dd></div></dl>
        <div class="mt-5 rounded-xl bg-slate-950/60 p-3 text-xs leading-5 text-slate-400">RapidOCR 运行时随 Core 版本发布，不支持上传任意权重热切换。需要升级时走受控 Core 发布、备份和回滚流程。</div>
      </article>
    </section>

    <section v-if="inventory?.vision4?.events?.length" class="rounded-2xl border border-slate-800 bg-slate-900 p-5">
      <h2 class="mb-3 font-semibold">模型操作审计</h2>
      <div class="overflow-x-auto"><table class="w-full min-w-[560px] text-left text-sm"><thead class="text-xs text-slate-500"><tr><th class="pb-2">时间</th><th class="pb-2">版本</th><th class="pb-2">操作</th><th class="pb-2">管理员 ID</th></tr></thead><tbody><tr v-for="event in inventory.vision4.events" :key="event.id" class="border-t border-slate-800"><td class="py-2">{{ pretty(event.created_at) }}</td><td class="py-2">{{ event.version_key || "—" }}</td><td class="py-2">{{ event.action }}</td><td class="py-2">{{ event.actor_id }}</td></tr></tbody></table></div>
    </section>
    <p class="flex items-center gap-2 text-xs text-slate-500"><Check class="size-3 text-emerald-400" />模型仅通过管理员 API 操作；Web Core 不具有 Docker 管理权限，安装成功不会自动切换线上模型。</p>
  </div>
</template>
