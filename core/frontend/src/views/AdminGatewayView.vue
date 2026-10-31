<script setup>
import AdminTabs from "../components/AdminTabs.vue";
import { onMounted, ref } from "vue";
import { api } from "../api/client";

const data = ref(null);
const sources = ref([]);
const dialogs = ref([]);
const flash = ref("");
const backupSelections = ref({});
const primarySelections = ref({});

function notify(msg) {
  flash.value = msg;
  setTimeout(() => (flash.value = ""), 2500);
}

async function loadAll() {
  data.value = await api("/api/admin/gateway/overview");
  sources.value = (await api("/api/admin/sources")).items;
  backupSelections.value = Object.fromEntries(
    sources.value.map((s) => [s.id, (s.backup_accounts || []).map((a) => String(a.id))]),
  );
  primarySelections.value = Object.fromEntries(
    sources.value.map((s) => [s.id, String(s.account_id || "")]),
  );
  dialogs.value = (await api("/api/admin/sources/dialogs")).items;
}

const boundPeerIds = () => new Set(sources.value.map((s) => s.peer_id));
const backupCandidates = (s) => {
  const visible = new Set(dialogs.value.filter((d) => d.peer_id === s.peer_id).map((d) => String(d.account_id)));
  const selected = new Set(backupSelections.value[s.id] || []);
  return data.value.accounts.filter((a) => a.id !== s.account_id
    && (visible.has(String(a.id)) || selected.has(String(a.id))));
};
const primaryCandidates = (s) => {
  const visible = new Set(dialogs.value.filter((d) => d.peer_id === s.peer_id).map((d) => String(d.account_id)));
  return data.value.accounts.filter((a) => a.status === "active"
    && (visible.has(String(a.id)) || String(a.id) === String(s.account_id)));
};

async function bindDialog(d) {
  try {
    const r = await api("/api/admin/sources", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        peer_id: d.peer_id,
        title: d.title,
        username: d.username,
        account_id: d.account_id,
      }),
    });
    notify(`已绑定采集：${d.title}（来源#${r.source_id}）`);
    await loadAll();
  } catch (e) {
    notify(`绑定失败: ${String(e.message).slice(0, 60)}`);
  }
}

async function toggleSource(s) {
  await api(`/api/admin/sources/${s.id}/active`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ is_active: !s.is_active }),
  });
  await loadAll();
}

async function saveBackupAccounts(s) {
  try {
    await api(`/api/admin/sources/${s.id}/backup-accounts`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ account_ids: backupSelections.value[s.id].map(Number) }),
    });
    notify(`已保存 ${s.title || `来源#${s.id}`} 的备用账号`);
    await loadAll();
  } catch (e) {
    notify(`保存失败: ${String(e.message).slice(0, 60)}`);
  }
}

async function savePrimaryAccount(s) {
  try {
    const accountId = Number(primarySelections.value[s.id]);
    await api(`/api/admin/sources/${s.id}/primary-account`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ account_id: accountId }),
    });
    notify(`已更新 ${s.title || `来源#${s.id}`} 的主账号`);
    await loadAll();
  } catch (e) {
    notify(`主账号保存失败: ${String(e.message).slice(0, 60)}`);
  }
}

async function backfillAll() {
  try {
    const r = await api("/api/admin/sources/backfill-all", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ limit: 100 }),
    });
    notify(`已发起 ${r.queued} 个来源的历史回填（每群最多 ${r.limit} 条，提取后高置信自动上线）`);
  } catch (e) {
    notify(`发起失败: ${String(e.message).slice(0, 60)}`);
  }
}

async function backfillMedia() {
  try {
    notify("媒体回填进行中（下载图片/视频，视频超过 50MB 跳过）…");
    const r = await api("/api/admin/sources/backfill-media-all", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ limit: 100 }),
    });
    notify(`已发起 ${r.queued} 个来源的媒体回填`);
  } catch (e) {
    notify(`发起失败: ${String(e.message).slice(0, 60)}`);
  }
}

onMounted(loadAll);
</script>

<template>
  <div v-if="data" class="space-y-6">
  <AdminTabs />

    <div class="flex items-center gap-3">
      <h2 class="text-lg font-semibold">系统大盘</h2>
      <span v-if="flash" class="rounded bg-emerald-600/20 px-3 py-1 text-xs text-emerald-300">{{ flash }}</span>
    </div>

    <div class="grid grid-cols-2 gap-3 md:grid-cols-4">
      <div v-for="(count, state) in data.extract" :key="state"
        class="rounded-xl border border-slate-800 bg-slate-900 p-4">
        <div class="text-2xl font-bold text-sky-400">{{ count }}</div>
        <div class="text-xs text-slate-500">抽取队列 · {{ state }}</div>
      </div>
      <div v-for="(count, state) in data.outbox" :key="state"
        class="rounded-xl border border-slate-800 bg-slate-900 p-4">
        <div class="text-2xl font-bold text-amber-400">{{ count }}</div>
        <div class="text-xs text-slate-500">发信队列 · {{ state }}</div>
      </div>
    </div>

    <div class="overflow-x-auto rounded-xl border border-slate-800">
      <table class="w-full text-sm">
        <thead class="bg-slate-900 text-left text-xs text-slate-500">
          <tr><th class="px-4 py-2.5">账号</th><th class="px-4 py-2.5">UID</th><th class="px-4 py-2.5">状态</th><th class="px-4 py-2.5">最近活跃</th></tr>
        </thead>
        <tbody>
          <tr v-for="a in data.accounts" :key="a.id" class="border-t border-slate-800">
            <td class="px-4 py-2.5">{{ a.display_name || a.username || `账号#${a.id}` }}</td>
            <td class="px-4 py-2.5 text-slate-500">{{ a.uid ?? "—" }}</td>
            <td class="px-4 py-2.5">
              <span class="rounded px-2 py-0.5 text-xs"
                :class="a.status === 'active' ? 'bg-emerald-600/20 text-emerald-300' : 'bg-slate-800 text-slate-400'">
                {{ a.status }}
              </span>
            </td>
            <td class="px-4 py-2.5 text-slate-500">{{ a.last_active_at ? new Date(a.last_active_at).toLocaleString("zh-CN") : "—" }}</td>
          </tr>
          <tr v-if="!data.accounts.length"><td colspan="4" class="px-4 py-8 text-center text-slate-600">暂无网关账号（等待 runner.login）</td></tr>
        </tbody>
      </table>
    </div>

    <div>
      <div class="mb-2 flex items-center gap-3">
        <h3 class="text-sm font-semibold text-slate-300">来源管理（群组 / 频道绑定）</h3>
        <button @click="backfillAll"
          class="rounded-lg border border-sky-700/60 px-3 py-1 text-xs text-sky-300 hover:bg-sky-900/30">
          回填全部历史（每群 100 条）
        </button>
        <button @click="backfillMedia"
          class="rounded-lg border border-violet-700/60 px-3 py-1 text-xs text-violet-300 hover:bg-violet-900/30">
          补充媒体（为已采消息下载图片/视频）
        </button>
      </div>
      <div class="grid gap-4 lg:grid-cols-2">
        <div class="overflow-x-auto rounded-xl border border-slate-800">
          <div class="bg-slate-900 px-4 py-2 text-xs text-slate-500">可采集（账号可见的群组/频道，由网关定期刷新）</div>
          <table class="w-full text-sm">
            <tbody>
              <tr v-for="d in dialogs" :key="`${d.account_id}-${d.peer_id}`" class="border-t border-slate-800">
                <td class="px-4 py-2.5">
                  <div class="font-medium">{{ d.title || `#${d.peer_id}` }}</div>
                  <div class="text-xs text-slate-500">{{ d.kind }} · {{ d.username ? "@" + d.username : d.peer_id }} · 账号 {{ d.account_name || d.account_id }}</div>
                </td>
                <td class="px-4 py-2.5 text-right">
                  <button v-if="!boundPeerIds().has(d.peer_id)" @click="bindDialog(d)"
                    class="rounded-lg bg-sky-600 px-3 py-1.5 text-xs font-medium hover:bg-sky-500">绑定采集</button>
                  <span v-else class="rounded bg-slate-800 px-2 py-1 text-xs text-slate-500">已绑定</span>
                </td>
              </tr>
              <tr v-if="!dialogs.length"><td class="px-4 py-8 text-center text-slate-600">暂无数据——账号登录后自动列出其加入的群组/频道</td></tr>
            </tbody>
          </table>
        </div>
        <div class="overflow-x-auto rounded-xl border border-slate-800">
          <div class="bg-slate-900 px-4 py-2 text-xs text-slate-500">已绑定来源</div>
          <table class="w-full text-sm">
            <tbody>
              <tr v-for="s in sources" :key="s.id" class="border-t border-slate-800">
                <td class="px-4 py-2.5">
                  <div class="font-medium">{{ s.title || `来源#${s.id}` }}</div>
                  <div class="text-xs text-slate-500">
                    {{ s.username ? "@" + s.username : s.peer_id }} · 账号 {{ s.account_name || s.account_id || "未分配" }}
                    · 已采集 {{ s.raw_count }} 条 · 游标 {{ s.last_msg_id }}
                  </div>
                  <div class="mt-2 flex flex-wrap items-center gap-2">
                    <span class="text-xs text-slate-500">主账号</span>
                    <select v-model="primarySelections[s.id]"
                      class="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs text-slate-300">
                      <option v-for="a in primaryCandidates(s)" :key="a.id" :value="String(a.id)">
                        {{ a.display_name || a.username || `账号#${a.id}` }} · {{ a.status }}
                      </option>
                    </select>
                    <button v-if="String(primarySelections[s.id]) !== String(s.account_id)"
                      @click="savePrimaryAccount(s)"
                      class="rounded border border-slate-700 px-2 py-1 text-xs text-slate-300 hover:bg-slate-800">保存主账号</button>
                  </div>
                  <div class="mt-2 flex flex-wrap items-center gap-2">
                    <span class="text-xs text-slate-500">备用账号</span>
                    <label v-for="a in backupCandidates(s)" :key="a.id"
                      class="flex items-center gap-1 text-xs text-slate-400">
                      <input v-model="backupSelections[s.id]" type="checkbox" :value="String(a.id)"
                        class="accent-sky-500" />
                      {{ a.display_name || a.username || `账号#${a.id}` }} · {{ a.status }}
                    </label>
                    <span v-if="!backupCandidates(s).length" class="text-xs text-slate-600">暂无其他账号已确认可访问该来源</span>
                    <button @click="saveBackupAccounts(s)"
                      class="rounded border border-slate-700 px-2 py-1 text-xs text-slate-300 hover:bg-slate-800">保存</button>
                  </div>
                </td>
                <td class="px-4 py-2.5 text-right">
                  <button @click="toggleSource(s)"
                    class="rounded-lg border px-3 py-1.5 text-xs"
                    :class="s.is_active
                      ? 'border-emerald-700/60 text-emerald-300 hover:bg-emerald-900/30'
                      : 'border-slate-600 text-slate-400 hover:bg-slate-800'">
                    {{ s.is_active ? "采集中" : "已暂停" }}
                  </button>
                </td>
              </tr>
              <tr v-if="!sources.length"><td class="px-4 py-8 text-center text-slate-600">尚未绑定任何来源</td></tr>
            </tbody>
          </table>
        </div>
      </div>
      <p class="mt-2 text-xs text-slate-600">来源监听在主账号断连或未授权时按备用顺序接管；资料深采 V2 会在该来源的主、备用账号中轮询已授权且未冷却的账号。触发 FloodWait 的任务保留冷却，其他独立任务继续分给可用账号。聊天会话按账号负载分配，建立后固定账号。</p>
    </div>
  </div>
</template>
