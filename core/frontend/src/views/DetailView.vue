<script setup>
import { onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { Star, ArrowLeft, MessageCircle, TriangleAlert, ExternalLink, Pencil, Save, X, RotateCcw } from "lucide-vue-next";
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

const fieldRowsTop = (it) => [
  ["昵称", it.nickname],
  ["城区", it.district],
  ["价格", it.price_amount != null ? `${it.price_amount} ${it.price_currency}` : null],
];
const fieldRowsBottom = (it) => [
  ["状态", it.online_tier === "online" ? "在线" : it.online_tier === "recent" ? "近期活跃" : "未知"],
  ["签到", it.duty_label === "today" ? "今日签到" : it.duty_label === "yesterday" ? "昨日签到" : null],
  ["发布时间", it.published_at ? new Date(it.published_at).toLocaleString("zh-CN") : null],
];
</script>

<template>
  <div v-if="error" class="py-24 text-center text-slate-500">{{ error }}</div>
  <div v-else-if="item" class="grid gap-6 pb-[calc(150px+env(safe-area-inset-bottom,0px))] lg:grid-cols-2 lg:pb-0">
    <AdminMediaGrid v-if="editing && editForm" v-model:media="editForm.media" editable @order-change="manualOrder = true" />
    <MediaGallery v-else :media="item.media" fixed-mobile-frame />
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
      <table v-if="!editing" class="w-full overflow-hidden rounded-xl border border-slate-800 text-sm">
        <tbody>
          <tr v-for="([label, value], i) in fieldRowsTop(item)" :key="label"
            class="border-b border-slate-800" :class="i % 2 ? 'bg-slate-900/60' : ''">
            <td class="w-24 px-4 py-2.5 text-slate-500">{{ label }}</td>
            <td class="px-4 py-2.5">{{ value ?? "—" }}</td>
          </tr>
          <!-- 联系方式行：管理员看真实号码；普通用户只见聊天入口 -->
          <tr class="border-b border-slate-800 bg-slate-900/60">
            <td class="w-24 px-4 py-2.5 text-slate-500">联系方式</td>
            <td class="flex flex-wrap items-center gap-3 px-4 py-2.5">
              <template v-if="session.isAdmin && item.contact_username">
                <span>@{{ item.contact_username }}</span>
              </template>
              <template v-else>
                <span class="text-slate-500">通过立即聊天联系</span>
              </template>
              <button @click="startChat"
                class="nx-grad hidden items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-semibold text-white hover:opacity-90 lg:flex">
                <MessageCircle class="size-4" />立即聊天
              </button>
            </td>
          </tr>
          <tr v-for="([label, value], i) in fieldRowsBottom(item)" :key="label"
            class="border-b border-slate-800 last:border-0" :class="i % 2 ? '' : 'bg-slate-900/60'">
            <td class="w-24 px-4 py-2.5 text-slate-500">{{ label }}</td>
            <td class="px-4 py-2.5">{{ value ?? "—" }}</td>
          </tr>
        </tbody>
      </table>
      <p v-if="!editing && item.description"
        class="whitespace-pre-wrap rounded-xl border border-slate-800 bg-slate-900 p-4 text-sm leading-relaxed text-slate-300">
        {{ item.description }}
      </p>
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
