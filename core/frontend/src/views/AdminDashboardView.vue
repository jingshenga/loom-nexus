<script setup>
import { onMounted, ref } from "vue";
import { RouterLink } from "vue-router";
import { Activity, AlertTriangle, Bot, CheckCheck, Copy, Image, ListChecks, Radio, ShieldCheck } from "lucide-vue-next";
import { api, mediaUrl } from "../api/client";

const loading = ref(true);
const error = ref("");
const data = ref(null);

async function load() {
  loading.value = true;
  error.value = "";
  try {
    data.value = await api("/api/admin/dashboard");
  } catch (e) {
    error.value = e.message || "总览数据暂时不可用";
  } finally {
    loading.value = false;
  }
}
onMounted(load);

const cards = [
  { key: "raw_today", label: "今日新消息", help: "按 Telegram 发布时间，Asia/Shanghai", icon: Radio },
  { key: "rule_hits_today", label: "今日规则命中", help: "Active 解析结果含选中规则", icon: CheckCheck },
  { key: "ocr_today", label: "今日 OCR 处理", help: "今日更新的 OCR 记录", icon: Image },
  { key: "review_open", label: "待审核", help: "资料、规则及 Lifecycle 队列", icon: ListChecks },
  { key: "published_listings", label: "已发布资料", help: "当前 status=published", icon: ShieldCheck },
  { key: "duplicate_candidates", label: "重复候选", help: "当前重复待核验对数", icon: Copy },
];

function percent(value) {
  return `${Number(value || 0).toFixed(1)}%`;
}

function timeText(value) {
  if (!value) return "时间未知";
  return new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false });
}

function classificationLabel(value) {
  return ({
    info_card: "资料卡", profile_photo: "人像", advertisement: "广告", other: "其他",
    sixiang: "私享", ltjt: "聊天/推荐", yyzt: "语音/主题", hj: "环境", bqb: "表情包",
  }[value] || value || "未分类");
}

function processingLabel(image) {
  if (!image.raw_id) return "没有 Telegram raw 关联，未进入 Router 规则解析";
  if (image.run_reason === "active") {
    return `Active 规则解析 · 规则版本 ${image.rule_version_id || "—"}`;
  }
  if (image.processed_mode === "legacy") {
    const version = image.rule_version_source === "legacy_raw" ? `raw 记录版本 ${image.rule_version_id}` : "无规则版本记录";
    return `Legacy 模式 · ${version} · 未保存逐条规则命中诊断，Active OCR 字段融合未运行`;
  }
  return "未找到该图片对应的消息级规则解析记录";
}
</script>

<template>
  <div class="space-y-6">
    <div class="flex flex-wrap items-end justify-between gap-3">
      <div><p class="text-xs font-semibold uppercase tracking-[.18em] text-sky-400">NEXUS CONTROL</p><h1 class="mt-1 text-2xl font-bold">管理总览</h1><p class="mt-1 text-sm text-slate-400">生产指标来自当前数据库快照，不包含静态演示数据。</p></div>
      <button class="rounded-xl border border-slate-700 px-3 py-2 text-sm hover:bg-slate-800" :disabled="loading" @click="load">刷新状态</button>
    </div>
    <div v-if="error" class="rounded-xl border border-rose-500/40 bg-rose-500/10 p-4 text-sm text-rose-300">{{ error }}</div>
    <div v-if="loading" class="rounded-xl border border-slate-800 bg-slate-900 p-5 text-sm text-slate-400">正在读取实时统计…</div>
    <template v-else-if="data">
      <section class="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        <article v-for="card in cards" :key="card.key" class="rounded-2xl border border-slate-800 bg-slate-900 p-4 shadow-sm">
          <div class="flex items-center justify-between"><span class="text-sm text-slate-400">{{ card.label }}</span><component :is="card.icon" class="size-4 text-sky-400" /></div>
          <div class="mt-3 text-3xl font-semibold tabular-nums">{{ data.metrics[card.key] ?? "—" }}</div>
          <div class="mt-2 text-xs text-slate-500">{{ card.help }}</div>
        </article>
      </section>

      <section class="grid gap-4 xl:grid-cols-[1.3fr_1fr]">
        <article class="rounded-2xl border border-slate-800 bg-slate-900 p-5">
          <div class="mb-4 flex items-center justify-between"><h2 class="font-semibold">运行状态</h2><Activity class="size-4 text-sky-400" /></div>
          <div class="grid gap-3 sm:grid-cols-2">
            <div v-for="item in data.health" :key="item.name" class="flex min-w-0 items-center justify-between gap-3 rounded-xl bg-slate-950/60 px-3 py-3">
              <div class="min-w-0"><div class="text-sm font-medium">{{ item.name }}</div><div class="truncate text-xs text-slate-500">{{ item.detail || "—" }}</div></div>
              <span class="shrink-0 rounded-full px-2 py-1 text-xs" :class="item.ok ? 'bg-emerald-500/10 text-emerald-300' : 'bg-amber-500/10 text-amber-300'">{{ item.status }}</span>
            </div>
          </div>
        </article>
        <article class="rounded-2xl border border-slate-800 bg-slate-900 p-5">
          <div class="mb-4 flex items-center justify-between"><h2 class="font-semibold">最近活跃来源</h2><Bot class="size-4 text-sky-400" /></div>
          <div v-if="!data.recent_sources.length" class="text-sm text-slate-500">暂无来源消息记录</div>
          <div v-for="source in data.recent_sources" :key="source.id" class="flex items-center justify-between gap-3 border-b border-slate-800 py-3 last:border-0">
            <div class="min-w-0"><div class="truncate text-sm font-medium">{{ source.title || `来源 ${source.id}` }}</div><div class="text-xs text-slate-500">最近消息 {{ source.last_message_at || "无记录" }}</div></div>
            <span class="shrink-0 rounded-full px-2 py-1 text-xs" :class="source.router_mode === 'active' ? 'bg-amber-500/10 text-amber-300' : 'bg-slate-800 text-slate-300'">{{ source.router_mode || "legacy" }}</span>
          </div>
        </article>
      </section>

      <section class="space-y-4 rounded-2xl border border-sky-900/60 bg-slate-900 p-5">
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p class="text-xs font-semibold uppercase tracking-[.16em] text-sky-400">RECOGNITION TRACE · 30 DAYS</p>
            <h2 class="mt-1 text-lg font-semibold">识别效果与采集流程</h2>
            <p class="mt-1 text-xs text-slate-500">{{ data.recognition.metric_scope.window }}。统计展示实际落库结果，不代表独立准确率。</p>
          </div>
          <RouterLink to="/admin/models" class="rounded-lg border border-slate-700 px-3 py-2 text-sm text-sky-300 hover:bg-slate-800">查看模型状态</RouterLink>
        </div>

        <div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <article class="rounded-xl bg-slate-950/70 p-3">
            <div class="text-xs text-slate-400">主分类器实际记录</div>
            <div class="mt-2 text-lg font-semibold">Vision4 {{ data.recognition.metrics.vision4_calls }}</div>
            <div class="text-xs text-slate-500">近 30 天有模型版本的预测</div>
          </article>
          <article class="rounded-xl bg-slate-950/70 p-3">
            <div class="text-xs text-slate-400">站内分类器 #2</div>
            <div class="mt-2 text-lg font-semibold">{{ data.recognition.metrics.classifier_v2_calls }} 次</div>
            <div class="text-xs text-slate-500">与当前 Vision4 引擎分开统计</div>
          </article>
          <article class="rounded-xl bg-slate-950/70 p-3">
            <div class="text-xs text-slate-400">RapidOCR 识字</div>
            <div class="mt-2 text-lg font-semibold">{{ data.recognition.metrics.ocr_text_records }} / {{ data.recognition.metrics.ocr_done }}</div>
            <div class="text-xs text-slate-500">done 记录中有原文或规范化文本</div>
          </article>
          <article class="rounded-xl bg-slate-950/70 p-3">
            <div class="text-xs text-slate-400">结构字段提取</div>
            <div class="mt-2 text-lg font-semibold">{{ data.recognition.metrics.field_records }} 条 · {{ data.recognition.metrics.field_value_count }} 个字段值</div>
            <div class="text-xs text-slate-500">非空 fields_json / OCR done {{ percent(data.recognition.metrics.field_rate) }}；不是准确率</div>
          </article>
          <article class="rounded-xl bg-slate-950/70 p-3">
            <div class="text-xs text-slate-400">进入 Listing 或审核</div>
            <div class="mt-2 text-lg font-semibold">{{ data.recognition.metrics.ocr_adopted }} 条 · {{ percent(data.recognition.metrics.ocr_adoption_rate) }}</div>
            <div class="text-xs text-slate-500">按 OCR done 记录计算；Listing {{ data.recognition.metrics.listing_linked }} · 人工分类审核 {{ data.recognition.metrics.review_involved }}</div>
          </article>
          <article class="rounded-xl bg-slate-950/70 p-3">
            <div class="text-xs text-slate-400">人工分类纠错</div>
            <div class="mt-2 text-lg font-semibold">{{ data.recognition.metrics.corrected_records }} / {{ data.recognition.metrics.labeled_records }} · {{ percent(data.recognition.metrics.correction_rate) }}</div>
            <div class="text-xs text-slate-500">人工标签与模型分类不同；不代表字段纠错率</div>
          </article>
          <article class="rounded-xl bg-slate-950/70 p-3">
            <div class="text-xs text-slate-400">Active Router 实际解析</div>
            <div class="mt-2 text-lg font-semibold">{{ data.recognition.metrics.active_runs }} 次 · 命中 {{ data.recognition.metrics.active_rule_hits }}</div>
            <div class="text-xs text-slate-500">来自 rule_parse_runs 审计记录</div>
          </article>
          <article class="rounded-xl bg-slate-950/70 p-3">
            <div class="text-xs text-slate-400">按来源模式映射的消息量</div>
            <div class="mt-2 text-sm font-semibold">Legacy {{ data.recognition.metrics.legacy_messages }} · Active {{ data.recognition.metrics.active_messages }} · Shadow {{ data.recognition.metrics.shadow_messages }}</div>
            <div class="text-xs text-slate-500">done/dropped：{{ data.recognition.metrics.legacy_processed_messages }} / {{ data.recognition.metrics.active_processed_messages }} / {{ data.recognition.metrics.shadow_processed_messages }}；Active 解析审计 {{ data.recognition.metrics.active_runs }} 次</div>
          </article>
          <article class="rounded-xl bg-slate-950/70 p-3">
            <div class="text-xs text-slate-400">新旧规则差异（Shadow）</div>
            <div class="mt-2 text-lg font-semibold">{{ data.recognition.metrics.shadow_differences }} / {{ data.recognition.metrics.shadow_comparisons }}</div>
            <div class="text-xs text-slate-500">近 30 天有差异的对比数 / 总对比数；不是准确率</div>
          </article>
        </div>

        <div class="flex flex-wrap gap-2 text-xs">
          <span class="rounded-full bg-slate-950 px-3 py-1.5">Vision4 配置 {{ data.recognition.configuration.vision4_mode }} · {{ data.recognition.configuration.vision4_loaded ? '运行时已加载' : '运行时未加载' }}</span>
          <span class="rounded-full bg-slate-950 px-3 py-1.5">主分类引擎 {{ data.recognition.configuration.ocr_classifier_engine || '未设置' }}</span>
          <span class="rounded-full bg-slate-950 px-3 py-1.5">实际主分类 {{ data.recognition.configuration.ocr_classifier_engine === 'vision4' && data.recognition.configuration.vision4_mode === 'active' ? 'Vision4' : data.recognition.configuration.ocr_classifier_engine || '未确定' }}</span>
          <span class="rounded-full bg-slate-950 px-3 py-1.5">Vision4 活动版 {{ data.recognition.configuration.vision4_active_version || '未登记' }}</span>
          <span class="rounded-full bg-slate-950 px-3 py-1.5">分类器 #{{ data.recognition.configuration.classifier_active_id || '—' }} {{ data.recognition.configuration.classifier_active_status || '无活动版本' }}</span>
          <span class="rounded-full bg-slate-950 px-3 py-1.5">Router {{ data.recognition.configuration.active_router_enabled ? 'enabled' : 'disabled' }} · 目标 {{ data.recognition.configuration.active_router_source_ids || '未限制' }} · 急停 {{ data.recognition.configuration.active_router_kill_switch ? 'ON' : 'OFF' }} · OCR assist {{ data.recognition.configuration.ocr_review_assist_enabled ? 'ON' : 'OFF' }}</span>
          <span class="rounded-full bg-slate-950 px-3 py-1.5">活动来源模式 {{ Object.entries(data.recognition.configuration.source_modes).map(([mode, count]) => `${mode} ${count}`).join(' · ') || '无活动来源' }}</span>
          <span class="rounded-full px-3 py-1.5" :class="data.recognition.assist_status === 'all_sources_legacy' ? 'bg-amber-500/10 text-amber-200' : 'bg-emerald-500/10 text-emerald-200'">
            {{ data.recognition.assist_status === 'all_sources_legacy' ? `OCR 审核辅助${data.recognition.configuration.ocr_review_assist_enabled ? '全局已开，但当前来源全为 Legacy，未运行' : '关闭'}` : '存在 Active 来源；审核辅助仍受全局开关与急停保护' }}
          </span>
        </div>
        <div class="flex flex-wrap gap-2 text-xs">
          <span class="text-slate-500">Vision4 分类结果：</span>
          <span v-for="item in data.recognition.category_distribution" :key="item.category" class="rounded-full bg-slate-950 px-2.5 py-1 text-slate-300">
            {{ classificationLabel(item.category) }} {{ item.count }}
          </span>
          <span v-if="!data.recognition.category_distribution.length" class="text-slate-500">暂无预测记录</span>
        </div>
        <div class="flex flex-wrap gap-2 text-xs">
          <span v-for="source in data.recognition.configuration.sources" :key="source.id" class="rounded-lg border px-2.5 py-1.5" :class="source.is_active ? 'border-slate-700 bg-slate-950 text-slate-300' : 'border-slate-800 bg-slate-950/50 text-slate-600'">
            {{ source.title || `来源 ${source.id}` }} · {{ source.router_mode }}{{ source.is_active ? '' : ' · 已停用' }}
          </span>
        </div>
        <p class="text-xs text-slate-500">{{ data.recognition.metric_scope.vision4_calls }}；{{ data.recognition.metric_scope.correction_rate }}；{{ data.recognition.metric_scope.mode_messages }}</p>
        <p class="text-xs text-slate-500">未关联 Listing 且未进入人工审核：{{ data.recognition.metrics.not_business_involved }} 条；其中分类策略标记为无需审核 {{ data.recognition.metrics.no_flow_not_required }} 条。</p>

        <div class="space-y-3">
          <h3 class="font-medium">最近模型已执行图片的完整处理链路</h3>
          <div v-if="!data.recognition.recent_images.length" class="rounded-xl bg-slate-950/60 p-4 text-sm text-slate-500">近 30 天没有带有模型版本的图片识别记录。</div>
          <details v-for="image in data.recognition.recent_images" :key="image.id" class="group min-w-0 rounded-xl border border-slate-800 bg-slate-950/50">
            <summary class="flex cursor-pointer list-none items-center gap-3 p-3">
              <img v-if="image.media_path" :src="mediaUrl(image.media_path)" class="size-14 shrink-0 rounded-lg bg-slate-900 object-cover" alt="识别原图" loading="lazy" />
              <div class="min-w-0 flex-1">
                <div class="truncate text-sm font-medium">{{ image.source_title || `来源 ${image.source_id || '未知'}` }} · {{ classificationLabel(image.classification) }}</div>
                <div class="mt-1 break-all text-xs text-slate-500">{{ timeText(image.posted_at) }} · raw #{{ image.raw_id || '无' }} · {{ image.processed_mode }}</div>
              </div>
              <span class="shrink-0 rounded-full bg-slate-800 px-2 py-1 text-xs">{{ image.review_status || image.ocr_status }}</span>
            </summary>
            <div class="grid min-w-0 gap-3 border-t border-slate-800 p-3 md:grid-cols-2 xl:grid-cols-3">
              <div class="min-w-0 rounded-lg bg-slate-900 p-3">
                <div class="text-xs font-semibold text-sky-300">1 · 原图与消息</div>
                <a v-if="image.media_path" :href="mediaUrl(image.media_path)" target="_blank" rel="noreferrer" class="mt-2 block text-xs text-sky-300 underline">打开原图</a>
                <p class="mt-2 max-h-24 overflow-auto whitespace-pre-wrap break-words text-xs text-slate-300">{{ image.message_text || '没有关联原始消息文本' }}</p>
              </div>
              <div class="min-w-0 rounded-lg bg-slate-900 p-3">
                <div class="text-xs font-semibold text-sky-300">2 · Vision4 / 图片分类</div>
                <p class="mt-2 break-all text-xs text-slate-300">{{ image.vision4_version ? `Vision4 · ${image.vision4_version}` : image.classifier_version_id ? `站内分类器 #${image.classifier_version_id}` : '未找到持久化模型版本' }}</p>
                <p class="mt-1 text-xs text-slate-400">结果：{{ classificationLabel(image.classification) }} · 置信度 {{ image.confidence == null ? '—' : percent(image.confidence * 100) }}</p>
                <p v-if="image.human_classification" class="mt-1 text-xs text-amber-200">人工标签：{{ classificationLabel(image.human_classification) }}</p>
              </div>
              <div class="min-w-0 rounded-lg bg-slate-900 p-3">
                <div class="text-xs font-semibold text-sky-300">3 · RapidOCR</div>
                <p class="mt-2 break-all text-xs text-slate-300">{{ image.ocr_engine || '引擎版本未知' }} · {{ image.ocr_status }}</p>
                <details class="mt-2"><summary class="cursor-pointer text-xs text-slate-400">查看识别文本</summary><pre class="mt-2 max-h-36 overflow-auto whitespace-pre-wrap break-words text-xs text-slate-300">{{ image.ocr_text || image.normalized_text || '无 OCR 文本' }}</pre></details>
                <p v-if="image.error" class="mt-2 break-words text-xs text-rose-300">{{ image.error }}</p>
              </div>
              <div class="min-w-0 rounded-lg bg-slate-900 p-3">
                <div class="text-xs font-semibold text-sky-300">4 · 结构字段</div>
                <pre class="mt-2 max-h-40 overflow-auto whitespace-pre-wrap break-words text-xs text-slate-300">{{ JSON.stringify(image.fields || {}, null, 2) }}</pre>
                <div v-if="!Object.keys(image.fields || {}).length" class="text-xs text-slate-500">没有提取到结构字段。</div>
              </div>
              <div class="min-w-0 rounded-lg bg-slate-900 p-3">
                <div class="text-xs font-semibold text-sky-300">5 · 规则匹配</div>
                <p class="mt-2 break-words text-xs text-slate-300">{{ processingLabel(image) }}</p>
                <p v-if="image.selected_rule_ids?.length" class="mt-1 break-all text-xs text-slate-400">选中规则：{{ image.selected_rule_ids.join(', ') }} · {{ image.parser_key }}</p>
                <p v-else-if="image.run_reason === 'active'" class="mt-1 text-xs text-slate-400">该 Active 运行未选中规则</p>
              </div>
              <div class="min-w-0 rounded-lg bg-slate-900 p-3">
                <div class="text-xs font-semibold text-sky-300">6 · Listing / 审核</div>
                <p class="mt-2 break-words text-xs text-slate-300">{{ image.listing_id ? `Listing #${image.listing_id} · ${image.listing_status || '状态未知'}` : '未关联 Listing' }}</p>
                <p class="mt-1 break-words text-xs text-slate-400">审核状态：{{ image.review_status || '无审核记录' }}</p>
                <p class="mt-1 break-words text-xs text-amber-200">结果说明：{{ image.outcome_reason }}</p>
                <RouterLink v-if="image.media_path" :to="{ path: '/admin/ocr', query: { media_path: image.media_path, status: 'all' } }" class="mt-2 inline-flex text-xs text-sky-300 underline">在 OCR 核对页查看</RouterLink>
              </div>
            </div>
          </details>
        </div>
      </section>
      <section class="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <RouterLink v-for="link in data.actions" :key="link.to" :to="link.to" class="flex items-center gap-3 rounded-xl border border-slate-800 bg-slate-900 p-4 text-sm hover:border-sky-500/50 hover:bg-slate-800">
          <AlertTriangle v-if="link.alert" class="size-4 text-amber-400" /><Activity v-else class="size-4 text-sky-400" />{{ link.label }}
        </RouterLink>
      </section>
      <p class="text-xs text-slate-500">统计时间：{{ data.as_of }}。待审核合计由当前 Listing、规则待审与 Lifecycle 队列组成，重复记录按各自队列状态统计。</p>
    </template>
  </div>
</template>
