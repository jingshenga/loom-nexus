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
const nicknameFilter = ref("");
const appliedNickname = ref("");
const recollectStatus = ref("");
const activeWorkbench = ref("review");
const dutyRecovery = ref([]);
const selectedRecovery = ref(null);
const recoveryForm = ref({ contact_peer_id: "", profile_url: "", channel_url: "", bot_url: "" });
const manualForm = ref({ nickname: "", district: "", price_amount: "", price_currency: "CNY", description: "", source_url: "", image_data: "", image_mime: "" });
let timer = null;
let recollectTimer = null;
let recollectJobIds = [];
let pollingRecollect = false;
const RECOLLECT_STORAGE_KEY = "nexus-admin-recollect-jobs";

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
  await removeCards(selectedIds.value);
}

async function removeCards(ids) {
  const r = await api("/api/admin/review/batch-delete", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ids }),
  });
  notify(`已删除 ${r.deleted} 条卡片（原始消息保留）`);
  selectedIds.value = [];
  await loadQueue();
}

async function deleteCurrent() {
  if (!current.value) return;
  const label = current.value.nickname || `#${current.value.id}`;
  if (!window.confirm(`删除「${label}」？原始 Telegram 消息会保留。`)) return;
  await removeCards([current.value.id]);
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
  const params = new URLSearchParams({ status: statusTab.value });
  if (appliedNickname.value) params.set("nickname", appliedNickname.value);
  const data = await api(`/api/admin/review/queue?${params}`);
  queue.value = data.items;
  if (!data.items.some((x) => x.id === (current.value && current.value.id))) {
    select(data.items[0] || null);
  }
}

async function loadDutyRecovery() {
  const data = await api("/api/admin/review/duty-recovery");
  dutyRecovery.value = data.items;
  const refreshed = data.items.find((item) => item.id === selectedRecovery.value?.id);
  if (refreshed) {
    selectedRecovery.value = refreshed;
  } else {
    selectRecovery(data.items[0] || null);
  }
}

function selectRecovery(item) {
  selectedRecovery.value = item;
  recoveryForm.value = item ? {
    contact_peer_id: item.contact_peer_id || "",
    profile_url: item.profile_url || "",
    channel_url: item.channel_url || "",
    bot_url: item.bot_url || "",
  } : { contact_peer_id: "", profile_url: "", channel_url: "", bot_url: "" };
  manualForm.value = {
    ...manualForm.value,
    contact_username: item?.contact_username || "",
    bot_url: item?.bot_url || "",
  };
}

async function saveRecoveryLinks() {
  if (!selectedRecovery.value) return;
  await api(`/api/admin/review/duty-recovery/${selectedRecovery.value.id}`, {
    method: "PATCH", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...recoveryForm.value, contact_peer_id: recoveryForm.value.contact_peer_id || null }),
  });
  notify("TG 账号与频道/Bot 链接已保存");
  await loadDutyRecovery();
}

async function scanRecovery(target) {
  if (!selectedRecovery.value) return;
  try {
    await api(`/api/admin/review/duty-recovery/${selectedRecovery.value.id}/scan`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target }),
    });
    notify(`已排入公开频道扫描：最多 ${target} 条`);
    await loadDutyRecovery();
  } catch (e) {
    notify(`扫描未启动：${e.message}`);
  }
}

async function chooseRecoveryImage(event) {
  const file = event.target.files?.[0];
  if (!file) return;
  if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type) || file.size > 10 * 1024 * 1024) {
    notify("请选择 10 MB 以内的 JPEG、PNG 或 WebP 图片");
    event.target.value = "";
    return;
  }
  const dataUrl = await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
  manualForm.value.image_data = String(dataUrl).split(",", 2)[1];
  manualForm.value.image_mime = file.type;
}

async function createManualDutyCard() {
  if (!selectedRecovery.value) return;
  const body = { ...manualForm.value, contact_peer_id: recoveryForm.value.contact_peer_id || null,
    contact_username: selectedRecovery.value.contact_username, bot_url: recoveryForm.value.bot_url };
  if (body.price_amount === "") body.price_amount = null;
  const result = await api(`/api/admin/review/duty-recovery/${selectedRecovery.value.id}/manual-card`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  notify(`已创建待审核卡片 #${result.id}`);
  manualForm.value = { nickname: "", district: "", price_amount: "", price_currency: "CNY", description: "", source_url: "", image_data: "", image_mime: "", contact_username: selectedRecovery.value.contact_username, bot_url: recoveryForm.value.bot_url || "" };
  await loadQueue();
  await loadDutyRecovery();
}

async function applyNicknameFilter() {
  appliedNickname.value = nicknameFilter.value.trim();
  selectedIds.value = [];
  await loadQueue();
}

async function pollRecollect() {
  if (pollingRecollect || !recollectJobIds.length) return;
  pollingRecollect = true;
  try {
    const params = new URLSearchParams();
    recollectJobIds.forEach((id) => params.append("job_ids", id));
    const data = await api(`/api/admin/review/recollect/status?${params}`);
    const missing = recollectJobIds.length - data.jobs.length;
    const done = data.jobs.filter((job) => ["done", "failed"].includes(job.status));
    const failed = done.filter((job) => job.status === "failed");
    recollectStatus.value = `Telegram 重采集：${done.length}/${recollectJobIds.length}，失败 ${failed.length + missing}`;
    if (missing || done.length === recollectJobIds.length) {
      clearInterval(recollectTimer);
      recollectTimer = null;
      recollectJobIds = [];
      sessionStorage.removeItem(RECOLLECT_STORAGE_KEY);
      if (failed.length || missing) {
        notify(`重采集结束，${failed.length + missing} 条失败或任务已不存在：${failed[0]?.error || "请检查任务状态"}`);
      } else {
        notify("原消息和媒体已重新拉取，卡片正在更新");
      }
      setTimeout(loadQueue, 1200);
    }
  } catch (e) {
    notify(`重采集状态读取失败：${e.message}`);
  } finally {
    pollingRecollect = false;
  }
}

async function recollectSelected() {
  if (!selectedIds.value.length || selectedIds.value.length > 20 || recollectJobIds.length) return;
  const result = await api("/api/admin/review/recollect", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ids: selectedIds.value }),
  });
  selectedIds.value = [];
  recollectJobIds = result.jobs.map((job) => job.job_id);
  sessionStorage.setItem(RECOLLECT_STORAGE_KEY, JSON.stringify(recollectJobIds));
  recollectStatus.value = `Telegram 重采集：0/${recollectJobIds.length}`;
  if (recollectTimer) clearInterval(recollectTimer);
  await pollRecollect();
  if (recollectJobIds.length) recollectTimer = setInterval(pollRecollect, 2500);
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
  await loadDutyRecovery();
  window.addEventListener("keydown", onKey);
  timer = setInterval(() => { loadQueue(); loadDutyRecovery(); }, 15000);
  try {
    recollectJobIds = JSON.parse(sessionStorage.getItem(RECOLLECT_STORAGE_KEY) || "[]");
    if (recollectJobIds.length) {
      await pollRecollect();
      if (recollectJobIds.length) recollectTimer = setInterval(pollRecollect, 2500);
    }
  } catch {
    sessionStorage.removeItem(RECOLLECT_STORAGE_KEY);
  }
});
onUnmounted(() => {
  window.removeEventListener("keydown", onKey);
  clearInterval(timer);
  clearInterval(recollectTimer);
});
</script>

<template>
  <div class="space-y-3">
  <AdminTabs />

    <div class="flex gap-2">
      <button @click="activeWorkbench = 'review'" class="rounded-lg px-3 py-1.5 text-sm" :class="activeWorkbench === 'review' ? 'bg-sky-600 text-white' : 'bg-slate-800 text-slate-300'">审核队列</button>
      <button @click="activeWorkbench = 'duty'" class="rounded-lg px-3 py-1.5 text-sm" :class="activeWorkbench === 'duty' ? 'bg-sky-600 text-white' : 'bg-slate-800 text-slate-300'">在岗补采（{{ dutyRecovery.length }}）</button>
    </div>

    <section v-if="activeWorkbench === 'duty'" class="grid gap-4 lg:grid-cols-12">
      <aside class="max-h-56 space-y-2 overflow-y-auto lg:col-span-4 lg:max-h-[calc(100dvh-210px)]">
        <button v-for="item in dutyRecovery" :key="item.id" @click="selectRecovery(item)" class="w-full rounded-lg border p-3 text-left" :class="selectedRecovery?.id === item.id ? 'border-sky-500 bg-sky-600/10' : 'border-slate-800 bg-slate-900'">
          <div class="font-medium">@{{ item.contact_username }}</div>
          <div class="mt-1 text-xs text-slate-400">{{ item.source_title || '来源群组' }} · {{ item.duty_date }} · {{ item.status }}</div>
          <div v-if="item.error" class="mt-1 line-clamp-2 text-xs text-amber-300">{{ item.error }}</div>
        </button>
        <div v-if="!dutyRecovery.length" class="py-10 text-center text-sm text-slate-500">暂无未找到或失败的在岗账号</div>
      </aside>
      <div v-if="selectedRecovery" class="space-y-4 rounded-xl border border-slate-800 bg-slate-900/50 p-4 lg:col-span-8">
        <div>
          <h3 class="font-semibold">@{{ selectedRecovery.contact_username }} 的人工补采</h3>
          <div class="mt-1 text-xs text-slate-400">原来源：{{ selectedRecovery.source_title || '未知' }}；TG 数字 ID 仅在可确认时填写。不会按昵称匹配或读取头像。</div>
          <a v-if="selectedRecovery.target_msg_id && selectedRecovery.source_username" class="mt-1 inline-block text-xs text-sky-300" :href="`https://t.me/${selectedRecovery.source_username}/${selectedRecovery.target_msg_id}`" target="_blank" rel="noreferrer">打开原在岗消息</a>
        </div>
        <div class="grid gap-2 sm:grid-cols-2">
          <label class="text-xs text-slate-400">Telegram 数字 ID<input v-model="recoveryForm.contact_peer_id" inputmode="numeric" class="mt-1 w-full rounded border border-slate-700 bg-slate-950 px-2 py-2 text-sm" /></label>
          <label class="text-xs text-slate-400">公开简介链接<input v-model="recoveryForm.profile_url" class="mt-1 w-full rounded border border-slate-700 bg-slate-950 px-2 py-2 text-sm" /></label>
          <label class="text-xs text-slate-400">频道链接<input v-model="recoveryForm.channel_url" class="mt-1 w-full rounded border border-slate-700 bg-slate-950 px-2 py-2 text-sm" /></label>
          <label class="text-xs text-slate-400">Bot 联系地址（单独保存）<input v-model="recoveryForm.bot_url" class="mt-1 w-full rounded border border-slate-700 bg-slate-950 px-2 py-2 text-sm" /></label>
        </div>
        <button @click="saveRecoveryLinks" class="rounded-lg border border-slate-700 px-3 py-2 text-sm hover:bg-slate-800">保存账号与链接</button>
        <div class="rounded-lg border border-slate-800 bg-slate-950/60 p-3">
          <div class="flex flex-wrap items-center gap-2">
            <span class="mr-auto text-sm">频道消息扫描：{{ selectedRecovery.scanned_count }}/{{ selectedRecovery.scan_target }} · {{ selectedRecovery.recovery_scan_status }}</span>
            <button v-for="depth in [20, 50, 100]" :key="depth" @click="scanRecovery(depth)" :disabled="selectedRecovery.scanned_count >= depth || selectedRecovery.recovery_scan_status === 'processing' || !recoveryForm.channel_url" class="rounded border border-sky-800 px-2 py-1 text-xs text-sky-300 disabled:opacity-40">扫到 {{ depth }} 条</button>
          </div>
          <div v-if="selectedRecovery.scan_error" class="mt-2 text-xs text-rose-300">{{ selectedRecovery.scan_error }}</div>
          <div v-if="selectedRecovery.candidates?.length" class="mt-3 space-y-2">
            <div v-for="candidate in selectedRecovery.candidates" :key="candidate.message_id" class="rounded bg-slate-900 p-2 text-xs">
              <div class="flex justify-between gap-2"><a :href="candidate.url" target="_blank" rel="noreferrer" class="text-sky-300">打开候选 Telegram 消息 ↗</a><button @click="manualForm.source_url = candidate.url" class="text-slate-400 hover:text-white">用作卡片来源</button></div>
              <div v-if="candidate.posted_at" class="mt-1 text-slate-500">{{ new Date(candidate.posted_at).toLocaleString('zh-CN') }}</div>
              <p v-if="candidate.text" class="mt-1 whitespace-pre-wrap text-slate-300">{{ candidate.text }}</p>
            </div>
          </div>
          <p v-else class="mt-2 text-xs text-slate-500">扫描结果只保存匹配账号的消息链接和文字；如需图片，请打开候选消息并由管理员上传确认后的图片。</p>
        </div>
        <div class="border-t border-slate-800 pt-3">
          <h4 class="mb-2 font-medium">管理员手工补录（创建后进入待审核）</h4>
          <div class="grid gap-2 sm:grid-cols-2">
            <input v-model="manualForm.nickname" placeholder="昵称" class="rounded border border-slate-700 bg-slate-950 px-2 py-2 text-sm" />
            <input v-model="manualForm.district" placeholder="位置" class="rounded border border-slate-700 bg-slate-950 px-2 py-2 text-sm" />
            <input v-model.number="manualForm.price_amount" type="number" placeholder="价格" class="rounded border border-slate-700 bg-slate-950 px-2 py-2 text-sm" />
            <input v-model="manualForm.price_currency" placeholder="币种" class="rounded border border-slate-700 bg-slate-950 px-2 py-2 text-sm" />
            <input v-model="manualForm.source_url" placeholder="Telegram 消息/来源链接" class="rounded border border-slate-700 bg-slate-950 px-2 py-2 text-sm sm:col-span-2" />
            <textarea v-model="manualForm.description" rows="3" placeholder="文字介绍" class="rounded border border-slate-700 bg-slate-950 px-2 py-2 text-sm sm:col-span-2" />
            <input type="file" accept="image/jpeg,image/png,image/webp" @change="chooseRecoveryImage" class="text-sm text-slate-400 sm:col-span-2" />
          </div>
          <button @click="createManualDutyCard" class="mt-3 rounded-lg bg-sky-600 px-4 py-2 text-sm font-medium hover:bg-sky-500">创建待审核卡片</button>
        </div>
      </div>
      <div v-else class="flex items-center justify-center rounded-xl border border-slate-800 text-sm text-slate-500 lg:col-span-8">选择一个未找到账号</div>
    </section>

    <template v-else>

    <div class="flex flex-wrap items-center gap-2">
      <h2 class="text-lg font-semibold">审核工作台</h2>
      <select v-model="statusTab" @change="selectedIds = []; loadQueue()"
        class="rounded-lg border border-slate-700 bg-slate-900 px-2 py-1 text-sm">
        <option value="pending">待审核</option>
        <option value="published">已发布</option>
        <option value="rejected">已驳回</option>
        <option value="archived">已下架</option>
      </select>
      <div class="flex min-w-0 flex-1 items-center gap-1.5 sm:flex-none">
        <input v-model="nicknameFilter" @keyup.enter="applyNicknameFilter"
          placeholder="按昵称筛选"
          class="min-w-0 flex-1 rounded-lg border border-slate-700 bg-slate-900 px-2 py-1 text-sm sm:w-36 sm:flex-none" />
        <button @click="applyNicknameFilter"
          class="rounded-lg border border-slate-700 px-2 py-1 text-xs text-slate-300 hover:bg-slate-800">筛选</button>
        <button v-if="appliedNickname" @click="nicknameFilter = ''; applyNicknameFilter()"
          class="rounded-lg px-2 py-1 text-xs text-slate-500 hover:text-slate-300">清除</button>
      </div>
      <label class="flex items-center gap-1.5 text-xs text-slate-400">
        <input type="checkbox" :checked="allSelected()" @change="toggleSelectAll" class="size-4 accent-sky-500" />
        全选
      </label>
      <button v-if="selectedIds.length" @click="batchRemove"
        class="flex items-center gap-1 rounded-lg border border-rose-700/60 px-3 py-1 text-xs text-rose-300 hover:bg-rose-900/30">
        <Trash2 class="size-3.5" />批量删除（{{ selectedIds.length }}）
      </button>
      <button v-if="selectedIds.length" @click="recollectSelected"
        :disabled="selectedIds.length > 20 || recollectJobIds.length > 0"
        class="rounded-lg border border-sky-700/60 px-3 py-1 text-xs text-sky-300 hover:bg-sky-900/30 disabled:opacity-40">
        重新采集（{{ selectedIds.length }}）
      </button>
      <span v-if="recollectStatus" class="text-xs text-sky-300">{{ recollectStatus }}</span>
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
          <button @click="deleteCurrent"
            class="flex items-center justify-center gap-1.5 rounded-lg border border-rose-700/60 px-3 py-2 text-sm text-rose-300 hover:bg-rose-900/30">
            <Trash2 class="size-4" />删除
          </button>
        </div>
      </section>
    </div>
    </template>
  </div>
</template>
