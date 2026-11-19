<script setup>
import { useRoute, useRouter } from "vue-router";
import { Compass, Star, MessageCircle, ShieldCheck } from "lucide-vue-next";
import { useSession } from "../stores/session";

const route = useRoute();
const router = useRouter();
const session = useSession();
defineProps({ unread: { type: Number, default: 0 } });

const tabs = [
  { to: "/explore", label: "广场", icon: Compass },
  { to: "/favorites", label: "收藏", icon: Star },
  { to: "/messages", label: "消息", icon: MessageCircle },
];
const adminTab = { to: "/admin/workbench", label: "管理", icon: ShieldCheck };
</script>

<template>
  <nav
    class="nx-glass fixed inset-x-0 bottom-0 z-40 flex h-[62px] border-t border-slate-800 pb-[env(safe-area-inset-bottom)] lg:hidden"
    aria-label="主导航"
  >
    <RouterLink
      v-for="t in tabs" :key="t.to" :to="t.to"
      class="relative flex flex-1 flex-col items-center gap-0.5 py-2 text-[10px]"
      :class="route.path.startsWith(t.to) ? 'text-sky-400 font-semibold' : 'text-slate-500'"
    >
      <span
        class="flex h-7 w-11 items-center justify-center rounded-full"
        :class="route.path.startsWith(t.to) ? 'bg-sky-400/15' : ''"
      >
        <component :is="t.icon" class="size-5" />
      </span>
      <span v-if="t.label === '消息' && session.unread > 0"
        class="absolute -top-0.5 ml-6 min-w-4 rounded-full nx-grad px-1 text-center text-[9px] font-bold leading-4 text-white">
        {{ session.unread > 99 ? "99+" : session.unread }}
      </span>
      {{ t.label }}
    </RouterLink>
    <RouterLink
      v-if="session.isAdmin" :to="adminTab.to"
      class="relative flex flex-1 flex-col items-center gap-0.5 py-2 text-[10px]"
      :class="route.path.startsWith('/admin') ? 'text-sky-400 font-semibold' : 'text-slate-500'"
    >
      <span
        class="flex h-7 w-11 items-center justify-center rounded-full"
        :class="route.path.startsWith('/admin') ? 'bg-sky-400/15' : ''"
      >
        <component :is="adminTab.icon" class="size-5" />
      </span>
      {{ adminTab.label }}
    </RouterLink>
  </nav>
</template>
