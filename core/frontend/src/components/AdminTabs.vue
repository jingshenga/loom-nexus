<script setup>
import { computed } from "vue";
import { useRoute } from "vue-router";
const route = useRoute();
const title = computed(() => route.meta.title || "管理总览");
</script>

<template>
  <div class="mb-4 text-xs text-slate-500" aria-label="当前位置">管理后台 / {{ title }}</div>
</template>
