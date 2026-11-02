<script setup>
import { useRoute } from "vue-router";
import { ShieldCheck, ImageIcon, Wrench, Activity, ShieldQuestion, Users, UserCog, Database } from "lucide-vue-next";

const route = useRoute();
const tabs = [
  { to: "/admin/review", label: "审核", icon: ShieldCheck },
  { to: "/admin/history", label: "资料库", icon: Database },
  { to: "/admin/ocr", label: "OCR核对", icon: ImageIcon },
  { to: "/admin/ocr-classifier", label: "分类训练", icon: Wrench },
  { to: "/admin/rules", label: "规则", icon: Wrench },
  { to: "/admin/probe", label: "穿透", icon: ShieldQuestion },
  { to: "/admin/gateway", label: "大盘", icon: Activity },
  { to: "/admin/users", label: "用户", icon: UserCog },
  { to: "/admin/accounts", label: "账号", icon: Users },
];
</script>

<template>
  <!-- 手机：管理页顶部页签（桌面用顶栏导航，隐藏） -->
  <div class="nx-noscroll -mx-1 mb-4 flex gap-2 overflow-x-auto px-1 lg:hidden">
    <RouterLink
      v-for="t in tabs" :key="t.to" :to="t.to"
      class="flex shrink-0 items-center gap-1.5 rounded-full border px-3.5 py-1.5 text-xs"
      :class="route.path === t.to
        ? 'border-transparent nx-grad font-semibold text-white'
        : 'border-slate-700 bg-slate-900 text-slate-400'"
    >
      <component :is="t.icon" class="size-3.5" />{{ t.label }}
    </RouterLink>
  </div>
</template>
