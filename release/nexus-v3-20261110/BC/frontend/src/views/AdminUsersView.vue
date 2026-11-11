<script setup>
import AdminTabs from "../components/AdminTabs.vue";
import { onMounted, ref } from "vue";
import { UserPlus, KeyRound, Trash2, ShieldCheck, UserCog } from "lucide-vue-next";
import { api } from "../api/client";

const users = ref([]);
const flash = ref("");
const form = ref({ username: "", password: "", display_name: "", is_admin: false });

function notify(msg) {
  flash.value = msg;
  setTimeout(() => (flash.value = ""), 4000);
}

async function load() {
  users.value = (await api("/api/admin/users")).items;
}

async function create() {
  if (!form.value.display_name.trim()) {
    notify("显示名必填（咨询首条消息会以此向对方表明身份）");
    return;
  }
  try {
    await api("/api/admin/users", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(form.value),
    });
    notify(`已创建用户 ${form.value.username}`);
    form.value = { username: "", password: "", display_name: "", is_admin: false };
    await load();
  } catch (e) {
    notify(`创建失败: ${String(e.message).slice(0, 90)}`);
  }
}

async function resetPassword(user) {
  const pw = prompt(`为用户 ${user.username} 设置新密码（至少 6 位）`);
  if (!pw) return;
  try {
    await api(`/api/admin/users/${user.id}/password`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: pw }),
    });
    notify(`已重置 ${user.username} 的密码`);
  } catch (e) {
    notify(`失败: ${String(e.message).slice(0, 80)}`);
  }
}

async function toggleAdmin(user) {
  try {
    await api(`/api/admin/users/${user.id}/admin`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ value: !user.is_admin }),
    });
    notify(`${user.username} ${user.is_admin ? "已改为普通用户" : "已设为管理员"}`);
    await load();
  } catch (e) {
    notify(`失败: ${String(e.message).slice(0, 90)}`);
  }
}

async function toggleActive(user) {
  try {
    await api(`/api/admin/users/${user.id}/active`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ value: !user.is_active }),
    });
    notify(`${user.username} ${user.is_active ? "已停用" : "已启用"}`);
    await load();
  } catch (e) {
    notify(`失败: ${String(e.message).slice(0, 90)}`);
  }
}

async function remove(user) {
  if (!confirm(`确认删除用户 ${user.username}？其开放中的咨询会话会被关闭。`)) return;
  try {
    await api(`/api/admin/users/${user.id}`, { method: "DELETE" });
    notify(`已删除 ${user.username}`);
    await load();
  } catch (e) {
    notify(`失败: ${String(e.message).slice(0, 90)}`);
  }
}

onMounted(load);
</script>

<template>
  <div class="mx-auto max-w-4xl space-y-5">
  <AdminTabs />

    <div class="flex items-center gap-3">
      <h2 class="text-lg font-semibold">站点用户</h2>
      <span v-if="flash" class="rounded bg-emerald-600/20 px-3 py-1 text-xs text-emerald-300">{{ flash }}</span>
    </div>

    <div class="space-y-3 rounded-xl border border-violet-800/50 bg-violet-950/20 p-4">
      <div class="flex items-center gap-2 text-sm font-medium">
        <UserPlus class="size-4 text-violet-300" />新建用户
      </div>
      <div class="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        <input v-model="form.username" placeholder="用户名（字母/数字/-/_）"
          class="rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm" />
        <input v-model="form.password" type="password" placeholder="密码（≥6 位）"
          class="rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm" />
        <input v-model="form.display_name" placeholder="显示名（可选）"
          class="rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm" />
        <label class="flex items-center gap-2 text-xs text-slate-400">
          <input v-model="form.is_admin" type="checkbox" class="size-4 accent-violet-500" />
          管理员（可访问全部管理页）
          <button @click="create"
            class="ml-auto rounded-lg bg-violet-600 px-4 py-2 text-xs font-medium text-white hover:bg-violet-500">
            创建
          </button>
        </label>
      </div>
    </div>

    <div class="space-y-2">
      <div v-if="!users.length" class="rounded-xl border border-slate-800 bg-slate-900/50 p-6 text-center text-sm text-slate-500">
        暂无用户
      </div>
      <div v-for="u in users" :key="u.id"
        class="flex flex-wrap items-center gap-3 rounded-xl border border-slate-800 bg-slate-900/50 p-4">
        <div class="flex min-w-32 flex-col">
          <span class="font-medium">{{ u.display_name || u.username }}</span>
          <span class="text-xs text-slate-500">{{ u.username }}</span>
        </div>
        <span class="flex items-center gap-1 rounded px-2 py-0.5 text-xs font-medium"
          :class="u.is_admin ? 'bg-sky-900/50 text-sky-300' : 'bg-slate-800 text-slate-400'">
          <component :is="u.is_admin ? ShieldCheck : UserCog" class="size-3" />
          {{ u.is_admin ? "管理员" : "普通用户" }}
        </span>
        <span v-if="!u.is_active" class="rounded bg-rose-900/40 px-2 py-0.5 text-xs text-rose-300">已停用</span>
        <span class="text-xs text-slate-500">
          上次登录 {{ u.last_login_at ? new Date(u.last_login_at).toLocaleString("zh-CN") : "从未" }}
        </span>
        <div class="ml-auto flex flex-wrap gap-2">
          <button @click="resetPassword(u)"
            class="flex items-center gap-1 rounded-lg border border-slate-600 px-2.5 py-1.5 text-xs hover:bg-slate-800">
            <KeyRound class="size-3.5" />改密码
          </button>
          <button @click="toggleAdmin(u)"
            class="flex items-center gap-1 rounded-lg border border-sky-700/60 px-2.5 py-1.5 text-xs text-sky-300 hover:bg-sky-900/30">
            {{ u.is_admin ? "降为普通" : "设为管理员" }}
          </button>
          <button @click="toggleActive(u)"
            class="rounded-lg border border-amber-700/60 px-2.5 py-1.5 text-xs text-amber-300 hover:bg-amber-900/30">
            {{ u.is_active ? "停用" : "启用" }}
          </button>
          <button @click="remove(u)"
            class="rounded-lg border border-rose-700/60 p-1.5 text-rose-300 hover:bg-rose-900/30">
            <Trash2 class="size-3.5" />
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
