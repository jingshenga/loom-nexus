<script setup>
import { computed, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { Star, ArrowLeft, MessageCircle, TriangleAlert, ExternalLink, Pencil, Save, X, RotateCcw, ArchiveRestore, ShieldAlert, ChevronDown } from "lucide-vue-next";
import { api } from "../api/client";
import { useSession } from "../stores/session";
import MediaGallery from "../components/MediaGallery.vue";
import AdminMediaGrid from "../components/AdminMediaGrid.vue";
import BackToTop from "../components/BackToTop.vue";

const route = useRoute();
const router = useRouter();
const session = useSession();
const item = ref(null);
const favorited = ref(false);
const error = ref("");
const withdrawError = ref("");
const conflict = ref(false);
const editing = ref(false);
const savingEdit = ref(false);
const withdrawing = ref(false);
const editError = ref("");
const editForm = ref(null);
const manualOrder = ref(false);
const showEvidence = ref(false);
const attributes = computed(() => item.value?.attributes || {});
const profileFacts = computed(() => {
  const profile = attributes.value.profile || {};
  return [
    ["年龄", profile.age != null ? `${profile.age}岁` : null],
    ["身高", profile.height != null ? `${profile.height}cm` : null],
    ["体重", profile.weight != null ? `${profile.weight}kg` : null],
    ["胸围", profile.bust],
    ["城区", item.value?.district],
  ].filter(([, value]) => value != null && value !== "");
});
const tariffs = computed(() => attributes.value.tariffs || {});
const hasTariffs = computed(() => ["p1", "p2", "cash_note", "raw_fees"]
  .some((key) => tariffs.value[key]?.length));
const extraFees = computed(() => (tariffs.value.raw_fees || []).filter((line) =>
  !(tariffs.value.p1 || []).includes(line) && !(tariffs.value.p2 || []).includes(line)));
const evidenceRows = (entry) => Object.entries(entry.fields || {}).map(([key, value]) => ({
  key, value: Array.isArray(value) ? value.join("、") : String(value),
  line: entry.evidence?.[key]?.line,
  score: entry.evidence?.[key]?.score,
}));

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

function restoreAutomaticMedia() {
  for (const media of editForm.value.media) {
    if (media.image_data) {
      media.visibility = "admin";
      media.visibility_override = true;
      continue;
    }
    delete media.manual_order;
    delete media.visibility_override;
    if (media.kind === "photo") {
      media.visibility = media.ocr_classification === "profile_photo"
        ? "public"
        : media.ocr_classification === "info_card" ? "admin"
          : media.ocr_classification ? "pending" : (media.visibility || "admin");
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
          image_data: media.image_data,
          image_mime: media.image_mime,
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

async function withdrawListing() {
  if (!item.value || withdrawing.value) return;
  if (!window.confirm(`将「${item.value.nickname || "该卡片"}」从广场下架？`)) return;
  withdrawing.value = true;
  try {
    await api(`/api/admin/review/${item.value.id}/withdraw`, { method: "POST" });
    await router.push("/explore");
  } catch (err) {
    withdrawError.value = err.message || "下架失败，请重试";
  } finally {
    withdrawing.value = false;
  }
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

</script>

<template>
  <div v-if="error" class="py-24 text-center text-slate-500">{{ error }}</div>
  <div v-else-if="item" class="grid gap-6 pb-[calc(150px+env(safe-area-inset-bottom,0px))] lg:grid-cols-2 lg:pb-0">
    <AdminMediaGrid v-if="editing && editForm" v-model:media="editForm.media" editable @order-change="manualOrder = true" />
    <MediaGallery v-else :media="item.media" fixed-mobile-frame />
    <div class="min-w-0 space-y-4">
      <div class="flex flex-wrap items-center gap-2">
        <h2 class="flex min-w-0 items-center gap-2 break-words text-2xl font-bold">
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
        <button v-if="session.isAdmin && !editing" @click="withdrawListing" :disabled="withdrawing"
          class="flex items-center gap-1 rounded-lg border border-amber-700 px-3 py-1.5 text-sm text-amber-300 disabled:opacity-50">
          <ArchiveRestore class="size-4" />{{ withdrawing ? "下架中" : "下架" }}
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
      <p v-if="withdrawError" class="rounded-lg border border-rose-700/50 bg-rose-900/20 px-3 py-2 text-sm text-rose-300">{{ withdrawError }}</p>
      <div v-if="conflict"
        class="flex items-center gap-3 rounded-xl border border-amber-600/40 bg-amber-600/15 px-4 py-3 text-sm text-amber-300">
        <TriangleAlert class="size-5 shrink-0" />
        <span class="flex-1">该联系人当前没有空闲的独立网关账号，为保证不串线已禁用立即聊天。</span>
        <a href="https://web.telegram.org/" target="_blank"
          class="flex items-center gap-1 rounded-lg border border-amber-500/50 px-3 py-1.5 text-xs hover:bg-amber-600/25">
          <ExternalLink class="size-3.5" />官方客户端直连
        </a>
      </div>
      <template v-if="!editing">
        <section v-if="profileFacts.length" class="rounded-2xl border border-slate-800 bg-slate-900 p-4">
          <h3 class="mb-3 text-sm font-semibold text-slate-200">基础档案</h3>
          <div class="grid grid-cols-2 gap-2 sm:grid-cols-3">
            <div v-for="([label, value]) in profileFacts" :key="label" class="min-w-0 rounded-xl bg-slate-800/70 px-3 py-2.5">
              <p class="text-xs text-slate-500">{{ label }}</p>
              <p class="mt-1 break-words font-semibold text-slate-100">{{ value }}</p>
            </div>
          </div>
        </section>
        <section v-if="item.price_amount != null || hasTariffs" class="min-w-0 rounded-2xl border border-sky-500/20 bg-slate-900 p-4">
          <h3 class="mb-3 text-sm font-semibold text-slate-200">资费与课表</h3>
          <p v-if="item.price_amount != null" class="mb-3 text-2xl font-bold text-sky-400">
            {{ item.price_amount }} <span class="text-sm font-normal text-slate-400">{{ item.price_currency }}</span>
          </p>
          <div v-if="tariffs.p1?.length || tariffs.p2?.length" class="grid gap-2 sm:grid-cols-2">
            <div v-for="([label, lines]) in [['1P 套餐', tariffs.p1], ['2P 套餐', tariffs.p2]]" :key="label" v-show="lines?.length"
              class="min-w-0 rounded-xl border border-slate-700 bg-slate-950/50 p-3">
              <p class="mb-2 text-xs font-semibold text-sky-300">{{ label }}</p>
              <p v-for="line in lines || []" :key="line" class="break-words text-sm leading-relaxed text-slate-200">{{ line }}</p>
            </div>
          </div>
          <div v-if="extraFees.length" class="mt-3 space-y-1 text-sm text-slate-400">
            <p v-for="line in extraFees" :key="line" class="break-words">{{ line }}</p>
          </div>
          <div v-if="tariffs.cash_note?.length" class="mt-3 rounded-lg border border-amber-600/40 bg-amber-600/10 p-3 text-sm text-amber-200">
            <p v-for="line in tariffs.cash_note" :key="line" class="break-words">{{ line }}</p>
          </div>
        </section>
        <section v-if="attributes.services?.length" class="rounded-2xl border border-slate-800 bg-slate-900 p-4">
          <h3 class="mb-3 text-sm font-semibold text-slate-200">服务与特色</h3>
          <div class="flex flex-wrap gap-2">
            <span v-for="line in attributes.services" :key="line" class="max-w-full break-words rounded-lg border border-violet-500/30 bg-violet-500/10 px-2.5 py-1.5 text-sm text-violet-200">{{ line }}</span>
          </div>
        </section>
        <section v-if="attributes.restrictions?.length" class="rounded-2xl border border-amber-600/40 bg-amber-600/10 p-4">
          <h3 class="mb-3 flex items-center gap-2 text-sm font-semibold text-amber-200"><ShieldAlert class="size-4" />服务禁忌与规范</h3>
          <div class="flex flex-wrap gap-2">
            <span v-for="line in attributes.restrictions" :key="line" class="max-w-full break-words rounded-lg border px-2.5 py-1.5 text-sm"
              :class="line.startsWith('禁') ? 'border-rose-600/50 bg-rose-600/15 text-rose-200' : 'border-amber-600/40 bg-amber-600/10 text-amber-200'">{{ line }}</span>
          </div>
        </section>
        <section class="rounded-2xl border border-slate-800 bg-slate-900 p-4 text-sm">
          <h3 class="mb-3 font-semibold text-slate-200">联系与状态</h3>
          <div class="space-y-2 text-slate-400">
            <p>状态：{{ item.online_tier === 'online' ? '在线' : item.online_tier === 'recent' ? '近期活跃' : '未知' }}</p>
            <p v-if="item.duty_label">签到：{{ item.duty_label === 'today' ? '今日签到' : '昨日签到' }}</p>
            <p v-if="item.published_at">发布时间：{{ new Date(item.published_at).toLocaleString('zh-CN') }}</p>
            <p class="break-words">联系方式：{{ session.isAdmin && item.contact_username ? `@${item.contact_username}` : '通过立即聊天联系' }}</p>
          </div>
          <div class="mt-3 flex flex-wrap gap-2">
            <a v-if="item.bot_url" :href="item.bot_url" target="_blank" rel="noreferrer"
              class="rounded-lg border border-violet-700/60 px-3 py-1.5 text-xs text-violet-300">联系 Bot</a>
            <button @click="startChat" class="nx-grad hidden items-center gap-1.5 rounded-lg px-3 py-1.5 font-semibold text-white lg:flex">
              <MessageCircle class="size-4" />立即聊天
            </button>
          </div>
        </section>
        <section v-if="session.isAdmin && (item.ocr_evidence?.length || Object.keys(item.evidence_json || {}).length)"
          class="min-w-0 rounded-2xl border border-slate-700 bg-slate-900 p-4">
          <button @click="showEvidence = !showEvidence" :aria-expanded="showEvidence"
            class="flex w-full items-center justify-between text-sm font-semibold text-slate-200">
            查看 OCR 证据 <ChevronDown class="size-4 transition" :class="showEvidence ? 'rotate-180' : ''" />
          </button>
          <div v-if="showEvidence" class="mt-4 space-y-4">
            <div v-for="(entry, index) in item.ocr_evidence || []" :key="`${entry.media_path}-${index}`" class="min-w-0 rounded-xl bg-slate-950/60 p-3">
              <p class="mb-2 break-all text-xs text-slate-400">{{ entry.media_path }} · 图像置信度 {{ entry.confidence == null ? '未知' : `${Math.round(entry.confidence * 100)}%` }}</p>
              <div v-for="row in evidenceRows(entry)" :key="row.key" class="border-t border-slate-800 py-2 text-xs">
                <p class="break-words text-slate-200">{{ row.key }}：{{ row.value }}</p>
                <p v-if="row.line" class="mt-1 break-words text-slate-400">原文：{{ row.line }}<span v-if="row.score != null"> · {{ Math.round(row.score * 100) }}%</span></p>
              </div>
            </div>
            <pre v-if="Object.keys(item.evidence_json || {}).length" class="max-h-60 overflow-auto whitespace-pre-wrap break-all rounded-xl bg-slate-950/60 p-3 text-xs text-slate-400">{{ JSON.stringify(item.evidence_json, null, 2) }}</pre>
          </div>
        </section>
        <p v-if="session.isAdmin && item.description" class="whitespace-pre-wrap break-words rounded-xl border border-slate-800 bg-slate-900 p-4 text-sm leading-relaxed text-slate-300">{{ item.description }}</p>
      </template>
      <div v-if="editing && editForm" class="space-y-3 rounded-xl border border-slate-800 bg-slate-900 p-4">
        <p class="text-xs leading-relaxed text-slate-500">自动识别与排序优先；这里的修改仅覆盖当前卡片。在线和签到状态仍由自动采集维护。</p>
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
          <h3 class="text-sm font-semibold">图片与顺序</h3>
          <button @click="restoreAutomaticMedia" class="flex items-center gap-1 text-xs text-sky-400 hover:text-sky-300">
            <RotateCcw class="size-3.5" />恢复自动识别与排序
          </button>
        </div>
        <p class="text-xs text-slate-500">移除只会从这张卡片中取消展示，原始媒体文件保留。新增图片默认仅管理员可见。</p>
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
