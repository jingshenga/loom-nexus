<script setup>
import { RouterLink, useRoute } from "vue-router";

const route = useRoute();
</script>

<template>
  <div class="mb-4 flex min-w-0 items-center gap-2 text-xs text-slate-500">
    <RouterLink to="/admin" class="shrink-0 hover:text-sky-400">管理总览</RouterLink>
    <span aria-hidden="true">/</span>
    <span class="truncate text-slate-300">{{ route.meta.title || "管理页面" }}</span>
  </div>
</template>
