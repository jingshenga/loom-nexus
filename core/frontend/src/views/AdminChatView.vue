<script setup>
import { onMounted, ref } from "vue";
import { api } from "../api/client";
import { MessageCircle, RefreshCw } from "lucide-vue-next";

const sessions = ref([]);
const selected = ref(null);
const messages = ref([]);
const aliasAudit = ref([]);
const loading = ref(false);
const error = ref("");

async function loadSessions() {
  loading.value = true;
  error.value = "";
  try {
    sessions.value = (await api("/api/admin/chat/sessions?limit=100")).items;
    if (selected.value) {
      selected.value = sessions.value.find((item) => item.session_id === selected.value.session_id) || null;
      if (selected.value) await selectSession(selected.value);
    }
  } catch (e) {
    error.value = String(e.message || e);
  } finally {
    loading.value = false;
  }
}

async function selectSession(item) {
  selected.value = item;
  const [messageData, auditData] = await Promise.all([
    api(`/api/admin/chat/sessions/${item.session_id}/messages`),
    api(`/api/admin/chat/users/${item.user_id}/alias-audit`),
  ]);
  messages.value = messageData.items;
  aliasAudit.value = auditData.items;
}

function formatTime(value) {
  return value ? new Date(value).toLocaleString() : "—";
}

onMounted(loadSessions);
</script>

<template>
  <div class="space-y-4">
    <header class="flex min-w-0 flex-wrap items-center justify-between gap-3">
      <div class="flex min-w-0 items-center gap-2">
        <MessageCircle class="size-5 shrink-0 text-sky-300" />
        <div><h1 class="text-xl font-semibold">聊天诊断</h1><p class="text-xs text-slate-400">管理员可见投递账号、咨询昵称快照和发送回执</p></div>
      </div>
      <button @click="loadSessions" class="inline-flex shrink-0 items-center gap-2 rounded-lg border border-slate-700 px-3 py-2 text-sm hover:bg-slate-800" :disabled="loading">
        <RefreshCw class="size-4" :class="loading ? 'animate-spin' : ''" />刷新
      </button>
    </header>
    <p v-if="error" role="alert" class="rounded-lg border border-rose-700/50 bg-rose-950/30 p-3 text-sm text-rose-200">加载失败：{{ error }}</p>

    <div class="grid min-w-0 gap-4 xl:grid-cols-[minmax(19rem,0.8fr)_minmax(0,1.4fr)]">
      <section class="min-w-0 space-y-2">
        <h2 class="px-1 text-sm font-medium text-slate-300">最近会话（{{ sessions.length }}）</h2>
        <p v-if="!sessions.length && !loading" class="rounded-xl border border-slate-800 p-4 text-sm text-slate-500">暂无聊天会话</p>
        <button v-for="item in sessions" :key="item.session_id" @click="selectSession(item)"
          class="block w-full min-w-0 rounded-xl border p-3 text-left transition"
          :class="selected?.session_id === item.session_id ? 'border-sky-600 bg-sky-950/30' : 'border-slate-800 bg-slate-900/50 hover:border-slate-700'">
          <div class="flex min-w-0 items-center justify-between gap-2">
            <span class="min-w-0 truncate font-medium">{{ item.username }} · {{ item.chat_alias || '未设置昵称' }}</span>
            <span class="shrink-0 rounded-full bg-slate-800 px-2 py-0.5 text-xs text-slate-300">{{ item.status }}</span>
          </div>
          <p class="mt-1 break-all text-xs text-slate-400">目标：{{ item.contact_username ? `@${item.contact_username}` : item.contact_peer_id }}</p>
          <p class="mt-1 break-words text-xs text-slate-500">发送账号：{{ item.gateway_account || '未分配' }} · {{ item.account_uid || '—' }}</p>
          <p class="mt-1 line-clamp-2 break-words text-xs text-slate-500">{{ item.last_content || '暂无消息内容' }}</p>
          <p class="mt-1 text-[11px] text-slate-600">{{ formatTime(item.last_msg_at || item.created_at) }} · {{ item.delivery_channel || 'direct' }}</p>
        </button>
      </section>

      <section class="min-w-0 space-y-4">
        <div v-if="!selected" class="flex min-h-44 items-center justify-center rounded-xl border border-dashed border-slate-800 p-6 text-sm text-slate-500">选择一个会话查看投递详情和咨询昵称审计</div>
        <template v-else>
          <div class="min-w-0 rounded-xl border border-slate-800 bg-slate-900/50 p-4">
            <h2 class="break-words font-semibold">会话 #{{ selected.session_id }} · {{ selected.username }}</h2>
            <div class="mt-3 grid min-w-0 grid-cols-1 gap-2 text-sm sm:grid-cols-2">
              <p class="break-all"><span class="text-slate-500">咨询昵称：</span>{{ selected.chat_alias }}</p>
              <p class="break-all"><span class="text-slate-500">TG 联系人：</span>{{ selected.contact_username ? `@${selected.contact_username}` : selected.contact_peer_id }}</p>
              <p class="break-all"><span class="text-slate-500">网关账号：</span>{{ selected.gateway_account || '—' }} / {{ selected.account_uid || '—' }}</p>
              <p><span class="text-slate-500">通道/状态：</span>{{ selected.delivery_channel || 'direct' }} / {{ selected.status }}</p>
            </div>
          </div>

          <div class="min-w-0 rounded-xl border border-slate-800 bg-slate-900/50 p-4">
            <h2 class="mb-3 font-semibold">消息投递记录</h2>
            <div v-if="!messages.length" class="text-sm text-slate-500">该会话没有保留的消息。</div>
            <div v-for="message in messages" :key="message.id" class="mb-3 min-w-0 rounded-lg border border-slate-800 p-3 last:mb-0">
              <div class="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-500">
                <span>{{ message.direction === 'out' ? '发出' : '收到' }}</span>
                <span>{{ message.status }}<template v-if="message.delivery_status"> / {{ message.delivery_status }}</template></span>
                <span>{{ formatTime(message.created_at) }}</span>
                <span v-if="message.platform_msg_id">TG 消息 #{{ message.platform_msg_id }}</span>
              </div>
              <p class="mt-2 whitespace-pre-wrap break-words text-sm">{{ message.telegram_text || message.content || '图片消息' }}</p>
              <p v-if="message.media_kind" class="mt-1 text-xs text-slate-400">附件：{{ message.media_kind }}</p>
              <p v-if="message.error_text || message.last_error" class="mt-2 break-words text-xs text-rose-300">失败原因：{{ message.error_text || message.last_error }}</p>
              <p v-if="message.attempts" class="mt-1 text-xs text-slate-500">重试次数：{{ message.attempts }}</p>
            </div>
          </div>

          <div class="min-w-0 rounded-xl border border-slate-800 bg-slate-900/50 p-4">
            <h2 class="mb-3 font-semibold">咨询昵称修改审计</h2>
            <div v-if="!aliasAudit.length" class="text-sm text-slate-500">暂无修改记录</div>
            <div v-for="entry in aliasAudit" :key="entry.id" class="flex min-w-0 flex-wrap justify-between gap-2 border-t border-slate-800 py-2 text-sm first:border-0">
              <span class="min-w-0 break-words">{{ entry.old_alias }} → {{ entry.new_alias }}<span v-if="entry.is_initial" class="ml-2 text-xs text-slate-500">首次设置</span></span>
              <time class="shrink-0 text-xs text-slate-500">{{ formatTime(entry.changed_at) }}</time>
            </div>
          </div>
        </template>
      </section>
    </div>
  </div>
</template>
