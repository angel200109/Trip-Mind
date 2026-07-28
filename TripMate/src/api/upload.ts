import type { ApiResponse } from "@/types/index";
import { axiosInstance } from "@/api/client";

/**
 * 上传文件
 * POST /uploadFile
 */
export const uploadFileApi = async (
  file: File,
): Promise<ApiResponse<string>> => {
  const formData = new FormData();
  formData.append("file", file);

  const response = await axiosInstance.post<ApiResponse<string>>(
    "/uploadFile",
    formData,
    {
      headers: {
        "Content-Type": "multipart/form-data",
      },
    },
  );
  return response.data;
};
