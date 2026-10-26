<script setup>
import { computed, onMounted, onUnmounted, ref } from "vue";
import { mediaUrl } from "../api/client";
import { ImageIcon, Clapperboard, ChevronLeft, ChevronRight } from "lucide-vue-next";

const props = defineProps({ media: { type: Array, default: () => [] } });
const active = ref(0);

// photos first, videos last (defensive: also enforced at ingestion)
const sorted = computed(() =>
  [...(props.media || [])].sort((a, b) =>
    (a.kind === "photo" ? 0 : 1) - (b.kind === "photo" ? 0 : 1),
  ),
);
const counts = computed(() => {
  const photos = sorted.value.filter((m) => m.kind === "photo").length;
  const videos = sorted.value.filter((m) => m.kind === "video").length;
  const parts = [];
  if (photos) parts.push(`${photos} 图`);
  if (videos) parts.push(`${videos} 视频`);
  return parts.join(" · ");
});
const current = computed(() => sorted.value[Math.min(active.value, sorted.value.length - 1)]);
const multi = computed(() => sorted.value.length > 1);

function step(delta) {
  if (!multi.value) return;
  active.value = (active.value + delta + sorted.value.length) % sorted.value.length;
}

// touch swipe (H5): horizontal drag over 40px flips the page
let touchX = 0;
let touchY = 0;
function onTouchStart(e) {
  touchX = e.changedTouches[0].clientX;
  touchY = e.changedTouches[0].clientY;
}
function onTouchEnd(e) {
  const dx = e.changedTouches[0].clientX - touchX;
  const dy = e.changedTouches[0].clientY - touchY;
  if (Math.abs(dx) > 40 && Math.abs(dx) > Math.abs(dy) * 1.5) {
    step(dx < 0 ? 1 : -1);
  }
}

function onKey(e) {
  if (e.key === "ArrowLeft") step(-1);
  else if (e.key === "ArrowRight") step(1);
}

onMounted(() => window.addEventListener("keydown", onKey));
onUnmounted(() => window.removeEventListener("keydown", onKey));
</script>

<template>
  <div v-if="sorted.length" class="space-y-2">
    <div class="relative overflow-hidden rounded-xl border border-slate-800 bg-black"
      @touchstart.passive="onTouchStart" @touchend.passive="onTouchEnd">
      <img
        v-if="current && current.kind === 'photo'"
        :src="mediaUrl(current.path)"
        class="max-h-[70vh] w-full object-contain"
        loading="lazy"
      />
      <video
        v-else-if="current"
        :src="mediaUrl(current.path)"
        :poster="current.poster ? mediaUrl(current.poster) : undefined"
        controls
        playsinline
        class="max-h-[70vh] w-full object-contain"
      ></video>
      <!-- 桌面翻页箭头（触屏用滑动） -->
      <button v-if="multi" @click="step(-1)" aria-label="上一张"
        class="absolute left-2 top-1/2 hidden -translate-y-1/2 rounded-full bg-slate-950/70 p-2.5 text-slate-200 backdrop-blur transition hover:bg-slate-900 sm:block">
        <ChevronLeft class="size-5" />
      </button>
      <button v-if="multi" @click="step(1)" aria-label="下一张"
        class="absolute right-2 top-1/2 hidden -translate-y-1/2 rounded-full bg-slate-950/70 p-2.5 text-slate-200 backdrop-blur transition hover:bg-slate-900 sm:block">
        <ChevronRight class="size-5" />
      </button>
      <!-- 页点指示器（手机主用） -->
      <div v-if="multi" class="absolute bottom-2.5 left-1/2 flex -translate-x-1/2 gap-1.5">
        <span v-for="(m, i) in sorted" :key="i"
          class="h-1.5 rounded-full transition-all"
          :class="i === active ? 'w-4 bg-sky-400' : 'w-1.5 bg-white/30'"></span>
      </div>
      <span v-if="counts"
        class="absolute right-2 top-2 rounded-lg bg-slate-950/80 px-2 py-1 text-xs text-slate-300 backdrop-blur">
        {{ counts }}
      </span>
    </div>
    <div v-if="multi" class="nx-noscroll flex gap-2 overflow-x-auto pb-1">
      <button
        v-for="(m, i) in sorted" :key="i" @click="active = i"
        class="relative size-16 shrink-0 overflow-hidden rounded-lg border transition"
        :class="i === active ? 'border-sky-400' : 'border-slate-800 opacity-60 hover:opacity-100'"
      >
        <img v-if="m.kind === 'photo' || m.poster" :src="mediaUrl(m.kind === 'photo' ? m.path : m.poster)" class="size-full object-cover" loading="lazy" />
        <div v-else class="flex size-full items-center justify-center bg-slate-900 text-slate-400">
          <Clapperboard class="size-5" />
        </div>
        <span v-if="m.kind === 'photo'" class="absolute bottom-0.5 left-0.5 text-slate-200/80">
          <ImageIcon class="size-3" />
        </span>
      </button>
    </div>
  </div>
</template>
