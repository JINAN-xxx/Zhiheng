import request from "@/utils/request";
import { getToken } from "@/utils/auth";
import { normalizeErrorMessage } from "@/utils/errorMessage";
import { parsePlatformSSE } from "@/utils/platformSse.js";
import { assertChatUploadResponse, chatFileDownloadPath } from "../utils/chatFiles.js";

export {
  listChatSessions as listChatSession,
  getChatSession,
  deleteChatSession as delChatSession,
} from "../public/chatSessions.js";

export async function uploadChatFile(file, { signal, onUploadProgress } = {}) {
  const data = new FormData();
  data.append("file", file);
  let uploadResponse;
  try {
    const response = await request({
      url: "/common/files/upload",
      method: "post",
      data,
      headers: { "Content-Type": "multipart/form-data", repeatSubmit: false },
      timeout: 120000,
      signal,
      onUploadProgress,
      // Preserve the server's explanation even if the shared business-code handler rejects a string.
      transformResponse: [(body) => {
        try { uploadResponse = typeof body === "string" ? JSON.parse(body) : body; }
        catch { uploadResponse = body; }
        return uploadResponse;
      }],
    });
    return assertChatUploadResponse(response);
  } catch (error) {
    if (signal?.aborted) throw error;
    throw new Error(normalizeErrorMessage(uploadResponse && uploadResponse.code !== 200 ? uploadResponse : error, "文件上传失败，请重试"));
  }
}

export async function downloadChatFile(file, signal) {
  const blob = await request({
    url: chatFileDownloadPath(file),
    method: "get",
    responseType: "blob",
    timeout: 120000,
    signal,
  });
  if (blob.type?.includes("application/json")) {
    let detail;
    try { detail = JSON.parse(await blob.text()); } catch { /* Use the download fallback. */ }
    throw new Error(normalizeErrorMessage(detail, "文件下载失败"));
  }
  return blob;
}

// 获取用户对话配置
export function getUserChatConfig() {
  return request({
    url: "/ai/chat/config",
    method: "get",
  });
}

// 保存用户对话配置
export function saveUserChatConfig(data) {
  return request({
    url: "/ai/chat/config",
    method: "put",
    data: data,
  });
}

// 取消对话
export function cancelChatRun(runId) {
  return request({
    url: "/ai/chat/cancel",
    method: "post",
    data: {
      runId: runId,
    },
  });
}

export async function createChatStream(payload, signal) {
  const response = await fetch(import.meta.env.VITE_APP_BASE_API + "/ai/chat/send", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: "Bearer " + getToken(),
    },
    body: JSON.stringify(payload),
    signal,
  });
  if (!response.ok) {
    let detail = null;
    try {
      detail = await response.json();
    } catch {
      // Keep the public fallback stable when a proxy returns a non-JSON error.
    }
    const error = new Error(normalizeErrorMessage(detail, "对话请求失败"));
    error.code = detail?.detail?.code || detail?.code || "CHAT_REQUEST_FAILED";
    error.status = response.status;
    throw error;
  }
  if (!response.body) throw new Error("对话响应为空");
  return parsePlatformSSE(response.body);
}
