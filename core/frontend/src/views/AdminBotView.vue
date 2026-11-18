<script setup>
import { onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import { api } from "../api/client";
import { Bot, Check, CircleHelp, RotateCw, XCircle } from "lucide-vue-next";

const items = ref([]);
const route = useRoute();
const focusJob = ref(/^\d+$/.test(String(route.query.item || "")) ? String(route.query.item) : "");
const status = ref("unverified");
const busy = ref(false);
const reason = ref("");
const flash = ref("");
const outcomes = {
  no_bot: "检查完成，未发现 Bot", multiple_bots: "发现多个 Bot，需要确认归属",
  lookup_incomplete: "查询不完整，需要人工判断", lookup_failed: "查询失败，需要人工判断",
  ownership_unverified: "已验证是 Telegram Bot，归属待确认", contact_is_bot: "联系人本身是 Bot，需要人工判断",
  contact_conflict: "联系人身份冲突",
};

async function load() {
  busy.value = true;
  try {
    const query = new URLSearchParams({ status: focusJob.value ? "all" : status.value, limit: "100" });
    if (focusJob.value) query.set("job_id", focusJob.value);
    items.value = (await api(`/api/admin/bots?${query}`)).items;
  } catch (error) {
    flash.value = error.message || "加载失败";
  } finally {
    busy.value = false;
  }
}

async function decide(item, decision) {
  try {
    await api(`/api/admin/bots/${item.job_id}/decision`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: decision, reason: reason.value.trim(), candidate_url: item.selected_bot_url || null }),
    });
    reason.value = "";
    flash.value = "审核结果已记录；本操作不会改变 Listing 状态";
    await load();
  } catch (error) {
    flash.value = error.message || "保存失败";
  }
}

onMounted(load);
</script>

<template>
  <div class="space-y-4">
    <header class="flex flex-wrap items-end justify-between gap-3">
      <div><h1 class="text-xl font-bold">Bot 地址审核</h1><p class="mt-1 text-sm text-slate-400">采集到的 Bot 链接先作为候选。只有管理员确认关联后，才可由其他审核流程使用。</p></div>
      <button @click="load" class="flex items-center gap-2 rounded-lg border border-slate-700 px-3 py-2 text-sm"><RotateCw class="size-4" />刷新</button>
    </header>
    <div class="flex flex-wrap items-center gap-2">
      <select v-model="status" @change="focusJob = ''; load()" class="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm">
        <option value="unverified">待核候选</option><option value="verified">已确认</option>
        <option value="needs_review">查询异常或多候选</option>
        <option value="unrelated">无关 Bot</option><option value="unavailable">不可访问</option><option value="no_bot">未发现 Bot</option><option value="all">全部</option>
      </select>
      <button v-if="focusJob" @click="focusJob = ''; load()" class="rounded border border-slate-700 px-3 py-2 text-xs">返回 Bot 列表</button>
      <input v-model="reason" maxlength="500" class="min-w-0 flex-1 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm" placeholder="审核原因（可选）" />
      <span class="text-xs text-slate-500">{{ items.length }} 条</span>
    </div>
    <p v-if="flash" role="status" class="rounded-lg border border-sky-900 bg-sky-950/50 p-3 text-sm text-sky-200">{{ flash }}</p>
    <div v-if="busy" class="p-8 text-center text-slate-400">加载中…</div>
    <div v-else-if="!items.length" class="rounded-xl border border-slate-800 p-8 text-center text-slate-400">没有符合条件的 Bot 采集记录</div>
    <div v-else class="grid gap-3 lg:grid-cols-2">
      <article v-for="item in items" :key="item.job_id" class="min-w-0 rounded-xl border border-slate-800 bg-slate-900/70 p-4">
        <div class="flex items-start gap-3">
          <Bot class="mt-1 size-5 shrink-0 text-sky-400" />
          <div class="min-w-0 flex-1">
            <h2 class="truncate font-semibold">@{{ item.contact_username }} <span class="text-xs font-normal text-slate-500">任务 #{{ item.job_id }}</span></h2>
            <p class="mt-1 break-all text-sm text-sky-300">{{ item.bot_url || outcomes[item.discovery_json?.outcome] || "没有可用 Bot 地址" }}</p>
            <select v-if="item.discovery_json?.candidate_urls?.length > 1 && item.verification_status === 'unverified'"
              v-model="item.selected_bot_url" aria-label="选择要确认的 Bot" class="mt-2 w-full rounded-lg border border-slate-700 bg-slate-950 px-2 py-2 text-xs">
              <option value="">选择要确认的 Bot</option>
              <option v-for="url in item.discovery_json.candidate_urls" :key="url" :value="url">{{ url }}</option>
            </select>
            <a v-if="item.profile_url" :href="item.profile_url" target="_blank" rel="noreferrer" class="mt-1 inline-block text-xs text-slate-400 underline">打开 Telegram 资料</a>
            <p class="mt-2 text-xs text-slate-500">状态：{{ item.verification_status }} · Listing {{ item.listing_id ? `#${item.listing_id}（${item.listing_status}）` : "尚无" }} · {{ item.collected_at ? new Date(item.collected_at).toLocaleString() : "" }}</p>
            <p v-if="item.job_error || item.verification_reason" class="mt-2 break-words text-xs text-amber-200">{{ item.verification_reason || item.job_error }}</p>
            <div v-if="(item.bot_url || item.job_status === 'needs_review') && item.verification_status === 'unverified'" class="mt-3 flex flex-wrap gap-2">
              <button :disabled="!item.bot_url && !item.selected_bot_url" @click="decide(item,'verified')" class="flex items-center gap-1 rounded-lg bg-emerald-700 px-3 py-2 text-xs font-semibold disabled:opacity-50"><Check class="size-4" />确认关联</button>
              <button @click="decide(item,'unrelated')" class="flex items-center gap-1 rounded-lg border border-slate-700 px-3 py-2 text-xs"><XCircle class="size-4" />标记无关</button>
              <button @click="decide(item,'unavailable')" class="flex items-center gap-1 rounded-lg border border-slate-700 px-3 py-2 text-xs"><CircleHelp class="size-4" />暂不可访问</button>
            </div>
          </div>
        </div>
      </article>
    </div>
  </div>
</template>
