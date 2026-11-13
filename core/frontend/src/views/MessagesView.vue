<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ArrowLeft, Clock, Check, XCircle, Send, TriangleAlert, ExternalLink, PhoneOff, Bell, ImagePlus, MoreHorizontal, UserRoundPen, Trash2, X } from "lucide-vue-next";
import { api } from "../api/client";
import { useSession } from "../stores/session";

const session = useSession();
const route = useRoute();
const router = useRouter();
const desktopQuery = window.matchMedia("(min-width: 1024px)");
const mobile = ref(!desktopQuery.matches);
const chatOpen = ref(false);
const menuOpen = ref(false);
const aliasDialog = ref(false);
const aliasInfo = ref({ chat_alias: "", is_custom: false, changed_at: null, next_change_at: null });
const aliasInput = ref("");
const previewSrc = ref("");
const aliasCanChange = computed(() => !aliasInfo.value.next_change_at
  || new Date(aliasInfo.value.next_change_at).getTime() <= Date.now());
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
const mediaObjectUrls = new Set();

function clearMediaUrls() {
  for (const url of mediaObjectUrls) URL.revokeObjectURL(url);
  mediaObjectUrls.clear();
  previewSrc.value = "";
}

async function hydrateMedia(items) {
  await Promise.all(items.map(async (message) => {
    if (message.media_kind !== "photo" || !message.media_url || message.local_url) return;
    try {
      const response = await fetch(message.media_url, {
        headers: { Authorization: `Bearer ${session.token}` },
      });
      if (!response.ok) throw new Error("图片加载失败");
      const url = URL.createObjectURL(await response.blob());
      mediaObjectUrls.add(url);
      message.local_url = url;
    } catch {
      message.media_error = true;
    }
  }));
}

async function loadSessions() {
  sessions.value = (await api("/api/chat/sessions")).items;
  const wanted = Number(route.query.session);
  if (current.value) {
    current.value =
      sessions.value.find((s) => s.session_id === current.value.session_id) || null;
    if (!current.value) chatOpen.value = false;
    return;
  }
  if (mobile.value && !wanted) return;
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
  chatOpen.value = false;
  menuOpen.value = false;
  messages.value = [];
  clearMediaUrls();
  if (ws) { ws.close(); ws = null; }
  if (route.query.session) {
    const query = { ...route.query };
    delete query.session;
    await router.replace({ query });
  }
  await loadSessions();
}

async function deleteCurrent() {
  if (!current.value) return;
  const ok = window.confirm("确定删除这段聊天记录？删除后无法在 Nexus 中恢复，Telegram 对方已经收到的消息不会因此被删除。");
  if (!ok) return;
  try {
    await api(`/api/chat/sessions/${current.value.session_id}`, { method: "DELETE" });
    if (ws) { ws.close(); ws = null; }
    current.value = null;
    messages.value = [];
    clearMediaUrls();
    chatOpen.value = false;
    menuOpen.value = false;
    const query = { ...route.query };
    delete query.session;
    await router.replace({ query });
    await loadSessions();
    notify("聊天记录已删除");
  } catch (e) {
    notify(String(e.message).includes("稍后再删除") ? "消息正在投递，请稍后重试删除" : `删除失败：${String(e.message).slice(0, 40)}`);
  }
}

async function loadAlias() {
  try {
    aliasInfo.value = await api("/api/chat/alias");
    aliasInput.value = aliasInfo.value.chat_alias || "";
  } catch { /* API error is shown when the dialog is opened */ }
}

async function saveAlias() {
  try {
    await api("/api/chat/alias", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat_alias: aliasInput.value }),
    });
    await loadAlias();
    aliasDialog.value = false;
    notify("咨询昵称已保存");
  } catch (e) {
    notify(`保存失败：${String(e.message).slice(0, 60)}`);
  }
}

function previewImage(src) {
  if (src) previewSrc.value = src;
}

async function select(item) {
  clearMediaUrls();
  current.value = item;
  chatOpen.value = true;
  conflict.value = false;
  messages.value = (await api(`/api/chat/sessions/${item.session_id}/messages`)).items;
  await hydrateMedia(messages.value);
  scrollBottom();
  connectWs();
}

function connectWs() {
  if (ws) { ws.close(); ws = null; }
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(
    `${proto}://${location.host}/api/chat/ws/${current.value.session_id}?token=${encodeURIComponent(session.token)}`
  );
  ws.onmessage = async (ev) => {
    const msg = JSON.parse(ev.data);
    const existing = messages.value.find((m) => m.id === msg.id);
    if (existing) {
      existing.status = msg.status;
      return;
    }
    await hydrateMedia([msg]);
    messages.value.push(msg);
    scrollBottom();
  };
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
  desktopQuery.addEventListener("change", onViewportChange);
  loadAlias();
  loadSessions();
  listTimer = setInterval(loadSessions, 10000);
});
onUnmounted(() => {
  desktopQuery.removeEventListener("change", onViewportChange);
  ws && ws.close();
  clearInterval(listTimer);
  clearMediaUrls();
});

function onViewportChange(event) {
  mobile.value = !event.matches;
  if (event.matches && !current.value) loadSessions();
}
</script>

<template>
  <div class="chat-shell grid min-h-[320px] grid-cols-1 gap-4 lg:grid-cols-12">
    <aside v-show="!mobile || !chatOpen" class="min-h-0 space-y-2 overflow-y-auto lg:col-span-3">
      <button @click="aliasDialog = true"
        class="flex w-full items-center justify-between rounded-lg border border-slate-800 bg-slate-900 px-3 py-2 text-left text-sm hover:border-slate-600">
        <span class="min-w-0 truncate">咨询昵称：{{ aliasInfo.chat_alias || "加载中" }}</span>
        <UserRoundPen class="ml-2 size-4 shrink-0 text-slate-400" />
      </button>
      <div v-if="conflict && mobile" class="rounded-lg border border-amber-600/40 bg-amber-600/15 p-3 text-sm text-amber-300">
        该联系人当前没有空闲的独立网关账号，请稍后再试。
      </div>
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
        </div>
        <div class="mt-1 flex items-center gap-2 text-xs"
          :class="s.status === 'open' ? 'text-slate-500' : 'text-slate-600'">
          {{ s.contact_username ? `@${s.contact_username}` : "资料联系人" }} · {{ s.status === "open" ? "进行中" : "已结束" }}
        </div>
      </button>
    </aside>

    <section v-show="!mobile || chatOpen" class="flex min-h-0 flex-col overflow-hidden rounded-xl border border-slate-800 lg:col-span-9">
      <div v-if="current" class="flex items-center gap-2 border-b border-slate-800 px-3 py-2 text-xs text-slate-500 sm:px-4">
        <button v-if="mobile" @click="chatOpen = false; menuOpen = false" aria-label="返回会话列表"
          class="flex size-9 shrink-0 items-center justify-center rounded-lg hover:bg-slate-800">
          <ArrowLeft class="size-5" />
        </button>
        <span class="min-w-0 flex-1 truncate">{{ current.nickname || (current.contact_username ? "@" + current.contact_username : "资料联系人") }}</span>
        <div v-if="mobile" class="relative shrink-0">
          <button @click="menuOpen = !menuOpen" aria-label="会话操作" :aria-expanded="menuOpen"
            class="flex size-9 items-center justify-center rounded-lg hover:bg-slate-800">
            <MoreHorizontal class="size-5" />
          </button>
          <div v-if="menuOpen" class="absolute right-0 top-10 z-20 w-44 rounded-xl border border-slate-700 bg-slate-900 p-1 shadow-xl">
            <button @click="toggleNotify(); menuOpen = false" class="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left hover:bg-slate-800">
              <Bell class="size-4" />{{ notifyOn ? "关闭通知" : "开启通知" }}
            </button>
            <button v-if="current.status === 'open'" @click="closeCurrent" class="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left hover:bg-slate-800">
              <PhoneOff class="size-4" />结束会话
            </button>
            <button @click="deleteCurrent" class="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-rose-300 hover:bg-slate-800">
              <Trash2 class="size-4" />删除聊天记录
            </button>
          </div>
        </div>
        <button @click="toggleNotify" :title="notifyOn ? '桌面通知：开' : '桌面通知：关'"
          class="hidden items-center gap-1 rounded-lg border px-2.5 py-1 lg:flex"
          :class="notifyOn ? 'border-sky-600/60 text-sky-300' : 'border-slate-700 text-slate-500 hover:text-slate-300'">
          <Bell class="size-3.5" />{{ notifyOn ? "通知开" : "通知关" }}
        </button>
            <button v-if="current.status === 'open'" @click="closeCurrent"
          class="hidden items-center gap-1 rounded-lg border border-slate-700 px-2.5 py-1 hover:bg-slate-800 hover:text-slate-300 lg:flex">
          <PhoneOff class="size-3.5" />结束会话（释放账号）
        </button>
        <button @click="deleteCurrent"
          class="hidden items-center gap-1 rounded-lg border border-rose-800/70 px-2.5 py-1 text-rose-300 hover:bg-rose-950/40 lg:flex">
          <Trash2 class="size-3.5" />删除记录
        </button>
        <span v-if="current.status !== 'open'" class="hidden rounded bg-slate-800 px-2 py-0.5 lg:inline">会话已结束</span>
      </div>
      <div v-if="conflict" class="flex flex-wrap items-center gap-3 border-b border-amber-600/40 bg-amber-600/15 px-4 py-3 text-sm text-amber-300">
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
          <div class="max-w-[85%] rounded-2xl px-4 py-2 text-sm leading-relaxed sm:max-w-[70%]"
            :class="m.direction === 'out'
              ? 'rounded-br-sm nx-grad text-white shadow-lg shadow-sky-400/10'
              : 'rounded-bl-sm bg-slate-800 text-slate-100'">
            <img v-if="m.media_kind === 'photo'" :src="m.local_url"
              class="mb-1 max-h-72 w-full cursor-zoom-in rounded-xl object-cover" loading="lazy"
              @click="previewImage(m.local_url)" @error="notify('图片加载失败，请稍后重试')" />
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
          class="min-w-0 flex-1 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm outline-none focus:border-sky-500 disabled:opacity-50" />
        <button :disabled="conflict || !current"
          aria-label="发送消息"
          class="nx-grad flex size-11 shrink-0 items-center justify-center gap-1.5 rounded-xl text-sm font-semibold text-white hover:opacity-90 disabled:opacity-50 sm:w-auto sm:px-4">
          <Send class="size-4" /><span class="hidden sm:inline">发送</span>
        </button>
      </form>
    </section>
    <div v-if="flash" role="status" class="fixed bottom-24 left-1/2 z-50 max-w-[90vw] -translate-x-1/2 rounded-xl bg-slate-800 px-4 py-2 text-sm text-slate-100 shadow-lg">
      {{ flash }}
    </div>
    <div v-if="aliasDialog" class="fixed inset-0 z-[80] flex items-center justify-center bg-black/70 p-4" @click.self="aliasDialog = false">
      <section class="w-full max-w-sm rounded-2xl border border-slate-700 bg-slate-900 p-4 shadow-2xl">
        <div class="mb-3 flex items-center justify-between"><h2 class="font-semibold">咨询昵称</h2><button @click="aliasDialog = false" aria-label="关闭" class="rounded-lg p-1 hover:bg-slate-800"><X class="size-5" /></button></div>
        <p class="mb-3 text-sm text-slate-400">此昵称会显示在发给联系人的每条消息前。首次设置不受修改周期限制，之后每六个月可修改一次。</p>
        <input v-model="aliasInput" maxlength="24" class="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm" aria-label="咨询昵称" />
        <p v-if="aliasInfo.next_change_at" class="mt-2 text-xs text-slate-500">下次可修改：{{ new Date(aliasInfo.next_change_at).toLocaleString() }}</p>
        <button @click="saveAlias" :disabled="!aliasCanChange" class="nx-grad mt-4 w-full rounded-lg px-3 py-2 text-sm font-semibold disabled:cursor-not-allowed disabled:opacity-40">保存</button>
      </section>
    </div>
    <div v-if="previewSrc" class="fixed inset-0 z-[90] flex items-center justify-center bg-black/95 p-3" @click="previewSrc = ''" @keydown.esc.window="previewSrc = ''">
      <button @click.stop="previewSrc = ''" aria-label="关闭图片预览" class="absolute right-3 top-3 z-10 rounded-full bg-slate-800/80 p-2 text-white"><X class="size-6" /></button>
      <img :src="previewSrc" alt="聊天图片预览" class="max-h-[92dvh] max-w-[96vw] touch-auto object-contain" @click.stop />
    </div>
  </div>
</template>

<style scoped>
.chat-shell {
  height: calc(100dvh - 134px - env(safe-area-inset-bottom));
}
@media (min-width: 1024px) {
  .chat-shell { height: calc(100dvh - 96px); }
}
</style>
