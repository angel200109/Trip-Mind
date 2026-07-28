import type {
  ApiResponse,
  SearchGoodsType,
  ServerGoodsDetailsItem,
  ServerSearchGoodsType,
} from "@/types/index";
import { axiosInstance } from "@/api/client";

/**
 * 搜索商品（基于 LLM 的智能搜索）
 * POST /searchGoods
 */
export const searchGoodsApi = async (
  data: SearchGoodsType,
): Promise<ApiResponse<ServerSearchGoodsType>> => {
  const response = await axiosInstance.post<ApiResponse<ServerSearchGoodsType>>(
    "/searchGoods",
    data,
  );
  return response.data;
};

/**
 * 获取商品详情
 * GET /goodsDetail
 */
export const goodsDetailApi = async (
  goodsId: string,
): Promise<ApiResponse<ServerGoodsDetailsItem>> => {
  const response = await axiosInstance.get<ApiResponse<ServerGoodsDetailsItem>>(
    "/goodsDetail",
    {
      params: { goodsId },
    },
  );
  return response.data;
};
