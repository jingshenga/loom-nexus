<script setup>
import { useRoute } from "vue-router";
import { adminCenters } from "../router";

const route = useRoute();
</script>

<template>
  <nav class="mb-4 flex flex-wrap gap-2 lg:hidden" aria-label="管理中心">
    <RouterLink v-for="center in adminCenters" :key="center.path" :to="center.path"
      :aria-current="route.path === center.path ? 'page' : undefined"
      class="rounded-full border px-3 py-1.5 text-xs"
      :class="route.path === center.path
        ? 'border-transparent nx-grad font-semibold text-white'
        : 'border-slate-700 bg-slate-900 text-slate-400'">
      {{ center.label }}
    </RouterLink>
  </nav>
</template>
