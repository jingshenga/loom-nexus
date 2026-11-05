import { createRouter, createWebHistory } from "vue-router";
import { useSession } from "../stores/session";

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
    path: "/admin/review",
    component: () => import("../views/AdminReviewView.vue"),
    meta: { admin: true },
  },
  {
    path: "/admin/history",
    component: () => import("../views/AdminHistoryView.vue"),
    meta: { admin: true },
  },
  {
    path: "/admin/ocr",
    component: () => import("../views/AdminOcrView.vue"),
    meta: { admin: true },
  },
  {
    path: "/admin/ocr-classifier",
    component: () => import("../views/AdminOcrClassifierView.vue"),
    meta: { admin: true },
  },
  {
    path: "/admin/rules",
    component: () => import("../views/AdminRulesView.vue"),
    meta: { admin: true },
  },
  {
    path: "/admin/probe",
    component: () => import("../views/AdminProbeView.vue"),
    meta: { admin: true },
  },
  {
    path: "/admin/gateway",
    component: () => import("../views/AdminGatewayView.vue"),
    meta: { admin: true },
  },
  {
    path: "/admin/users",
    component: () => import("../views/AdminUsersView.vue"),
    meta: { admin: true },
  },
  {
    path: "/admin/accounts",
    component: () => import("../views/AdminAccountsView.vue"),
    meta: { admin: true },
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
