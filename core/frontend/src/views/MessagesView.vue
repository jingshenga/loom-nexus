<script setup>
import { nextTick, onMounted, onUnmounted, ref } from "vue";
import { useRoute } from "vue-router";
import { Clock, Check, XCircle, Send, TriangleAlert, ExternalLink, PhoneOff, Bell, ImagePlus } from "lucide-vue-next";
import { api, mediaUrl } from "../api/client";
import { useSession } from "../stores/session";

const session = useSession();
const route = useRoute();
const sessions = ref([]);
const current = ref(null);
const messages = ref([]);
const flash = ref("");
const draft = ref("");
const conflict = ref(false);
const externalUrl = "https://web.telegram.org/";
let ws = null;
let bottomEl = null;
let listTimer = null;

async function loadSessions() {
  sessions.value = (await api("/api/chat/sessions")).items;
  const wanted = Number(route.query.session);
  if (current.value) {
    current.value =
      sessions.value.find((s) => s.session_id === current.value.session_id) || null;
    return;
  }
  const target =
    sessions.value.find((s) => s.session_id === wanted) ||
    (wanted ? null : sessions.value.find((s) => s.status === "open") || sessions.value[0]);
  if (target) {
    select(target);
  } else if (wanted) {
    conflict.value = true;  // deep link to a session this user does not own
  }
}

async function closeCurrent() {
  if (!current.value) return;
  await api(`/api/chat/sessions/${current.value.session_id}/close`, { method: "POST" });
  current.value = null;
  messages.value = [];
  if (ws) { ws.close(); ws = null; }
  await loadSessions();
  if (sessions.value.length && sessions.value[0].status === "open") {
    select(sessions.value[0]);
  }
}

async function select(item) {
  current.value = item;
  conflict.value = false;
  messages.value = (await api(`/api/chat/sessions/${item.session_id}/messages`)).items;
  scrollBottom();
  connectWs();
}

function connectWs() {
  if (ws) { ws.close(); ws = null; }
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(
    `${proto}://${location.host}/api/chat/ws/${current.value.session_id}?token=${encodeURIComponent(session.token)}`
  );
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    const existing = messages.value.find((m) => m.id === msg.id);
    if (existing) {
      existing.status = msg.status;
      return;
    }
    messages.value.push(msg);
    scrollBottom();
  };
}

async function startChat() {
  const contact = prompt("联系人 username（不带@，演示用，正式入口在卡片「咨询」按钮）");
  if (!contact) return;
  const data = await api("/api/chat/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ contact_username: contact.trim().replace(/^@+/, "") }),
  });
  if (data.status === "CONFLICT_LOCKED") {
    conflict.value = true;
    return;
  }
  await loadSessions();
  const fresh = sessions.value.find((s) => s.session_id === data.session_id);
  if (fresh) select(fresh);
}

// 普通用户只见 发送中/已送达/发送失败；管理员额外看 重试次数/错误原因/通道
function statusLine(m) {
  if (m.direction !== "out") return null;
  if (m.status === "sent") {
    return { icon: Check, text: session.isAdmin && m.channel === "bot" ? "已送达 · Bot通道" : "已送达", cls: "text-slate-500" };
  }
  if (m.status === "failed") {
    return {
      icon: XCircle,
      text: session.isAdmin
        ? `发送失败 · ${m.error_text || "未知原因"}`
        : "发送失败，请联系管理员",
      cls: "text-rose-400",
    };
  }
  if (session.isAdmin && m.attempts > 0) {
    return { icon: Clock, text: `重试中（第 ${m.attempts} 次失败后）`, cls: "text-amber-300" };
  }
  return { icon: Clock, text: "发送中", cls: "text-slate-500" };
}

async function retryMessage(m, toBot) {
  try {
    await api(`/api/chat/sessions/${current.value.session_id}/messages/${m.id}/retry`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ to_bot: toBot }),
    });
    notify(toBot ? "已改为 Bot 通道重新发送" : "已重新排队发送");
    messages.value = (await api(`/api/chat/sessions/${current.value.session_id}/messages`)).items;
  } catch (e) {
    notify(`重试失败: ${String(e.message).slice(0, 60)}`);
  }
}

function notify(msg) {
  flash.value = msg;
  setTimeout(() => (flash.value = ""), 3000);
}

const notifyOn = ref(localStorage.getItem("nexus-notify") === "1");
async function toggleNotify() {
  if (notifyOn.value) {
    localStorage.setItem("nexus-notify", "0");
    notifyOn.value = false;
    return;
  }
  if (!("Notification" in window)) {
    notify("此浏览器不支持桌面通知");
    return;
  }
  const perm = await Notification.requestPermission();
  if (perm === "granted") {
    localStorage.setItem("nexus-notify", "1");
    notifyOn.value = true;
    notify("已开启桌面通知");
  } else {
    notify("浏览器拒绝了通知权限");
  }
}

// camera / gallery picker: one native input covers both on iOS and
// Android; the image is compressed on-canvas before it becomes a data url
const fileInput = ref(null);

function pickImage() {
  fileInput.value && fileInput.value.click();
}

function compressImage(file) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    const url = URL.createObjectURL(file);
    img.onload = () => {
      const MAX = 1560;
      const scale = Math.min(1, MAX / Math.max(img.width, img.height));
      const w = Math.round(img.width * scale);
      const h = Math.round(img.height * scale);
      const canvas = document.createElement("canvas");
      canvas.width = w; canvas.height = h;
      canvas.getContext("2d").drawImage(img, 0, 0, w, h);
      URL.revokeObjectURL(url);
      resolve(canvas.toDataURL("image/jpeg", 0.85));
    };
    img.onerror = reject;
    img.src = url;
  });
}

async function onFileChosen(e) {
  const file = e.target.files && e.target.files[0];
  e.target.value = "";
  if (!file || !current.value) return;
  try {
    const dataUrl = await compressImage(file);
    const text = draft.value.trim();
    const data = await api(`/api/chat/sessions/${current.value.session_id}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: text, data_url: dataUrl, client_msg_id: `img-${Date.now()}` }),
    });
    draft.value = "";
    messages.value.push({
      id: data.message_id, direction: "out",
      content: text, status: "queued",
      created_at: new Date().toISOString(),
      media_kind: "photo", local_url: dataUrl,
    });
    scrollBottom();
  } catch (err) {
    notify(`图片发送失败: ${String(err.message).slice(0, 60)}`);
  }
}

async function send() {
  if (!draft.value.trim() || !current.value) return;
  const text = draft.value;
  draft.value = "";
  await api(`/api/chat/sessions/${current.value.session_id}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content: text, client_msg_id: `c-${Date.now()}` }),
  });
  messages.value = (
    await api(`/api/chat/sessions/${current.value.session_id}/messages`)
  ).items;
  scrollBottom();
}

function scrollBottom() {
  nextTick(() => bottomEl && bottomEl.scrollIntoView({ behavior: "smooth" }));
}

onMounted(() => {
  loadSessions();
  listTimer = setInterval(loadSessions, 10000);
});
onUnmounted(() => {
  ws && ws.close();
  clearInterval(listTimer);
});
</script>

<template>
  <div class="grid h-[calc(100dvh-140px)] grid-cols-1 gap-4 lg:grid-cols-12">
    <aside class="max-h-40 space-y-2 overflow-y-auto lg:col-span-3 lg:max-h-none">
      <button @click="startChat"
        class="w-full rounded-lg bg-sky-600 py-2 text-sm font-medium hover:bg-sky-500">
        + 新建咨询
      </button>
      <button v-for="s in sessions" :key="s.session_id" @click="select(s)"
        class="w-full rounded-lg border p-3 text-left text-sm"
        :class="current && current.session_id === s.session_id
          ? 'border-sky-500 bg-sky-600/10' : 'border-slate-800 bg-slate-900 hover:border-slate-600'">
        <div class="flex items-center justify-between gap-2">
          <span class="flex items-center gap-1.5 truncate font-medium">
            {{ s.nickname || `会话 #${s.session_id}` }}
            <span v-if="s.unread > 0 && (!current || current.session_id !== s.session_id)"
              class="rounded-full bg-rose-500 px-1.5 py-0.5 text-[11px] font-semibold text-white">
              {{ s.unread > 99 ? "99+" : s.unread }}
            </span>
          </span>
          <span class="shrink-0 text-xs text-slate-500">账号#{{ s.account_id }}</span>
        </div>
        <div class="mt-1 flex items-center gap-2 text-xs"
          :class="s.status === 'open' ? 'text-slate-500' : 'text-slate-600'">
          联系人 {{ s.contact_peer_id }} · {{ s.status === "open" ? "进行中" : "已结束" }}
        </div>
      </button>
    </aside>

    <section class="flex h-[calc(100dvh-230px)] min-h-[45dvh] flex-col overflow-hidden rounded-xl border border-slate-800 lg:col-span-9">
      <div v-if="current" class="flex items-center gap-3 border-b border-slate-800 px-4 py-2 text-xs text-slate-500">
        <span>{{ current.contact_username ? "@" + current.contact_username : "联系人 " + current.contact_peer_id }} · 网关账号#{{ current.account_id }} 代理</span>
        <button @click="toggleNotify" :title="notifyOn ? '桌面通知：开' : '桌面通知：关'"
          class="ml-auto flex items-center gap-1 rounded-lg border px-2.5 py-1"
          :class="notifyOn ? 'border-sky-600/60 text-sky-300' : 'border-slate-700 text-slate-500 hover:text-slate-300'">
          <Bell class="size-3.5" />{{ notifyOn ? "通知开" : "通知关" }}
        </button>
        <button v-if="current.status === 'open'" @click="closeCurrent"
          class="flex items-center gap-1 rounded-lg border border-slate-700 px-2.5 py-1 hover:bg-slate-800 hover:text-slate-300">
          <PhoneOff class="size-3.5" />结束会话（释放账号）
        </button>
        <span v-else class="ml-auto rounded bg-slate-800 px-2 py-0.5">会话已结束</span>
      </div>
      <div v-if="conflict" class="flex items-center gap-3 border-b border-amber-600/40 bg-amber-600/15 px-4 py-3 text-sm text-amber-300">
        <TriangleAlert class="size-5 shrink-0" />
        <span class="flex-1">该联系人当前没有空闲的独立网关账号（已被其他会话占用），为保证不串线，已禁用输入。</span>
        <a :href="externalUrl" target="_blank"
          class="flex items-center gap-1 rounded-lg border border-amber-500/50 px-3 py-1.5 text-xs hover:bg-amber-600/25">
          <ExternalLink class="size-3.5" />官方客户端直连
        </a>
      </div>

      <div class="flex-1 space-y-3 overflow-y-auto p-4">
        <div v-for="m in messages" :key="m.id"
          class="flex" :class="m.direction === 'out' ? 'justify-end' : 'justify-start'">
          <div class="max-w-[70%] rounded-2xl px-4 py-2 text-sm leading-relaxed"
            :class="m.direction === 'out'
              ? 'rounded-br-sm nx-grad text-white shadow-lg shadow-sky-400/10'
              : 'rounded-bl-sm bg-slate-800 text-slate-100'">
            <img v-if="m.media_kind === 'photo'" :src="m.local_url || mediaUrl(m.media_path)"
              class="mb-1 max-h-72 w-full rounded-xl object-cover" loading="lazy" />
            <p v-if="m.content" class="whitespace-pre-wrap">{{ m.content }}</p>
            <div v-if="statusLine(m)" class="mt-1 flex items-center justify-end gap-1 text-[11px]"
              :class="[statusLine(m).cls, m.direction === 'out' && m.status !== 'failed' ? 'text-sky-200/80' : '']">
              <component :is="statusLine(m).icon" class="size-3" />
              {{ statusLine(m).text }}
            </div>
            <div v-if="session.isAdmin && m.status === 'failed'" class="mt-1.5 flex justify-end gap-2">
              <button @click="retryMessage(m, false)"
                class="rounded-lg border border-slate-600 px-2 py-0.5 text-[11px] hover:bg-slate-800">重试</button>
              <button @click="retryMessage(m, true)"
                class="rounded-lg border border-sky-600/60 px-2 py-0.5 text-[11px] text-sky-300 hover:bg-sky-900/30">转Bot发送</button>
            </div>
          </div>
        </div>
        <div ref="bottomEl"></div>
      </div>

      <form class="flex gap-2 border-t border-slate-800 p-3" @submit.prevent="send">
        <input ref="fileInput" type="file" accept="image/*" capture="environment"
          class="hidden" @change="onFileChosen" />
        <button type="button" @click="pickImage" :disabled="conflict || !current"
          aria-label="拍照或从相册选择"
          class="flex size-11 shrink-0 items-center justify-center rounded-xl border border-slate-700 text-slate-400 hover:text-sky-400 disabled:opacity-40">
          <ImagePlus class="size-5" />
        </button>
        <input v-model="draft" :disabled="conflict || !current" placeholder="输入消息…"
          class="flex-1 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm outline-none focus:border-sky-500 disabled:opacity-50" />
        <button :disabled="conflict || !current"
          class="nx-grad flex items-center gap-1.5 rounded-xl px-4 py-2 text-sm font-semibold text-white hover:opacity-90 disabled:opacity-50">
          <Send class="size-4" />发送
        </button>
      </form>
    </section>
  </div>
</template>
