import { defineStore } from "pinia";

// Server-issued identity: the login token carries the site-user id and
// role; everything (favorites, chat, admin pages) derives from it.
export const useSession = defineStore("session", {
  state: () => ({
    unread: 0,
    token: localStorage.getItem("nexus-token") || "",
    userId: Number(localStorage.getItem("nexus-uid")) || 0,
    username: localStorage.getItem("nexus-username") || "",
    isAdmin: localStorage.getItem("nexus-admin") === "1",
  }),
  getters: {
    loggedIn: (state) => Boolean(state.token),
  },
  actions: {
    login(data) {
      this.token = data.token;
      this.userId = data.user_id;
      this.username = data.username;
      this.isAdmin = Boolean(data.is_admin);
      localStorage.setItem("nexus-token", data.token);
      localStorage.setItem("nexus-uid", String(data.user_id));
      localStorage.setItem("nexus-username", data.username);
      localStorage.setItem("nexus-admin", data.is_admin ? "1" : "0");
    },
    logout() {
      this.unread = 0;
      this.token = "";
      this.userId = 0;
      this.username = "";
      this.isAdmin = false;
      ["nexus-token", "nexus-uid", "nexus-username", "nexus-admin", "nexus-user-id2"]
        .forEach((k) => localStorage.removeItem(k));
    },
  },
});
