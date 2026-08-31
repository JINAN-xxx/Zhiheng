import request from "@/utils/request";
import { getToken } from "@/utils/auth";
import { normalizeErrorMessage } from "@/utils/errorMessage";
import { parsePlatformSSE } from "../utils/sseParser";

// 获取会话列表
export function listChatSession() {
  return request({
    url: "/ai/chat/session/list",
    method: "get",
  });
}

// 删除会话
export function delChatSession(sessionId) {
  return request({
    url: "/ai/chat/session/" + sessionId,
    method: "delete",
  });
}

// 获取会话详情
export function getChatSession(sessionId) {
  return request({
    url: "/ai/chat/session/" + sessionId,
    method: "get",
  });
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
