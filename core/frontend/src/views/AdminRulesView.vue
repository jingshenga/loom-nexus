<script setup>
import AdminTabs from "../components/AdminTabs.vue";
import { onMounted, ref } from "vue";
import { Save, FlaskConical, Sparkles, Trash2, Link2, Settings2, PlugZap, RefreshCw } from "lucide-vue-next";
import { api } from "../api/client";

const rules = ref([]);
const current = ref(null);
const configText = ref("");
const sampleText = ref("");
const dryrun = ref(null);
const llm = ref({ available: false, model: null });
const flash = ref("");
const sources = ref([]);
const bindSourceId = ref(null);
const sampleSourceId = ref(null);
const sampleLimit = ref(20);
const showSettings = ref(false);
const llmForm = ref({ base_url: "", model: "", api_key: "", allow_private: false });
const llmTestResult = ref("");
const apForm = ref({ autopublish_enabled: true, autopublish_threshold: 0.8 });

const EMPTY_CONFIG = {
  gate: { require_any_keywords: [], blocked_sender_peer_ids: [] },
  extract: { nickname: [], username: [], price: [], district: [], currency_map: {} },
  ocr: { enabled: true, trigger_when_missing: ["price"] },
  lifecycle_terms: ["已售", "已出", "离开", "休息", "已结"],
  display: { exclude_patterns: [] },
};

const FIELD_HELP = [
  ["_说明（任意层级）",
    "注释键，引擎自动忽略，可在 JSON 任何位置写给自己看。如 \"_说明\": \"价格取第一个数字，乘100变成元\""],
  ["gate.require_any_keywords",
    "门禁开关：正文含任一关键词才处理，空数组=不过滤。写法 [\"老师编号\"]"],
  ["gate.blocked_sender_peer_ids",
    "发送者黑名单：这些频道号发的消息直接丢弃。写法 [-1001942025496]"],
  ["extract.nickname / username / district",
    "正则数组（Python re 语法），按顺序试，首个命中生效；正则必须恰好带 1 个捕获组 (…) 括住要的值。username 存储时自动去掉前导@，捕获组里带不带 @ 都不会出现双@@"],
  ["extract.price",
    "价格正则数组，同上带 1 个捕获组。例：【修车水费】：#6P → \"【修车水费】[#：:\\\\s]*([0-9]+(?:\\\\.[0-9]{1,2})?)\" 抓到 6；多条消息里有多个价格时，取正则匹配到的第一个数字"],
  ["extract.price_multiply",
    "价格倍率（数字）：提取后自动相乘。填 100 表示 6P→600 元；不填或 0 = 不乘。小木屋 800P 本身就是元，不要填"],
  ["extract.currency_map",
    "币种判定：{\"正则\": \"币种代码\"}，正文命中正则就用该币种。写法 {\"P\": \"CNY\"}"],
  ["ocr.enabled / trigger_when_missing",
    "图片兜底：缺这些字段时对照片 OCR 再抽一遍（OCR 结果置信度封顶 0.79，会进人工审核）。写法 {\"enabled\": true, \"trigger_when_missing\": [\"price\"]}"],
  ["lifecycle_terms",
    "下架词：新消息命中任一词，自动下架该联系人已上架卡片。写法 [\"已售\", \"已出\", \"离开\"]"],
  ["display.exclude_patterns",
    "展示剔除：正则数组，命中的整行不对外显示（原始数据不动）。写法 [\"宁波天一生水\", \"频道[:：]\"]"],
  ["改完规则怎么生效",
    "①保存 ②在「绑定来源」处选群组点「重跑提取」→ 存量卡片按新规则重算，结果有变化时原卡自动刷新（发布时间保持消息时间，不重复建卡）"],
];

async function reextractSource() {
  if (!bindSourceId.value) {
    notify("先选择要重跑的来源");
    return;
  }
  const data = await api("/api/admin/rules/reextract", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source_id: bindSourceId.value }),
  });
  notify(`已重置 ${data.requeued} 条消息，正在按当前规则重新提取…`);
}

function notify(msg) {
  flash.value = msg;
  setTimeout(() => (flash.value = ""), 3000);
}

const boundGroups = () => (current.value && current.value.bindings) || [];

function select(rule) {
  current.value = rule;
  configText.value = JSON.stringify(rule.config, null, 2);
  dryrun.value = null;
  // default the sample drawer to the first group this rule is bound to
  const first = (rule.bindings || [])[0];
  if (first) sampleSourceId.value = first.source_id;
}

function newRule() {
  current.value = { rule_name: "", is_active: true };
  configText.value = JSON.stringify(EMPTY_CONFIG, null, 2);
  dryrun.value = null;
}

async function loadRules() {
  rules.value = (await api("/api/admin/rules")).items;
  if (!current.value && rules.value.length) select(rules.value[0]);
}

async function save() {
  let config;
  try {
    config = JSON.parse(configText.value);
  } catch {
    notify("JSON 语法错误");
    return;
  }
  if (!current.value.rule_name.trim()) {
    notify("规则名不能为空");
    return;
  }
  const data = await api("/api/admin/rules", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ rule_name: current.value.rule_name, config, is_active: true }),
  });
  notify(`已保存 v${data.version}`);
  await loadRules();
  const fresh = rules.value.find((r) => r.id === data.rule_id);
  if (fresh) select(fresh);
}

async function runDryRun() {
  let config;
  try {
    config = JSON.parse(configText.value);
  } catch {
    notify("JSON 语法错误");
    return;
  }
  dryrun.value = await api("/api/admin/rules/dryrun", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ config, sample_limit: 50 }),
  });
}

async function remove() {
  if (!current.value || !current.value.id) return;
  await api(`/api/admin/rules/${current.value.id}`, { method: "DELETE" });
  current.value = null;
  await loadRules();
  notify("已删除");
}

async function bindToSource() {
  if (!current.value || !current.value.id || !bindSourceId.value) {
    notify("先选择要绑定的来源");
    return;
  }
  await api(`/api/admin/rules/${current.value.id}/bind`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source_id: bindSourceId.value, priority: 100 }),
  });
  notify(`已绑定来源#${bindSourceId.value}（优先级 100，覆盖默认规则）`);
}

async function loadRecentSamples() {
  const params = new URLSearchParams({ limit: String(sampleLimit.value) });
  if (sampleSourceId.value) params.set("source_id", String(sampleSourceId.value));
  const data = await api(`/api/admin/rules/samples?${params}`);
  if (data.samples.length) {
    sampleText.value = data.samples.map((s) => s.text).join("\n---\n");
    const bySource = {};
    for (const s of data.samples) bySource[s.source] = (bySource[s.source] || 0) + 1;
    const breakdown = Object.entries(bySource)
      .map(([name, n]) => `${name}×${n}`)
      .join("，");
    notify(`已载入 ${data.samples.length} 条（${breakdown}）`);
  } else {
    notify(sampleSourceId.value ? "该来源暂无消息样本" : "暂无历史消息样本");
  }
}

async function generateByAI() {
  const samples = sampleText.value.split("\n---\n").map((s) => s.trim()).filter(Boolean);
  if (!samples.length) {
    notify("先粘贴几条样本消息");
    return;
  }
  try {
    const data = await api("/api/admin/rules/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ samples }),
    });
    configText.value = JSON.stringify(data.config, null, 2);
    notify("AI 已生成规则草稿（请回测后保存）");
  } catch (e) {
    notify(`生成失败: ${String(e.message).slice(0, 80)}`);
  }
}

const llmHasKey = ref(false);

async function openSettings() {
  const data = await api("/api/admin/settings/llm");
  llmForm.value = {
    base_url: data.base_url || "",
    model: data.model || "",
    api_key: "",
    allow_private: data.allow_private || false,
  };
  llmHasKey.value = Boolean(data.has_key);
  apForm.value = {
    autopublish_enabled: data.autopublish_enabled,
    autopublish_threshold: data.autopublish_threshold,
  };
  llmTestResult.value = "";
  showSettings.value = true;
}

async function saveSettings() {
  await api("/api/admin/settings/llm", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...llmForm.value, ...apForm.value }),
  });
  llm.value = await api("/api/admin/rules/llm-status");
  notify("设置已保存，立即生效");
}

async function testSettings() {
  try {
    llmTestResult.value = "测试中…";
    await api("/api/admin/settings/llm", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(llmForm.value),
    });
    const r = await api("/api/admin/settings/llm/test", { method: "POST" });
    llmTestResult.value = `连通成功：${r.reply || "OK"}`;
    llm.value = await api("/api/admin/rules/llm-status");
  } catch (e) {
    llmTestResult.value = `失败：${String(e.message).slice(0, 90)}`;
  }
}

async function backfillSamples() {
  if (!sampleSourceId.value) {
    notify("先选择要拉取的来源");
    return;
  }
  try {
    notify("已发起历史拉取，等待网关执行…");
    await api(`/api/admin/sources/${sampleSourceId.value}/backfill`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ limit: sampleLimit.value }),
    });
    for (let i = 0; i < 30; i++) {
      await new Promise((r) => setTimeout(r, 2000));
      const st = await api(`/api/admin/sources/${sampleSourceId.value}/backfill/status`);
      if (st.status === "done") {
        notify(`历史拉取完成：${st.fetched} 条新样本`);
        await loadRecentSamples();
        return;
      }
      if (st.status === "failed") {
        notify(`拉取失败：${(st.error || "").slice(0, 60)}`);
        return;
      }
    }
    notify("拉取仍在进行，稍后手动点『载入样本』");
  } catch (e) {
    notify(`发起失败: ${String(e.message).slice(0, 60)}`);
  }
}

onMounted(async () => {
  await loadRules();
  llm.value = await api("/api/admin/rules/llm-status");
  sources.value = (await api("/api/admin/sources")).items;
});
</script>

<template>
  <div class="space-y-3">
  <AdminTabs />

    <div class="flex items-center gap-3">
      <h2 class="text-lg font-semibold">AI 规则工坊</h2>
      <span v-if="flash" class="rounded bg-emerald-600/20 px-3 py-1 text-xs text-emerald-300">{{ flash }}</span>
      <span class="ml-auto text-xs"
        :class="llm.available ? 'text-emerald-400' : 'text-slate-500'">
        {{ llm.available ? `AI 可用 (${llm.model})` : "AI 未配置" }}
      </span>
      <button @click="openSettings"
        class="flex items-center gap-1 rounded-lg border border-slate-700 px-2.5 py-1 text-xs text-slate-300 hover:bg-slate-800">
        <Settings2 class="size-3.5" />AI 设置
      </button>
    </div>

    <div v-if="showSettings"
      class="space-y-3 rounded-xl border border-violet-800/50 bg-violet-950/20 p-4 text-sm">
      <div class="text-xs text-slate-400">
        OpenAI 兼容端点（如 GLM / DeepSeek / 本地 Ollama）。保存后立即生效；API Key 只存不显。
      </div>
      <div class="grid gap-3 md:grid-cols-3">
        <label class="block text-xs">Base URL
          <input v-model="llmForm.base_url" placeholder="https://api.example.com/v1"
            class="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm" />
        </label>
        <label class="block text-xs">模型名
          <input v-model="llmForm.model" placeholder="glm-4-flash / deepseek-chat …"
            class="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm" />
        </label>
        <label class="block text-xs">API Key（留空=保留已存）
          <input v-model="llmForm.api_key" type="password" :placeholder="llmHasKey ? '已设置（留空保持不变）' : 'sk-…'"
            class="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm" />
          <span v-if="llmHasKey" class="text-emerald-400">✓ 已保存（数据库每日自动备份，不会丢失）</span>
        </label>
      </div>
      <div class="flex flex-wrap items-center gap-3">
        <label class="flex items-center gap-2 text-xs text-slate-400">
          <input v-model="llmForm.allow_private" type="checkbox" class="size-4 accent-violet-500" />
          允许私网端点（局域网大模型，如 Ollama）
        </label>
        <button @click="saveSettings"
          class="rounded-lg border border-slate-600 px-4 py-1.5 text-xs hover:bg-slate-800">保存</button>
        <button @click="testSettings"
          class="flex items-center gap-1.5 rounded-lg bg-violet-600 px-4 py-1.5 text-xs font-medium hover:bg-violet-500">
          <PlugZap class="size-3.5" />保存并测试
        </button>
        <span v-if="llmTestResult" class="text-xs"
          :class="llmTestResult.startsWith('失败') ? 'text-rose-400' : 'text-emerald-400'">
          {{ llmTestResult }}
        </span>
      </div>
      <div class="space-y-2 border-t border-slate-800 pt-3">
        <div class="text-xs font-medium text-slate-300">采集发布策略</div>
        <label class="flex items-center gap-2 text-xs text-slate-400">
          <input v-model="apForm.autopublish_enabled" type="checkbox" class="size-4 accent-emerald-500" />
          高置信自动上线（关闭则全部进待审核）
        </label>
        <div class="flex items-center gap-3 text-xs text-slate-400">
          自动上线置信度阈值
          <input v-model.number="apForm.autopublish_threshold" type="range" min="0.3" max="1" step="0.05"
            class="w-48 accent-emerald-500" />
          <span class="w-10 font-mono text-emerald-400">{{ apForm.autopublish_threshold.toFixed(2) }}</span>
          <span class="text-slate-500">≥阈值直接上线；OCR 参与或缺字段的一律进待审核</span>
        </div>
      </div>
    </div>

    <div class="grid grid-cols-1 gap-4 lg:h-[calc(100dvh-190px)] lg:grid-cols-12">
      <aside class="flex gap-2 overflow-x-auto pb-1 lg:col-span-2 lg:block lg:space-y-2 lg:overflow-x-visible">
        <button @click="newRule"
          class="w-full rounded-lg bg-sky-600 py-2 text-sm font-medium hover:bg-sky-500">+ 新规则</button>
        <button v-for="r in rules" :key="r.id" @click="select(r)"
          class="w-full rounded-lg border p-2.5 text-left text-sm"
          :class="current && current.id === r.id ? 'border-sky-500 bg-sky-600/10' : 'border-slate-800 bg-slate-900'">
          <div class="truncate font-medium">{{ r.rule_name }}</div>
          <div class="text-xs text-slate-500">v{{ r.version }}</div>
        </button>
      </aside>

      <section class="lg:col-span-5 space-y-3 lg:overflow-y-auto">
        <div v-if="boundGroups().length" class="flex flex-wrap items-center gap-2 text-xs">
          <span class="text-slate-500">已绑定群组：</span>
          <span v-for="b in boundGroups()" :key="b.source_id"
            class="flex items-center gap-1 rounded-full border border-sky-800/60 bg-sky-950/40 px-2.5 py-1 text-sky-300">
            {{ b.title || `来源#${b.source_id}` }}
            <span class="text-slate-500">优先级{{ b.priority }}</span>
          </span>
          <span class="text-slate-600">（改完规则记得选对应群组「重跑提取」刷新存量卡片）</span>
        </div>
        <div class="flex gap-2">
          <input v-model="current.rule_name" placeholder="规则名" :disabled="current.rule_name === 'default-rule'"
            class="flex-1 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm" />
          <button @click="save" :disabled="current.rule_name === 'default-rule'"
            class="flex items-center gap-1.5 rounded-lg border border-slate-600 px-3 py-2 text-sm hover:bg-slate-800 disabled:opacity-40">
            <Save class="size-4" />保存
          </button>
          <button v-if="current.id && current.rule_name !== 'default-rule'" @click="remove"
            class="rounded-lg border border-rose-700/60 p-2 text-rose-300 hover:bg-rose-900/30"><Trash2 class="size-4" /></button>
          <button v-if="current.id" @click="bindToSource"
            class="flex items-center gap-1.5 rounded-lg border border-slate-600 px-3 py-2 text-sm hover:bg-slate-800">
            <Link2 class="size-4" />绑定来源
          </button>
          <select v-model="bindSourceId"
            class="rounded-lg border border-slate-700 bg-slate-950 px-2 py-2 text-xs">
            <option :value="null" disabled>选择来源…</option>
            <option v-for="s in sources" :key="s.id" :value="s.id">
              {{ s.title || `来源#${s.id}` }}{{ s.is_active ? "" : "（已暂停）" }}
            </option>
          </select>
          <button @click="reextractSource"
            class="flex items-center gap-1.5 whitespace-nowrap rounded-lg border border-amber-700/60 px-2.5 py-2 text-xs text-amber-300 hover:bg-amber-900/30">
            <RefreshCw class="size-3.5" />重跑提取
          </button>
        </div>
        <textarea v-model="configText" rows="20" spellcheck="false"
          class="w-full rounded-lg border border-slate-700 bg-slate-950 p-3 font-mono text-xs leading-relaxed"></textarea>
        <details class="rounded-xl border border-slate-800 bg-slate-900/50 p-3 text-xs">
          <summary class="cursor-pointer text-slate-300">配置字段说明（点开）</summary>
          <div class="mt-2 space-y-1.5">
            <div v-for="[key, desc] in FIELD_HELP" :key="key" class="flex gap-2">
              <code class="shrink-0 text-sky-300">{{ key }}</code>
              <span class="text-slate-400">{{ desc }}</span>
            </div>
            <div class="mt-2 border-t border-slate-800 pt-2 text-slate-500">
              改完规则保存后，选来源点『重跑提取』即可让存量卡片按新规则刷新（新提取分数更高时更新原卡，不会重复建卡）。
            </div>
          </div>
        </details>
      </section>

      <section class="lg:col-span-5 space-y-3 lg:overflow-y-auto">
        <div class="space-y-2 rounded-xl border border-slate-800 bg-slate-900/50 p-3">
          <div class="flex items-center gap-2 text-sm font-medium"><Sparkles class="size-4 text-sky-400" />样本输入</div>
          <textarea v-model="sampleText" rows="5" placeholder="粘贴样本消息，多条用 --- 分隔"
            class="w-full rounded-lg border border-slate-700 bg-slate-950 p-2.5 text-xs"></textarea>
          <div class="flex flex-wrap items-center gap-2 text-xs">
            <select v-model="sampleSourceId"
              class="rounded-lg border border-slate-700 bg-slate-950 px-2 py-1.5">
              <option :value="null">全部来源</option>
              <option v-for="s in sources" :key="s.id" :value="s.id">
                {{ s.title || `来源#${s.id}` }}
              </option>
            </select>
            <input v-model.number="sampleLimit" type="number" min="1" max="200"
              title="要往前获取的消息条数"
              class="w-20 rounded-lg border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs" />
            <span class="text-slate-500">条</span>
            <button @click="loadRecentSamples"
              class="rounded-lg border border-slate-600 py-1.5 text-slate-300 hover:bg-slate-800">
              载入样本
            </button>
            <button v-if="sampleSourceId" @click="backfillSamples"
              class="rounded-lg border border-sky-700/60 py-1.5 text-sky-300 hover:bg-sky-900/30">
              拉取历史样本
            </button>
            <button @click="generateByAI" :disabled="!llm.available"
              class="flex-1 rounded-lg bg-violet-600 py-1.5 font-medium hover:bg-violet-500 disabled:opacity-40">
              AI 生成规则草稿
            </button>
          </div>
        </div>

        <div class="rounded-xl border border-slate-800 bg-slate-900/50 p-3">
          <div class="mb-2 flex items-center gap-2 text-sm font-medium">
            <FlaskConical class="size-4 text-emerald-400" />沙盒回测
            <span v-if="dryrun" class="ml-auto text-xs text-slate-400">
              命中 {{ dryrun.hits }}/{{ dryrun.total }}（{{ (dryrun.hit_rate * 100).toFixed(0) }}%）
            </span>
          </div>
          <button @click="runDryRun"
            class="mb-2 w-full rounded-lg border border-emerald-700/60 py-2 text-sm text-emerald-300 hover:bg-emerald-900/30">
            对最近 50 条真实消息回测
          </button>
          <div v-if="dryrun" class="max-h-80 space-y-1.5 overflow-y-auto">
            <div v-for="r in dryrun.results" :key="r.raw_id"
              class="rounded-lg border p-2 text-xs"
              :class="r.matched ? 'border-emerald-800/60 bg-emerald-950/20' : 'border-slate-800'">
              <div class="flex items-center gap-2">
                <span class="font-mono text-slate-500">#{{ r.raw_id }}</span>
                <span v-if="r.gate !== 'pass'" class="rounded bg-rose-900/40 px-1.5 py-0.5 text-rose-300">{{ r.gate }}</span>
                <span v-if="r.lifecycle" class="rounded bg-amber-900/40 px-1.5 py-0.5 text-amber-300">生命周期:{{ r.lifecycle }}</span>
                <span v-for="(v, k) in r.fields" :key="k"
                  class="rounded bg-slate-800 px-1.5 py-0.5">{{ k }}={{ v }}</span>
              </div>
              <div class="mt-1 truncate text-slate-500">{{ r.text }}</div>
            </div>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>
