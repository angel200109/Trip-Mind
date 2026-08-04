/**
 * fetchEventSource 流式请求 API
 *
 * 基于 @microsoft/fetch-event-source 实现 Server-Sent Events (SSE) 流式通信
 * 支持断线重连、自动重试和会话恢复
 */
import { fetchEventSource, EventStreamContentType } from "@microsoft/fetch-event-source";
import type { ConversationType, ServerDataType } from "@/types/index";
import { chatbotMessage } from "@/store/index";
import { ElMessage } from "element-plus";
import { perfTracker } from "@/utils/perf";

// API 基础路径
const baseUrl = "/api";

// 最大恢复尝试次数（断线重连）
const MAX_RESUME_ATTEMPTS = 3;

// 重试延迟配置（指数退避 + 抖动）
const INITIAL_RETRY_DELAY_MS = 1200; // 初始延迟 1.2 秒
const MAX_RETRY_DELAY_MS = 8000; // 最大延迟 8 秒
const RETRY_BACKOFF_FACTOR = 2; // 退避因子（每次翻倍）
const RETRY_JITTER_FACTOR = 0.2; // 抖动因子（避免雷群效应）

/**
 * 流式会话状态
 */
type StreamState = {
  requestId: string; // 会话唯一标识
  lastChunkId: number; // 最后接收的数据块 ID
  done: boolean; // 流是否已完成
};

/**
 * 延迟函数
 * @param ms 延迟毫秒数
 */
const delay = (ms: number) =>
  new Promise((resolve) => {
    setTimeout(resolve, ms);
  });

/**
 * 创建流式错误对象
 * @param message 错误信息
 */
const createStreamError = (message: string) => {
  const error = new Error(message);
  error.name = "StreamError";
  return error;
};

/**
 * 计算重试延迟时间（指数退避 + 随机抖动）
 *
 * 公式：min(INITIAL_DELAY * BACKOFF^(attempt-1), MAX_DELAY) ± jitter
 *
 * @param attempt 当前尝试次数
 * @returns 延迟毫秒数
 */
const getRetryDelay = (attempt: number) => {
  // 计算基础延迟（指数退避）
  const baseDelay = Math.min(
    INITIAL_RETRY_DELAY_MS * RETRY_BACKOFF_FACTOR ** Math.max(attempt - 1, 0),
    MAX_RETRY_DELAY_MS,
  );
  // 添加随机抖动（避免多个客户端同时重试）
  const jitterRange = baseDelay * RETRY_JITTER_FACTOR;
  const jitterOffset = (Math.random() * 2 - 1) * jitterRange;
  return Math.max(0, Math.round(baseDelay + jitterOffset));
};

/**
 * 处理流式消息载荷
 *
 * 负责解析后端返回的数据块，更新会话状态，并将数据分发到 store
 *
 * @param payload 后端返回的数据载荷
 * @param state 当前流式会话状态
 */
const handleStreamPayload = (
  payload: ServerDataType & {
    requestId?: string;
    chunkId?: number;
    done?: boolean;
    error?: string;
    conversationId?: string;
  },
  state: StreamState,
) => {
  // 更新会话 ID
  if (payload.requestId) {
    state.requestId = payload.requestId;
  }

  // 更新数据块 ID（去重：跳过已处理的数据块）
  if (typeof payload.chunkId === "number") {
    if (payload.chunkId <= state.lastChunkId) {
      return; // 已处理过，跳过
    }
    state.lastChunkId = payload.chunkId;
  }

  // 处理元数据类型（包含会话信息）
  if (payload.type === "meta") {
    const metaRequestId = payload.data?.requestId;
    if (typeof metaRequestId === "string" && metaRequestId) {
      state.requestId = metaRequestId;
    }
    // 从 meta 事件提取 conversationId 并更新 store
    if (payload.conversationId) {
      const store = chatbotMessage();
      store.currentConversationId = payload.conversationId;
      void store.loadConversations();
    }
    return; // 元数据不需要显示
  }

  // 记录性能指标
  perfTracker.markChunk(
    typeof payload.data === "string"
      ? payload.data.length
      : JSON.stringify(payload.data ?? "").length,
  );
  // 将数据分发到 store 进行处理
  chatbotMessage().serverData(payload);
};

/**
 * 执行单次流式请求
 *
 * 使用 @microsoft/fetch-event-source 发起 SSE 请求，处理消息流
 *
 * @param endpoint API 端点路径
 * @param body 请求体
 * @param state 流式会话状态
 * @throws {Error} 当连接失败、内容类型错误或流异常关闭时抛出错误
 */
const runSingleStream = async (
  endpoint: string,
  body: Record<string, unknown>,
  state: StreamState,
) => {
  // 用于中断流式请求
  const controller = new AbortController();

  await fetchEventSource(`${baseUrl}${endpoint}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    body: JSON.stringify(body),
    signal: controller.signal,
    openWhenHidden: true, // 页面隐藏时仍保持连接

    // === 连接建立时的回调 ===
    async onopen(response) {
      const contentType = response.headers.get("content-type") || "";
      if (!response.ok) {
        throw createStreamError(`HTTP_${response.status}`);
      }
      if (!contentType.includes(EventStreamContentType)) {
        throw createStreamError("INVALID_CONTENT_TYPE");
      }
    },

    // === 接收到消息时的回调 ===
    onmessage(message) {
      // 处理完成事件
      if (message.event === "done") {
        try {
          const payload = JSON.parse(message.data) as {
            requestId?: string;
            chunkId?: number;
            done?: boolean;
          };
          if (payload.requestId) {
            state.requestId = payload.requestId;
          }
          if (typeof payload.chunkId === "number") {
            state.lastChunkId = Math.max(state.lastChunkId, payload.chunkId);
          }
        } catch {}
        state.done = true;
        controller.abort(); // 中断连接
        return;
      }

      // 处理错误事件
      if (message.event === "error") {
        let errorMessage = "流式请求失败";
        try {
          const payload = JSON.parse(message.data) as { error?: string };
          errorMessage = payload.error || errorMessage;
        } catch {}
        throw createStreamError(errorMessage);
      }

      // 处理普通消息事件
      if (!message.data) return;
      const payload = JSON.parse(message.data) as ServerDataType & {
        requestId?: string;
        chunkId?: number;
        conversationId?: string;
      };
      handleStreamPayload(payload, state);
    },

    // === 连接关闭时的回调 ===
    onclose() {
      // 如果未正常完成，视为异常关闭
      if (!state.done) {
        throw createStreamError("STREAM_CLOSED");
      }
    },

    // === 错误处理回调 ===
    onerror(error) {
      throw error;
    },
  });
};

/**
 * 发送消息并使用 fetch-event-source 处理流式响应
 *
 * 核心特性：
 * 1. 断线重连：网络中断时自动从上次位置恢复
 * 2. 指数退避：重试间隔逐渐增加，避免服务器压力
 * 3. 会话恢复：通过 requestId + lastChunkId 精确恢复
 *
 * 只传当前用户提问（userQuery），会话历史由后端从 PG 读取
 *
 * @param data 包含当前用户提问的请求数据
 * @throws {Error} 超过最大重试次数后抛出错误
 */
export const sendMessageByFetchEventSourceApi = async (data: {
  userQuery: string;
}): Promise<void> => {
  console.log("[chat-perf] api entered");
  perfTracker.startStream("chat-stream");

  // 初始化会话状态
  const state: StreamState = {
    requestId: crypto.randomUUID(), // 生成唯一会话 ID
    lastChunkId: 0,
    done: false,
  };

  let attempts = 0; // 当前重试次数

  // 主循环：持续尝试直到流完成或超过最大重试次数
  while (!state.done) {
    // 构建请求体（统一接口）
    const body = {
      ...data,
      conversationId: chatbotMessage().currentConversationId || undefined,
      requestId: state.requestId,
      lastChunkId: state.lastChunkId,
    };

    try {
      // 记录请求性能（通过 lastChunkId 判断是否为恢复请求）
      const isResume = state.lastChunkId > 0;
      perfTracker.markRequest(isResume);

      console.log(
        `[🔌 重连调试] ${isResume ? "恢复请求" : "首次请求"} | requestId: ${state.requestId} | lastChunkId: ${state.lastChunkId}`
      );

      // 执行流式请求（统一使用 /chatMessage/stream 接口）
      await runSingleStream("/chatMessage/stream", body, state);
      // 成功则重置重试计数
      attempts = 0;
      // 检查是否完成
      if (state.done) {
        chatbotMessage().finishStreamingMessage();
        return;
      }
    } catch (error) {
      // 错误处理：尝试重连
      attempts += 1;

      const retryDelay = getRetryDelay(attempts);
      console.warn(
        `[🔌 重连调试] 连接断开，第 ${attempts} 次重试 | ${retryDelay}ms 后重试 | lastChunkId: ${state.lastChunkId}`
      );

      if (attempts > MAX_RESUME_ATTEMPTS) {
        // 超过最大重试次数，放弃
        perfTracker.endStream("failed");
        chatbotMessage().finishStreamingMessage();
        console.error("fetch-event-source stream failed:", error);
        ElMessage.error({ message: "流式连接已中断，请稍后重试" });
        throw error;
      }
      // 等待后重试（指数退避 + 抖动）
      await delay(retryDelay);
    }
  }
};
