<script setup>
import { onMounted, onUnmounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { Search, MapPin } from "lucide-vue-next";
import BackToTop from "../components/BackToTop.vue";

const route = useRoute();
const router = useRouter();
import { api } from "../api/client";
import ListingCard from "../components/ListingCard.vue";

const items = ref([]);
const total = ref(0);
const q = ref("");
const district = ref("");
const districts = ref([]);
const minPrice = ref(null);
const maxPrice = ref(null);
const onlineOnly = ref(false);
const duty = ref("all");
const offset = ref(0);
const loading = ref(false);
const firstLoading = ref(true);

const favSet = ref(new Set());

function syncFavs(items) {
  favSet.value = new Set(items.filter((x) => x.favorited).map((x) => x.id));
}

function onToggleFav({ id, favorited, error }) {
  if (error) return;
  if (favorited) favSet.value.add(id);
  else favSet.value.delete(id);
  favSet.value = new Set(favSet.value);
}

async function load(reset) {
  loading.value = true;
  if (reset) offset.value = 0;
  try {
    const data = await api(`/api/listings?${listingParams(24, offset.value)}`);
    items.value = reset ? data.items : [...items.value, ...data.items];
    total.value = data.total;
    syncFavs(items.value);
  } finally {
    loading.value = false;
    firstLoading.value = false;
  }
}

function listingParams(limit, pageOffset) {
  const params = new URLSearchParams();
  if (q.value) params.set("q", q.value);
  if (district.value) params.set("district", district.value);
  if (minPrice.value != null && minPrice.value !== "") params.set("min_price", minPrice.value);
  if (maxPrice.value != null && maxPrice.value !== "") params.set("max_price", maxPrice.value);
  if (onlineOnly.value) params.set("online", "true");
  params.set("duty", duty.value);
  params.set("limit", limit);
  params.set("offset", pageOffset);
  return params;
}

async function refreshSaved(snapshot) {
  q.value = snapshot.q;
  district.value = snapshot.district;
  minPrice.value = snapshot.minPrice;
  maxPrice.value = snapshot.maxPrice;
  onlineOnly.value = snapshot.onlineOnly;
  duty.value = snapshot.duty || "all";
  const pageOffsets = Array.from(
    { length: Math.ceil(snapshot.items.length / 24) },
    (_, page) => page * 24,
  );
  if (!pageOffsets.length) return load(true);
  loading.value = true;
  try {
    const pages = await Promise.all(pageOffsets.map((pageOffset) =>
      api(`/api/listings?${listingParams(24, pageOffset)}`),
    ));
    items.value = pages.flatMap((page) => page.items);
    total.value = pages[0].total;
    offset.value = pageOffsets[pageOffsets.length - 1];
    syncFavs(items.value);
    requestAnimationFrame(() => window.scrollTo(0, snapshot.y));
  } finally {
    loading.value = false;
    firstLoading.value = false;
  }
}

function more() {
  offset.value += 24;
  load(false);
}

// browsing position restore: keep the last browse snapshot in a module
// cache; returning to the square restores items+scroll instantly
let saved = null;

function onPopState() {
  if (location.pathname === "/explore" && saved) restoreIfBack();
}

function persistScroll() {
  saved = {
    y: window.scrollY,
    offset: offset.value,
    items: items.value,
    total: total.value,
    q: q.value,
    district: district.value,
    minPrice: minPrice.value,
    maxPrice: maxPrice.value,
    onlineOnly: onlineOnly.value,
    duty: duty.value,
  };
}

function restoreIfBack() {
  // only for back/forward navigation (popstate), never fresh visits
  if (saved && window.navigationTriggeredByPopstate !== false) {
    items.value = saved.items;
    total.value = saved.total;
    offset.value = saved.offset;
    requestAnimationFrame(() => window.scrollTo(0, saved.y));
  }
}

onMounted(async () => {
  window.addEventListener("popstate", onPopState);
  restoreIfBack();
  if (saved) refreshSaved(saved);
  else load(true);
  try {
    districts.value = (await api("/api/meta/districts")).districts;
  } catch { /* non-fatal */ }
});

onUnmounted(() => {
  window.removeEventListener("popstate", onPopState);
  persistScroll(); // leaving the square (detail / other tab): snapshot
});
</script>

<template>
  <div class="space-y-4">
    <!-- 搜索 + 计数 -->
    <div class="flex flex-wrap items-center gap-2">
      <div class="flex flex-1 items-center gap-2 rounded-xl border border-slate-700 bg-slate-900 px-3.5 py-2.5 focus-within:border-sky-400/60 sm:flex-none">
        <Search class="size-4 shrink-0 text-slate-500" />
        <input
          v-model="q" @keyup.enter="load(true)" placeholder="关键词 / 昵称"
          class="w-full bg-transparent text-sm outline-none placeholder:text-slate-600 sm:w-44"
        />
      </div>
      <div class="ml-auto flex items-center gap-1.5 text-xs text-slate-500">
        <input v-model.number="minPrice" @change="load(true)" placeholder="最低价" type="number"
          class="w-20 rounded-lg border border-slate-700 bg-slate-900 px-2 py-1.5" />
        <span>—</span>
        <input v-model.number="maxPrice" @change="load(true)" placeholder="最高价" type="number"
          class="w-20 rounded-lg border border-slate-700 bg-slate-900 px-2 py-1.5" />
        <span class="w-16 text-right">共 {{ total }} 条</span>
      </div>
    </div>

    <!-- 筛选 chips：横滚不换行 -->
    <div class="nx-noscroll -mx-1 flex gap-2 overflow-x-auto px-1 pb-1">
      <button
        v-for="option in [{ value: 'all', label: '全部' }, { value: 'today', label: '今日在岗' }, { value: 'yesterday', label: '昨日在岗' }]"
        :key="option.value"
        @click="duty = option.value; load(true)"
        class="flex shrink-0 items-center gap-1.5 rounded-full border px-3.5 py-1.5 text-xs transition"
        :class="duty === option.value
          ? 'border-transparent nx-grad text-white font-semibold'
          : 'border-slate-700 bg-slate-900 text-slate-400 hover:text-slate-200'"
      >
        {{ option.label }}
      </button>
      <button
        @click="onlineOnly = !onlineOnly; load(true)"
        class="flex shrink-0 items-center gap-1.5 rounded-full border px-3.5 py-1.5 text-xs transition"
        :class="onlineOnly
          ? 'border-transparent nx-grad text-white font-semibold'
          : 'border-slate-700 bg-slate-900 text-slate-400 hover:text-slate-200'"
      >
        <span class="inline-block size-2 rounded-full" :class="onlineOnly ? 'bg-white' : 'bg-slate-500'"></span>
        仅在线
      </button>
      <button
        v-for="d in districts" :key="d"
        @click="district = district === d ? '' : d; load(true)"
        class="flex shrink-0 items-center gap-1 rounded-full border px-3.5 py-1.5 text-xs transition"
        :class="district === d
          ? 'border-transparent bg-sky-400/15 text-sky-300 font-semibold'
          : 'border-slate-700 bg-slate-900 text-slate-400 hover:text-slate-200'"
      >
        <MapPin class="size-3" />{{ d }}
      </button>
    </div>

    <!-- 首屏骨架 -->
    <div v-if="firstLoading" class="nx-listing-grid">
      <div v-for="i in 8" :key="i" class="overflow-hidden rounded-2xl border border-slate-800 bg-slate-900">
        <div class="nx-skel aspect-[4/5]" style="border-radius:0;"></div>
        <div class="space-y-2 p-3">
          <div class="nx-skel h-3.5 w-2/5"></div>
          <div class="nx-skel h-3 w-1/4"></div>
        </div>
      </div>
    </div>

    <!-- 卡片流：按可用宽度自动换列 -->
    <div v-else-if="items.length"
      class="nx-listing-grid">
      <ListingCard v-for="item in items" :key="item.id" :item="item" favoritable
        :favorited="favSet.has(item.id)" @toggle-favorite="onToggleFav"
        @click="persistScroll" />
    </div>
    <div v-else-if="!loading" class="py-24 text-center text-slate-500">
      暂无已发布内容（待审核发布后显示）
    </div>

    <BackToTop />

    <div v-if="items.length < total" class="pb-2 text-center">
      <button @click="more" :disabled="loading"
        class="w-full rounded-xl border border-slate-700 py-3 text-sm text-slate-300 hover:bg-slate-800 disabled:opacity-50 sm:w-auto sm:px-8 sm:py-2">
        {{ loading ? "加载中…" : "加载更多" }}
      </button>
    </div>
  </div>
</template>

