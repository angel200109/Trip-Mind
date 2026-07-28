import Mock from "mockjs";
import type { Conversation, ApiResponse } from "@/types/index";
import { mockConversations } from "@/store/mockConversations";

/**
 * mock.ts
 *
 * 使用 Mock.js 模拟会话管理相关的 API
 */

// 生成唯一 ID
const generateId = () => `session-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;

// Mock 数据存储
let conversations = [...mockConversations];

// 模拟网络延迟
Mock.setup({ timeout: "200-600" });

// ==================== 响应生成器 ====================

const success = <T>(data: T, msg = "SUCCESS"): ApiResponse<T> => ({
  data,
  code: 200,
  msg,
  error: null,
  serviceCode: 200,
});

const error = (code: number, msg: string): ApiResponse<null> => ({
  data: null,
  code,
  msg,
  error: msg,
  serviceCode: code,
});

// ==================== Mock 接口配置 ====================

/**
 * GET /api/conversations - 获取对话列表
 * GET /api/conversations/:id - 获取对话详情
 */
Mock.mock(/\/api\/conversations$/, "get", (options: { url: string }) => {

  // 检查是否有查询参数 id
  const urlObj = new URL(options.url, "http://localhost");
  const queryId = urlObj.searchParams.get("id");

  if (queryId) {
    const conversation = conversations.find((c) => c.id === queryId);
    if (conversation) {
      return success({ ...conversation, messages: [...conversation.messages] });
    }
    return error(404, "对话不存在");
  }

  // 返回对话列表
  return success(conversations.map((c) => ({ ...c, messages: [...c.messages] })));
});

/**
 * GET /api/conversations/:id - 获取对话详情（路径参数方式）
 */
Mock.mock(/\/api\/conversations\/[a-zA-Z0-9\-]+$/, "get", (options: { url: string }) => {

  const id = options.url.match(/\/api\/conversations\/([a-zA-Z0-9\-]+)/)?.[1];
  if (!id) {
    return error(400, "Invalid ID");
  }

  const conversation = conversations.find((c) => c.id === id);
  if (conversation) {
    return success({ ...conversation, messages: [...conversation.messages] });
  }

  return error(404, "对话不存在");
});

/**
 * POST /api/conversations - 创建新对话
 */
Mock.mock(/\/api\/conversations$/, "post", (options: { body: string }) => {

  const body = JSON.parse(options.body);
  const newConversation: Conversation = {
    id: generateId(),
    title: body.title || "新的对话",
    groupLabel: body.groupLabel || "今天",
    messages: body.messages || [],
  };

  conversations.unshift(newConversation);

  return success(newConversation, "创建成功");
});

/**
 * PATCH /api/conversations/:id - 更新对话
 */
Mock.mock(/\/api\/conversations\/[a-zA-Z0-9\-]+$/, "patch", (options: { url: string; body: string }) => {

  const id = options.url.match(/\/api\/conversations\/([a-zA-Z0-9\-]+)/)?.[1];
  if (!id) {
    return error(400, "Invalid ID");
  }

  const index = conversations.findIndex((c) => c.id === id);

  if (index !== -1) {
    const body = JSON.parse(options.body);
    conversations[index] = {
      ...conversations[index],
      ...body,
    };

    return success({ ...conversations[index], messages: [...conversations[index].messages] }, "更新成功");
  }

  return error(404, "对话不存在");
});

/**
 * DELETE /api/conversations/:id - 删除对话
 */
Mock.mock(/\/api\/conversations\/[a-zA-Z0-9\-]+$/, "delete", (options: { url: string }) => {

  const id = options.url.match(/\/api\/conversations\/([a-zA-Z0-9\-]+)/)?.[1];
  if (!id) {
    return error(400, "Invalid ID");
  }

  const index = conversations.findIndex((c) => c.id === id);

  if (index !== -1) {
    conversations.splice(index, 1);
    return success(true, "删除成功");
  }

  return error(404, "对话不存在");
});

/**
 * 启用 Mock 服务
 *
 * 注意：这个文件被导入时会自动初始化 Mock
 */
export const setupMock = () => {};

export default Mock;
