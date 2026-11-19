<script setup>
import { RouterLink } from "vue-router";
import { MapPin, Star, Images, MessageCircle, Play, ShieldAlert } from "lucide-vue-next";
import { api, mediaUrl } from "../api/client";

const props = defineProps({
  item: { type: Object, required: true },
  favoritable: { type: Boolean, default: false },
  favorited: { type: Boolean, default: false },
});
const emit = defineEmits(["toggle-favorite"]);
// cover = best photo (already score-ordered server-side) or a video
// with a poster frame; focus drives the face-aware cover crop
const cover = () => props.item.cover ||
  (props.item.media || []).find((m) => m.kind === "photo") ||
  (props.item.media || []).find((m) => m.kind === "video" && m.poster) ||
  null;
const isPosterCover = () => cover() && cover().kind === "video";
const mediaCount = () => props.item.media_count ?? (props.item.media || []).length;
const focusStyle = () => {
  const c = cover() || {};
  return {
    objectPosition: `${((c.focus_x ?? 0.5) * 100).toFixed(1)}% ${((c.focus_y ?? 0.3) * 100).toFixed(1)}%`,
  };
};

const tierText = () =>
  props.item.online_tier === "online" ? "在线"
    : props.item.online_tier === "recent" ? "近期活跃" : "状态未知";
const tierClass = () =>
  props.item.online_tier === "online" ? "bg-emerald-400"
    : props.item.online_tier === "recent" ? "bg-amber-400" : "bg-slate-400";
const dutyText = () =>
  props.item.duty_label === "today" ? "今日签到"
    : props.item.duty_label === "yesterday" ? "昨日签到" : "";
const profileText = () => {
  const profile = props.item.attributes?.profile || {};
  return [
    profile.age != null ? `${profile.age}岁` : null,
    profile.height != null ? `${profile.height}cm` : null,
    profile.weight != null ? `${profile.weight}kg` : null,
    profile.bust,
  ].filter(Boolean).join(" · ");
};
const tariffHint = () => {
  const lines = props.item.attributes?.tariffs?.p1 || [];
  for (const line of lines) {
    const match = String(line).match(/1\s*p\s*[:：]?\s*[¥￥]?\s*(\d{2,6})|[¥￥]?\s*(\d{2,6})\s*元?\s*\/\s*1\s*p/i);
    if (match) return `${match[1] || match[2]}起/1P`;
  }
  return "";
};
const hasRestriction = () => (props.item.attributes?.restrictions || []).some((line) => line.startsWith("禁"));

async function toggleFavorite() {
  // optimistic: the caller owns the state and updates instantly; on
  // failure the caller reverts (the API result decides the truth)
  try {
    const data = await api(`/api/favorites/${props.item.id}`, { method: "POST" });
    emit("toggle-favorite", { id: props.item.id, favorited: data.favorited });
  } catch {
    emit("toggle-favorite", { id: props.item.id, error: true });
  }
}
</script>

<template>
  <RouterLink
    :to="`/explore/${item.id}`"
    :id="`listing-card-${item.id}`"
    :data-listing-id="item.id"
    class="group block overflow-hidden rounded-2xl border border-slate-800 bg-slate-900 transition hover:border-sky-400/60 hover:shadow-lg hover:shadow-sky-400/5"
  >
    <div class="relative aspect-[4/5] bg-slate-800">
      <!-- cover crop keeps the detected face in frame via object-position -->
      <img
        v-if="cover()"
        :src="mediaUrl(isPosterCover() ? cover().poster : cover().path, { thumbnail: true })" :alt="item.nickname"
        class="size-full object-cover" :style="focusStyle()" loading="lazy"
      />
      <div v-else class="flex size-full items-center justify-center text-slate-600">
        <MessageCircle class="size-10 opacity-40" />
      </div>
      <!-- 封面是视频封面帧时叠加播放标 -->
      <span v-if="isPosterCover()"
        class="absolute inset-0 flex items-center justify-center">
        <span class="flex size-12 items-center justify-center rounded-full bg-slate-950/60 text-white backdrop-blur">
          <Play class="size-6" />
        </span>
      </span>
      <!-- 悬浮价格徽章 -->
      <span v-if="tariffHint() || item.price_amount != null"
        class="nx-price-chip absolute left-2.5 top-2.5 max-w-[calc(100%-5rem)] truncate rounded-xl px-2.5 py-0.5 text-xs sm:text-[15px]">
        <template v-if="tariffHint()">{{ tariffHint() }}</template>
        <template v-else>{{ item.price_amount }}<small class="ml-0.5 text-[10px] font-normal opacity-85">{{ item.price_currency }}</small></template>
      </span>
      <span v-if="mediaCount() > 1"
        class="absolute right-2.5 top-2.5 flex items-center gap-1 rounded-lg bg-slate-950/70 px-2 py-0.5 text-[10px] text-slate-200 backdrop-blur">
        <Images class="size-3" />{{ mediaCount() }}
      </span>
      <button v-if="favoritable" @click.prevent.stop="toggleFavorite"
        :aria-label="favorited ? '取消收藏' : '收藏'"
        class="absolute bottom-2.5 right-2.5 flex size-11 items-center justify-center rounded-2xl bg-slate-950/60 backdrop-blur active:scale-90 transition"
        :class="favorited ? 'text-amber-400' : 'text-slate-300 hover:text-amber-400'">
        <Star class="size-5" :class="favorited ? 'fill-amber-400' : ''" />
      </button>
      <span v-if="dutyText()"
        class="absolute bottom-2.5 left-2.5 rounded-lg px-2 py-1 text-[10px] font-semibold text-white shadow backdrop-blur"
        :class="item.duty_label === 'today' ? 'bg-emerald-600/85' : 'bg-amber-600/85'">
        {{ dutyText() }}
      </span>
    </div>
    <div class="min-w-0 space-y-1.5 p-2.5 sm:p-3">
      <div class="flex min-w-0 items-center gap-2">
        <span class="size-2 shrink-0 rounded-full ring-2 ring-slate-950/20"
          :class="tierClass()" :title="tierText()" :aria-label="tierText()"></span>
        <span class="min-w-0 truncate font-semibold text-slate-100">{{ item.nickname || "未命名" }}</span>
      </div>
      <div class="flex min-w-0 items-center gap-1 text-xs text-slate-500">
        <MapPin class="size-3 shrink-0" />
        <span class="truncate">{{ item.district || "位置未标注" }}</span>
      </div>
      <p v-if="profileText()" :title="profileText()" class="truncate rounded-full bg-slate-800 px-2 py-1 text-[11px] text-slate-300">
        {{ profileText() }}
      </p>
      <p v-if="hasRestriction()" class="flex items-center gap-1 text-[10px] text-amber-300">
        <ShieldAlert class="size-3 shrink-0" /> <span class="truncate">有服务禁忌，请查看详情</span>
      </p>
    </div>
  </RouterLink>
</template>
