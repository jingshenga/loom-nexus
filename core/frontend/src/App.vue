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
  Sun,
  Moon,
  MonitorSmartphone,
} from "lucide-vue-next";

const session = useSession();
const router = useRouter();
const route = useRoute();
const theme = useTheme();

const nav = [
  { to: "/explore", label: "广场", icon: Compass },
  { to: "/favorites", label: "收藏", icon: Star },
  { to: "/messages", label: "消息", icon: MessageSquare },
];
const adminNav = [
  { to: "/admin/review", label: "审核", icon: ShieldCheck },
  { to: "/admin/ocr", label: "OCR核对", icon: ImageIcon },
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
    <!-- 玻璃拟态顶栏：桌面完整导航，手机只留 logo + 主题 + 退出 -->
    <header class="nx-glass sticky top-0 z-40 border-b border-slate-800">
      <div class="mx-auto flex max-w-7xl items-center gap-x-4 gap-y-2 px-4 py-2.5 sm:py-3">
        <RouterLink to="/explore" class="text-lg font-bold tracking-widest">
          Loom <span class="nx-brand-text">织网</span>
        </RouterLink>
        <nav class="hidden flex-1 gap-1 lg:flex" aria-label="主导航">
          <RouterLink
            v-for="item in nav" :key="item.to" :to="item.to"
            class="flex items-center gap-1.5 rounded-xl px-3 py-1.5 text-sm text-slate-300 hover:bg-slate-800 hover:text-white"
            active-class="bg-slate-800 text-sky-400 font-semibold"
          >
            <component :is="item.icon" class="size-4" />{{ item.label }}
          </RouterLink>
          <span class="mx-1 self-center text-slate-700">｜</span>
          <template v-if="session.isAdmin">
            <RouterLink
              v-for="item in adminNav" :key="item.to" :to="item.to"
              class="flex items-center gap-1.5 rounded-xl px-3 py-1.5 text-sm text-slate-400 hover:bg-slate-800 hover:text-white"
              active-class="bg-slate-800 text-sky-400 font-semibold"
            >
              <component :is="item.icon" class="size-4" />{{ item.label }}
            </RouterLink>
          </template>
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

    <main class="mx-auto max-w-7xl px-3 py-4 pb-24 sm:px-4 sm:py-6 lg:pb-6">
      <RouterView />
    </main>

    <BottomNav :unread="session.unread" />
  </div>
</template>
