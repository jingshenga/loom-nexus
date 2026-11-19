<script setup>
import { computed } from "vue";
import { useRoute } from "vue-router";
import AdminTabs from "../components/AdminTabs.vue";

const props = defineProps({ tabs: { type: Array, required: true } });
const route = useRoute();
const current = computed(() => props.tabs.find((tab) => tab.key === route.query.section) || props.tabs[0]);

function tabLink(tab) {
  return current.value.key === tab.key ? route.fullPath
    : { path: route.path, query: tab === props.tabs[0] ? {} : { section: tab.key } };
}
</script>

<template>
  <AdminTabs />
  <nav class="mb-4 flex flex-wrap gap-2 border-b border-slate-800 pb-3" aria-label="中心功能">
    <RouterLink v-for="tab in tabs" :key="tab.key" :to="tabLink(tab)"
      :aria-current="tab.key === current.key ? 'page' : undefined"
      class="rounded-lg px-3 py-2 text-sm"
      :class="tab.key === current.key ? 'bg-sky-500/15 text-sky-300' : 'text-slate-400 hover:bg-slate-800'">
      {{ tab.label }}
    </RouterLink>
  </nav>
  <component :is="current.component" :key="current.key" />
</template>
