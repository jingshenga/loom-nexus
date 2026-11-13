<script setup>
import { computed, onMounted, ref } from "vue";
import { RouterLink } from "vue-router";
import { Activity, MessageSquareText, Radio, RefreshCw, ShieldCheck, Users } from "lucide-vue-next";
import { api } from "../api/client";

const data = ref(null);
const error = ref("");
const loading = ref(true);
const rawCount = computed(() => Object.values(data.value?.extract || {})
  .reduce((sum, count) => sum + Number(count || 0), 0));
const outboxCount = computed(() => Object.values(data.value?.outbox || {})
  .reduce((sum, count) => sum + Number(count || 0), 0));

async function load() {
  loading.value = true;
  error.value = "";
  try {
    data.value = await api("/api/admin/gateway/overview");
  } catch (err) {
    error.value = err.message || "管理总览暂不可用";
  } finally {
    loading.value = false;
  }
}

onMounted(load);
</script>

<template>
  <section class="space-y-5" aria-labelledby="admin-overview-title">
    <div class="flex flex-wrap items-end justify-between gap-3">
      <div>
        <p class="text-xs font-semibold uppercase tracking-[.16em] text-sky-300">NEXUS CONTROL</p>
        <h1 id="admin-overview-title" class="mt-1 text-2xl font-semibold">管理总览</h1>
        <p class="mt-1 text-sm text-slate-500">实时读取生产来源和处理队列，不显示静态演示数据。</p>
      </div>
      <button class="flex items-center gap-2 rounded-lg border border-slate-700 px-3 py-2 text-sm hover:bg-slate-800 disabled:opacity-50" :disabled="loading" @click="load">
        <RefreshCw class="size-4" :class="loading ? 'animate-spin' : ''" />刷新
      </button>
    </div>

    <p v-if="error" role="alert" class="rounded-lg border border-rose-800 bg-rose-950/40 px-4 py-3 text-sm text-rose-300">{{ error }}</p>
    <div v-else-if="loading" role="status" class="rounded-xl border border-slate-800 bg-slate-900 p-5 text-sm text-slate-400">正在读取生产状态…</div>

    <template v-else-if="data">
      <div class="grid grid-cols-2 gap-3 xl:grid-cols-4">
        <article class="min-w-0 rounded-xl border border-slate-800 bg-slate-900 p-4">
          <Radio class="size-4 text-sky-300" />
          <p class="mt-3 text-2xl font-semibold tabular-nums">{{ data.sources.length }}</p>
          <p class="mt-1 text-xs text-slate-500">采集来源</p>
          <p class="mt-2 text-xs text-slate-400">当前启用 {{ data.sources.filter((s) => s.is_active).length }}</p>
        </article>
        <article class="min-w-0 rounded-xl border border-slate-800 bg-slate-900 p-4">
          <Activity class="size-4 text-emerald-300" />
          <p class="mt-3 text-2xl font-semibold tabular-nums">{{ rawCount }}</p>
          <p class="mt-1 text-xs text-slate-500">Raw 总量，按当前 extract_state 汇总</p>
        </article>
        <article class="min-w-0 rounded-xl border border-slate-800 bg-slate-900 p-4">
          <MessageSquareText class="size-4 text-violet-300" />
          <p class="mt-3 text-2xl font-semibold tabular-nums">{{ outboxCount }}</p>
          <p class="mt-1 text-xs text-slate-500">发信队列总量</p>
        </article>
        <article class="min-w-0 rounded-xl border border-slate-800 bg-slate-900 p-4">
          <Users class="size-4 text-amber-300" />
          <p class="mt-3 text-2xl font-semibold tabular-nums">{{ data.accounts.length }}</p>
          <p class="mt-1 text-xs text-slate-500">网关账号</p>
        </article>
      </div>

      <div class="grid gap-5 xl:grid-cols-2">
        <section class="min-w-0 rounded-xl border border-slate-800 bg-slate-900 p-4">
          <div class="mb-3 flex items-center gap-2"><Radio class="size-4 text-sky-300" /><h2 class="font-medium">采集来源</h2></div>
          <ul class="divide-y divide-slate-800">
            <li v-for="source in data.sources" :key="source.id" class="flex min-w-0 items-center justify-between gap-3 py-3 text-sm">
              <span class="min-w-0 truncate">{{ source.title || `来源 #${source.id}` }}</span>
              <span class="shrink-0 text-xs" :class="source.is_active ? 'text-emerald-300' : 'text-slate-500'">{{ source.is_active ? '采集中' : '已停用' }}</span>
            </li>
            <li v-if="!data.sources.length" class="py-5 text-center text-sm text-slate-500">暂无来源</li>
          </ul>
        </section>
        <section class="min-w-0 rounded-xl border border-slate-800 bg-slate-900 p-4">
          <div class="mb-3 flex items-center gap-2"><Activity class="size-4 text-sky-300" /><h2 class="font-medium">处理状态</h2></div>
          <dl class="divide-y divide-slate-800">
            <div v-for="(count, state) in data.extract" :key="state" class="flex justify-between gap-3 py-3 text-sm">
              <dt class="break-all text-slate-400">Raw · {{ state }}</dt><dd class="shrink-0 tabular-nums">{{ count }}</dd>
            </div>
            <div v-for="(count, state) in data.outbox" :key="`outbox-${state}`" class="flex justify-between gap-3 py-3 text-sm">
              <dt class="break-all text-slate-400">发信 · {{ state }}</dt><dd class="shrink-0 tabular-nums">{{ count }}</dd>
            </div>
          </dl>
        </section>
      </div>

      <nav class="grid grid-cols-2 gap-3 sm:grid-cols-3" aria-label="常用管理入口">
        <RouterLink to="/admin/review" class="flex min-h-12 items-center gap-2 rounded-lg border border-slate-800 px-3 py-3 text-sm hover:border-slate-600"><ShieldCheck class="size-4 shrink-0 text-sky-300" />内容审核</RouterLink>
        <RouterLink to="/admin/rules" class="flex min-h-12 items-center rounded-lg border border-slate-800 px-3 py-3 text-sm hover:border-slate-600">采集规则</RouterLink>
        <RouterLink to="/admin/bots" class="flex min-h-12 items-center rounded-lg border border-slate-800 px-3 py-3 text-sm hover:border-slate-600">Bot 质量</RouterLink>
        <RouterLink to="/admin/ocr" class="flex min-h-12 items-center rounded-lg border border-slate-800 px-3 py-3 text-sm hover:border-slate-600">OCR 核对</RouterLink>
        <RouterLink to="/admin/chat" class="flex min-h-12 items-center rounded-lg border border-slate-800 px-3 py-3 text-sm hover:border-slate-600">聊天诊断</RouterLink>
        <RouterLink to="/admin/accounts" class="flex min-h-12 items-center rounded-lg border border-slate-800 px-3 py-3 text-sm hover:border-slate-600">账号管理</RouterLink>
      </nav>
    </template>
  </section>
</template>
