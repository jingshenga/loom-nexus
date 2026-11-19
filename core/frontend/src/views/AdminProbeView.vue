<script setup>
import { onMounted, ref } from "vue";
import { ShieldQuestion, CheckCircle2, XCircle } from "lucide-vue-next";
import { api } from "../api/client";

const statusTab = ref("pending");
const items = ref([]);
const flash = ref("");
let timer = null;

async function load() {
  items.value = (await api(`/api/admin/probe/queue?status=${statusTab.value}`)).items;
}

function notify(msg) {
  flash.value = msg;
  setTimeout(() => (flash.value = ""), 2500);
}

async function decide(id, action) {
  try {
    await api(`/api/admin/probe/${id}/${action}`, { method: "POST" });
    notify(action === "approve" ? `已确认 #${id}，网关将进入房间拉取` : `已拒绝 #${id}`);
    await load();
  } catch (e) {
    notify(`操作失败: ${String(e.message).slice(0, 60)}`);
  }
}

onMounted(() => {
  load();
  timer = setInterval(load, 10000);
});
</script>

<template>
  <div class="space-y-4">

    <div class="flex items-center gap-3">
      <h2 class="flex items-center gap-2 text-lg font-semibold">
        <ShieldQuestion class="size-5 text-amber-400" />穿透队列（人工确认门禁）
      </h2>
      <select v-model="statusTab" @change="load"
        class="rounded-lg border border-slate-700 bg-slate-900 px-2 py-1 text-sm">
        <option value="pending">待确认</option>
        <option value="approved">已确认</option>
        <option value="fetched">已拉取</option>
        <option value="failed">失败</option>
        <option value="rejected">已拒绝</option>
      </select>
      <span v-if="flash" class="rounded bg-emerald-600/20 px-3 py-1 text-xs text-emerald-300">{{ flash }}</span>
    </div>

    <p class="text-xs text-slate-500">
      从消息联系人简介中提取到的候选房间。仅在你点击「确认进入」后，网关账号才会加入房间并读取置顶帖与房主最近 3 条消息合并入库。
    </p>

    <div v-if="items.length" class="space-y-3">
      <div v-for="p in items" :key="p.id"
        class="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
        <div class="flex flex-wrap items-center gap-3 text-sm">
          <span class="font-mono text-xs text-slate-500">#{{ p.id }}</span>
          <span>联系人 <span class="text-sky-400">{{ p.contact_peer_id }}</span></span>
          <span class="rounded bg-slate-800 px-2 py-0.5 text-xs">{{ p.status }}</span>
          <span class="ml-auto text-xs text-slate-500">
            {{ new Date(p.created_at).toLocaleString("zh-CN") }}
          </span>
        </div>
        <p class="mt-2 rounded-lg bg-slate-950 p-2.5 text-sm text-slate-300">{{ p.bio_text }}</p>
        <div class="mt-2 flex flex-wrap items-center gap-2">
          <a v-for="l in p.links" :key="l" :href="l" target="_blank"
            class="rounded bg-slate-800 px-2 py-0.5 font-mono text-xs text-sky-400 hover:bg-slate-700">
            {{ l }}
          </a>
          <template v-if="p.status === 'pending'">
            <button @click="decide(p.id, 'approve')"
              class="ml-auto flex items-center gap-1.5 rounded-lg bg-emerald-600 px-4 py-1.5 text-sm font-medium hover:bg-emerald-500">
              <CheckCircle2 class="size-4" />确认进入
            </button>
            <button @click="decide(p.id, 'reject')"
              class="flex items-center gap-1.5 rounded-lg border border-rose-700/60 px-4 py-1.5 text-sm text-rose-300 hover:bg-rose-900/30">
              <XCircle class="size-4" />拒绝
            </button>
          </template>
        </div>
      </div>
    </div>
    <div v-else class="py-20 text-center text-slate-600">当前队列为空</div>
  </div>
</template>
