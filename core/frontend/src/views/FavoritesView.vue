<script setup>
import { onMounted, ref } from "vue";
import { api } from "../api/client";
import ListingCard from "../components/ListingCard.vue";

const items = ref([]);

function onToggleFav({ id, favorited, error }) {
  if (!error && !favorited) items.value = items.value.filter((item) => item.id !== id);
}

onMounted(async () => {
  items.value = (await api("/api/favorites")).items;
});
</script>

<template>
  <div class="space-y-5">
    <h2 class="text-lg font-semibold">我的收藏</h2>
    <div v-if="items.length" class="nx-listing-grid">
      <ListingCard v-for="item in items" :key="item.id" :item="item" favoritable
        favorited @toggle-favorite="onToggleFav" />
    </div>
    <div v-else class="py-24 text-center text-slate-500">还没有收藏，去广场逛逛</div>
  </div>
</template>
