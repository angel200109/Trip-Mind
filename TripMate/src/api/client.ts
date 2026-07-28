import axios, { type AxiosInstance, type AxiosResponse } from "axios";
import type { ApiResponse } from "@/types/index";
import { ElMessage } from "element-plus";

const baseUrl = "/api";

const axiosInstance: AxiosInstance = axios.create({
  baseURL: baseUrl,
  timeout: 30000,
  headers: {
    "Content-Type": "application/json",
  },
});

axiosInstance.interceptors.request.use(
  (config) => config,
  (error) => {
    console.error("[axios Request Error]", error);
    return Promise.reject(error);
  },
);

axiosInstance.interceptors.response.use(
  (response: AxiosResponse<ApiResponse<unknown>>) => response,
  (error) => {
    console.error("[axios Response Error]", error);

    if (error.code === "ECONNABORTED" && error.message.includes("timeout")) {
      ElMessage.error({ message: "请求超时，请稍后重试", duration: 2000 });
    } else if (!error.response) {
      ElMessage.error({ message: "网络连接失败", duration: 2000 });
    } else {
      const status = error.response?.status;
      switch (status) {
        case 400:
          ElMessage.error({ message: "请求参数错误", duration: 2000 });
          break;
        case 404:
          ElMessage.error({ message: "请求的资源不存在", duration: 2000 });
          break;
        case 500:
        case 502:
        case 503:
        case 504:
          ElMessage.error({ message: "服务器错误", duration: 2000 });
          break;
        default:
          ElMessage.error({ message: `请求失败 (${status})`, duration: 2000 });
      }
    }

    return Promise.reject(error);
  },
);

export { axiosInstance };
