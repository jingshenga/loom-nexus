<script setup>
import { computed, ref } from "vue";
import { CheckSquare, ChevronDown, ChevronLeft, ChevronRight, ChevronUp, Clapperboard, Plus, Square, X } from "lucide-vue-next";
import { mediaUrl } from "../api/client";

const props = defineProps({
  media: { type: Array, default: () => [] },
  editable: { type: Boolean, default: false },
});
const emit = defineEmits(["update:media", "order-change"]);
const selected = ref([]);
const previewIndex = ref(-1);
const draggingIndex = ref(-1);
const items = computed(() => props.media || []);
const allSelected = computed(() => items.value.length > 0 && items.value.every((_, index) => selected.value.includes(index)));
const preview = computed(() => items.value[previewIndex.value] || null);

function update(next, ordered = false) {
  emit("update:media", next);
  if (ordered) {
    selected.value = [];
    emit("order-change");
  } else {
    selected.value = selected.value.filter((index) => index < next.length);
  }
}

function toggle(index) {
  selected.value = selected.value.includes(index)
    ? selected.value.filter((item) => item !== index)
    : [...selected.value, index];
}

function toggleAll() {
  selected.value = allSelected.value ? [] : items.value.map((_, index) => index);
}

function removeIndexes(indexes) {
  const removing = new Set(indexes);
  const next = items.value.filter((_, index) => !removing.has(index));
  if (previewIndex.value >= 0 && removing.has(previewIndex.value)) previewIndex.value = -1;
  else if (previewIndex.value >= 0) previewIndex.value -= [...removing].filter((index) => index < previewIndex.value).length;
  update(next, true);
}

function removeSelected() {
  if (!selected.value.length || !window.confirm(`移除选中的 ${selected.value.length} 个媒体？原始文件会保留。`)) return;
  removeIndexes(selected.value);
}

function removeSingle(index) {
  if (!window.confirm("从此卡片移除这张图片？原始文件会保留。")) return;
  removeIndexes([index]);
}

function setSelectedVisibility(visibility) {
  if (!selected.value.length) return;
  const selectedSet = new Set(selected.value);
  update(items.value.map((item, index) => selectedSet.has(index) && item.kind === "photo"
    ? { ...item, visibility, visibility_override: true }
    : item));
}

function setVisibility(index, visibility) {
  update(items.value.map((item, i) => i === index
    ? { ...item, visibility, visibility_override: true }
    : item));
}

function onDragStart(index, event) {
  if (!props.editable || selected.value.length) return event.preventDefault();
  draggingIndex.value = index;
  event.dataTransfer.effectAllowed = "move";
}

function onDrop(index) {
  const from = draggingIndex.value;
  draggingIndex.value = -1;
  if (from < 0 || from === index) return;
  const next = [...items.value];
  const [moved] = next.splice(from, 1);
  next.splice(index, 0, moved);
  update(next, true);
}

function move(index, delta) {
  if (selected.value.length) return;
  const target = index + delta;
  if (target < 0 || target >= items.value.length) return;
  const next = [...items.value];
  [next[index], next[target]] = [next[target], next[index]];
  update(next, true);
}

function addFiles(event) {
  const files = [...(event.target.files || [])];
  event.target.value = "";
  const accepted = files.filter((file) => ["image/jpeg", "image/png", "image/webp"].includes(file.type) && file.size <= 10 * 1024 * 1024);
  if (accepted.length !== files.length) window.alert("仅支持 10 MB 以内的 JPEG、PNG 或 WebP 图片；不符合条件的文件已跳过。");
  Promise.all(accepted.map((file) => new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve({
      kind: "photo",
      image_data: String(reader.result).split(",", 2)[1],
      image_mime: file.type,
      preview_url: reader.result,
      visibility: "admin",
      visibility_override: true,
      temp_key: `${Date.now()}-${Math.random()}`,
    });
    reader.onerror = reject;
    reader.readAsDataURL(file);
  }))).then((added) => update([...items.value, ...added], true));
}

function mediaSrc(item) {
  if (item.preview_url) return item.preview_url;
  return mediaUrl(item.kind === "photo" ? item.path : item.poster);
}

function stepPreview(delta) {
  if (!items.value.length) return;
  previewIndex.value = (previewIndex.value + delta + items.value.length) % items.value.length;
}

function onTouchStart(event) {
  touchStartX = event.changedTouches[0].clientX;
  touchStartY = event.changedTouches[0].clientY;
}
let touchStartX = 0;
let touchStartY = 0;
function onTouchEnd(event) {
  const dx = event.changedTouches[0].clientX - touchStartX;
  const dy = event.changedTouches[0].clientY - touchStartY;
  if (Math.abs(dx) > 40 && Math.abs(dx) > Math.abs(dy) * 1.5) stepPreview(dx < 0 ? 1 : -1);
}
</script>

<template>
  <div class="space-y-2">
    <div v-if="editable" class="flex flex-wrap items-center gap-2">
      <button @click="toggleAll" class="flex items-center gap-1 rounded-lg border border-slate-700 px-2.5 py-1.5 text-xs text-slate-300">
        <component :is="allSelected ? CheckSquare : Square" class="size-3.5" />{{ allSelected ? "取消全选" : "全选" }}
      </button>
      <span class="mr-auto text-xs text-slate-500">{{ selected.length ? `已选 ${selected.length} 项` : "拖动图片可调整顺序" }}</span>
      <button v-if="selected.length" @click="setSelectedVisibility('public')" class="rounded-lg border border-emerald-800 px-2.5 py-1.5 text-xs text-emerald-300">批量上架</button>
      <button v-if="selected.length" @click="setSelectedVisibility('admin')" class="rounded-lg border border-slate-700 px-2.5 py-1.5 text-xs text-slate-300">设为仅管理员</button>
      <button v-if="selected.length" @click="removeSelected" class="rounded-lg border border-rose-800 px-2.5 py-1.5 text-xs text-rose-300">批量删除</button>
      <label class="flex cursor-pointer items-center gap-1 rounded-lg bg-sky-700 px-2.5 py-1.5 text-xs text-white hover:bg-sky-600">
        <Plus class="size-3.5" />新增图片
        <input type="file" accept="image/jpeg,image/png,image/webp" multiple class="hidden" @change="addFiles" />
      </label>
    </div>
    <div v-if="items.length" class="grid grid-cols-3 gap-2 sm:grid-cols-4 xl:grid-cols-5">
      <article v-for="(item, index) in items" :key="item.path || item.temp_key || index"
        class="group relative aspect-[3/4] min-w-0 overflow-hidden rounded-lg border border-slate-800 bg-black"
        :class="draggingIndex === index ? 'opacity-40' : ''"
        :draggable="editable && selected.length === 0" @dragstart="onDragStart(index, $event)" @dragover.prevent @drop.prevent="onDrop(index)" @dragend="draggingIndex = -1">
        <button class="size-full" @click="previewIndex = index" :aria-label="`预览第 ${index + 1} 张图片`">
          <img v-if="item.kind === 'photo' || item.poster" :src="mediaSrc(item)" class="size-full object-contain" loading="lazy" />
          <span v-else class="flex size-full items-center justify-center text-slate-400"><Clapperboard class="size-8" /></span>
        </button>
        <span v-if="item.kind === 'photo' && item.visibility !== 'public'" class="absolute bottom-1 left-1 rounded bg-black/75 px-1.5 py-0.5 text-[10px] text-slate-100">仅管理员</span>
        <span v-if="editable" class="absolute left-1 top-1 rounded bg-black/70 px-1 text-[10px] text-white">{{ index + 1 }}</span>
        <input v-if="editable" type="checkbox" :checked="selected.includes(index)" @click.stop="toggle(index)"
          class="absolute right-1 top-1 size-5 cursor-pointer accent-sky-500" :aria-label="`选择第 ${index + 1} 张`" />
        <button v-if="editable" @click.stop="removeSingle(index)" :aria-label="`删除第 ${index + 1} 张`"
          class="absolute right-1 top-7 rounded-full bg-rose-600/90 p-1 text-white shadow hover:bg-rose-500"><X class="size-3.5" /></button>
        <div v-if="editable" class="absolute bottom-8 left-1 flex flex-col gap-0.5 sm:opacity-0 sm:transition group-hover:sm:opacity-100">
          <button @click.stop="move(index, -1)" :disabled="index === 0" aria-label="上移图片" class="rounded bg-black/75 p-1 text-white disabled:opacity-30"><ChevronUp class="size-3.5" /></button>
          <button @click.stop="move(index, 1)" :disabled="index === items.length - 1" aria-label="下移图片" class="rounded bg-black/75 p-1 text-white disabled:opacity-30"><ChevronDown class="size-3.5" /></button>
        </div>
        <select v-if="editable && item.kind === 'photo'" :value="item.visibility || 'admin'" @click.stop @change="setVisibility(index, $event.target.value)"
          class="absolute bottom-1 right-1 max-w-[70%] rounded border border-slate-700 bg-slate-950/90 px-1 py-0.5 text-[10px] text-slate-200">
          <option value="public">普通用户可见</option><option value="admin">仅管理员</option>
        </select>
      </article>
    </div>
    <div v-else class="rounded-lg border border-dashed border-slate-700 py-10 text-center text-sm text-slate-500">暂无媒体{{ editable ? "，可新增图片" : "" }}</div>

    <div v-if="preview" class="fixed inset-0 z-[70] flex items-center justify-center bg-black/85 p-3" @click.self="previewIndex = -1" @touchstart.passive="onTouchStart" @touchend.passive="onTouchEnd">
      <button @click="previewIndex = -1" aria-label="关闭预览" class="absolute right-4 top-4 rounded-full bg-slate-900/80 p-2 text-white"><X class="size-5" /></button>
      <button v-if="items.length > 1" @click="stepPreview(-1)" aria-label="上一张" class="absolute left-3 rounded-full bg-slate-900/80 p-2 text-white sm:left-6"><ChevronLeft class="size-6" /></button>
      <img v-if="preview.kind === 'photo'" :src="mediaSrc(preview)" class="max-h-[88dvh] max-w-[88vw] object-contain" />
      <video v-else :src="mediaSrc(preview)" :poster="preview.poster ? mediaUrl(preview.poster) : undefined" controls playsinline class="max-h-[88dvh] max-w-[88vw]" />
      <button v-if="items.length > 1" @click="stepPreview(1)" aria-label="下一张" class="absolute right-3 rounded-full bg-slate-900/80 p-2 text-white sm:right-6"><ChevronRight class="size-6" /></button>
      <span class="absolute bottom-4 rounded bg-slate-900/80 px-3 py-1 text-xs text-slate-200">{{ previewIndex + 1 }} / {{ items.length }}<span v-if="preview.visibility !== 'public'"> · 仅管理员</span></span>
    </div>
  </div>
</template>
