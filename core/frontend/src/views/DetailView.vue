<script setup>
import { computed, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { Star, ArrowLeft, MessageCircle, TriangleAlert, ExternalLink, Pencil, Save, X, ChevronUp, ChevronDown, RotateCcw, Wallet, Sparkles, ScanSearch, Ruler } from "lucide-vue-next";
import { api, mediaUrl } from "../api/client";
import { useSession } from "../stores/session";
import MediaGallery from "../components/MediaGallery.vue";
import BackToTop from "../components/BackToTop.vue";

const route = useRoute();
const router = useRouter();
const session = useSession();
const item = ref(null);
const favorited = ref(false);
const error = ref("");
const conflict = ref(false);
const editing = ref(false);
const savingEdit = ref(false);
const editError = ref("");
const editForm = ref(null);
const manualOrder = ref(false);

function startEdit() {
  editForm.value = {
    nickname: item.value.nickname || "",
    district: item.value.district || "",
    price_amount: item.value.price_amount,
    price_currency: item.value.price_currency || "CNY",
    description: item.value.description || "",
    media: JSON.parse(JSON.stringify(item.value.media || [])),
  };
  manualOrder.value = editForm.value.media.some((media) => Number.isInteger(media.manual_order));
  editing.value = true;
  editError.value = "";
}

function cancelEdit() {
  editing.value = false;
  editForm.value = null;
  editError.value = "";
}

function moveMedia(index, delta) {
  const target = index + delta;
  if (target < 0 || target >= editForm.value.media.length) return;
  const media = editForm.value.media;
  [media[index], media[target]] = [media[target], media[index]];
  manualOrder.value = true;
}

function setVisibility(index, value) {
  const media = editForm.value.media[index];
  media.visibility = value;
  media.visibility_override = true;
}

function restoreAutomaticMedia() {
  for (const media of editForm.value.media) {
    delete media.manual_order;
    delete media.visibility_override;
    if (media.kind === "photo") {
      media.visibility = media.ocr_classification === "profile_photo"
        ? "public"
        : media.ocr_classification === "info_card" ? "admin" : "pending";
    }
  }
  editForm.value.media.sort((a, b) => {
    const kind = (a.kind === "photo" ? 0 : 1) - (b.kind === "photo" ? 0 : 1);
    if (kind) return kind;
    if (a.kind !== "photo") return 0;
    const visibilityOrder = { public: 0, pending: 1, admin: 2 };
    const visibility = (visibilityOrder[a.visibility] ?? 1)
      - (visibilityOrder[b.visibility] ?? 1);
    return visibility || Number(b.score || 0) - Number(a.score || 0);
  });
  manualOrder.value = false;
}

async function saveEdit() {
  savingEdit.value = true;
  editError.value = "";
  try {
    await api(`/api/admin/review/${item.value.id}/content`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        nickname: editForm.value.nickname,
        district: editForm.value.district,
        price_amount: editForm.value.price_amount === "" ? null : editForm.value.price_amount,
        price_currency: editForm.value.price_currency,
        description: editForm.value.description,
        manual_order: manualOrder.value,
        media: editForm.value.media.map((media) => ({
          path: media.path,
          visibility: media.visibility,
          visibility_override: media.visibility_override === true,
        })),
      }),
    });
    item.value = await api(`/api/listings/${item.value.id}`);
    cancelEdit();
  } catch (err) {
    editError.value = err.message || "保存失败，请重试";
  } finally {
    savingEdit.value = false;
  }
}

async function toggleFavorite() {
  const data = await api(`/api/favorites/${route.params.id}`, { method: "POST" });
  favorited.value = data.favorited;
}

async function startChat() {
  conflict.value = false;
  try {
    const data = await api("/api/chat/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ listing_id: Number(route.params.id) }),
    });
    if (data.status === "CONFLICT_LOCKED") {
      conflict.value = true;
      return;
    }
    router.push({ path: "/messages", query: { session: data.session_id } });
  } catch (e) {
    conflict.value = true;
  }
}

onMounted(async () => {
  try {
    item.value = await api(`/api/listings/${route.params.id}`);
  } catch (e) {
    error.value = "内容不存在或未发布";
  }
});

const statusText = () =>
  item.value?.online_tier === "online" ? "在线"
    : item.value?.online_tier === "recent" ? "近期活跃" : "未知";
const dutyText = () =>
  item.value?.duty_label === "today" ? "今日在岗"
    : item.value?.duty_label === "yesterday" ? "昨日在岗" : null;

// ---- 结构化属性模块（attributes 由后端下发，缺失字段优雅降级） ----

// 身体档案：年龄 / 身高 / 体重 / 胸围，无值的指标直接不渲染
const profileMetrics = computed(() => {
  const profile = item.value?.attributes?.profile || {};
  return [
    { label: "年龄", value: profile.age != null ? `${profile.age}岁` : null },
    { label: "身高", value: profile.height != null ? `${profile.height}cm` : null },
    { label: "体重", value: profile.weight != null ? `${profile.weight}kg` : null },
    { label: "胸围", value: profile.bust || null },
  ].filter((metric) => metric.value);
});

// 阶梯资费看板：1P/2P 是轮次（单次/加钟两轮），而非人数
const tariffGroups = computed(() => {
  const tariffs = item.value?.attributes?.tariffs || {};
  const quoted = [...(tariffs.p1 || []), ...(tariffs.p2 || []), ...(tariffs.bt || [])];
  return [
    { label: "1P 价格", lines: tariffs.p1 || [], tone: "text-sky-400" },
    { label: "2P 价格", lines: tariffs.p2 || [], tone: "text-violet-400" },
    { label: "BT 基础课时", lines: tariffs.bt || [], tone: "text-cyan-400" },
    { label: "现金要求", lines: tariffs.cash_note || [], tone: "text-amber-400" },
    {
      label: "其他资费",
      lines: (tariffs.raw_fees || []).filter((line) => !quoted.includes(line)),
      tone: "text-slate-400",
    },
  ].filter((group) => group.lines.length);
});

const serviceTags = computed(() => item.value?.attributes?.services || []);
const restrictionTags = computed(() => item.value?.attributes?.restrictions || []);

// 管理员 OCR 溯源抽屉：识别行 + 置信度，仅管理员数据（后端已鉴权）
const ocrTrace = computed(() => (session.isAdmin ? item.value?.ocr_trace : null) || []);
const listingEvidence = computed(() => (session.isAdmin ? item.value?.evidence : null) || {});
const formatConf = (value) =>
  value == null ? "—" : `${Math.round(Number(value) * 100)}%`;
const formatTime = (iso) =>
  iso ? new Date(iso).toLocaleString("zh-CN") : null;
</script>

<template>
  <div v-if="error" class="py-24 text-center text-slate-500">{{ error }}</div>
  <div v-else-if="item" class="grid gap-6 pb-[calc(150px+env(safe-area-inset-bottom,0px))] lg:grid-cols-2 lg:pb-0">
    <MediaGallery :media="editing && editForm ? editForm.media : item.media" fixed-mobile-frame />
    <div class="space-y-4">
      <div class="flex flex-wrap items-center gap-2">
        <h2 class="flex items-center gap-2 text-2xl font-bold">
          {{ item.nickname || "未命名" }}
          <span v-if="item.online_tier === 'online' || item.online_tier === 'recent'"
            class="inline-block size-2.5 rounded-full"
            :class="item.online_tier === 'online' ? 'bg-emerald-400' : 'bg-amber-400'"
            :title="item.online_tier === 'online' ? '在线' : '近期活跃'"></span>
        </h2>
        <button v-if="session.isAdmin && !editing" @click="startEdit"
          class="flex items-center gap-1 rounded-lg border border-slate-700 px-3 py-1.5 text-sm text-slate-300 hover:text-sky-400">
          <Pencil class="size-4" />编辑
        </button>
        <button v-if="session.isAdmin && editing" @click="saveEdit" :disabled="savingEdit"
          class="flex items-center gap-1 rounded-lg border border-emerald-700 px-3 py-1.5 text-sm text-emerald-300 disabled:opacity-50">
          <Save class="size-4" />{{ savingEdit ? "保存中" : "保存" }}
        </button>
        <button v-if="session.isAdmin && editing" @click="cancelEdit"
          class="flex items-center gap-1 rounded-lg border border-slate-700 px-3 py-1.5 text-sm text-slate-400">
          <X class="size-4" />取消
        </button>
        <button @click="toggleFavorite"
          class="flex items-center gap-1 rounded-lg border border-slate-700 px-3 py-1.5 text-sm"
          :class="favorited ? 'border-amber-500 text-amber-400' : 'text-slate-400 hover:text-slate-200'">
          <Star class="size-4" />{{ favorited ? "已收藏" : "收藏" }}
        </button>
        <RouterLink to="/explore" class="ml-auto flex items-center gap-1 text-sm text-slate-500 hover:text-slate-300">
          <ArrowLeft class="size-4" />返回广场
        </RouterLink>
      </div>
      <div v-if="conflict"
        class="flex items-center gap-3 rounded-xl border border-amber-600/40 bg-amber-600/15 px-4 py-3 text-sm text-amber-300">
        <TriangleAlert class="size-5 shrink-0" />
        <span class="flex-1">该联系人当前没有空闲的独立网关账号，为保证不串线已禁用立即聊天。</span>
        <a href="https://web.telegram.org/" target="_blank"
          class="flex items-center gap-1 rounded-lg border border-amber-500/50 px-3 py-1.5 text-xs hover:bg-amber-600/25">
          <ExternalLink class="size-3.5" />官方客户端直连
        </a>
      </div>
      <p v-if="!editing" class="text-3xl font-bold text-sky-400">
        {{ item.price_amount != null ? item.price_amount : "?" }}
        <span class="text-base text-slate-400">{{ item.price_currency }}</span>
      </p>
      <template v-if="!editing">
        <!-- 身体档案网格：缺失的指标不渲染单个格子 -->
        <section v-if="profileMetrics.length"
          class="grid grid-cols-4 gap-2 sm:grid-cols-4">
          <div v-for="metric in profileMetrics" :key="metric.label"
            class="rounded-xl border border-slate-800 bg-slate-900 px-2 py-3 text-center">
            <p class="flex items-center justify-center gap-1 text-[11px] text-slate-500">
              <Ruler class="size-3" />{{ metric.label }}
            </p>
            <p class="mt-0.5 truncate text-lg font-bold text-slate-100">{{ metric.value }}</p>
          </div>
        </section>

        <!-- 阶梯资费看板 -->
        <section v-if="tariffGroups.length"
          class="rounded-xl border border-slate-800 bg-slate-900 p-4">
          <h3 class="mb-2.5 flex items-center gap-1.5 text-sm font-semibold text-slate-300">
            <Wallet class="size-4 text-sky-400" />阶梯资费
          </h3>
          <div class="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <div v-for="group in tariffGroups" :key="group.label"
              class="rounded-lg border border-slate-800 bg-slate-950 p-2.5">
              <p class="text-[11px] font-semibold" :class="group.tone">{{ group.label }}</p>
              <p v-for="(line, i) in group.lines" :key="i"
                class="mt-1 break-words text-xs leading-5 text-slate-200">{{ line }}</p>
            </div>
          </div>
        </section>

        <!-- 服务与特色标签云 -->
        <section v-if="serviceTags.length"
          class="rounded-xl border border-slate-800 bg-slate-900 p-4">
          <h3 class="mb-2.5 flex items-center gap-1.5 text-sm font-semibold text-slate-300">
            <Sparkles class="size-4 text-sky-400" />服务与特色
          </h3>
          <div class="flex flex-wrap gap-1.5">
            <span v-for="tag in serviceTags" :key="tag"
              class="rounded-full bg-slate-800 px-3 py-1 text-xs text-slate-300">{{ tag }}</span>
          </div>
        </section>

        <!-- 禁忌规范警示栏 -->
        <section v-if="restrictionTags.length"
          class="rounded-xl border border-rose-600/40 bg-rose-600/10 p-4">
          <h3 class="mb-2.5 flex items-center gap-1.5 text-sm font-semibold text-rose-400">
            <TriangleAlert class="size-4" />禁忌规范
          </h3>
          <div class="flex flex-wrap gap-1.5">
            <span v-for="tag in restrictionTags" :key="tag"
              class="rounded-full border border-rose-500/40 bg-rose-500/15 px-3 py-1 text-xs font-medium text-rose-300">{{ tag }}</span>
          </div>
        </section>

        <!-- 基本信息：状态 / 在岗 / 发布时间 / 联系方式 -->
        <section class="rounded-xl border border-slate-800 bg-slate-900 p-4">
          <dl class="grid grid-cols-2 gap-x-4 gap-y-3 text-sm">
            <div><dt class="text-xs text-slate-500">状态</dt>
              <dd class="mt-0.5">{{ statusText() }}</dd></div>
            <div><dt class="text-xs text-slate-500">在岗</dt>
              <dd class="mt-0.5">{{ dutyText() ?? "—" }}</dd></div>
            <div class="col-span-2"><dt class="text-xs text-slate-500">发布时间</dt>
              <dd class="mt-0.5">{{ formatTime(item.published_at) ?? "—" }}</dd></div>
            <div class="col-span-2"><dt class="text-xs text-slate-500">联系方式</dt>
              <dd class="mt-1 flex flex-wrap items-center gap-3">
                <template v-if="session.isAdmin && item.contact_username">
                  <span>@{{ item.contact_username }}</span>
                </template>
                <template v-else>
                  <span class="text-slate-500">通过立即聊天联系</span>
                </template>
                <button @click="startChat"
                  class="nx-grad flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-semibold text-white hover:opacity-90">
                  <MessageCircle class="size-4" />立即聊天
                </button>
              </dd></div>
          </dl>
        </section>

        <!-- 管理员专属：OCR 溯源抽屉 -->
        <details v-if="session.isAdmin"
          class="rounded-xl border border-slate-800 bg-slate-900 open:pb-4">
          <summary class="flex cursor-pointer select-none items-center gap-1.5 px-4 py-3 text-sm font-semibold text-slate-300 [&::-webkit-details-marker]:hidden">
            <ScanSearch class="size-4 text-sky-400" />OCR 溯源
            <ChevronDown class="ml-auto size-4 text-slate-500" />
          </summary>
          <div class="space-y-3 border-t border-slate-800 px-4 pt-3 text-xs">
            <p v-if="!ocrTrace.length" class="text-slate-500">本卡片没有已完成的 OCR 图像记录。</p>
            <div v-for="card in ocrTrace" :key="card.media_path"
              class="space-y-2 rounded-lg border border-slate-800 bg-slate-950 p-3">
              <div class="flex items-center justify-between gap-2">
                <span class="min-w-0 truncate text-slate-300">{{ card.media_path.split("/").pop() }}</span>
                <span class="shrink-0 rounded-md bg-slate-800 px-2 py-0.5 text-[10px] text-slate-300">
                  {{ card.classification }} · {{ formatConf(card.confidence) }}
                </span>
              </div>
              <div v-for="(entry, key) in card.evidence" :key="key"
                class="flex items-start gap-2 border-b border-slate-800/60 pb-1.5 last:border-0 last:pb-0">
                <span class="w-28 shrink-0 text-slate-500">{{ key }}</span>
                <span class="min-w-0 flex-1 break-words text-slate-300">
                  {{ entry.line || entry.value }}
                </span>
                <span class="shrink-0 text-slate-500">{{ formatConf(entry.score ?? entry.ocr_confidence) }}</span>
              </div>
            </div>
            <div v-if="Object.keys(listingEvidence).length"
              class="space-y-2 rounded-lg border border-slate-800 bg-slate-950 p-3">
              <p class="text-[11px] font-semibold text-slate-400">卡片级抽取证据（evidence_json）</p>
              <div v-for="(entry, key) in listingEvidence" :key="key"
                class="flex items-start gap-2">
                <span class="w-28 shrink-0 text-slate-500">{{ key }}</span>
                <span class="min-w-0 flex-1 break-words text-slate-300">
                  {{ entry.value != null ? entry.value : entry }}
                  <template v-if="entry && typeof entry === 'object' && entry.line">
                    <span class="text-slate-500">（{{ entry.line }}）</span>
                  </template>
                </span>
                <span v-if="entry && typeof entry === 'object'" class="shrink-0 text-slate-500">
                  {{ entry.src }}<template v-if="entry.ocr_confidence != null"> · {{ formatConf(entry.ocr_confidence) }}</template>
                </span>
              </div>
            </div>
          </div>
        </details>
      </template>
      <p v-if="!editing && item.description"
        class="whitespace-pre-wrap rounded-xl border border-slate-800 bg-slate-900 p-4 text-sm leading-relaxed text-slate-300">
        {{ item.description }}
      </p>
      <div v-if="editing && editForm" class="space-y-3 rounded-xl border border-slate-800 bg-slate-900 p-4">
        <p class="text-xs leading-relaxed text-slate-500">自动识别与排序优先；这里的修改仅覆盖当前卡片。在线和在岗状态仍由自动采集维护。</p>
        <label class="block space-y-1 text-sm"><span class="text-slate-400">昵称</span>
          <input v-model="editForm.nickname" maxlength="128" class="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2" />
        </label>
        <div class="grid grid-cols-2 gap-2">
          <label class="space-y-1 text-sm"><span class="text-slate-400">城区</span>
            <input v-model="editForm.district" maxlength="64" class="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2" />
          </label>
          <label class="space-y-1 text-sm"><span class="text-slate-400">价格</span>
            <input v-model.number="editForm.price_amount" type="number" min="0" step="0.01" class="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2" />
          </label>
          <label class="col-span-2 space-y-1 text-sm"><span class="text-slate-400">货币单位</span>
            <input v-model="editForm.price_currency" maxlength="16" class="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2" />
          </label>
        </div>
        <label class="block space-y-1 text-sm"><span class="text-slate-400">介绍</span>
          <textarea v-model="editForm.description" rows="6" class="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2" />
        </label>
        <div class="flex items-center justify-between gap-2">
          <h3 class="text-sm font-semibold">媒体顺序与公开范围</h3>
          <button @click="restoreAutomaticMedia" class="flex items-center gap-1 text-xs text-sky-400 hover:text-sky-300">
            <RotateCcw class="size-3.5" />恢复自动识别与排序
          </button>
        </div>
        <div v-for="(media, index) in editForm.media" :key="media.path"
          class="flex items-center gap-2 rounded-lg border border-slate-800 bg-slate-950 p-2">
          <img v-if="media.kind === 'photo' || media.poster"
            :src="mediaUrl(media.kind === 'photo' ? media.path : media.poster)"
            class="size-14 shrink-0 rounded-md object-cover" />
          <div v-else class="flex size-14 shrink-0 items-center justify-center rounded-md bg-slate-800 text-xs">视频</div>
          <div class="min-w-0 flex-1">
            <p class="truncate text-xs text-slate-300">{{ media.ocr_classification || media.kind }} · {{ media.path.split('/').pop() }}</p>
            <label v-if="media.kind === 'photo'" class="mt-1 flex items-center gap-2 text-xs text-slate-500">
              <span>可见范围</span>
              <select :value="media.visibility || 'pending'" @change="setVisibility(index, $event.target.value)"
                class="rounded border border-slate-700 bg-slate-900 px-1.5 py-1 text-slate-300">
                <option value="public">公开</option><option value="admin">仅管理员</option>
              </select>
            </label>
          </div>
          <div class="flex shrink-0 flex-col">
            <button @click="moveMedia(index, -1)" :disabled="index === 0" aria-label="上移"
              class="rounded p-1 text-slate-400 disabled:opacity-30"><ChevronUp class="size-4" /></button>
            <button @click="moveMedia(index, 1)" :disabled="index === editForm.media.length - 1" aria-label="下移"
              class="rounded p-1 text-slate-400 disabled:opacity-30"><ChevronDown class="size-4" /></button>
          </div>
        </div>
        <p v-if="editError" class="text-sm text-rose-400">{{ editError }}</p>
      </div>
    </div>
    <BackToTop />

    <!-- 手机吸底拇指区：收藏 + 立即聊天 -->
    <div v-if="!editing"
      class="nx-glass fixed inset-x-0 z-40 flex gap-2.5 border-t border-slate-800 p-3 lg:hidden"
      style="bottom: calc(62px + env(safe-area-inset-bottom, 0px));"
    >
      <button @click="toggleFavorite"
        class="flex w-14 items-center justify-center rounded-xl border text-xl"
        :class="favorited
          ? 'border-amber-500/60 bg-amber-500/10 text-amber-400'
          : 'border-slate-700 bg-slate-900 text-slate-400'"
        :aria-label="favorited ? '已收藏' : '收藏'">
        <Star class="size-5" />
      </button>
      <button @click="startChat"
        class="nx-grad flex flex-1 items-center justify-center gap-2 rounded-xl py-3.5 text-[15px] font-bold text-white shadow-lg shadow-sky-400/20">
        <MessageCircle class="size-5" />立即聊天
      </button>
    </div>
  </div>
</template>
