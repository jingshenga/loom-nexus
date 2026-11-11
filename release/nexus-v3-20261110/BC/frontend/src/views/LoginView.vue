<script setup>
import { ref } from "vue";
import { useRouter } from "vue-router";
import { KeyRound } from "lucide-vue-next";
import { api } from "../api/client";
import { useSession } from "../stores/session";

const router = useRouter();
const session = useSession();
const username = ref("");
const password = ref("");
const error = ref("");
const busy = ref(false);

async function submit() {
  error.value = "";
  busy.value = true;
  try {
    const data = await api("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: username.value, password: password.value }),
    });
    session.login(data);
    router.push(data.is_admin ? "/admin/review" : "/explore");
  } catch (e) {
    error.value = String(e.message).includes("账户") ? "账户或密码错误" : "登录失败";
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <div class="mx-auto mt-12 max-w-sm space-y-4 rounded-xl border border-slate-800 bg-slate-900 p-6 sm:mt-20">
    <h2 class="flex items-center gap-2 text-lg font-semibold">
      <span class="nx-grad flex size-9 items-center justify-center rounded-xl text-white"><KeyRound class="size-5" /></span>账户登录
    </h2>
    <form class="space-y-3" @submit.prevent="submit">
      <input
        v-model="username" placeholder="用户名" autocomplete="username" autofocus
        class="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm outline-none focus:border-sky-500"
      />
      <input
        v-model="password" type="password" placeholder="密码" autocomplete="current-password"
        class="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm outline-none focus:border-sky-500"
      />
      <p v-if="error" class="text-sm text-rose-400">{{ error }}</p>
      <button :disabled="busy"
        class="nx-grad w-full rounded-xl py-2.5 text-sm font-bold text-white hover:opacity-90 disabled:opacity-50">
        {{ busy ? "登录中…" : "登录" }}
      </button>
    </form>
    <p class="text-xs text-slate-500">
      本站需登录后访问；普通账户可浏览广场、收藏与立即聊天，管理员账户由管理后台分配。
    </p>
  </div>
</template>
