import type {
  ApiResponse,
  Conversation,
  ConversationType,
} from "@/types/index";
import { axiosInstance } from "@/api/client";

/**
 * 获取对话列表
 * GET /conversations
 */
export const getConversationsApi = async (): Promise<
  ApiResponse<Conversation[]>
> => {
  const response =
    await axiosInstance.get<ApiResponse<Conversation[]>>("/conversations");
  return response.data;
};

/**
 * 获取对话详情
 * GET /conversations/:id
 */
export const getConversationDetailApi = async (
  id: string,
): Promise<ApiResponse<Conversation>> => {
  const response = await axiosInstance.get<ApiResponse<Conversation>>(
    `/conversations/${id}`,
  );
  return response.data;
};

/**
 * 创建新对话
 * POST /conversations
 */
export const createConversationApi = async (data: {
  title: string;
  groupLabel: string;
  messages: ConversationType;
}): Promise<ApiResponse<Conversation>> => {
  const response = await axiosInstance.post<ApiResponse<Conversation>>(
    "/conversations",
    data,
  );
  return response.data;
};

/**
 * 更新对话标题
 * PATCH /conversations/:id/title
 */
export const updateConversationTitleApi = async (
  id: string,
  title: string,
): Promise<ApiResponse<boolean>> => {
  const response = await axiosInstance.patch<ApiResponse<boolean>>(
    `/conversations/${id}/title`,
    { title },
  );
  return response.data;
};

/**
 * 删除对话
 * DELETE /conversations/:id
 */
export const deleteConversationApi = async (
  id: string,
): Promise<ApiResponse<boolean>> => {
  const response = await axiosInstance.delete<ApiResponse<boolean>>(
    `/conversations/${id}`,
  );
  return response.data;
};
