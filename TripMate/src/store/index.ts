/**
 * 聊天机器人状态管理 Store
 *
 * 负责管理对话列表、当前消息、打字机效果等核心状态
 */
import { defineStore } from "pinia";
import type {
  SendMessage,
  ConversationType,
  ServerDataType,
  TextContent,
  Conversation,
  ChatMessage,
} from "@/types/index";
import {
  getConversationsApi,
  getConversationDetailApi,
  createConversationApi,
} from "@/api/conversation";
import { sendMessageByFetchEventSourceApi } from "@/api/fetchEventSourceRequest";
import { Typewriter } from "@/utils/typewriter";
import { perfTracker } from "@/utils/perf";
import { nextTick } from "vue";

// 消息 ID 种子，用于生成唯一消息 ID
let messageIdSeed = 0;

/**
 * 创建唯一的消息 ID
 * 格式: msg-{timestamp}-{seed}
 */
const createMessageId = () => {
  messageIdSeed += 1;
  return `msg-${Date.now()}-${messageIdSeed}`;
};

const waitNextFrame = () =>
  new Promise<void>((resolve) => {
    window.requestAnimationFrame(() => resolve());
  });

/**
 * 确保消息有 ID，没有则生成新 ID
 */
const ensureMessageId = (message: ChatMessage): ChatMessage => ({
  ...message,
  id: message.id || createMessageId(),
});

/**
 * 规范化消息列表，确保每条消息都有 ID
 */
const normalizeMessages = (messages: ConversationType): ConversationType =>
  messages.map((message) => ensureMessageId(message));

/**
 * 深拷贝消息列表（通过 JSON 序列化/反序列化）
 * 用于避免直接修改原始消息对象
 */
const cloneMessages = (messages: ConversationType): ConversationType =>
  normalizeMessages(
    JSON.parse(JSON.stringify(messages)) as ConversationType,
  );

/**
 * 创建批量模拟消息（用于测试）
 * @param count 消息对数量
 */
const createBulkMockMessages = (count: number): ConversationType => {
  const messages: ConversationType = [];

  for (let index = 1; index <= count; index += 1) {
    const label = String(index).padStart(3, "0");
    messages.push(
      ensureMessageId({
        role: "user",
        content: `Mock question ${label}`,
      }),
    );
    messages.push(
      ensureMessageId({
        role: "assistant",
        content: `Mock answer ${label}`,
      }),
    );
  }

  return messages;
};

/**
 * 扩展第一个模拟对话的消息数量
 * 用于测试大量消息时的性能
 */
const expandFirstMockConversation = (conversations: Conversation[]) => {
  const firstConversation = conversations.find((item) => item.id === "session-1");
  if (!firstConversation) {
    return conversations;
  }

  if (firstConversation.messages.length >= 20000) {
    return conversations;
  }

  firstConversation.messages = [
    ...normalizeMessages(firstConversation.messages),
    ...createBulkMockMessages(5000),
  ];

  return conversations;
};

/**
 * 从用户输入生成对话标题
 * 取前 20 个字符，超出则省略
 */
const getConversationTitle = (content: SendMessage) => {
  const text =
    typeof content === "string" ? content : (content[0] as TextContent).text;
  const trimmedText = text.trim();

  if (!trimmedText) return "新的对话";

  return trimmedText.length > 20
    ? `${trimmedText.slice(0, 20)}...`
    : trimmedText;
};

// ========== 打字机效果管理 ==========

// 当前活跃的打字机实例
let activeTypewriter: Typewriter | null = null;
// 当前打字机绑定的 AI 消息
let activeAssistantMessage: ChatMessage | null = null;

/**
 * 重置打字机状态
 */
const resetActiveTypewriter = () => {
  activeTypewriter = null;
  activeAssistantMessage = null;
};

/**
 * 完成当前打字机效果并重置状态
 */
const flushActiveTypewriter = () => {
  activeTypewriter?.done();
  resetActiveTypewriter();
};

/**
 * 确保有活跃的打字机实例
 * 如果已有且是同一条消息则复用，否则创建新实例
 * @param message AI 消息对象
 */
const ensureActiveTypewriter = (message: ChatMessage) => {
  // 如果已有打字机且是同一条消息，复用现有实例
  if (activeTypewriter && activeAssistantMessage === message) {
    return activeTypewriter;
  }

  // 否则：完成旧打字机，创建新实例
  flushActiveTypewriter();
  activeAssistantMessage = message;
  activeTypewriter = new Typewriter(async (str: string) => {
    const start = performance.now();

    if (typeof message.content !== "string") {
      message.content = "";
    }

    message.content += str;
    message.progress = false;

    await nextTick();
    const commitMs = performance.now() - start;

    await waitNextFrame();
    const frameMs = performance.now() - start;

    perfTracker.recordUiUpdate(commitMs, frameMs);
  });

  return activeTypewriter;
};

// ========== Store 定义 ==========

export const chatbotMessage = defineStore("chatbotMessage", {
  state: () => ({
    // 所有对话列表
    conversations: [] as Conversation[],
    // 当前对话的消息列表
    messages: [] as ConversationType,
    // 当前对话 ID
    currentConversationId: "",
    // 是否禁止操作（AI 响应期间）
    prohibit: false,
    // 用户是否手动滚动（用于判断是否自动滚动到底部）
    userScrolled: false,
    // 草稿输入内容
    draftInput: "",
  }),
  actions: {
    /**
     * 加载对话列表
     */
    async loadConversations() {
      const response = await getConversationsApi();
      this.conversations = expandFirstMockConversation(
        JSON.parse(JSON.stringify(response.data)) as Conversation[],
      );
    },

    /**
     * 同步当前对话到服务器
     * 后端自动持久化消息，前端不再需要同步
     */
    syncCurrentConversation() {
      // 后端自动持久化消息，前端不再需要同步
    },

    /**
     * 切换到指定对话
     * @param id 对话 ID
     */
    async switchConversation(id: string) {
      flushActiveTypewriter();
      const target = this.conversations.find((item) => item.id === id);
      if (!target) {
        // 本地没有，从服务器加载
        const response = await getConversationDetailApi(id);
        this.currentConversationId = id;
        this.messages = cloneMessages(response.data.messages);
        this.userScrolled = false;
        return;
      }

      // 同步当前对话后再切换
      this.syncCurrentConversation();
      this.currentConversationId = id;
      this.userScrolled = false;
      this.messages = cloneMessages(target.messages);
    },

    /**
     * 开始新对话
     * 清空当前对话状态
     */
    startNewConversation() {
      flushActiveTypewriter();
      this.syncCurrentConversation();
      this.currentConversationId = "";
      this.messages = [];
      this.userScrolled = false;
    },

    /**
     * 创建新对话
     * @param content 可选的用户输入内容，用于生成标题
     * @returns 新对话的 ID
     */
    async createConversation(content?: SendMessage) {
      const response = await createConversationApi({
        title: content ? getConversationTitle(content) : "新的对话",
        groupLabel: "今天",
        messages: [],
      });

      this.conversations.unshift(response.data);
      this.currentConversationId = response.data.id;
      this.messages = [];
      this.userScrolled = false;

      return response.data.id;
    },

    /**
     * 确保有活跃对话，没有则创建
     * @param content 可选的用户输入内容
     * @returns 对话 ID
     */
    async ensureConversation(content?: SendMessage) {
      if (this.currentConversationId) {
        return this.currentConversationId;
      }

      return this.createConversation(content);
    },

    /**
     * 发送消息
     * @param content 用户输入的内容
     * @returns 当前对话 ID
     */
    async sendMessage(content: SendMessage) {
      // 确保有活跃对话
      await this.ensureConversation(content);
      flushActiveTypewriter();

      // 添加用户消息
      this.messages.push(ensureMessageId({ role: "user", content }));
      // 创建空的 AI 消息占位
      const assistantMessage = ensureMessageId({
        role: "assistant",
        content: "",
        rawContent: "",
        progress: true,
      });
      this.messages.push(assistantMessage);
      this.syncCurrentConversation();
      this.userScrolled = false;
      this.prohibit = true;

      // 调用流式 API
      await sendMessageByFetchEventSourceApi({ chatMessages: this.messages });
      const aiMessage = this.messages[this.messages.length - 1];
      aiMessage.progress = false;
      this.syncCurrentConversation();
      this.prohibit = false;

      return this.currentConversationId;
    },

    /**
     * 重新生成最后一条 AI 回答
     */
    async regenerateLastAnswer() {
      if (this.prohibit || this.messages.length < 2) return;
      flushActiveTypewriter();

      // 验证最后两条消息是否是 user + assistant
      const lastMessage = this.messages[this.messages.length - 1];
      const lastUserMessage = this.messages[this.messages.length - 2];

      if (
        !lastMessage ||
        !lastUserMessage ||
        lastMessage.role !== "assistant" ||
        lastUserMessage.role !== "user"
      ) {
        return;
      }

      // 移除旧的 AI 回答，创建新的
      this.messages.pop();
      const assistantMessage = ensureMessageId({
        role: "assistant",
        content: "",
        rawContent: "",
        progress: true,
      });
      this.messages.push(assistantMessage);
      this.syncCurrentConversation();
      this.userScrolled = false;
      this.prohibit = true;

      // 重新调用流式 API
      await sendMessageByFetchEventSourceApi({ chatMessages: this.messages });
      const aiMessage = this.messages[this.messages.length - 1];
      aiMessage.progress = false;
      this.syncCurrentConversation();
      this.prohibit = false;
    },

    /**
     * 设置草稿输入
     * @param content 草稿内容
     */
    setDraftInput(content: string) {
      this.draftInput = content;
    },

    /**
     * 完成流式消息传输
     * 清理打字机状态并同步对话
     */
    finishStreamingMessage() {
      flushActiveTypewriter();
      this.syncCurrentConversation();
      perfTracker.endStream("completed");
    },

    /**
     * 根据路由同步对话
     * 用于页面刷新或路由跳转时恢复对话状态
     * @param id 可选的对话 ID
     */
    async syncConversationByRoute(id?: string) {
      flushActiveTypewriter();
      if (!id) {
        // 无 ID，开始新对话
        this.startNewConversation();
        return;
      }

      // 尝试在本地查找对话
      const target = this.conversations.find((item) => item.id === id);
      if (!target) {
        // 本地没有，从服务器加载
        const response = await getConversationDetailApi(id).catch(() => null);
        if (!response) {
          // 服务器也没有，开始新对话
          this.startNewConversation();
          return;
        }
        this.currentConversationId = id;
        this.messages = cloneMessages(response.data.messages);
        // 更新或添加到对话列表
        const index = this.conversations.findIndex((item) => item.id === id);
        if (index >= 0) {
          this.conversations[index] = response.data;
        } else {
          this.conversations.unshift(response.data);
        }
        this.userScrolled = false;
        return;
      }

      // 使用本地缓存
      this.currentConversationId = id;
      this.messages = cloneMessages(target.messages);
      this.userScrolled = false;
    },

    /**
     * 处理服务器返回的流式数据
     * @param res 服务器数据载荷
     */
    async serverData(res: ServerDataType) {
      // 获取当前 AI 消息（最后一条消息）
      const aiMessage = this.messages[this.messages.length - 1];
      if (!aiMessage) return;

      // 处理状态类型消息（显示加载状态等）
      if (res && res.type == "status") {
        flushActiveTypewriter();
        aiMessage.progress = true;
        aiMessage.content = String(res.data ?? "");
        aiMessage.rawContent = "";
      }

      // 处理内容类型消息（流式文本）
      if (res && res.type == "content") {
        aiMessage.progress = false;
        // 清理数据中的 undefined 字符串
        const nextContent = String(res.data ?? "").replace(/undefined/g, "");
        // 首次接收内容时清空 content
        if (typeof aiMessage.content !== "string" || aiMessage.rawContent === "") {
          aiMessage.content = "";
        }
        // 累积原始内容
        aiMessage.rawContent = (aiMessage.rawContent ?? "") + nextContent;
        // 使用打字机效果输出
        const typewriter = ensureActiveTypewriter(aiMessage);
        typewriter.add(nextContent);
        typewriter.start();
      }

      // 同步对话状态
      this.syncCurrentConversation();
    },
  },
});
