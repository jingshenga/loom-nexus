<script setup>
import AdminTabs from "../components/AdminTabs.vue";
import { onMounted, onUnmounted, ref } from "vue";
import { api } from "../api/client";

const data = ref(null);
const sources = ref([]);
const dialogs = ref([]);
const flash = ref("");
const backupSelections = ref({});
const primarySelections = ref({});
let statusTimer;

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

async function refreshOverview() {
  try {
    data.value = await api("/api/admin/gateway/overview");
  } catch (e) {
    // Keep the last usable snapshot visible during a temporary refresh error.
  }
}

const STAGE_LABELS = {
  queued: "等待领取",
  resolve_profile: "解析个人资料",
  resolve_channel: "查找关联频道",
  check_source: "检查频道更新",
  read_pins: "读取置顶消息",
  read_followup: "补读置顶后续",
  scan_recent: "扫描最近消息",
  scan_archive: "扫描更多历史",
  collect_media: "整理消息与媒体",
  save_result: "保存结果 / 排 OCR",
  cooldown: "Telegram 冷却",
  completed: "已完成",
  checked: "检查完成",
  failed: "失败",
  in_progress: "执行中",
};

function stageLabel(stage) {
  return STAGE_LABELS[stage] || stage || "执行中";
}

function ageLabel(seconds) {
  const age = Math.max(0, Number(seconds) || 0);
  if (age < 60) return `${age} 秒前`;
  if (age < 3600) return `${Math.floor(age / 60)} 分钟前`;
  return `${Math.floor(age / 3600)} 小时 ${Math.floor((age % 3600) / 60)} 分钟前`;
}

function localTime(value) {
  return value ? new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" }) : "—";
}

function jobProgress(job) {
  if (!job.progress_total) return 0;
  return Math.max(0, Math.min(100, Math.round(job.progress_current * 100 / job.progress_total)));
}

async function requeueIneffective() {
  const ruleId = data.value?.collection?.rule_id;
  if (!ruleId) return notify("未找到启用的 V2 规则");
  try {
    const result = await api(`/api/admin/rules/${ruleId}/profile-collection/requeue-ineffective`, {
      method: "POST",
    });
    notify(`已将 ${result.requeued} 个无有效结果任务重新排队`);
    await refreshOverview();
  } catch (e) {
    notify(`重采排队失败: ${String(e.message).slice(0, 60)}`);
  }
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

onMounted(async () => {
  await loadAll();
  statusTimer = window.setInterval(refreshOverview, 30000);
});
onUnmounted(() => window.clearInterval(statusTimer));
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

    <section class="space-y-4 rounded-xl border border-violet-800/60 bg-slate-950/60 p-4">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 class="font-semibold text-violet-200">资料深采 V2 · 运行监控</h3>
          <p class="mt-1 text-xs text-slate-500">
            队列状态每 30 秒刷新；进度更新时间 {{ localTime(data.collection?.last_updated) }}
          </p>
        </div>
        <div class="flex gap-2">
          <button @click="refreshOverview" class="rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-300">立即刷新</button>
          <button @click="requeueIneffective" class="rounded-lg border border-amber-700/60 px-3 py-1.5 text-xs text-amber-300">
            重采无有效资料
          </button>
          <RouterLink to="/admin/rules" class="rounded-lg border border-violet-700/60 px-3 py-1.5 text-xs text-violet-300">打开 V2 规则 / 复核结果</RouterLink>
        </div>
      </div>

      <div class="grid grid-cols-2 gap-2 sm:grid-cols-4 xl:grid-cols-7">
        <div v-for="item in [
          ['待领取', data.collection.summary.pending, 'text-slate-200'],
          ['处理中', data.collection.summary.processing, 'text-sky-300'],
          ['等待冷却', data.collection.summary.deferred, 'text-amber-300'],
          ['已采集', data.collection.summary.fetched, 'text-emerald-300'],
          ['无可用资料', data.collection.summary.no_usable, 'text-slate-300'],
          ['失败', data.collection.summary.failed, 'text-rose-300'],
          ['疑似停滞', data.collection.summary.stale_processing, 'text-red-300'],
        ]" :key="item[0]" class="rounded-lg border border-slate-800 bg-slate-900 p-3">
          <div class="text-xl font-bold" :class="item[2]">{{ item[1] }}</div>
          <div class="mt-1 text-xs text-slate-500">{{ item[0] }}</div>
        </div>
      </div>

      <div class="grid gap-3 md:grid-cols-3">
        <article v-for="a in data.collection.accounts" :key="a.id" class="rounded-lg border border-slate-800 bg-slate-900 p-3">
          <div class="flex items-center justify-between gap-2">
            <div class="font-medium">{{ a.display_name || a.username || `账号#${a.id}` }}</div>
            <span class="rounded px-2 py-0.5 text-xs"
              :class="a.status === 'active' ? 'bg-emerald-600/20 text-emerald-300' : 'bg-slate-800 text-slate-400'">{{ a.status }}</span>
          </div>
          <div class="mt-2 grid grid-cols-2 gap-y-1 text-xs text-slate-400">
            <span>V2 正在执行</span><b class="text-sky-300">{{ a.processing_jobs }}</b>
            <span>24 小时已采集</span><b class="text-emerald-300">{{ a.fetched_24h }}</b>
            <span>可采来源</span><b>{{ a.eligible_sources }}</b>
            <span>最近完成</span><b class="font-normal">{{ localTime(a.last_finished_at) }}</b>
            <span>冷却截至</span><b class="font-normal text-amber-300">{{ a.cooldown_until ? localTime(a.cooldown_until) : '当前无冷却' }}</b>
          </div>
        </article>
        <div v-if="!data.collection.accounts.length" class="rounded-lg border border-slate-800 p-4 text-sm text-slate-500">暂无账号或资料深采任务</div>
      </div>

      <div>
        <h4 class="mb-2 text-sm font-medium text-slate-300">正在执行的任务</h4>
        <div v-if="data.collection.current_jobs.length" class="grid gap-3 lg:grid-cols-2">
          <article v-for="job in data.collection.current_jobs" :key="job.job_id" class="rounded-lg border p-3"
            :class="job.stale ? 'border-rose-700/70 bg-rose-950/20' : 'border-slate-800 bg-slate-900'">
            <div class="flex flex-wrap items-center justify-between gap-2">
              <div class="font-medium">@{{ job.contact_username }} <span class="text-xs text-slate-500">任务 #{{ job.job_id }}</span></div>
              <span v-if="job.stale" class="rounded bg-rose-600/20 px-2 py-1 text-xs text-rose-300">心跳超 5 分钟，疑似停滞</span>
              <span v-else class="rounded bg-sky-600/20 px-2 py-1 text-xs text-sky-300">{{ stageLabel(job.stage) }}</span>
            </div>
            <div class="mt-1 text-xs text-slate-500">来源 {{ job.source_title || `#${job.source_id}` }} · 账号 {{ job.account_name }} · 开始 {{ localTime(job.started_at) }}</div>
            <div class="mt-3 h-2 overflow-hidden rounded-full bg-slate-800">
              <div class="h-full rounded-full bg-sky-500 transition-all" :style="{ width: `${jobProgress(job)}%` }"></div>
            </div>
            <div class="mt-1 flex justify-between gap-2 text-xs text-slate-400">
              <span>{{ job.progress_note || stageLabel(job.stage) }}</span>
              <span class="shrink-0">心跳 {{ ageLabel(job.heartbeat_age_seconds) }}</span>
            </div>
          </article>
        </div>
        <div v-else class="rounded-lg border border-slate-800 bg-slate-900 p-4 text-sm text-slate-500">当前没有正在执行的 V2 任务</div>
      </div>

      <div class="grid gap-4 lg:grid-cols-2">
        <div>
          <h4 class="mb-2 text-sm font-medium text-slate-300">来源队列</h4>
          <div class="space-y-2">
            <div v-for="s in data.collection.sources" :key="s.id" class="rounded-lg border border-slate-800 bg-slate-900 p-3">
              <div class="font-medium">{{ s.title || `来源#${s.id}` }}</div>
              <div class="mt-1 text-xs text-slate-500">主账号 {{ s.primary_account }} · 备用 {{ (s.backup_accounts || []).map((a) => `${a.name}(${a.status})`).join('、') || '无' }}</div>
              <div class="mt-2 flex flex-wrap gap-2 text-xs">
                <span class="text-slate-300">待领取 {{ s.pending }}</span><span class="text-sky-300">处理中 {{ s.processing }}</span>
                <span class="text-amber-300">冷却 {{ s.deferred }}</span><span class="text-emerald-300">已采 {{ s.fetched }}</span>
                <span class="text-rose-300">需复核 {{ s.needs_attention }}</span>
              </div>
            </div>
            <div v-if="!data.collection.sources.length" class="text-sm text-slate-500">V2 尚未绑定采集来源</div>
          </div>
        </div>
        <div>
          <h4 class="mb-2 text-sm font-medium text-slate-300">待复核 / 可重采任务</h4>
          <div v-if="data.collection.attention.length" class="max-h-72 space-y-2 overflow-y-auto">
            <div v-for="job in data.collection.attention" :key="job.job_id" class="rounded-lg border border-slate-800 bg-slate-900 p-3">
              <div class="flex justify-between gap-2 text-sm">
                <span>@{{ job.contact_username }} · {{ job.source_title || `来源#${job.source_id}` }}</span>
                <span class="shrink-0 text-amber-300">{{ job.status }}</span>
              </div>
              <div class="mt-1 text-xs text-slate-500">账号 {{ job.account_name }} · 重试 {{ job.retry_count }} 次 · 更新 {{ localTime(job.updated_at) }}</div>
              <div v-if="job.error || job.progress_note" class="mt-1 line-clamp-2 text-xs text-slate-400">{{ job.error || job.progress_note }}</div>
              <div v-if="job.next_attempt_at" class="mt-1 text-xs text-amber-400">自动重试 {{ localTime(job.next_attempt_at) }}</div>
            </div>
          </div>
          <div v-else class="rounded-lg border border-slate-800 bg-slate-900 p-4 text-sm text-emerald-300">暂无待复核或可重采任务</div>
        </div>
      </div>
    </section>

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
