<script setup>
import { onMounted, ref } from "vue";
import { api } from "../api/client";

const data = ref(null);
const sources = ref([]);
const dialogs = ref([]);
const flash = ref("");
const backupSelections = ref({});

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
  dialogs.value = (await api("/api/admin/sources/dialogs")).items;
  loadOrigins();
}

const boundPeerIds = () => new Set(sources.value.map((s) => s.peer_id));

// ---- 上游母频发现：转发链聚合排行 + 一键纳管 ----

const forwardOrigins = ref([]);
const originAccountId = ref(null);

async function loadOrigins() {
  try {
    forwardOrigins.value = (await api("/api/admin/sources/forward-origins?limit=50")).items;
  } catch {
    forwardOrigins.value = [];  // 表未迁移/旧库：面板静默降级
  }
  if (!originAccountId.value) {
    originAccountId.value = data.value?.accounts?.[0]?.id || null;
  }
}

const originPeerId = (origin) => Number(`-100${origin.from_id}`);
const originTitle = (origin) => origin.from_name || origin.known_title || `频道 ${origin.from_id}`;
const originBound = (origin) => sources.value.some(
  (s) => String(s.peer_id) === String(originPeerId(origin)) || String(s.peer_id) === String(origin.from_id),
);

async function adoptOrigin(origin) {
  if (!originAccountId.value) {
    notify("请先选择用于监听该母频的网关账号");
    return;
  }
  try {
    const r = await api("/api/admin/sources", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        peer_id: originPeerId(origin),
        title: originTitle(origin),
        username: null,
        account_id: originAccountId.value,
      }),
    });
    notify(`已纳管母频「${originTitle(origin)}」为采集来源#${r.source_id}`);
    await loadAll();
  } catch (e) {
    notify(`纳管失败: ${String(e.message).slice(0, 70)}`);
  }
}

async function copyOriginId(origin) {
  const text = String(originPeerId(origin));
  try {
    await navigator.clipboard.writeText(text);
    notify(`已复制母频 ID：${text}`);
  } catch {
    notify(`母频 ID：${text}（复制失败，请手动记录）`);
  }
}
const backupCandidates = (s) => {
  const visible = new Set(dialogs.value.filter((d) => d.peer_id === s.peer_id).map((d) => String(d.account_id)));
  const selected = new Set(backupSelections.value[s.id] || []);
  return data.value.accounts.filter((a) => a.id !== s.account_id
    && (visible.has(String(a.id)) || selected.has(String(a.id))));
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

// ---- 单来源采集操作：增量同步 / 游标深采 / 画像学习 ----

async function backfillOne(s, limit, offsetId, label) {
  try {
    await api(`/api/admin/sources/${s.id}/backfill`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ limit, offset_id: offsetId }),
    });
    notify(`${s.title || `来源#${s.id}`}：${label}任务已排队（去重幂等，可在队列看板观察进度）`);
  } catch (e) {
    notify(`发起失败: ${String(e.message).slice(0, 60)}`);
  }
}

const learning = ref({});

async function learnProfile(s) {
  learning.value = { ...learning.value, [s.id]: true };
  try {
    const r = await api(`/api/admin/sources/${s.id}/learn-profile`, { method: "POST" });
    notify(
      `${s.title || `来源#${s.id}`}：画像已学习 —— ${r.style_notes || "完成"}` +
      `（few-shot ${r.few_shot_count} 条，价格写法 ${(r.price_patterns || []).length} 种）`,
    );
    await loadAll();
  } catch (e) {
    notify(`画像学习失败: ${String(e.message).slice(0, 90)}`);
  } finally {
    learning.value = { ...learning.value, [s.id]: false };
  }
}

const profileSummary = (s) => {
  const p = s.extraction_profile;
  if (!p) return null;
  const bits = [p.style_notes, (p.price_patterns || []).slice(0, 3).join(" / ")]
    .filter(Boolean);
  return bits.join(" · ") || "已学习";
};

onMounted(loadAll);
</script>

<template>
  <div v-if="data" class="space-y-6">

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

    <!-- 上游母频发现：转发链聚合排行（高频被转发的源头频道优先） -->
    <div class="rounded-xl border border-violet-800/50 bg-violet-950/10 p-4">
      <div class="mb-3 flex flex-wrap items-center gap-3">
        <h3 class="text-sm font-semibold text-violet-300">上游源头挖掘 (Top Forwarded Origins)</h3>
        <select v-model="originAccountId"
          class="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs text-slate-300">
          <option :value="null" disabled>选择纳管账号</option>
          <option v-for="a in data?.accounts || []" :key="a.id" :value="a.id">
            {{ a.display_name || `账号#${a.id}` }}
          </option>
        </select>
        <span class="text-[11px] text-slate-500">母频 = 高价值卡片的原始出处；纳管后即可直接监听源头，跳过二道贩子。</span>
      </div>
      <div class="overflow-x-auto rounded-lg border border-slate-800">
        <table class="w-full text-sm">
          <thead class="bg-slate-900 text-left text-xs text-slate-500">
            <tr>
              <th class="px-3 py-2">母频</th>
              <th class="px-3 py-2">转发频次</th>
              <th class="px-3 py-2">传播群组</th>
              <th class="px-3 py-2">最近转发</th>
              <th class="px-3 py-2 text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="origin in forwardOrigins" :key="origin.from_id"
              class="border-t border-slate-800">
              <td class="px-3 py-2">
                <div class="flex items-center gap-2">
                  <span class="max-w-52 truncate font-medium"
                    :title="originTitle(origin)">{{ originTitle(origin) }}</span>
                  <span v-if="origin.is_already_source"
                    class="rounded bg-emerald-900/40 px-1.5 py-0.5 text-[10px] text-emerald-300">已纳管</span>
                  <span v-else class="rounded bg-amber-900/40 px-1.5 py-0.5 text-[10px] text-amber-300">未纳管</span>
                </div>
                <div class="text-[11px] text-slate-500">
                  {{ origin.from_type || "channel" }} · ID {{ origin.from_id }}
                </div>
              </td>
              <td class="px-3 py-2">
                <span class="rounded bg-violet-900/40 px-2 py-0.5 text-xs font-semibold text-violet-300">
                  {{ origin.forward_count }} 次
                </span>
              </td>
              <td class="px-3 py-2 text-xs text-slate-300">{{ origin.source_count }} 个</td>
              <td class="px-3 py-2 text-xs text-slate-500">
                {{ origin.latest_forwarded_at ? new Date(origin.latest_forwarded_at).toLocaleString("zh-CN") : "—" }}
              </td>
              <td class="px-3 py-2 text-right">
                <div class="flex items-center justify-end gap-1.5">
                  <button v-if="!originBound(origin)" @click="adoptOrigin(origin)"
                    class="rounded-lg bg-violet-700 px-2.5 py-1 text-xs font-medium text-white hover:bg-violet-600">
                    一键纳管
                  </button>
                  <button @click="copyOriginId(origin)"
                    class="rounded-lg border border-slate-600 px-2.5 py-1 text-xs text-slate-400 hover:bg-slate-800">
                    复制 ID
                  </button>
                </div>
              </td>
            </tr>
            <tr v-if="!forwardOrigins.length">
              <td colspan="5" class="px-4 py-6 text-center text-xs text-slate-600">
                暂无转发链数据。Bridge 会在实时监听与历史回采时自动记录转发出处（fwd_from），积累后此处即出现母频排行。
              </td>
            </tr>
          </tbody>
        </table>
      </div>
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
                  <div class="flex items-center gap-2">
                    <span class="font-medium">{{ s.title || `来源#${s.id}` }}</span>
                    <span v-if="s.extraction_profile"
                      class="rounded bg-sky-900/40 px-1.5 py-0.5 text-[10px] text-sky-300">✨ 画像已学习</span>
                  </div>
                  <div class="text-xs text-slate-500">
                    {{ s.username ? "@" + s.username : s.peer_id }} · 账号 {{ s.account_name || s.account_id || "未分配" }}
                  </div>
                  <!-- 采集水位：最新 ID / 最早 ID / 本地卡片数 -->
                  <div class="mt-0.5 text-[11px] text-slate-600">
                    水位 {{ s.max_msg_id || 0 }} ← {{ s.min_msg_id || 0 }}
                    · 本地 {{ s.raw_count }} 条 · 游标 {{ s.last_msg_id }}
                  </div>
                  <div v-if="profileSummary(s)"
                    class="mt-0.5 truncate text-[11px] text-sky-300/80"
                    :title="profileSummary(s)">✨ {{ profileSummary(s) }}</div>
                  <div class="mt-1.5 flex flex-wrap gap-1.5">
                    <button @click="backfillOne(s, 50, 0, '增量同步')"
                      class="rounded-lg border border-slate-600 px-2.5 py-1 text-xs text-slate-300 hover:bg-slate-800">
                      增量同步
                    </button>
                    <button @click="backfillOne(s, 100, -1, '向后深采 100 条')"
                      class="rounded-lg border border-sky-700/60 px-2.5 py-1 text-xs text-sky-300 hover:bg-sky-900/30">
                      深采 100
                    </button>
                    <button @click="backfillOne(s, 500, -1, '向后深采 500 条')"
                      class="rounded-lg border border-sky-700/60 px-2.5 py-1 text-xs text-sky-300 hover:bg-sky-900/30">
                      深采 500
                    </button>
                    <button @click="learnProfile(s)" :disabled="learning[s.id]"
                      class="rounded-lg border px-2.5 py-1 text-xs disabled:opacity-40"
                      :class="s.extraction_profile
                        ? 'border-violet-700/60 text-violet-300 hover:bg-violet-900/30'
                        : 'border-amber-700/60 text-amber-300 hover:bg-amber-900/30'">
                      {{ learning[s.id] ? "学习中…" : s.extraction_profile ? "重新学习" : "学习画像" }}
                    </button>
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
                <td class="px-4 py-2.5 text-right align-top">
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
      <p class="mt-2 text-xs text-slate-600">备用账号仅在主账号断连或未授权时按顺序接管来源；Telegram 返回 FloodWait 时会等待其指定时长，不会切换账号继续同一受限请求。</p>
    </div>
  </div>
</template>
