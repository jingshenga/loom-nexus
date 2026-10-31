<script setup>
import AdminTabs from "../components/AdminTabs.vue";
import { onMounted, onUnmounted, ref } from "vue";
import { LogOut, Power, UserPlus, KeyRound, RefreshCw } from "lucide-vue-next";
import { api } from "../api/client";

const accounts = ref([]);
const login = ref(null);
const flash = ref("");
const loginPhone = ref("");
const loginName = ref("");
const secretInput = ref("");
let timer = null;

const STATE_TEXT = {
  pending: "排队中…",
  running: "正在请求验证码…",
  code_sent: "等待验证码",
  password_needed: "等待两步验证密码",
  done: "登录成功",
  failed: "失败",
  cancelled: "已取消",
};

function notify(msg) {
  flash.value = msg;
  setTimeout(() => (flash.value = ""), 4000);
}

async function load() {
  const data = await api("/api/admin/accounts");
  accounts.value = data.items;
  login.value = data.login;
}

async function startLogin() {
  if (!loginPhone.value.trim()) {
    notify("先输入网关账号手机号（含国家码，如 +86…）");
    return;
  }
  try {
    const data = await api("/api/admin/accounts/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ phone: loginPhone.value.trim(), session_name: loginName.value.trim() }),
    });
    notify(`登录流程已发起（会话 ${data.session_name}），等待验证码…`);
    await load();
  } catch (e) {
    notify(`发起失败: ${String(e.message).slice(0, 90)}`);
  }
}

async function submitSecret() {
  if (!secretInput.value.trim() || !login.value) return;
  try {
    const data = await api("/api/admin/accounts/login/secret", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        request_id: login.value.id,
        secret: secretInput.value.trim(),
        is_password: login.value.state === "password_needed",
      }),
    });
    secretInput.value = "";
    notify(`已提交（当前状态: ${STATE_TEXT[data.state] || data.state}）`);
    await load();
  } catch (e) {
    notify(`提交失败: ${String(e.message).slice(0, 90)}`);
  }
}

function canCancelLogin() {
  return login.value && ["pending", "running", "code_sent", "password_needed"].includes(login.value.state);
}

async function cancelLogin() {
  if (!canCancelLogin()) return;
  try {
    await api(`/api/admin/accounts/login/${login.value.id}/cancel`, { method: "POST" });
    secretInput.value = "";
    notify("登录流程已取消，可以发起其他账号登录");
    await load();
  } catch (e) {
    notify(`取消失败: ${String(e.message).slice(0, 90)}`);
  }
}

async function disconnectAccount(acc) {
  const bound = acc.bound_sources > 0
    ? `；该账号还绑定着 ${acc.bound_sources} 个采集来源（将暂停采集）` : "";
  if (!confirm(`确认让账号 ${acc.display_name || acc.username || acc.account_uid} 下线？其开放中的咨询会话会被关闭${bound}`)) return;
  const data = await api(`/api/admin/accounts/${acc.id}/disconnect`, { method: "POST" });
  notify(`已下线（关闭 ${data.closed_sessions} 个会话${bound}）`);
  await load();
}

async function activateAccount(acc) {
  await api(`/api/admin/accounts/${acc.id}/activate`, { method: "POST" });
  notify(`账号 ${acc.display_name || acc.account_uid} 已重新启用（约 1 分钟内接入）`);
  await load();
}

const waitingSecret = () =>
  login.value && (login.value.state === "code_sent" || login.value.state === "password_needed");

onMounted(() => {
  load();
  timer = setInterval(load, 5000);
});
onUnmounted(() => clearInterval(timer));
</script>

<template>
  <div class="mx-auto max-w-4xl space-y-5">
  <AdminTabs />

    <div class="flex items-center gap-3">
      <h2 class="text-lg font-semibold">网关账号管理</h2>
      <span v-if="flash" class="rounded bg-emerald-600/20 px-3 py-1 text-xs text-emerald-300">{{ flash }}</span>
      <button @click="load" class="ml-auto flex items-center gap-1 rounded-lg border border-slate-700 px-2.5 py-1 text-xs text-slate-300 hover:bg-slate-800">
        <RefreshCw class="size-3.5" />刷新
      </button>
    </div>

    <!-- 登录向导 -->
    <div class="space-y-3 rounded-xl border border-violet-800/50 bg-violet-950/20 p-4">
      <div class="flex items-center gap-2 text-sm font-medium">
        <UserPlus class="size-4 text-violet-300" />登录新的网关账号
      </div>
      <div class="flex flex-wrap items-center gap-2 text-xs">
        <input v-model="loginPhone" placeholder="手机号（含国家码，如 +8613…）"
          class="w-56 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm" />
        <input v-model="loginName" placeholder="会话名（可选，默认自动）"
          class="w-44 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm" />
        <button @click="startLogin" :disabled="canCancelLogin()"
          class="rounded-lg bg-violet-600 px-4 py-2 text-sm font-medium hover:bg-violet-500 disabled:cursor-not-allowed disabled:opacity-50">
          {{ canCancelLogin() ? "先取消当前流程" : "发起登录" }}
        </button>
      </div>
      <div v-if="login" class="space-y-2 rounded-lg border border-slate-800 bg-slate-900/60 p-3 text-xs">
        <div class="flex flex-wrap items-center gap-2">
          <span class="font-mono text-slate-400">#{{ login.id }}</span>
          <span class="text-slate-300">{{ login.phone }}</span>
          <span class="rounded px-1.5 py-0.5 font-medium"
            :class="login.state === 'done' ? 'bg-emerald-900/50 text-emerald-300'
              : login.state === 'failed' ? 'bg-rose-900/50 text-rose-300'
              : 'bg-sky-900/50 text-sky-300'">
            {{ STATE_TEXT[login.state] || login.state }}
          </span>
          <span v-if="login.error" class="text-rose-400">{{ login.error }}</span>
          <button v-if="canCancelLogin()" @click="cancelLogin"
            class="ml-auto rounded-lg border border-rose-700/60 px-2.5 py-1 text-rose-300 hover:bg-rose-900/30">
            取消登录
          </button>
        </div>
        <div v-if="waitingSecret()" class="flex flex-wrap items-center gap-2">
          <KeyRound class="size-3.5 text-amber-300" />
          <input v-model="secretInput"
            :type="login.state === 'password_needed' ? 'password' : 'text'"
            :placeholder="login.state === 'password_needed' ? '两步验证密码' : 'Telegram 验证码'"
            class="w-48 rounded-lg border border-slate-700 bg-slate-950 px-3 py-1.5 text-sm"
            @keyup.enter="submitSecret" />
          <button @click="submitSecret"
            class="rounded-lg border border-amber-600/60 px-3 py-1.5 text-amber-300 hover:bg-amber-900/30">
            提交
          </button>
        </div>
      </div>
      <div class="text-[11px] text-slate-500">
        登录即接入采集/咨询/在线状态；同一账号的 Telegram 验证码短时间重复请求会复用同一个码。账号下线会暂停其绑定的采集来源。
      </div>
    </div>

    <!-- 账号列表 -->
    <div class="space-y-2">
      <div v-if="!accounts.length" class="rounded-xl border border-slate-800 bg-slate-900/50 p-6 text-center text-sm text-slate-500">
        暂无网关账号
      </div>
      <div v-for="acc in accounts" :key="acc.id"
        class="flex flex-wrap items-center gap-3 rounded-xl border border-slate-800 bg-slate-900/50 p-4">
        <div class="flex min-w-40 flex-col">
          <span class="font-medium">{{ acc.display_name || acc.username || `账号#${acc.id}` }}</span>
          <span class="text-xs text-slate-500">@{{ acc.username || "—" }} · uid {{ acc.account_uid || "—" }}</span>
        </div>
        <span class="rounded px-2 py-0.5 text-xs font-medium"
          :class="acc.status === 'active' ? 'bg-emerald-900/50 text-emerald-300' : 'bg-slate-800 text-slate-400'">
          {{ acc.status === 'active' ? "在线服务中" : acc.status }}
        </span>
        <span class="text-xs text-slate-400">咨询会话 {{ acc.open_sessions }} · 绑定来源 {{ acc.bound_sources }}</span>
        <span class="text-xs text-slate-600">{{ acc.session_file }}</span>
        <div class="ml-auto flex gap-2">
          <button v-if="acc.status === 'disconnected'" @click="activateAccount(acc)"
            class="flex items-center gap-1 rounded-lg border border-emerald-700/60 px-3 py-1.5 text-xs text-emerald-300 hover:bg-emerald-900/30">
            <Power class="size-3.5" />启用
          </button>
          <button v-else @click="disconnectAccount(acc)"
            class="flex items-center gap-1 rounded-lg border border-rose-700/60 px-3 py-1.5 text-xs text-rose-300 hover:bg-rose-900/30">
            <LogOut class="size-3.5" />退出登录
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
