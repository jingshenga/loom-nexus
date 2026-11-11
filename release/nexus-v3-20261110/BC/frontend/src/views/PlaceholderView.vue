<script setup>
defineProps({ title: String, note: String });
</script>

<template>
  <div class="flex min-h-[50vh] flex-col items-center justify-center gap-3 text-slate-400">
    <h2 class="text-xl font-semibold text-slate-200">{{ title }}</h2>
    <p>{{ note }}</p>
  </div>
</template>
