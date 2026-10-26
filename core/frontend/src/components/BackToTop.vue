<script setup>
import { onMounted, onUnmounted, ref } from "vue";
import { ChevronUp } from "lucide-vue-next";

// floating back-to-top button; appears after one viewport of scrolling
// and sits above the mobile tab bar + safe area
const show = ref(false);

function onScroll() {
  show.value = window.scrollY > window.innerHeight * 0.9;
}

function toTop() {
  window.scrollTo({ top: 0, behavior: "smooth" });
}

onMounted(() => window.addEventListener("scroll", onScroll, { passive: true }));
onUnmounted(() => window.removeEventListener("scroll", onScroll));
</script>

<template>
  <button
    v-show="show" @click="toTop" aria-label="返回顶部"
    class="nx-glass fixed right-4 z-30 flex size-11 items-center justify-center rounded-full border border-slate-700/70 text-slate-200 shadow-lg transition-opacity duration-200 active:scale-90"
    :class="show ? 'opacity-100' : 'opacity-0 pointer-events-none'"
    style="bottom: calc(76px + env(safe-area-inset-bottom, 0px))"
  >
    <ChevronUp class="size-5" />
  </button>
</template>
