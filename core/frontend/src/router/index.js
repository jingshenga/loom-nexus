import { defineAsyncComponent } from "vue";
import { createRouter, createWebHistory } from "vue-router";
import { useSession } from "../stores/session";

export const adminCenters = {
  workbench: { path: "/admin/workbench", label: "审核工作台" },
  rules: { path: "/admin/rules", label: "规则与样本" },
  gateway: { path: "/admin/gateway", label: "网关与运维" },
};

const center = () => import("../views/AdminCenterView.vue");
const tab = (key, label, loader) => ({ key, label, component: defineAsyncComponent(loader) });
const legacy = (path, target, section) => ({
  path: `/admin/${path}`,
  redirect: (to) => ({ path: adminCenters[target].path, query: { ...to.query, section }, hash: to.hash }),
});

const routes = [
  { path: "/", redirect: "/explore" },
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
    redirect: "/admin/workbench",
  },
  {
    path: adminCenters.workbench.path, component: center, meta: { admin: true },
    props: { tabs: [
      tab("review", "卡片纠偏", () => import("../views/AdminWorkbenchView.vue")),
      tab("bulk", "批量审核与去重", () => import("../views/AdminReviewView.vue")),
      tab("ocr", "图片核对", () => import("../views/AdminOcrView.vue")),
    ] },
  },
  {
    path: adminCenters.rules.path, component: center, meta: { admin: true },
    props: { tabs: [
      tab("extract", "抽取规则", () => import("../views/AdminRulesView.vue")),
      tab("classifier", "OCR 样本分类", () => import("../views/AdminOcrClassifierView.vue")),
      tab("history", "历史抽样库", () => import("../views/AdminHistoryView.vue")),
    ] },
  },
  {
    path: adminCenters.gateway.path, component: center, meta: { admin: true },
    props: { tabs: [
      tab("monitor", "网关大盘与监控", () => import("../views/AdminGatewayView.vue")),
      tab("accounts", "TG 账号管理与会话", () => import("../views/AdminAccountsView.vue")),
      tab("probe", "简介穿透审批", () => import("../views/AdminProbeView.vue")),
      tab("users", "系统用户", () => import("../views/AdminUsersView.vue")),
    ] },
  },
  legacy("review", "workbench", "bulk"),
  legacy("ocr", "workbench", "ocr"),
  legacy("ocr-classifier", "rules", "classifier"),
  legacy("history", "rules", "history"),
  legacy("accounts", "gateway", "accounts"),
  legacy("probe", "gateway", "probe"),
  legacy("users", "gateway", "users"),
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
