<script>
let saved = null;
</script>

<script setup>
import { nextTick, onBeforeUnmount, onMounted, onUnmounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { Search, MapPin, X } from "lucide-vue-next";
import BackToTop from "../components/BackToTop.vue";

const route = useRoute();
const router = useRouter();
import { api } from "../api/client";
import ListingCard from "../components/ListingCard.vue";

const PAGE_SIZE = 24;
const items = ref([]);
const total = ref(0);
const q = ref("");
const district = ref("");
const districts = ref([]);
const minPrice = ref(null);
const maxPrice = ref(null);
const minAge = ref(null);
const maxAge = ref(null);
const restriction = ref("");
const duty = ref("all");
const offset = ref(0);
const loading = ref(false);
const firstLoading = ref(true);
const loadError = ref(false);
const loadSentinel = ref(null);
let loadObserver = null;
let requestId = 0;
let viewActive = false;
let restoringPosition = false;
let navigationSnapshotSaved = false;

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
  if (loading.value && !reset) return;
  const currentRequest = reset ? ++requestId : requestId;
  const pageOffset = reset ? 0 : offset.value + PAGE_SIZE;
  loadError.value = false;
  loading.value = true;
  try {
    const data = await api(`/api/listings?${listingParams(PAGE_SIZE, pageOffset)}`);
    if (currentRequest !== requestId) return;
    items.value = reset ? data.items : [...items.value, ...data.items];
    total.value = data.total;
    offset.value = pageOffset;
    syncFavs(items.value);
  } catch {
    if (currentRequest === requestId) loadError.value = true;
  } finally {
    if (currentRequest === requestId) {
      loading.value = false;
      firstLoading.value = false;
      requestAnimationFrame(observeLoadSentinel);
    }
  }
}

function listingParams(limit, pageOffset) {
  const params = new URLSearchParams();
  if (q.value) params.set("q", q.value);
  if (district.value) params.set("district", district.value);
  if (minPrice.value != null && minPrice.value !== "") params.set("min_price", minPrice.value);
  if (maxPrice.value != null && maxPrice.value !== "") params.set("max_price", maxPrice.value);
  if (minAge.value != null && minAge.value !== "") params.set("min_age", minAge.value);
  if (maxAge.value != null && maxAge.value !== "") params.set("max_age", maxAge.value);
  if (restriction.value) params.set("restriction", restriction.value);
  params.set("duty", duty.value);
  params.set("limit", limit);
  params.set("offset", pageOffset);
  return params;
}

async function refreshSaved(snapshot) {
  const currentRequest = ++requestId;
  q.value = snapshot.q;
  district.value = snapshot.district;
  minPrice.value = snapshot.minPrice;
  maxPrice.value = snapshot.maxPrice;
  minAge.value = snapshot.minAge ?? null;
  maxAge.value = snapshot.maxAge ?? null;
  restriction.value = snapshot.restriction || "";
  duty.value = snapshot.duty || "all";
  const pageOffsets = Array.from(
    { length: Math.ceil(snapshot.items.length / PAGE_SIZE) },
    (_, page) => page * PAGE_SIZE,
  );
  if (!pageOffsets.length) return load(true);
  loading.value = true;
  try {
    const pages = await Promise.all(pageOffsets.map((pageOffset) =>
      api(`/api/listings?${listingParams(PAGE_SIZE, pageOffset)}`),
    ));
    if (!viewActive || currentRequest !== requestId) return;
    items.value = pages.flatMap((page) => page.items);
    total.value = pages[0].total;
    offset.value = pageOffsets[pageOffsets.length - 1];
    syncFavs(items.value);
    await nextTick();
    await restoreScrollPosition(snapshot);
  } catch {
    if (viewActive && currentRequest === requestId) loadError.value = true;
  } finally {
    if (viewActive && currentRequest === requestId) {
      loading.value = false;
      firstLoading.value = false;
      restoringPosition = false;
      requestAnimationFrame(observeLoadSentinel);
    }
  }
}

function observeLoadSentinel() {
  if (!loadObserver || !loadSentinel.value) return;
  loadObserver.unobserve(loadSentinel.value);
  loadObserver.observe(loadSentinel.value);
}

// Keep the last browse snapshot across ExploreView unmounts.

function onPopState() {
  if (location.pathname === "/explore" && saved) restoreIfBack();
}

function onCardClick(event) {
  if (!event.target?.closest?.("[data-listing-id]")) return;
  persistScroll(event);
  navigationSnapshotSaved = true;
}

function persistScroll(event) {
  const clickedCard = event?.target?.closest?.("[data-listing-id]");
  const visibleCard = [...document.querySelectorAll("[data-listing-id]")].find((card) => {
    const rect = card.getBoundingClientRect();
    return rect.bottom > 0 && rect.top < window.innerHeight;
  });
  const anchor = clickedCard || visibleCard;
  saved = {
    y: window.scrollY,
    anchorId: anchor?.dataset.listingId || null,
    anchorTop: anchor?.getBoundingClientRect().top ?? null,
    offset: offset.value,
    items: items.value,
    total: total.value,
    q: q.value,
    district: district.value,
    minPrice: minPrice.value,
    maxPrice: maxPrice.value,
    minAge: minAge.value,
    maxAge: maxAge.value,
    restriction: restriction.value,
    duty: duty.value,
  };
}

function restoreScrollPosition(snapshot) {
  return new Promise((resolve) => requestAnimationFrame(() => {
    if (!viewActive) return resolve();
    const anchor = snapshot.anchorId
      ? document.getElementById(`listing-card-${snapshot.anchorId}`)
      : null;
    if (anchor && snapshot.anchorTop != null) {
      window.scrollTo(0, Math.max(0, window.scrollY + anchor.getBoundingClientRect().top - snapshot.anchorTop));
    } else {
      window.scrollTo(0, snapshot.y);
    }
    requestAnimationFrame(resolve);
  }));
}

function restoreIfBack(snapshot = saved) {
  if (!snapshot) return;
  items.value = snapshot.items;
  total.value = snapshot.total;
  offset.value = snapshot.offset;
  firstLoading.value = false;
  restoringPosition = true;
  nextTick(() => restoreScrollPosition(snapshot));
}

onMounted(async () => {
  viewActive = true;
  navigationSnapshotSaved = false;
  window.history.scrollRestoration = "manual";
  window.addEventListener("popstate", onPopState);
  document.addEventListener("click", onCardClick, true);
  loadObserver = new IntersectionObserver((entries) => {
    if (entries.some((entry) => entry.isIntersecting)
        && items.value.length < total.value && !loading.value && !loadError.value && !restoringPosition) {
      load(false);
    }
  }, { rootMargin: "400px 0px" });
  observeLoadSentinel();
  if (saved) {
    const snapshot = saved;
    restoreIfBack(snapshot);
    refreshSaved(snapshot);
  }
  else load(true);
  try {
    districts.value = (await api("/api/meta/districts")).districts;
  } catch { /* non-fatal */ }
});

onBeforeUnmount(() => {
  viewActive = false;
  if (!navigationSnapshotSaved) persistScroll(); // save while the card grid is still in the DOM
});

onUnmounted(() => {
  window.removeEventListener("popstate", onPopState);
  document.removeEventListener("click", onCardClick, true);
  loadObserver?.disconnect();
  loadObserver = null;
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
        <button v-if="q" @click="q = ''; load(true)"
          aria-label="清除昵称搜索" title="清除昵称搜索"
          class="flex shrink-0 items-center gap-1 rounded-lg px-1.5 py-1 text-xs text-slate-400 hover:bg-slate-800 hover:text-slate-200">
          <X class="size-3.5" />清除
        </button>
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

    <div class="flex flex-wrap items-center gap-2 text-xs text-slate-400">
      <span>年龄</span>
      <input v-model.number="minAge" @change="load(true)" aria-label="最低年龄" placeholder="最低" type="number" min="0" max="120"
        class="w-16 rounded-lg border border-slate-700 bg-slate-900 px-2 py-1.5" />
      <span>—</span>
      <input v-model.number="maxAge" @change="load(true)" aria-label="最高年龄" placeholder="最高" type="number" min="0" max="120"
        class="w-16 rounded-lg border border-slate-700 bg-slate-900 px-2 py-1.5" />
      <select v-model="restriction" @change="load(true)" aria-label="服务规范"
        class="min-w-0 max-w-full rounded-lg border border-slate-700 bg-slate-900 px-2 py-1.5 text-slate-300">
        <option value="">全部规范</option>
        <option v-for="term in ['禁嗑药', '禁无套', '禁醉酒', '禁桩基', '禁拔套', '禁鸽子', '禁变态']" :key="term" :value="term">{{ term }}</option>
      </select>
    </div>

    <!-- 筛选 chips：横滚不换行 -->
    <div class="nx-noscroll -mx-1 flex gap-2 overflow-x-auto px-1 pb-1">
      <button
        v-for="option in [{ value: 'all', label: '全部' }, { value: 'today', label: '今日签到' }, { value: 'yesterday', label: '昨日签到' }]"
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
    <div v-else-if="loadError" class="py-24 text-center text-slate-500">
      <button @click="load(true)" class="rounded-lg border border-slate-700 px-4 py-2 text-slate-300 hover:bg-slate-800">
        加载失败，点击重试
      </button>
    </div>
    <div v-else-if="!loading" class="py-24 text-center text-slate-500">
      暂无已发布内容（待审核发布后显示）
    </div>

    <BackToTop />

    <div ref="loadSentinel" class="py-4 text-center text-sm text-slate-500">
      <span v-if="loading && items.length">正在加载…</span>
      <button v-else-if="loadError && items.length < total" @click="load(false)"
        class="rounded-lg border border-slate-700 px-4 py-2 text-slate-300 hover:bg-slate-800">
        加载失败，点击重试
      </button>
      <span v-else-if="items.length && items.length >= total">已经到底了</span>
    </div>
  </div>
</template>

