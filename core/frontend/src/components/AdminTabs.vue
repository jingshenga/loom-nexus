<script setup>
import { computed } from "vue";
import { RouterLink, useRoute } from "vue-router";

const route = useRoute();
const labels = {
  "/admin/review": "审核",
  "/admin/history": "资料库",
  "/admin/ocr": "OCR 核对",
  "/admin/bots": "Bot 质量",
  "/admin/chat": "聊天诊断",
  "/admin/ocr-classifier": "分类训练",
  "/admin/rules": "规则",
  "/admin/probe": "穿透",
  "/admin/gateway": "系统大盘",
  "/admin/users": "用户",
  "/admin/accounts": "账号",
};
const current = computed(() => labels[route.path] || "管理后台");
</script>

<template>
  <nav class="mb-4 flex min-w-0 items-center gap-2 text-xs text-slate-500" aria-label="面包屑">
    <RouterLink to="/admin" class="shrink-0 hover:text-slate-300">管理总览</RouterLink>
    <template v-if="route.path !== '/admin'">
      <span aria-hidden="true">/</span>
      <span class="truncate text-slate-300">{{ current }}</span>
    </template>
  </nav>
</template>
