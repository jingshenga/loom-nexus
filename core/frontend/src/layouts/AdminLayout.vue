<script setup>
import { computed, onMounted, onUnmounted, ref } from "vue";
import { RouterLink, RouterView, useRoute, useRouter } from "vue-router";
import { Activity, Boxes, Bot, ChevronRight, CircleHelp, ClipboardCheck, FileCog, Gauge, Image, LayoutDashboard, ListChecks, LogOut, Menu, MessageCircle, Moon, Radio, ScanText, Settings2, ShieldCheck, Sun, Users, X } from "lucide-vue-next";
import { api } from "../api/client";
import { useSession } from "../stores/session";
import { useTheme } from "../theme";

const route = useRoute();
const router = useRouter();
const session = useSession();
const theme = useTheme();
const menuOpen = ref(false);
const coreStatus = ref("读取中");

const groups = [
  { title: "管理总览", links: [{ to: "/admin", label: "总览", icon: LayoutDashboard }] },
  { title: "内容审核", links: [
    { to: "/admin/review", label: "资料审核与重复核对", icon: ClipboardCheck },
    { to: "/admin/rule-review", label: "规则待审", icon: ListChecks },
    { to: "/admin/lifecycle", label: "Lifecycle", icon: ShieldCheck },
    { to: "/admin/ocr", label: "OCR 图片核对", icon: Image },
  ] },
  { title: "资料管理", links: [
    { to: "/admin/history", label: "未命中与历史资料", icon: Boxes },
  ] },
  { title: "采集与规则", links: [
    { to: "/admin/rule-router", label: "来源与路由", icon: Radio },
    { to: "/admin/rules", label: "规则管理", icon: FileCog },
    { to: "/admin/accounts", label: "采集账号", icon: Users },
    { to: "/admin/bots", label: "Bot 质量管理", icon: Bot },
  ] },
  { title: "AI 模型中心", links: [
    { to: "/admin/models", label: "模型中心", icon: ScanText },
    { to: "/admin/ocr-classifier", label: "分类器训练", icon: Activity },
  ] },
  { title: "用户与聊天", links: [
    { to: "/admin/users", label: "用户权限", icon: Settings2 },
    { to: "/admin/chat", label: "聊天诊断", icon: MessageCircle },
  ] },
  { title: "系统管理", links: [
    { to: "/admin/gateway", label: "服务状态", icon: Gauge },
    { to: "/admin/probe", label: "连通检查", icon: CircleHelp },
  ] },
];
const pageTitle = computed(() => route.meta.title || "管理总览");
const isDark = computed(() => theme.pref.value === "dark");

async function checkCore() {
  try { const data = await api("/api/health"); coreStatus.value = data.status === "ok" ? "正常" : "异常"; }
  catch { coreStatus.value = "不可用"; }
}
function logout() { session.logout(); router.push("/login"); }
function toggleTheme() {
  theme.set(theme.pref.value === "auto" ? "light" : theme.pref.value === "light" ? "dark" : "auto");
}
onMounted(() => { checkCore(); });
</script>

<template>
  <div class="min-h-screen bg-slate-950 text-slate-100 lg:flex">
    <div v-if="menuOpen" class="fixed inset-0 z-40 bg-black/55 lg:hidden" @click="menuOpen = false"></div>
    <aside class="fixed inset-y-0 left-0 z-50 flex w-[min(84vw,300px)] -translate-x-full flex-col border-r border-slate-800 bg-slate-950 transition-transform lg:sticky lg:top-0 lg:h-screen lg:w-64 lg:translate-x-0" :class="menuOpen ? 'translate-x-0' : ''">
      <div class="flex h-16 items-center justify-between border-b border-slate-800 px-5">
        <RouterLink to="/admin" class="text-base font-bold tracking-wide" @click="menuOpen = false">Nexus <span class="text-sky-400">管理台</span></RouterLink>
        <button class="rounded-lg p-2 text-slate-400 hover:bg-slate-800 lg:hidden" aria-label="关闭导航" @click="menuOpen = false"><X class="size-4" /></button>
      </div>
      <nav class="min-h-0 flex-1 space-y-5 overflow-y-auto px-3 py-5" aria-label="管理员导航">
        <section v-for="group in groups" :key="group.title">
          <h2 class="px-3 pb-2 text-[11px] font-semibold uppercase tracking-[.16em] text-slate-500">{{ group.title }}</h2>
          <div class="space-y-1">
            <RouterLink v-for="item in group.links" :key="item.to" :to="item.to" class="flex min-w-0 items-center gap-3 rounded-xl px-3 py-2.5 text-sm text-slate-400 hover:bg-slate-900 hover:text-slate-100" :class="route.path === item.to ? 'bg-sky-500/10 font-semibold text-sky-300 ring-1 ring-sky-500/20' : ''" @click="menuOpen = false">
              <component :is="item.icon" class="size-4 shrink-0" /><span class="min-w-0 truncate">{{ item.label }}</span><ChevronRight v-if="route.path === item.to" class="ml-auto size-3.5" />
            </RouterLink>
          </div>
        </section>
      </nav>
      <div class="border-t border-slate-800 p-3">
        <div class="mb-2 flex items-center gap-2 rounded-xl bg-slate-900 px-3 py-2 text-xs"><span class="size-2 rounded-full" :class="coreStatus === '正常' ? 'bg-emerald-400' : 'bg-amber-400'"></span><span class="text-slate-400">Core / API</span><span class="ml-auto">{{ coreStatus }}</span></div>
        <div class="flex items-center gap-2 px-2 text-xs text-slate-400"><span class="min-w-0 flex-1 truncate">{{ session.username || "管理员" }}</span><button class="rounded-lg p-2 hover:bg-slate-800" :title="`主题：${theme.pref.value}`" @click="toggleTheme"><Moon v-if="!isDark" class="size-4" /><Sun v-else class="size-4" /></button><button class="rounded-lg p-2 hover:bg-slate-800" title="退出登录" @click="logout"><LogOut class="size-4" /></button></div>
      </div>
    </aside>

    <div class="min-w-0 flex-1">
      <header class="sticky top-0 z-30 flex h-16 items-center gap-3 border-b border-slate-800 bg-slate-950/90 px-4 backdrop-blur sm:px-6">
        <button class="rounded-lg p-2 text-slate-300 hover:bg-slate-800 lg:hidden" aria-label="打开导航" @click="menuOpen = true"><Menu class="size-5" /></button>
        <div class="min-w-0 flex-1"><div class="truncate text-sm font-semibold sm:text-base">{{ pageTitle }}</div><div class="hidden text-xs text-slate-500 sm:block">Nexus V3 · 生产管理</div></div>
        <RouterLink to="/explore" class="rounded-lg border border-slate-800 px-3 py-2 text-xs text-slate-400 hover:bg-slate-900">查看广场</RouterLink>
      </header>
      <main class="mx-auto w-full max-w-[1600px] min-w-0 px-4 py-5 pb-10 sm:px-6 lg:px-8">
        <RouterView />
      </main>
    </div>
  </div>
</template>
