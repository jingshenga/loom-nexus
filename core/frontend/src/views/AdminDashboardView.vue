<script setup>
import { onMounted, ref } from "vue";
import { RouterLink } from "vue-router";
import { Activity, AlertTriangle, Bot, CheckCheck, Copy, Image, ListChecks, Radio, ShieldCheck } from "lucide-vue-next";
import { api } from "../api/client";

const loading = ref(true);
const error = ref("");
const data = ref(null);

async function load() {
  loading.value = true;
  error.value = "";
  try {
    data.value = await api("/api/admin/dashboard");
  } catch (e) {
    error.value = e.message || "总览数据暂时不可用";
  } finally {
    loading.value = false;
  }
}
onMounted(load);

const cards = [
  { key: "raw_today", label: "今日新消息", help: "按 Telegram 发布时间，Asia/Shanghai", icon: Radio },
  { key: "rule_hits_today", label: "今日规则命中", help: "Active 解析结果含选中规则", icon: CheckCheck },
  { key: "ocr_today", label: "今日 OCR 处理", help: "今日更新的 OCR 记录", icon: Image },
  { key: "review_open", label: "待审核", help: "资料、规则及 Lifecycle 队列", icon: ListChecks },
  { key: "published_listings", label: "已发布资料", help: "当前 status=published", icon: ShieldCheck },
  { key: "duplicate_candidates", label: "重复候选", help: "当前重复待核验对数", icon: Copy },
];
</script>

<template>
  <div class="space-y-6">
    <div class="flex flex-wrap items-end justify-between gap-3">
      <div><p class="text-xs font-semibold uppercase tracking-[.18em] text-sky-400">NEXUS CONTROL</p><h1 class="mt-1 text-2xl font-bold">管理总览</h1><p class="mt-1 text-sm text-slate-400">生产指标来自当前数据库快照，不包含静态演示数据。</p></div>
      <button class="rounded-xl border border-slate-700 px-3 py-2 text-sm hover:bg-slate-800" :disabled="loading" @click="load">刷新状态</button>
    </div>
    <div v-if="error" class="rounded-xl border border-rose-500/40 bg-rose-500/10 p-4 text-sm text-rose-300">{{ error }}</div>
    <div v-if="loading" class="rounded-xl border border-slate-800 bg-slate-900 p-5 text-sm text-slate-400">正在读取实时统计…</div>
    <template v-else-if="data">
      <section class="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        <article v-for="card in cards" :key="card.key" class="rounded-2xl border border-slate-800 bg-slate-900 p-4 shadow-sm">
          <div class="flex items-center justify-between"><span class="text-sm text-slate-400">{{ card.label }}</span><component :is="card.icon" class="size-4 text-sky-400" /></div>
          <div class="mt-3 text-3xl font-semibold tabular-nums">{{ data.metrics[card.key] ?? "—" }}</div>
          <div class="mt-2 text-xs text-slate-500">{{ card.help }}</div>
        </article>
      </section>

      <section class="grid gap-4 xl:grid-cols-[1.3fr_1fr]">
        <article class="rounded-2xl border border-slate-800 bg-slate-900 p-5">
          <div class="mb-4 flex items-center justify-between"><h2 class="font-semibold">运行状态</h2><Activity class="size-4 text-sky-400" /></div>
          <div class="grid gap-3 sm:grid-cols-2">
            <div v-for="item in data.health" :key="item.name" class="flex min-w-0 items-center justify-between gap-3 rounded-xl bg-slate-950/60 px-3 py-3">
              <div class="min-w-0"><div class="text-sm font-medium">{{ item.name }}</div><div class="truncate text-xs text-slate-500">{{ item.detail || "—" }}</div></div>
              <span class="shrink-0 rounded-full px-2 py-1 text-xs" :class="item.ok ? 'bg-emerald-500/10 text-emerald-300' : 'bg-amber-500/10 text-amber-300'">{{ item.status }}</span>
            </div>
          </div>
        </article>
        <article class="rounded-2xl border border-slate-800 bg-slate-900 p-5">
          <div class="mb-4 flex items-center justify-between"><h2 class="font-semibold">最近活跃来源</h2><Bot class="size-4 text-sky-400" /></div>
          <div v-if="!data.recent_sources.length" class="text-sm text-slate-500">暂无来源消息记录</div>
          <div v-for="source in data.recent_sources" :key="source.id" class="flex items-center justify-between gap-3 border-b border-slate-800 py-3 last:border-0">
            <div class="min-w-0"><div class="truncate text-sm font-medium">{{ source.title || `来源 ${source.id}` }}</div><div class="text-xs text-slate-500">最近消息 {{ source.last_message_at || "无记录" }}</div></div>
            <span class="shrink-0 rounded-full px-2 py-1 text-xs" :class="source.router_mode === 'active' ? 'bg-amber-500/10 text-amber-300' : 'bg-slate-800 text-slate-300'">{{ source.router_mode || "legacy" }}</span>
          </div>
        </article>
      </section>
      <section class="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <RouterLink v-for="link in data.actions" :key="link.to" :to="link.to" class="flex items-center gap-3 rounded-xl border border-slate-800 bg-slate-900 p-4 text-sm hover:border-sky-500/50 hover:bg-slate-800">
          <AlertTriangle v-if="link.alert" class="size-4 text-amber-400" /><Activity v-else class="size-4 text-sky-400" />{{ link.label }}
        </RouterLink>
      </section>
      <p class="text-xs text-slate-500">统计时间：{{ data.as_of }}。待审核合计由当前 Listing、规则待审与 Lifecycle 队列组成，重复记录按各自队列状态统计。</p>
    </template>
  </div>
</template>
