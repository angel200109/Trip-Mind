<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from "vue";
import { chatbotMessage } from "@/store/index";
import { storeToRefs } from "pinia";
import "github-markdown-css/github-markdown.css";
import { DynamicScroller, DynamicScrollerItem } from "vue-virtual-scroller";
import type { ChatMessage } from "@/types";
import ChatMessageItem from "./ChatMessageItem.vue";

const store = chatbotMessage();
const { messages } = storeToRefs(store);

type VirtualMessage = {
  id: string;
  message: ChatMessage;
  index: number;
};

// 把原始消息数组包装成列表项对象
const virtualMessages = computed<VirtualMessage[]>(() =>
  messages.value.map((message, index) => ({
    id:
      message.id ||
      `${store.currentConversationId || "draft"}-${index}-${message.role}`,
    message,
    index,
  })),
);

const asVirtualMessage = (item: unknown) => item as VirtualMessage;

const getSizeDependencies = (item: VirtualMessage) => [
  item.message.role,
  item.message.progress,
  item.message.functionName,
  item.message.content,
  item.message.rawContent,
  item.message.toolData ? JSON.stringify(item.message.toolData) : "",
  item.message.searchGoodsData
    ? JSON.stringify(item.message.searchGoodsData)
    : "",
];

type FpsSample = {
  avgFps: number;
  minFps: number;
  longFrames16: number;
  longFrames33: number;
  frames: number;
  duration: number;
};

const SCROLL_IDLE_TIMEOUT = 1200;
const isDev = import.meta.env.DEV;
const scrollerRef = ref<{ $el?: Element | null } | HTMLElement | null>(null);

let scrollStopTimer: number | undefined;
let rafId = 0;
let isMeasuring = false;
let activeScrollElement: HTMLElement | null = null;
let startTime = 0;
let lastFrameTime = 0;
let frames = 0;
let minFps = Number.POSITIVE_INFINITY;
let longFrames16 = 0;
let longFrames33 = 0;

// 1秒累计法相关变量
let realtimeLastTime = 0;
let realtimeFrames = 0;
let realtimeFps = 0;
const REALTIME_INTERVAL = 500; // 每500ms输出一次实时FPS

// 内存检测相关类型
interface PerformanceMemory {
  usedJSHeapSize: number;
  totalJSHeapSize: number;
  jsHeapSizeLimit: number;
}

// 扩展 performance 类型（Chrome/Edge 特有 API）
declare global {
  interface Performance {
    memory?: PerformanceMemory;
  }
}

// 格式化字节大小
const formatBytes = (bytes: number): string => {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
};

// 获取当前内存占用（Chrome/Edge 支持）
const getMemoryInfo = (): { used: string; total: string; limit: string; usage: string } | null => {
  if (!performance.memory) return null;

  const { usedJSHeapSize, totalJSHeapSize, jsHeapSizeLimit } = performance.memory;
  const usagePercent = ((usedJSHeapSize / jsHeapSizeLimit) * 100).toFixed(1);

  return {
    used: formatBytes(usedJSHeapSize),
    total: formatBytes(totalJSHeapSize),
    limit: formatBytes(jsHeapSizeLimit),
    usage: `${usagePercent}%`,
  };
};

const resetFpsState = () => {
  startTime = 0;
  lastFrameTime = 0;
  frames = 0;
  minFps = Number.POSITIVE_INFINITY;
  longFrames16 = 0;
  longFrames33 = 0;
  // 重置实时FPS变量
  realtimeLastTime = 0;
  realtimeFrames = 0;
  realtimeFps = 0;
};

const stopMeasure = (): FpsSample | null => {
  if (!isMeasuring) return null;

  isMeasuring = false;
  cancelAnimationFrame(rafId);

  const duration = lastFrameTime - startTime;
  const sample: FpsSample = {
    avgFps: Number(
      (duration > 0 ? (frames * 1000) / duration : 0).toFixed(1),
    ),
    minFps: Number(
      (minFps === Number.POSITIVE_INFINITY ? 0 : minFps).toFixed(1),
    ),
    longFrames16,
    longFrames33,
    frames,
    duration: Math.round(duration),
  };

  resetFpsState();
  return sample;
};

const measureFrame = (now: number) => {
  if (!isMeasuring) return;

  if (!startTime) {
    startTime = now;
    lastFrameTime = now;
    realtimeLastTime = now;
  } else {
    const delta = now - lastFrameTime;
    const fps = 1000 / delta;

    frames += 1;
    minFps = Math.min(minFps, fps);

    if (delta > 16.7) longFrames16 += 1;
    if (delta > 33.3) longFrames33 += 1;

    lastFrameTime = now;

    // 1秒累计法：实时FPS计算
    realtimeFrames += 1;
    const realtimeDelta = now - realtimeLastTime;

    if (realtimeDelta >= REALTIME_INTERVAL) {
      realtimeFps = Math.round((realtimeFrames * 1000) / realtimeDelta);
      const memory = getMemoryInfo();

      // 带颜色的 FPS 标识
      const fpsEmoji = realtimeFps >= 55 ? '🟢' : realtimeFps >= 45 ? '🟡' : '🔴';

      if (memory) {
        console.log(
          `${fpsEmoji} FPS: ${realtimeFps} | 内存: ${memory.used}/${memory.limit} (${memory.usage})`,
        );
      } else {
        console.log(`${fpsEmoji} FPS: ${realtimeFps} | 内存: 不支持（非Chrome浏览器）`);
      }

      realtimeFrames = 0;
      realtimeLastTime = now;
    }
  }

  rafId = requestAnimationFrame(measureFrame);
};

const startMeasure = () => {
  if (!isDev || isMeasuring) return;

  resetFpsState();
  isMeasuring = true;
  rafId = requestAnimationFrame(measureFrame);
};

const handleScrollEnd = () => {
  window.clearTimeout(scrollStopTimer);
  scrollStopTimer = undefined;

  const sample = stopMeasure();
  if (!sample) return;

  console.table({
    avgFps: sample.avgFps,
    minFps: sample.minFps,
    longFrames16: sample.longFrames16,
    longFrames33: sample.longFrames33,
    frames: sample.frames,
    durationMs: sample.duration,
    itemCount: virtualMessages.value.length,
  });
};

const handleScroll = () => {
  if (!isDev) return;

  startMeasure();
  window.clearTimeout(scrollStopTimer);
  scrollStopTimer = window.setTimeout(handleScrollEnd, SCROLL_IDLE_TIMEOUT);
};

const resolveScrollElement = (
  target: { $el?: Element | null } | HTMLElement | null,
) => {
  if (target instanceof HTMLElement) return target;
  return target?.$el instanceof HTMLElement ? target.$el : null;
};

watch(scrollerRef, (current) => {
  activeScrollElement?.removeEventListener("scroll", handleScroll);
  activeScrollElement = resolveScrollElement(current);

  if (activeScrollElement && isDev) {
    activeScrollElement.addEventListener("scroll", handleScroll, {
      passive: true,
    });
  }
});

onUnmounted(() => {
  activeScrollElement?.removeEventListener("scroll", handleScroll);
  window.clearTimeout(scrollStopTimer);
  stopMeasure();
});
</script>

<template>
  <!-- 有虚拟滚动版本 -->
  <DynamicScroller
    v-if="virtualMessages.length > 0"
    ref="scrollerRef"
    class="chat-history-scroller"
    :items="virtualMessages"
    key-field="id"
    :min-item-size="96"
    :buffer="400"
    :prerender="8"
  >
    <template #default="{ item, active }">
      <DynamicScrollerItem
        :item="asVirtualMessage(item)"
        :active="active"
        :size-dependencies="getSizeDependencies(asVirtualMessage(item))"
        :emit-resize="true"
      >
        <div class="chat-history-item">
          <ChatMessageItem
            :message="asVirtualMessage(item).message"
            :index="asVirtualMessage(item).index"
            :is-last="asVirtualMessage(item).index === messages.length - 1"
          />
        </div>
      </DynamicScrollerItem>
    </template>
  </DynamicScroller>
  <!-- 有虚拟滚动版本 -->

  <!-- 无虚拟滚动版本 -->
  <!-- <div
    v-if="virtualMessages.length > 0"
    ref="scrollerRef"
    class="chat-history-scroller chat-history-fallback"
  >
    <div
      v-for="(message, index) in messages"
      :key="
        message.id ||
        `${store.currentConversationId || 'draft'}-${index}-${message.role}`
      "
      class="chat-history-item"
    >
      <ChatMessageItem
        :message="message"
        :index="index"
        :is-last="index === messages.length - 1"
      />
    </div>
  </div> -->
  <!-- 无虚拟滚动版本 -->
   
  <div v-else class="welcome-tip">今天有什么可以帮到你？</div>
</template>

<style scoped lang="less">
.chat-history-scroller {
  height: 100%;
  padding: 0 24px;
}

.chat-history-fallback {
  overflow-y: auto;
}

.chat-history-item {
  width: 100%;
  max-width: 800px;
  margin: 0 auto;
}

.welcome-tip {
  display: flex;
  justify-content: center;
  align-items: center;
  height: 100%;
  width: 100%;
  color: #333;
  font-size: 22px;
  font-weight: 500;
}
</style>
