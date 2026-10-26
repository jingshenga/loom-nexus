<script setup>
import { onMounted, ref } from "vue";
import { api } from "../api/client";
import ListingCard from "../components/ListingCard.vue";

const items = ref([]);

onMounted(async () => {
  items.value = (await api("/api/favorites")).items;
});
</script>

<template>
  <div class="space-y-5">
    <h2 class="text-lg font-semibold">我的收藏</h2>
    <div v-if="items.length" class="grid grid-cols-1 gap-4 sm:grid-cols-2 md:grid-cols-3 xl:grid-cols-4">
      <ListingCard v-for="item in items" :key="item.id" :item="item" favoritable />
    </div>
    <div v-else class="py-24 text-center text-slate-500">还没有收藏，去广场逛逛</div>
  </div>
</template>
