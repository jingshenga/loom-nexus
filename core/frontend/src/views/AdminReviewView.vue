<script setup>
import AdminTabs from "../components/AdminTabs.vue";
import { computed, onMounted, onUnmounted, ref } from "vue";
import { CheckCircle2, XCircle, Save, Inbox, ArchiveRestore, Trash2 } from "lucide-vue-next";
import { api, mediaUrl } from "../api/client";
import AdminMediaGrid from "../components/AdminMediaGrid.vue";

const queue = ref([]);
const queueTotal = ref(0);
const pageOffset = ref(0);
const pageSize = 60;
const sourceOptions = ref([]);
const fieldDefinitions = ref([]);
const showFieldSettings = ref(false);
const newField = ref({ key: "", label: "", aliases: "", extraction_source: "manual", required_for_publish: false, is_active: true });
const duplicatePairs = ref([]);
const selectedDuplicate = ref(null);
const duplicateSaving = ref(false);
const duplicateMediaSaving = ref(false);
const duplicateOrderIds = ref(new Set());
const current = ref(null);
const form = ref({ nickname: "", district: "", price_amount: null, price_currency: "", description: "", contact_username: "", bot_url: "" });
const mediaManualOrder = ref(false);
const createdManualCardId = ref(null);
const flash = ref("");
const statusTab = ref("pending");
const selectedIds = ref([]);
const nicknameFilter = ref("");
const appliedNickname = ref("");
const sourceFilter = ref("");
const appliedSource = ref("");
const mediaFilter = ref("");
const collectedFrom = ref("");
const collectedTo = ref("");
const originalFrom = ref("");
const originalTo = ref("");
const minMedia = ref("");
const maxMedia = ref("");
const sortBy = ref("original");
const sortThen = ref("none");
const sortOrder = ref("newest");
const recollectStatus = ref("");
const activeWorkbench = ref("review");
const dutyRecovery = ref([]);
const selectedRecovery = ref(null);
const recoveryModalOpen = ref(false);
const recoveryForm = ref({ contact_peer_id: "", profile_url: "", channel_url: "", bot_url: "" });
const manualForm = ref({ nickname: "", district: "", price_amount: "", price_currency: "CNY", description: "", source_url: "", media: [] });
let timer = null;
let recollectTimer = null;
let flashTimer = null;
let recollectJobIds = [];
let pollingRecollect = false;
const RECOLLECT_STORAGE_KEY = "nexus-admin-recollect-jobs";
const recoveryBotUsername = computed({
  get: () => botUsername(recoveryForm.value.bot_url),
  set: (value) => { recoveryForm.value.bot_url = botLink(value); },
});
const listingBotUsername = computed({
  get: () => botUsername(form.value.bot_url),
  set: (value) => { form.value.bot_url = botLink(value); },
});

function botUsername(value) {
  return String(value || "").trim()
    .replace(/^(?:https?:\/\/)?(?:www\.)?t\.me\//i, "")
    .replace(/^@/, "")
    .split(/[/?#]/, 1)[0];
}

function botLink(value) {
  const username = botUsername(value);
  return username ? `https://t.me/${username}` : "";
}

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
  if (!window.confirm(`删除选中的 ${selectedIds.value.length} 张卡片？原始 Telegram 消息会保留。`)) return;
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

async function select(item) {
  const detail = await api(`/api/admin/review/queue/${item.id}`);
  current.value = detail;
  mediaManualOrder.value = (detail.media || []).some((media) => Number.isInteger(media.manual_order));
  form.value = {
    nickname: detail.nickname || "",
    district: detail.district || "",
    price_amount: detail.price_amount,
    price_currency: detail.price_currency || "",
    description: detail.description || "",
    contact_username: detail.contact_username || "",
    bot_url: detail.bot_url || "",
    custom_fields: JSON.parse(JSON.stringify(detail.custom_fields || {})),
    media: JSON.parse(JSON.stringify(detail.media || [])),
  };
}

function uncertainMediaPaths() {
  return (current.value?.media || [])
    .filter((item) => item.ocr_classification === "uncertain" && item.path)
    .map((item) => item.path);
}

async function loadQueue(resetPage = false) {
  if (resetPage) pageOffset.value = 0;
  const bucket = activeWorkbench.value === "checkin" ? "checkin_supplement"
    : activeWorkbench.value === "tgprofile" ? "tg_profile_v2" : "";
  const params = new URLSearchParams({ status: statusTab.value });
  if (bucket) params.set("bucket", bucket);
  params.set("limit", String(pageSize));
  params.set("offset", String(pageOffset.value));
  if (appliedNickname.value) params.set("nickname", appliedNickname.value);
  if (appliedSource.value) params.set("source", appliedSource.value);
  if (mediaFilter.value) params.set("media", mediaFilter.value);
  if (collectedFrom.value) params.set("collected_from", collectedFrom.value);
  if (collectedTo.value) params.set("collected_to", collectedTo.value);
  if (originalFrom.value) params.set("original_from", originalFrom.value);
  if (originalTo.value) params.set("original_to", originalTo.value);
  if (minMedia.value !== "") params.set("min_media", minMedia.value);
  if (maxMedia.value !== "") params.set("max_media", maxMedia.value);
  params.set("sort", sortOrder.value);
  params.set("sort_by", sortBy.value);
  params.set("sort_then", sortThen.value);
  const countParams = new URLSearchParams({ status: statusTab.value });
  if (bucket) countParams.set("bucket", bucket);
  if (appliedNickname.value) countParams.set("nickname", appliedNickname.value);
  if (appliedSource.value) countParams.set("source", appliedSource.value);
  if (mediaFilter.value) countParams.set("media", mediaFilter.value);
  if (collectedFrom.value) countParams.set("collected_from", collectedFrom.value);
  if (collectedTo.value) countParams.set("collected_to", collectedTo.value);
  if (originalFrom.value) countParams.set("original_from", originalFrom.value);
  if (originalTo.value) countParams.set("original_to", originalTo.value);
  if (minMedia.value !== "") countParams.set("min_media", minMedia.value);
  if (maxMedia.value !== "") countParams.set("max_media", maxMedia.value);
  const [data, count] = await Promise.all([
    api(`/api/admin/review/queue?${params}`),
    api(`/api/admin/review/queue-total?${countParams}`),
  ]);
  queueTotal.value = count.total;
  if (!data.items.length && pageOffset.value > 0 && queueTotal.value > 0) {
    pageOffset.value = Math.floor((queueTotal.value - 1) / pageSize) * pageSize;
    return loadQueue();
  }
  queue.value = data.items;
  if (current.value) {
    const refreshed = data.items.find((x) => x.id === current.value.id);
    current.value = refreshed || null;
  }
}

async function loadReviewSources() {
  const data = await api("/api/admin/review/sources");
  sourceOptions.value = data.items;
}

async function loadFieldDefinitions() {
  fieldDefinitions.value = (await api("/api/admin/review/fields")).items || [];
}

function addFieldDefinition() {
  const key = newField.value.key.trim().toLowerCase();
  const label = newField.value.label.trim();
  if (!key || !label || fieldDefinitions.value.some((field) => field.key === key)) {
    notify("请填写唯一的字段键和名称");
    return;
  }
  fieldDefinitions.value.push({
    ...newField.value, key, label,
    aliases: newField.value.aliases.split(/[,，\n]/).map((x) => x.trim()).filter(Boolean),
  });
  newField.value = { key: "", label: "", aliases: "", extraction_source: "manual", required_for_publish: false, is_active: true };
}

async function saveFieldDefinitions() {
  await api("/api/admin/review/fields", {
    method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ items: fieldDefinitions.value }),
  });
  await loadFieldDefinitions();
  showFieldSettings.value = false;
  notify("字段定义已保存；既有卡片字段值保留");
}

function changePage(delta) {
  const lastOffset = Math.max(0, Math.floor((queueTotal.value - 1) / pageSize) * pageSize);
  pageOffset.value = Math.min(lastOffset, Math.max(0, pageOffset.value + delta * pageSize));
  selectedIds.value = [];
  loadQueue();
}

function setWorkbench(value) {
  activeWorkbench.value = value;
  pageOffset.value = 0;
  if (value === "duplicates") loadDuplicatePairs();
  if (["review", "checkin", "tgprofile"].includes(value)) loadQueue();
  if (value === "duty") loadDutyRecovery();
}

async function toggleFieldSettings() {
  if (!showFieldSettings.value) await loadFieldDefinitions();
  showFieldSettings.value = !showFieldSettings.value;
}

async function loadDuplicatePairs() {
  const data = await api("/api/admin/review/duplicates");
  duplicatePairs.value = data.items;
  const selected = data.items.find((pair) =>
    pair.left.id === selectedDuplicate.value?.left.id
    && pair.right.id === selectedDuplicate.value?.right.id);
  selectedDuplicate.value = selected || data.items[0] || null;
}

function newestCard(pair) {
  return [pair.left, pair.right].sort((a, b) =>
    new Date(b.collected_at) - new Date(a.collected_at) || b.id - a.id)[0];
}

async function decideDuplicate(decision, keepListingId = null) {
  const pair = selectedDuplicate.value;
  if (!pair || duplicateSaving.value) return;
  const ids = [pair.left.id, pair.right.id].sort((a, b) => a - b);
  if (decision === "merge_selected") {
    const sourceId = keepListingId === pair.left.id ? pair.right.id : pair.left.id;
    const confirmed = window.confirm(
      `将卡片 #${sourceId} 合并到 #${keepListingId}。只补齐目标卡片的空字段，冲突字段不覆盖；重复媒体会跳过。来源卡片会归档保留，不删除。继续吗？`,
    );
    if (!confirmed) return;
  }
  duplicateSaving.value = true;
  try {
    const result = await api(`/api/admin/review/duplicates/${ids[0]}/${ids[1]}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision, keep_listing_id: keepListingId }),
    });
    if (decision === "merge_selected") {
      const filled = (result.fields_filled || []).join("、");
      const conflicts = (result.conflicts || []).join("、");
      notify(`已合并到 #${keepListingId}，并入 ${result.media_added || 0} 个媒体${filled ? `；补齐：${filled}` : ""}${conflicts ? `；冲突未覆盖：${conflicts}，目标卡 #${keepListingId} 可继续编辑，来源卡 #${result.archived_id} 已归档保留` : `；来源卡 #${result.archived_id} 已归档保留`}`);
    } else {
      notify(decision === "keep_both"
        ? "已记录为不同账号，保留两张卡片"
        : `已保留卡片 #${keepListingId || newestCard(pair).id}，另一张已归档`);
    }
    await loadDuplicatePairs();
    await loadQueue();
  } catch (e) {
    notify(`处理失败：${e.message}`);
  } finally {
    duplicateSaving.value = false;
  }
}

function markDuplicateOrder(card) {
  duplicateOrderIds.value = new Set([...duplicateOrderIds.value, card.id]);
}

async function saveDuplicateMedia(card) {
  if (duplicateMediaSaving.value) return;
  duplicateMediaSaving.value = true;
  try {
    await api(`/api/admin/review/${card.id}/content`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        nickname: card.nickname || "", district: card.district || "",
        price_amount: card.price_amount, price_currency: card.price_currency || "",
        description: card.description || "", manual_order: duplicateOrderIds.value.has(card.id),
        media: (card.media || []).map((media) => ({
          path: media.path, visibility: media.visibility,
          visibility_override: media.visibility_override === true,
          image_data: media.image_data, image_mime: media.image_mime,
        })),
      }),
    });
    notify(`卡片 #${card.id} 的图片已保存`);
    duplicateOrderIds.value = new Set([...duplicateOrderIds.value].filter((id) => id !== card.id));
    await loadDuplicatePairs();
  } catch (e) {
    notify(`图片保存失败：${e.message}`);
  } finally {
    duplicateMediaSaving.value = false;
  }
}

async function loadDutyRecovery() {
  const data = await api("/api/admin/review/duty-recovery");
  dutyRecovery.value = data.items;
  const refreshed = data.items.find((item) => item.id === selectedRecovery.value?.id);
  if (refreshed) {
    selectedRecovery.value = refreshed;
  } else {
    selectRecovery(data.items[0] || null, false);
  }
}

function selectRecovery(item, open = true) {
  const changed = item?.id !== selectedRecovery.value?.id;
  selectedRecovery.value = item;
  recoveryForm.value = item ? {
    contact_peer_id: item.contact_peer_id || "",
    profile_url: item.profile_url || "",
    channel_url: item.channel_url || "",
    bot_url: item.bot_url || "",
  } : { contact_peer_id: "", profile_url: "", channel_url: "", bot_url: "" };
  manualForm.value = {
    ...(changed ? { nickname: "", district: "", price_amount: "", price_currency: "CNY", description: "", source_url: "", media: [] } : manualForm.value),
    contact_username: item?.contact_username || "",
    bot_url: item?.bot_url || "",
  };
  recoveryModalOpen.value = open;
  if (changed) createdManualCardId.value = null;
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

async function createManualDutyCard() {
  if (!selectedRecovery.value) return;
  const body = { ...manualForm.value, contact_peer_id: recoveryForm.value.contact_peer_id || null,
    contact_username: selectedRecovery.value.contact_username, bot_url: recoveryForm.value.bot_url };
  if (body.price_amount === "") body.price_amount = null;
  const result = await api(`/api/admin/review/duty-recovery/${selectedRecovery.value.id}/manual-card`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  notify(`已创建待审核卡片 #${result.id}`);
  createdManualCardId.value = result.id;
  manualForm.value = { nickname: "", district: "", price_amount: "", price_currency: "CNY", description: "", source_url: "", media: [], contact_username: selectedRecovery.value.contact_username, bot_url: recoveryForm.value.bot_url || "" };
  await loadQueue();
  await loadDutyRecovery();
}

async function applyNicknameFilter() {
  appliedNickname.value = nicknameFilter.value.trim();
  appliedSource.value = sourceFilter.value;
  selectedIds.value = [];
  await loadQueue(true);
}

async function applyMediaFilters() {
  selectedIds.value = [];
  await loadQueue(true);
}

function clearQueueFilters() {
  nicknameFilter.value = "";
  sourceFilter.value = "";
  appliedNickname.value = "";
  appliedSource.value = "";
  mediaFilter.value = "";
  collectedFrom.value = "";
  collectedTo.value = "";
  originalFrom.value = "";
  originalTo.value = "";
  minMedia.value = "";
  maxMedia.value = "";
  sortBy.value = "original";
  sortThen.value = "none";
  sortOrder.value = "newest";
  statusTab.value = "pending";
  selectedIds.value = [];
  applyMediaFilters();
}

async function batchPublish() {
  if (!selectedIds.value.length || statusTab.value !== "pending") return;
  if (!window.confirm(`将选中的 ${selectedIds.value.length} 张卡片上架到广场？`)) return;
  const ids = [...selectedIds.value];
  try {
    await Promise.all(ids.map((id) => api(`/api/admin/review/${id}/publish`, { method: "POST" })));
    notify(`已上架 ${ids.length} 张卡片`);
    selectedIds.value = [];
    await loadQueue();
  } catch (e) {
    notify(`批量上架未全部完成：${e.message}`);
    await loadQueue();
  }
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

async function botRecollectSelected() {
  if (!selectedIds.value.length || selectedIds.value.length > 20) return;
  try {
    const result = await api("/api/admin/review/bot-recollect", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids: selectedIds.value }),
    });
    selectedIds.value = [];
    notify(`已排入 ${result.queued} 个 Bot 名片重采任务；按账号冷却自动执行`);
  } catch (e) {
    notify(`Bot 重采排队失败：${e.message}`);
  }
}

function notify(msg) {
  flash.value = msg;
  clearTimeout(flashTimer);
  flashTimer = setTimeout(() => (flash.value = ""), 3500);
}

async function saveEdits() {
  if (!current.value) return;
  const body = { ...form.value };
  if (body.price_amount === "") body.price_amount = null;
  await api(`/api/admin/review/${current.value.id}/content`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ...body,
      manual_order: mediaManualOrder.value,
      media: body.media.map((media) => ({
        path: media.path,
        visibility: media.visibility,
        visibility_override: media.visibility_override === true,
        image_data: media.image_data,
        image_mime: media.image_mime,
      })),
    }),
  });
  notify("卡片与图片修改已保存");
  await loadQueue();
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

const filteredDuplicatePairs = computed(() => duplicatePairs.value
  .filter((pair) => {
    const cards = [pair.left, pair.right];
    if (appliedNickname.value && !cards.some((card) => (card.nickname || "").toLowerCase().includes(appliedNickname.value.toLowerCase()))) return false;
    if (appliedSource.value && !cards.some((card) => (card.source_title || "").toLowerCase().includes(appliedSource.value.toLowerCase()))) return false;
    if (mediaFilter.value && !cards.some((card) => (card.media || []).some((entry) => entry.kind === mediaFilter.value))) return false;
    return true;
  })
  .sort((a, b) => {
    const aDate = new Date(newestCard(a).collected_at).getTime();
    const bDate = new Date(newestCard(b).collected_at).getTime();
    return sortOrder.value === "oldest" ? aDate - bDate : bDate - aDate;
  }));

const filteredDutyRecovery = computed(() => dutyRecovery.value
  .filter((item) => {
    const name = `${item.profile_name || ""} ${item.contact_username || ""}`.toLowerCase();
    if (appliedNickname.value && !name.includes(appliedNickname.value.toLowerCase())) return false;
    if (appliedSource.value && !(item.source_title || "").toLowerCase().includes(appliedSource.value.toLowerCase())) return false;
    return true;
  })
  .sort((a, b) => {
    const aDate = new Date(a.created_at || 0).getTime();
    const bDate = new Date(b.created_at || 0).getTime();
    return sortOrder.value === "oldest" ? aDate - bDate : bDate - aDate;
  }));

onMounted(async () => {
  window.addEventListener("keydown", onKey);
  await Promise.allSettled([loadReviewSources(), loadQueue(true)]);
  timer = setInterval(() => {
    if (document.visibilityState !== "visible") return;
    if (activeWorkbench.value === "duty") loadDutyRecovery();
    else if (activeWorkbench.value === "duplicates") loadDuplicatePairs();
    else if (["review", "checkin", "tgprofile"].includes(activeWorkbench.value)) loadQueue();
  }, 60000);
  document.addEventListener("visibilitychange", onReviewVisibilityChange);
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
  document.removeEventListener("visibilitychange", onReviewVisibilityChange);
  clearInterval(timer);
  clearInterval(recollectTimer);
  clearTimeout(flashTimer);
});

function onReviewVisibilityChange() {
  if (document.visibilityState !== "visible") return;
  if (["review", "checkin", "tgprofile"].includes(activeWorkbench.value)) loadQueue();
  else if (activeWorkbench.value === "duty") loadDutyRecovery();
  else if (activeWorkbench.value === "duplicates") loadDuplicatePairs();
}
</script>

<template>
  <div class="space-y-3">
  <AdminTabs />
    <div v-if="flash" role="status" aria-live="polite" class="pointer-events-none fixed right-4 top-4 z-[100] max-w-[min(28rem,calc(100vw-2rem))] rounded-lg border border-emerald-700/60 bg-slate-950/95 px-4 py-3 text-sm text-emerald-200 shadow-xl backdrop-blur">
      {{ flash }}
    </div>

    <div class="flex gap-2">
      <button @click="setWorkbench('review')" class="rounded-lg px-3 py-1.5 text-sm" :class="activeWorkbench === 'review' ? 'bg-sky-600 text-white' : 'bg-slate-800 text-slate-300'">审核队列</button>
      <RouterLink to="/admin/history" class="rounded-lg bg-slate-800 px-3 py-1.5 text-sm text-slate-300 hover:bg-slate-700">历史资料库</RouterLink>
      <button @click="activeWorkbench = 'duplicates'; loadDuplicatePairs()" class="rounded-lg px-3 py-1.5 text-sm" :class="activeWorkbench === 'duplicates' ? 'bg-sky-600 text-white' : 'bg-slate-800 text-slate-300'">疑似重复</button>
      <button @click="statusTab = 'pending'; setWorkbench('checkin')" class="rounded-lg px-3 py-1.5 text-sm" :class="activeWorkbench === 'checkin' ? 'bg-sky-600 text-white' : 'bg-slate-800 text-slate-300'">签到补采审核</button>
      <button @click="statusTab = 'pending'; setWorkbench('tgprofile')" class="rounded-lg px-3 py-1.5 text-sm" :class="activeWorkbench === 'tgprofile' ? 'bg-sky-600 text-white' : 'bg-slate-800 text-slate-300'">TG号采集V2</button>
      <button @click="setWorkbench('duty')" class="rounded-lg px-3 py-1.5 text-sm" :class="activeWorkbench === 'duty' ? 'bg-sky-600 text-white' : 'bg-slate-800 text-slate-300'">旧签到恢复（{{ dutyRecovery.length }}）</button>
    </div>

    <div v-if="activeWorkbench !== 'review'" class="flex flex-wrap items-center gap-2 rounded-lg border border-slate-800 bg-slate-900/60 p-2">
      <input v-model="nicknameFilter" placeholder="按昵称筛选" class="min-w-28 rounded-lg border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm" />
      <select v-model="sourceFilter" class="min-w-36 rounded-lg border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm">
        <option value="">全部采集群组</option>
        <option v-for="source in sourceOptions" :key="source.id" :value="source.title">{{ source.title }}{{ source.username ? ` · @${source.username}` : "" }}</option>
      </select>
      <select v-if="activeWorkbench === 'duplicates'" v-model="mediaFilter" class="rounded-lg border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm">
        <option value="">全部媒体</option><option value="photo">有图片</option><option value="video">有视频</option>
      </select>
      <select v-model="sortOrder" class="rounded-lg border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm">
        <option value="newest">最新优先</option><option value="oldest">最早优先</option>
      </select>
      <button @click="applyNicknameFilter" class="rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-300">筛选</button>
      <button @click="clearQueueFilters" class="rounded-lg px-2 py-1.5 text-xs text-slate-500">清除</button>
    </div>

    <section v-if="activeWorkbench === 'duty'" class="space-y-4">
      <div class="grid grid-cols-3 gap-3 sm:grid-cols-4 lg:grid-cols-6 2xl:grid-cols-8">
        <button v-for="item in filteredDutyRecovery" :key="item.id" @click="selectRecovery(item)" class="min-w-0 overflow-hidden rounded-lg border border-slate-800 bg-slate-900 text-left hover:border-slate-600">
          <div class="relative flex aspect-[3/4] items-center justify-center bg-slate-950 text-3xl font-semibold text-slate-700">
            {{ (item.profile_name || item.contact_username || '?').slice(0, 1) }}
            <span class="absolute right-2 top-2 rounded bg-slate-800/90 px-2 py-1 text-[10px] text-slate-300">{{ item.status === 'deferred' ? '冷却重试' : item.status }}</span>
          </div>
          <div class="space-y-1 p-2">
            <div class="truncate text-xs font-medium">{{ item.profile_name || `@${item.contact_username}` }}</div>
            <div class="truncate text-[10px] text-slate-400">@{{ item.contact_username }} · {{ item.source_title || '来源未知' }}</div>
            <div class="truncate text-[10px] text-slate-500">{{ item.duty_date }}</div>
            <div v-if="item.error" class="line-clamp-2 text-[10px] text-amber-300">{{ item.error }}</div>
          </div>
        </button>
        <div v-if="!filteredDutyRecovery.length" class="col-span-full py-16 text-center text-sm text-slate-500">暂无符合筛选条件的签到账号</div>
      </div>
      <div v-if="selectedRecovery && recoveryModalOpen" class="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto bg-black/80 p-2 sm:p-5" @click.self="recoveryModalOpen = false">
      <section class="my-auto flex max-h-[96dvh] min-h-0 w-full max-w-5xl flex-col gap-3 touch-pan-y overflow-y-auto overscroll-contain rounded-xl border border-slate-800 bg-slate-950 p-4 shadow-2xl [-webkit-overflow-scrolling:touch]">
        <div class="flex items-start justify-between gap-2">
        <div>
          <h3 class="font-semibold">{{ selectedRecovery.profile_name || `@${selectedRecovery.contact_username}` }} · 自动采集结果 / 管理员补查</h3>
          <div class="mt-1 text-xs text-slate-400">原来源：{{ selectedRecovery.source_title || '未知' }}；TG 数字 ID 仅在可确认时填写。不会按昵称匹配或读取头像。</div>
          <p v-if="selectedRecovery.profile_about" class="mt-2 whitespace-pre-wrap rounded bg-slate-950 p-2 text-sm text-slate-300">公开简介：{{ selectedRecovery.profile_about }}</p>
          <div v-if="selectedRecovery.profile_checked_at" class="mt-1 text-xs text-slate-500">资料检查时间：{{ new Date(selectedRecovery.profile_checked_at).toLocaleString('zh-CN') }}</div>
          <a v-if="selectedRecovery.target_msg_id && selectedRecovery.source_username" class="mt-1 inline-block text-xs text-sky-300" :href="`https://t.me/${selectedRecovery.source_username}/${selectedRecovery.target_msg_id}`" target="_blank" rel="noreferrer">打开原签到消息</a>
        </div>
          <button @click="recoveryModalOpen = false" class="shrink-0 rounded border border-slate-700 px-2 py-1 text-xs text-slate-400">关闭</button>
        </div>
        <div v-if="selectedRecovery.status === 'deferred'" class="rounded-lg border border-amber-800 bg-amber-950/40 p-3 text-sm text-amber-200">
          Telegram 暂时限制了资料查询，系统会在 {{ selectedRecovery.next_attempt_at ? new Date(selectedRecovery.next_attempt_at).toLocaleString('zh-CN') : '冷却结束后' }} 自动重试。此任务仍在自动采集中，暂不需要人工补录。
        </div>
        <template v-else>
        <div class="grid gap-2 sm:grid-cols-2">
          <label class="text-xs text-slate-400">Telegram 数字 ID<input v-model="recoveryForm.contact_peer_id" inputmode="numeric" class="mt-1 w-full rounded border border-slate-700 bg-slate-950 px-2 py-2 text-sm" /></label>
          <label class="text-xs text-slate-400">公开简介链接<input v-model="recoveryForm.profile_url" class="mt-1 w-full rounded border border-slate-700 bg-slate-950 px-2 py-2 text-sm" /></label>
          <label class="text-xs text-slate-400">频道链接<input v-model="recoveryForm.channel_url" class="mt-1 w-full rounded border border-slate-700 bg-slate-950 px-2 py-2 text-sm" /></label>
          <label class="text-xs text-slate-400">Bot 用户名（单独保存）<span class="mt-1 flex rounded border border-slate-700 bg-slate-950 text-sm"><span class="flex items-center border-r border-slate-700 px-2 text-slate-500">https://t.me/</span><input v-model="recoveryBotUsername" placeholder="例如 xxxxxxbot" spellcheck="false" class="min-w-0 flex-1 bg-transparent px-2 py-2 text-slate-100 outline-none" /></span></label>
        </div>
        <button @click="saveRecoveryLinks" class="rounded-lg border border-slate-700 px-3 py-2 text-sm hover:bg-slate-800">保存账号与链接</button>
        <details class="rounded-lg border border-slate-800 bg-slate-950/60 p-3">
          <summary class="cursor-pointer text-sm text-slate-300">频道消息扫描 · {{ selectedRecovery.scanned_count }}/{{ selectedRecovery.scan_target }}</summary>
          <div class="mt-3">
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
        </details>
        <details class="border-t border-slate-800 pt-3">
          <summary class="cursor-pointer text-sm text-slate-300">管理员手工补录（创建后进入待审核）</summary>
          <div class="mt-3">
          <div class="grid gap-2 sm:grid-cols-2">
            <input v-model="manualForm.nickname" placeholder="昵称" class="rounded border border-slate-700 bg-slate-950 px-2 py-2 text-sm" />
            <input v-model="manualForm.district" placeholder="位置" class="rounded border border-slate-700 bg-slate-950 px-2 py-2 text-sm" />
            <input v-model.number="manualForm.price_amount" type="number" placeholder="价格" class="rounded border border-slate-700 bg-slate-950 px-2 py-2 text-sm" />
            <input v-model="manualForm.price_currency" placeholder="币种" class="rounded border border-slate-700 bg-slate-950 px-2 py-2 text-sm" />
            <input v-model="manualForm.source_url" placeholder="Telegram 消息/来源链接" class="rounded border border-slate-700 bg-slate-950 px-2 py-2 text-sm sm:col-span-2" />
            <textarea v-model="manualForm.description" rows="3" placeholder="文字介绍" class="rounded border border-slate-700 bg-slate-950 px-2 py-2 text-sm sm:col-span-2" />
            <div class="sm:col-span-2">
              <p class="mb-2 text-xs text-slate-400">补录图片（新增后默认仅管理员可见）</p>
              <AdminMediaGrid v-model:media="manualForm.media" editable />
            </div>
          </div>
          <button @click="createManualDutyCard" class="mt-3 rounded-lg bg-sky-600 px-4 py-2 text-sm font-medium hover:bg-sky-500">创建待审核卡片</button>
          <RouterLink v-if="createdManualCardId" :to="`/explore/${createdManualCardId}`" class="ml-3 text-sm text-sky-300 hover:text-sky-200">打开卡片图片审核 ↗</RouterLink>
          </div>
        </details>
        </template>
      </section>
      </div>
    </section>

    <template v-else-if="['review', 'checkin', 'tgprofile'].includes(activeWorkbench)">

    <div class="flex flex-wrap items-center gap-2">
      <h2 class="text-lg font-semibold">{{ activeWorkbench === 'checkin' ? '签到补采审核' : activeWorkbench === 'tgprofile' ? 'TG号采集V2' : '审核工作台' }}</h2>
      <select v-model="statusTab" @change="selectedIds = []; loadQueue(true)"
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
        <select v-model="sourceFilter"
          class="min-w-0 flex-1 rounded-lg border border-slate-700 bg-slate-900 px-2 py-1 text-sm sm:w-44 sm:flex-none">
          <option value="">全部采集群组</option>
          <option v-for="source in sourceOptions" :key="source.id" :value="source.title">{{ source.title }}{{ source.username ? ` · @${source.username}` : "" }}</option>
        </select>
        <button @click="applyNicknameFilter"
          class="rounded-lg border border-slate-700 px-2 py-1 text-xs text-slate-300 hover:bg-slate-800">筛选</button>
      </div>
      <select v-model="mediaFilter" @change="applyMediaFilters" class="rounded-lg border border-slate-700 bg-slate-900 px-2 py-1 text-sm">
        <option value="">全部媒体</option><option value="photo">有图片</option><option value="video">有视频</option>
      </select>
      <label class="flex items-center gap-1 text-xs text-slate-400">采集自<input v-model="collectedFrom" type="date" class="rounded border border-slate-700 bg-slate-900 px-1.5 py-1 text-xs" /></label>
      <label class="flex items-center gap-1 text-xs text-slate-400">至<input v-model="collectedTo" type="date" class="rounded border border-slate-700 bg-slate-900 px-1.5 py-1 text-xs" /></label>
      <label class="flex items-center gap-1 text-xs text-slate-400">原消息自<input v-model="originalFrom" type="date" class="rounded border border-slate-700 bg-slate-900 px-1.5 py-1 text-xs" /></label>
      <label class="flex items-center gap-1 text-xs text-slate-400">至<input v-model="originalTo" type="date" class="rounded border border-slate-700 bg-slate-900 px-1.5 py-1 text-xs" /></label>
      <label class="flex items-center gap-1 text-xs text-slate-400">媒体数<input v-model="minMedia" type="number" min="0" placeholder="最少" class="w-16 rounded border border-slate-700 bg-slate-900 px-1.5 py-1 text-xs" /></label>
      <label class="flex items-center gap-1 text-xs text-slate-400">至<input v-model="maxMedia" type="number" min="0" placeholder="最多" class="w-16 rounded border border-slate-700 bg-slate-900 px-1.5 py-1 text-xs" /></label>
      <button @click="applyMediaFilters" class="rounded-lg border border-slate-700 px-2 py-1 text-xs text-slate-300">应用</button>
      <select v-model="sortBy" @change="applyMediaFilters" class="rounded-lg border border-slate-700 bg-slate-900 px-2 py-1 text-sm">
        <option value="original">按原消息时间</option><option value="collected">按采集时间</option><option value="media">按媒体数量</option>
      </select>
      <select v-model="sortThen" @change="applyMediaFilters" class="rounded-lg border border-slate-700 bg-slate-900 px-2 py-1 text-sm" aria-label="次级排序">
        <option value="none">不加次级排序</option><option value="original">再按原消息时间</option><option value="collected">再按采集时间</option><option value="media">再按媒体数量</option>
      </select>
      <select v-model="sortOrder" @change="applyMediaFilters" class="rounded-lg border border-slate-700 bg-slate-900 px-2 py-1 text-sm">
        <option value="newest">最新优先</option><option value="oldest">最早优先</option>
      </select>
      <div class="flex items-center gap-1 text-xs text-slate-400">
        <span>{{ queueTotal ? pageOffset + 1 : 0 }}–{{ Math.min(pageOffset + queue.length, queueTotal) }}/{{ queueTotal }}张</span>
        <button :disabled="pageOffset === 0" @click="changePage(-1)" class="rounded border border-slate-700 px-2 py-1 disabled:opacity-40">上一页</button>
        <button :disabled="pageOffset + pageSize >= queueTotal" @click="changePage(1)" class="rounded border border-slate-700 px-2 py-1 disabled:opacity-40">下一页</button>
      </div>
      <button v-if="appliedNickname || appliedSource || mediaFilter || collectedFrom || collectedTo || originalFrom || originalTo || minMedia !== '' || maxMedia !== '' || sortBy !== 'original' || sortThen !== 'none' || sortOrder !== 'newest' || statusTab !== 'pending'" @click="clearQueueFilters"
        class="rounded-lg px-2 py-1 text-xs text-slate-500 hover:text-slate-300">清除筛选</button>
      <label class="flex items-center gap-1.5 text-xs text-slate-400">
        <input type="checkbox" :checked="allSelected()" @change="toggleSelectAll" class="size-4 accent-sky-500" />
        本页全选
      </label>
      <button v-if="selectedIds.length" @click="batchRemove"
        class="flex items-center gap-1 rounded-lg border border-rose-700/60 px-3 py-1 text-xs text-rose-300 hover:bg-rose-900/30">
        <Trash2 class="size-3.5" />批量删除（{{ selectedIds.length }}）
      </button>
      <button v-if="selectedIds.length && statusTab === 'pending'" @click="batchPublish"
        class="rounded-lg border border-emerald-700/60 px-3 py-1 text-xs text-emerald-300 hover:bg-emerald-900/30">批量上架（{{ selectedIds.length }}）</button>
      <button v-if="selectedIds.length" @click="recollectSelected"
        :disabled="selectedIds.length > 20 || recollectJobIds.length > 0"
        class="rounded-lg border border-sky-700/60 px-3 py-1 text-xs text-sky-300 hover:bg-sky-900/30 disabled:opacity-40">
        重新采集（{{ selectedIds.length }}）
      </button>
      <button v-if="selectedIds.length" @click="botRecollectSelected"
        :disabled="selectedIds.length > 20"
        class="rounded-lg border border-violet-700/60 px-3 py-1 text-xs text-violet-300 hover:bg-violet-900/30 disabled:opacity-40">
        重采 Bot 地址（{{ selectedIds.length }}）
      </button>
      <span v-if="recollectStatus" class="text-xs text-sky-300">{{ recollectStatus }}</span>
      <button @click="toggleFieldSettings" class="rounded-lg border border-slate-700 px-3 py-1 text-xs text-slate-300">自定义字段</button>
      <span class="text-xs text-slate-500">Ctrl+Enter 快捷发布</span>
    </div>

    <section v-if="showFieldSettings" class="my-3 space-y-3 rounded-xl border border-slate-800 bg-slate-900 p-3">
      <div class="flex items-center justify-between"><h3 class="text-sm font-semibold">审核字段定义</h3><button @click="saveFieldDefinitions" class="rounded bg-sky-600 px-3 py-1.5 text-xs">保存定义</button></div>
      <div class="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        <div v-for="field in fieldDefinitions" :key="field.key" class="space-y-2 rounded border border-slate-800 p-2">
          <div class="grid grid-cols-2 gap-2"><input v-model="field.key" class="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs" placeholder="字段键" /><input v-model="field.label" class="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs" placeholder="显示名称" /></div>
          <input :value="field.aliases.join('，')" @change="field.aliases = $event.target.value.split(/[,，]/).map(x => x.trim()).filter(Boolean)" class="w-full rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs" placeholder="别名，逗号分隔" />
          <div class="flex items-center justify-between text-xs text-slate-400"><select v-model="field.extraction_source" class="rounded border border-slate-700 bg-slate-950 px-1 py-1"><option value="manual">人工</option><option value="telegram">Telegram文本</option><option value="ocr">OCR</option></select><label><input v-model="field.required_for_publish" type="checkbox" /> 上架必填</label><label><input v-model="field.is_active" type="checkbox" /> 启用</label></div>
        </div>
      </div>
      <div class="grid gap-2 sm:grid-cols-6"><input v-model="newField.key" placeholder="新字段键" class="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs" /><input v-model="newField.label" placeholder="显示名称" class="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs" /><input v-model="newField.aliases" placeholder="别名，用逗号分隔" class="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs" /><select v-model="newField.extraction_source" class="rounded border border-slate-700 bg-slate-950 px-1 py-1 text-xs"><option value="manual">人工</option><option value="telegram">Telegram文本</option><option value="ocr">OCR</option></select><label class="flex items-center gap-1 text-xs text-slate-400"><input v-model="newField.required_for_publish" type="checkbox" />上架必填</label><button @click="addFieldDefinition" class="rounded border border-slate-700 px-2 py-1 text-xs">添加字段</button></div>
    </section>

    <div class="grid grid-cols-1 gap-4 lg:h-[calc(100dvh-190px)] lg:grid-cols-12">
      <!-- left: queue -->
      <div class="grid grid-cols-3 gap-2 sm:grid-cols-4 lg:col-span-12 lg:grid-cols-6 2xl:grid-cols-8">
        <article v-for="item in queue" :key="item.id" class="relative min-w-0 overflow-hidden rounded-lg border border-slate-800 bg-slate-900 hover:border-slate-600">
          <button @click="select(item)" class="block w-full text-left">
            <div class="relative aspect-[3/4] bg-black">
              <img v-if="item.media?.length" :src="mediaUrl(item.media.find((m) => m.kind === 'photo')?.path || item.media[0].poster || item.media[0].path, { thumbnail: true })" class="size-full object-contain" loading="lazy" />
              <span v-else class="flex size-full items-center justify-center text-xs text-slate-600">无图片</span>
            </div>
            <div class="space-y-1 p-2">
              <div class="truncate text-xs font-medium">{{ item.nickname || `#${item.id}` }}</div>
              <div class="truncate text-[10px] text-slate-400">{{ item.source_title || '来源未知' }}</div>
              <div class="truncate text-[10px] text-slate-500">{{ new Date(item.created_at).toLocaleString('zh-CN') }}</div>
            </div>
          </button>
          <input type="checkbox" :checked="isSelected(item.id)" @change="toggleSelect(item.id)"
            class="absolute left-2 top-2 size-5 cursor-pointer accent-sky-500" :aria-label="`选择 ${item.nickname || item.id}`" />
          <button v-if="statusTab === 'pending'" @click="decide('publish', item)"
            class="absolute bottom-14 right-2 rounded bg-emerald-600/90 px-2 py-1 text-[10px] text-white">上架</button>
        </article>
        <div v-if="!queue.length" class="col-span-full flex flex-col items-center gap-2 py-16 text-slate-600">
          <Inbox class="size-8" />队列已清空
        </div>
      </div>

      <div v-if="current" class="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-2 sm:p-5" @click.self="current = null">
      <section class="my-auto flex min-h-0 max-h-[96dvh] w-full max-w-6xl flex-col gap-3 touch-pan-y overflow-y-auto overscroll-contain rounded-xl border border-slate-800 bg-slate-950 p-4 shadow-2xl [-webkit-overflow-scrolling:touch]">
        <button @click="current = null" class="ml-auto rounded border border-slate-700 px-2 py-1 text-xs text-slate-400">关闭</button>
        <div class="flex flex-wrap items-start justify-between gap-2">
          <div><h3 class="font-semibold">{{ current.nickname || `卡片 #${current.id}` }}</h3><p class="mt-1 text-xs text-slate-400">{{ current.source_title || "未知来源" }}<span v-if="current.contact_username"> · @{{ current.contact_username }}</span></p></div>
          <div class="text-right text-xs"><div class="text-sky-300">采集于 {{ new Date(current.created_at).toLocaleString('zh-CN') }}</div><div v-if="current.source_posted_at" class="mt-1 text-slate-500">原消息 {{ new Date(current.source_posted_at).toLocaleString('zh-CN') }}</div></div>
        </div>
        <div class="flex flex-wrap gap-2 text-sm text-slate-300"><span v-if="current.district" class="rounded bg-slate-800 px-2 py-1">{{ current.district }}</span><span class="rounded bg-slate-800 px-2 py-1">{{ current.price_amount ?? "价格待核" }} {{ current.price_currency || "" }}</span><span v-if="current.contact_peer_id" class="rounded bg-slate-800 px-2 py-1">TG ID {{ current.contact_peer_id }}</span></div>
        <div class="flex flex-wrap gap-2 text-xs text-slate-400"><span class="rounded bg-slate-900 px-2 py-1">媒体 {{ current.media?.length || 0 }} 个 · 有效媒体 {{ current.effective_media_count || 0 }} 个</span><RouterLink v-if="uncertainMediaPaths().length" :to="{ path: '/admin/ocr', query: { listing_id: current.id, media_path: uncertainMediaPaths(), status: 'all' } }" class="rounded bg-amber-950/60 px-2 py-1 text-amber-200 hover:bg-amber-900">含 AI 未确认媒体 · 点击直接核对 →</RouterLink></div>
        <AdminMediaGrid v-model:media="form.media" editable @order-change="mediaManualOrder = true" />
        <details class="rounded-lg border border-slate-800 bg-slate-950/60 p-3"><summary class="cursor-pointer text-sm text-slate-300">查看原始消息</summary><p class="mt-3 whitespace-pre-wrap text-sm leading-relaxed text-slate-400">{{ current.source_text || "（无正文）" }}</p></details>
        <details class="rounded-lg border border-slate-800 bg-slate-950/60 p-3"><summary class="cursor-pointer text-sm text-slate-300">修订卡片字段</summary>
          <div class="mt-3 flex flex-wrap gap-1.5"><span v-for="(ev, field) in current.evidence" :key="field" class="rounded px-2 py-0.5 text-xs" :class="ev.src === 'ocr' ? 'bg-amber-600/20 text-amber-300' : 'bg-slate-800 text-slate-400'">{{ field }} · {{ ev.src }}</span></div>
          <div class="mt-3 grid gap-2 text-sm sm:grid-cols-2"><label>昵称<input v-model="form.nickname" class="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2" /></label><label>城区<input v-model="form.district" class="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2" /></label><label>价格<input v-model.number="form.price_amount" type="number" class="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2" /></label><label>币种<input v-model="form.price_currency" class="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2" /></label><label class="sm:col-span-2">TG号（填用户名，不含 @）<input v-model="form.contact_username" placeholder="例如 baitao431" spellcheck="false" class="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2" /></label><label class="sm:col-span-2">Bot 用户名<span class="mt-1 flex rounded-lg border border-slate-700 bg-slate-950"><span class="flex items-center border-r border-slate-700 px-3 text-slate-500">https://t.me/</span><input v-model="listingBotUsername" placeholder="例如 xxxxxxbot" spellcheck="false" class="min-w-0 flex-1 bg-transparent px-3 py-2 outline-none" /></span></label><label class="sm:col-span-2">描述<textarea v-model="form.description" rows="4" class="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2" /></label></div>
          <div v-for="field in fieldDefinitions.filter(item => item.is_active)" :key="field.key" class="mt-2 text-sm"><label>{{ field.label }}<span v-if="field.required_for_publish" class="text-rose-400"> · 必填</span><input v-model="form.custom_fields[field.key]" class="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2" /></label></div>
        </details>
        <div class="sticky bottom-0 flex gap-2 border-t border-slate-800 bg-slate-950/95 py-3 backdrop-blur">
          <button @click="saveEdits" class="flex flex-1 items-center justify-center gap-1.5 rounded-lg border border-slate-600 py-2 text-sm hover:bg-slate-800"><Save class="size-4" />保存修订</button>
          <template v-if="statusTab === 'pending'"><button @click="decide('reject')" class="flex flex-1 items-center justify-center gap-1.5 rounded-lg border border-rose-700/60 py-2 text-sm text-rose-300 hover:bg-rose-900/30"><XCircle class="size-4" />驳回</button><button @click="decide('publish')" class="flex flex-1 items-center justify-center gap-1.5 rounded-lg bg-emerald-600 py-2 text-sm font-medium hover:bg-emerald-500"><CheckCircle2 class="size-4" />发布</button></template>
          <button v-else-if="statusTab === 'published'" @click="decide('withdraw')" class="flex flex-1 items-center justify-center gap-1.5 rounded-lg border border-amber-600/60 py-2 text-sm text-amber-300 hover:bg-amber-900/30"><ArchiveRestore class="size-4" />撤回下架</button><button @click="deleteCurrent" class="flex items-center justify-center gap-1.5 rounded-lg border border-rose-700/60 px-3 py-2 text-sm text-rose-300 hover:bg-rose-900/30"><Trash2 class="size-4" />删除</button>
        </div>
      </section>
      </div>
    </div>
    </template>
    <section v-else class="grid grid-cols-1 gap-4 lg:h-[calc(100dvh-190px)] lg:grid-cols-12">
      <aside class="max-h-48 space-y-2 overflow-y-auto lg:col-span-4 lg:max-h-none lg:pr-1">
        <button v-for="pair in filteredDuplicatePairs" :key="`${pair.left.id}-${pair.right.id}`" @click="selectedDuplicate = pair"
          class="w-full rounded-lg border p-3 text-left" :class="selectedDuplicate?.left.id === pair.left.id && selectedDuplicate?.right.id === pair.right.id ? 'border-sky-500 bg-sky-600/10' : 'border-slate-800 bg-slate-900 hover:border-slate-600'">
          <div class="flex items-center justify-between gap-2"><span class="font-medium">{{ pair.left.nickname || pair.right.nickname || '疑似重复资料' }}</span><span class="rounded bg-amber-500/15 px-2 py-0.5 text-[11px] text-amber-300">待人工核对</span></div>
          <div class="mt-1 text-[11px] text-amber-200">{{ pair.match_reason }}</div>
          <div class="mt-1 truncate text-xs text-slate-400">@{{ pair.left.contact_username || '未记录' }} / @{{ pair.right.contact_username || '未记录' }}</div>
          <div class="mt-1 text-xs text-sky-300">最新采集 {{ new Date(newestCard(pair).collected_at).toLocaleString('zh-CN') }}</div>
        </button>
        <div v-if="!filteredDuplicatePairs.length" class="py-16 text-center text-sm text-slate-500">暂无符合筛选条件的疑似重复卡片</div>
      </aside>
      <section v-if="selectedDuplicate" class="space-y-4 overflow-y-auto rounded-xl border border-slate-800 bg-slate-900/50 p-4 lg:col-span-8">
        <div class="rounded-lg border border-amber-700/40 bg-amber-950/20 p-3 text-sm text-amber-200">候选可能由 TG 号、规范化昵称或资料内容匹配产生。请核对来源、账号、时间和媒体；系统只提供线索，不会自动合并。</div>
        <div class="grid gap-4 md:grid-cols-2">
          <article v-for="card in [selectedDuplicate.left, selectedDuplicate.right]" :key="card.id" class="min-w-0 space-y-3 rounded-lg border border-slate-800 bg-slate-950/50 p-3">
            <div class="flex flex-wrap items-start justify-between gap-2"><div><h3 class="font-semibold">{{ card.nickname }} · #{{ card.id }}</h3><p class="mt-1 text-xs text-slate-400">{{ card.source_title || '来源未知' }} · {{ card.status === 'published' ? '已发布' : '待审核' }}</p></div><span v-if="card.id === newestCard(selectedDuplicate).id" class="rounded bg-sky-500/15 px-2 py-1 text-xs text-sky-300">最新采集 · 优先建议</span></div>
            <div class="space-y-1 text-xs"><p class="text-sky-300">采集于 {{ new Date(card.collected_at).toLocaleString('zh-CN') }}</p><p class="text-slate-500">原消息 {{ card.posted_at ? new Date(card.posted_at).toLocaleString('zh-CN') : '时间未知' }}</p><p class="text-slate-300">TG：@{{ card.contact_username || '未记录' }}<span v-if="card.contact_peer_id"> · ID {{ card.contact_peer_id }}</span></p><p class="text-slate-400">{{ card.district || '位置未知' }} · {{ card.price_amount ?? '价格未知' }} {{ card.price_currency || '' }}</p></div>
            <AdminMediaGrid v-model:media="card.media" editable @order-change="markDuplicateOrder(card)" />
            <button @click="saveDuplicateMedia(card)" :disabled="duplicateMediaSaving" class="rounded-lg border border-emerald-800 px-3 py-1.5 text-xs text-emerald-300 disabled:opacity-50">保存图片修改</button>
            <RouterLink :to="`/explore/${card.id}`" class="inline-block text-xs text-sky-300 hover:text-sky-200">编辑此卡片和图片 ↗</RouterLink>
            <details class="rounded border border-slate-800 p-2"><summary class="cursor-pointer text-xs text-slate-400">查看原始消息</summary><p class="mt-2 whitespace-pre-wrap text-xs leading-relaxed text-slate-500">{{ card.source_text || card.description || '（无正文）' }}</p></details>
          </article>
        </div>
        <div class="sticky bottom-0 flex flex-col gap-2 border-t border-slate-800 bg-slate-950/95 py-3 backdrop-blur">
          <div class="grid gap-2 sm:grid-cols-2">
            <div v-for="card in [selectedDuplicate.left, selectedDuplicate.right]" :key="`actions-${card.id}`" class="grid gap-2">
              <button @click="decideDuplicate('keep_selected', card.id)" :disabled="duplicateSaving"
                class="rounded-lg bg-sky-700 px-3 py-2 text-sm font-medium hover:bg-sky-600 disabled:opacity-50">
                仅保留 #{{ card.id }}，归档 #{{ card.id === selectedDuplicate.left.id ? selectedDuplicate.right.id : selectedDuplicate.left.id }}{{ card.id === newestCard(selectedDuplicate).id ? ' · 最新采集' : '' }}
              </button>
              <button @click="decideDuplicate('merge_selected', card.id)" :disabled="duplicateSaving"
                class="rounded-lg border border-emerald-700 px-3 py-2 text-sm font-medium text-emerald-300 hover:bg-emerald-950/40 disabled:opacity-50">
                合并到 #{{ card.id }}，归档另一张
              </button>
            </div>
          </div>
          <button @click="decideDuplicate('keep_both')" :disabled="duplicateSaving" class="rounded-lg border border-slate-600 px-3 py-2.5 text-sm text-slate-200 hover:bg-slate-800 disabled:opacity-50">不同账号，保留两张</button>
        </div>
      </section>
      <section v-else class="flex items-center justify-center rounded-xl border border-slate-800 text-slate-600 lg:col-span-8">选择一组疑似重复卡片</section>
    </section>
  </div>
</template>
