import { createRouter, createWebHistory } from "vue-router";
import { useSession } from "../stores/session";

const routes = [
  { path: "/", beforeEnter: () => useSession().isAdmin ? "/admin" : "/explore" },
  { path: "/login", component: () => import("../views/LoginView.vue") },
  { path: "/explore", component: () => import("../views/ExploreView.vue") },
  { path: "/explore/:id", component: () => import("../views/DetailView.vue") },
  { path: "/favorites", component: () => import("../views/FavoritesView.vue") },
  {
    path: "/messages",
    component: () => import("../views/MessagesView.vue"),
  },
  {
    path: "/admin",
    component: () => import("../layouts/AdminLayout.vue"),
    meta: { admin: true },
    children: [
      { path: "", component: () => import("../views/AdminDashboardView.vue"), meta: { title: "管理总览" } },
      { path: "review", component: () => import("../views/AdminReviewView.vue"), meta: { title: "Listing 审核" } },
      { path: "history", component: () => import("../views/AdminHistoryView.vue"), meta: { title: "未命中与资料库" } },
      { path: "ocr", component: () => import("../views/AdminOcrView.vue"), meta: { title: "OCR 核对" } },
      { path: "ocr-classifier", component: () => import("../views/AdminOcrClassifierView.vue"), meta: { title: "分类器训练" } },
      { path: "models", component: () => import("../views/AdminModelsView.vue"), meta: { title: "模型中心" } },
      { path: "rules", component: () => import("../views/AdminRulesView.vue"), meta: { title: "规则管理" } },
      { path: "lifecycle", component: () => import("../views/AdminLifecycleView.vue"), meta: { title: "Lifecycle 审核" } },
      { path: "rule-router", component: () => import("../views/AdminRuleRouterView.vue"), meta: { title: "来源与路由" } },
      { path: "rule-review", component: () => import("../views/AdminRuleReviewView.vue"), meta: { title: "规则待审" } },
      { path: "probe", component: () => import("../views/AdminProbeView.vue"), meta: { title: "连通检查" } },
      { path: "gateway", component: () => import("../views/AdminGatewayView.vue"), meta: { title: "系统状态" } },
      { path: "users", component: () => import("../views/AdminUsersView.vue"), meta: { title: "用户权限" } },
      { path: "accounts", component: () => import("../views/AdminAccountsView.vue"), meta: { title: "采集账号" } },
      { path: "bots", component: () => import("../views/AdminBotView.vue"), meta: { title: "Bot 质量管理" } },
      { path: "chat", component: () => import("../views/AdminChatView.vue"), meta: { title: "聊天诊断" } },
    ],
  },
];

const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior(to, from, savedPosition) {
    // ExploreView restores its own saved card anchor after loading the list.
    if (to.path === "/explore") return false;
    return savedPosition || { top: 0, left: 0 };
  },
});

router.beforeEach((to) => {
  const session = useSession();
  // the whole site sits behind the account gate
  if (to.path !== "/login" && !session.loggedIn) return "/login";
  if (to.meta.admin && !session.isAdmin) return "/explore";
});

export default router;
