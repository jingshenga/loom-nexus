<script setup>
import { computed, onMounted, onUnmounted, watch, ref } from "vue";
import { RouterLink, RouterView, useRouter, useRoute } from "vue-router";
import { useSession } from "./stores/session";
import { api } from "./api/client";
import { useTheme } from "./theme";
import BottomNav from "./components/BottomNav.vue";
import {
  LogOut,
  Compass,
  Star,
  MessageSquare,
  ShieldCheck,
  Wrench,
  Activity,
  ShieldQuestion,
  Users,
  UserCog,
  ImageIcon,
  Bot,
  MessageCircle,
  Sun,
  Moon,
  MonitorSmartphone,
  Menu,
  X,
} from "lucide-vue-next";

const session = useSession();
const router = useRouter();
const route = useRoute();
const theme = useTheme();
const isAdminPage = computed(() => route.path.startsWith("/admin"));
const adminMenuOpen = ref(false);
const adminTitle = computed(() => adminNav.find((item) => route.path === item.to)?.label || "管理后台");

const nav = [
  { to: "/explore", label: "广场", icon: Compass },
  { to: "/favorites", label: "收藏", icon: Star },
  { to: "/messages", label: "消息", icon: MessageSquare },
];
const adminNav = [
  { to: "/admin/review", label: "审核", icon: ShieldCheck },
  { to: "/admin/ocr", label: "OCR核对", icon: ImageIcon },
  { to: "/admin/bots", label: "Bot质量", icon: Bot },
  { to: "/admin/chat", label: "聊天诊断", icon: MessageCircle },
  { to: "/admin/ocr-classifier", label: "分类训练", icon: Wrench },
  { to: "/admin/rules", label: "规则", icon: Wrench },
  { to: "/admin/probe", label: "穿透", icon: ShieldQuestion },
  { to: "/admin/gateway", label: "大盘", icon: Activity },
  { to: "/admin/users", label: "用户", icon: UserCog },
  { to: "/admin/accounts", label: "账号", icon: Users },
];

const themeIcon = computed(() =>
  theme.pref.value === "dark" ? Moon
    : theme.pref.value === "light" ? Sun
    : MonitorSmartphone,
);
function cycleTheme() {
  theme.set(
    theme.pref.value === "auto" ? "light"
      : theme.pref.value === "light" ? "dark"
      : "auto",
  );
}
function logout() {
  session.logout();
  session.unread = 0;
  document.title = "Loom 织网";
  router.push("/login");
}

// ---------- 未读提醒：Tab 角标 + 标题 + 桌面通知 ----------
let unreadTimer = null;
let lastUnread = 0;

async function pollUnread() {
  if (!session.loggedIn) return;
  try {
    const data = await api("/api/chat/sessions");
    const total = data.items.reduce((sum, x) => sum + (x.unread || 0), 0);
    session.unread = total;
    document.title = total > 0 ? `(${total}) Loom 织网` : "Loom 织网";
    const fresh = data.items.find((x) => x.unread > 0);
    if (
      total > lastUnread && fresh
      && localStorage.getItem("nexus-notify") === "1"
      && "Notification" in window
      && Notification.permission === "granted"
    ) {
      const n = new Notification(
        `${fresh.nickname || fresh.contact_username || "新消息"}`,
        { body: (fresh.last_content || "你有一条新消息").slice(0, 90) },
      );
      n.onclick = () => window.focus();
    }
    lastUnread = total;
  } catch { /* offline is fine */ }
}

watch(() => session.loggedIn, (on) => {
  if (on) {
    pollUnread();
    unreadTimer = setInterval(pollUnread, 20000);
  } else if (unreadTimer) {
    clearInterval(unreadTimer);
  }
});
onMounted(() => {
  if (session.loggedIn) {
    pollUnread();
    unreadTimer = setInterval(pollUnread, 20000);
  }
});
onUnmounted(() => unreadTimer && clearInterval(unreadTimer));
</script>

<template>
  <div class="min-h-screen">
    <!-- 普通用户保留站点导航；管理员页面使用独立分组导航。 -->
    <header class="nx-glass sticky top-0 z-40 border-b border-slate-800">
      <div class="mx-auto flex max-w-7xl items-center gap-x-4 gap-y-2 px-4 py-2.5 sm:py-3">
        <button v-if="session.isAdmin && isAdminPage" @click="adminMenuOpen = true"
          aria-label="打开管理导航" class="flex size-9 items-center justify-center rounded-lg hover:bg-slate-800 lg:hidden">
          <Menu class="size-5" />
        </button>
        <RouterLink to="/explore" class="text-lg font-bold tracking-widest">
          Loom <span class="nx-brand-text">织网</span>
        </RouterLink>
        <span v-if="isAdminPage" class="text-sm font-semibold text-slate-200 lg:hidden">{{ adminTitle }}</span>
        <nav v-if="!isAdminPage" class="hidden flex-1 gap-1 lg:flex" aria-label="主导航">
          <RouterLink
            v-for="item in nav" :key="item.to" :to="item.to"
            class="flex items-center gap-1.5 rounded-xl px-3 py-1.5 text-sm text-slate-300 hover:bg-slate-800 hover:text-white"
            active-class="bg-slate-800 text-sky-400 font-semibold"
          >
            <component :is="item.icon" class="size-4" />{{ item.label }}
          </RouterLink>
          <RouterLink v-if="session.isAdmin" to="/admin/review"
            class="flex items-center gap-1.5 rounded-xl px-3 py-1.5 text-sm text-slate-400 hover:bg-slate-800 hover:text-white">
            <ShieldCheck class="size-4" />管理后台
          </RouterLink>
        </nav>
        <button
          @click="cycleTheme"
          :title="`主题：${theme.pref.value === 'auto' ? '跟随系统' : theme.pref.value === 'light' ? '浅色' : '深色'}（点击切换）`"
          class="ml-auto flex items-center gap-1 rounded-xl px-2 py-1.5 text-sm text-slate-400 hover:text-white lg:ml-0"
        >
          <component :is="themeIcon" class="size-4" />
        </button>
        <button
          @click="logout"
          class="flex items-center gap-1 rounded-xl px-2 py-1.5 text-sm text-slate-400 hover:text-white"
        >
          <LogOut class="size-4" /><span class="hidden sm:inline">退出</span>
        </button>
      </div>
    </header>

    <aside v-if="session.isAdmin && isAdminPage"
      class="fixed bottom-0 left-0 top-[58px] z-30 hidden w-60 border-r border-slate-800 bg-slate-950/95 px-3 py-4 lg:block">
      <p class="mb-3 px-3 text-xs font-semibold uppercase tracking-wider text-slate-500">Nexus 管理</p>
      <nav class="space-y-1" aria-label="管理导航">
        <RouterLink v-for="item in adminNav" :key="item.to" :to="item.to"
          class="flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm text-slate-400 hover:bg-slate-900 hover:text-white"
          active-class="bg-slate-900 font-semibold text-sky-300">
          <component :is="item.icon" class="size-4 shrink-0" />{{ item.label }}
        </RouterLink>
      </nav>
    </aside>

    <div v-if="adminMenuOpen && session.isAdmin && isAdminPage" class="fixed inset-0 z-[60] bg-black/60 lg:hidden" @click="adminMenuOpen = false">
      <nav class="h-full w-[min(19rem,86vw)] overflow-y-auto border-r border-slate-800 bg-slate-950 p-3 shadow-2xl" aria-label="管理导航" @click.stop>
        <div class="mb-4 flex items-center justify-between px-2 py-2">
          <span class="font-semibold">Nexus 管理</span>
          <button @click="adminMenuOpen = false" aria-label="关闭管理导航" class="rounded-lg p-2 hover:bg-slate-800"><X class="size-5" /></button>
        </div>
        <RouterLink v-for="item in adminNav" :key="item.to" :to="item.to" @click="adminMenuOpen = false"
          class="flex items-center gap-3 rounded-lg px-3 py-3 text-sm text-slate-300 hover:bg-slate-900"
          active-class="bg-slate-900 font-semibold text-sky-300">
          <component :is="item.icon" class="size-4 shrink-0" />{{ item.label }}
        </RouterLink>
      </nav>
    </div>

    <main class="mx-auto max-w-7xl px-3 py-4 pb-24 sm:px-4 sm:py-6 lg:pb-6"
      :class="isAdminPage ? 'lg:ml-60 lg:max-w-none lg:px-6' : ''">
      <RouterView />
    </main>

    <BottomNav v-if="!isAdminPage" :unread="session.unread" />
  </div>
</template>
