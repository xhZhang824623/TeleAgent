"use client";

import Link from "next/link";
import { useEffect, useRef, useState, useCallback } from "react";
import { Markdown } from "./Markdown";
import { FolderPicker } from "./FolderPicker";
import { PermissionCard, PermissionRow } from "./PermissionCard";
import {
  Button, IconButton, Eyebrow, Badge, Chip, Avatar, Input, Textarea, Select,
  StatusBadge, ChatBubble, EventLine,
} from "../components/ui";

import {
  API_BROKER, API_AUTH, AGENT_OPTIONS, AGENT_OPTION_SCHEMA, TOKEN_KEY, EMAIL_KEY,
  getStoredToken, agentLabel, extractAssistantText, formatToolLine, findActiveTaskId,
  shortPathLabel, formatRelativeTime, taskDisplayStatus, formatBytes, formatTime,
} from "./lib";
import type { AgentType, Client, Conversation, Task, Message, LiveLine, LiveTask, FileTransfer } from "./lib";

export default function TeleAgentPage() {
  const [token, setToken] = useState<string | null>(null);
  const [authChecked, setAuthChecked] = useState(false);
  const messageListRef = useRef<HTMLDivElement | null>(null);
  const messageEndRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    setToken(getStoredToken());
    setAuthChecked(true);
  }, []);

  const api = useCallback(
    async (method: string, path: string, body?: object) => {
      const t = token ?? getStoredToken();
      const headers: Record<string, string> = { "Content-Type": "application/json" };
      if (t) headers["Authorization"] = `Token ${t}`;
      const opts: RequestInit = { method, headers };
      if (body) opts.body = JSON.stringify(body);
      const r = await fetch(API_BROKER + path, opts);
      if (r.status === 401) {
        if (typeof window !== "undefined") {
          localStorage.removeItem(TOKEN_KEY);
          localStorage.removeItem(EMAIL_KEY);
        }
        setToken(null);
        throw new Error("登录已过期，请重新登录");
      }
      if (!r.ok) throw new Error(await r.text());
      if (r.status === 204) return null;
      return r.json();
    },
    [token]
  );

  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [currentConvId, setCurrentConvId] = useState<string | null>(null);
  // 始终指向「当前选中的会话」。用于丢弃过期请求的回包：切换会话时多个 refresh 可能并发，
  // 或旧会话的 SSE/轮询回调晚到，只有 id 仍等于当前选中时才允许更新内容（最新选中者胜）。
  const currentConvIdRef = useRef<string | null>(null);
  // clients 以 ref 持有：refreshConversation 只为拼客户端名读它，若把 clients 进依赖会让
  // refreshConversation 每次 setClients 都变引用 → 流式 SSE effect 误判依赖变化而重连。
  const clientsRef = useRef<Client[]>([]);
  const [convDetail, setConvDetail] = useState<{
    title?: string;
    cwd: string;
    messages: Message[];
    assigned_client_id?: string;
    force?: boolean;
    session_id?: string | null;
    options?: Record<string, string>;
    agent_type?: AgentType;
  } | null>(null);
  const [clients, setClients] = useState<Client[]>([]);
  const [prompt, setPrompt] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState("");
  const [newCwd, setNewCwd] = useState("");
  const [newTitle, setNewTitle] = useState("");
  const [newOptions, setNewOptions] = useState<Record<string, string>>({});
  const [newClientId, setNewClientId] = useState("");
  const [newAgentType, setNewAgentType] = useState<AgentType>("cursor_agent");
  const [cwdPickerOpen, setCwdPickerOpen] = useState(true);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [pollTaskId, setPollTaskId] = useState<string | null>(null);
  const [liveTasks, setLiveTasks] = useState<Record<string, LiveTask>>({});
  const [sessionMetaOpen, setSessionMetaOpen] = useState(false);
  const [fileBrowserOpen, setFileBrowserOpen] = useState(false);
  const [convFiles, setConvFiles] = useState<FileTransfer[]>([]);
  const [convPerms, setConvPerms] = useState<import("./lib").PermissionCard[]>([]);
  const [permPanelOpen, setPermPanelOpen] = useState(false);
  const [permModalDismissed, setPermModalDismissed] = useState(false);
  // 本地已应答（批准/拒绝）的审批 id：乐观更新后，2s 轮询可能短暂仍返回 pending，
  // 用它把这些请求挡在「待批」之外，避免弹框在后端落库前重复弹出。会话切换时清空。
  const answeredPermIdsRef = useRef<Set<string>>(new Set());
  // 单调递增的行号，给流式事件行生成永不重复的 key（即便后面对 lines 截断也不冲突）。
  const lineSeqRef = useRef(0);
  // 自动滚动用 rAF 合帧：流式 token 高频更新时，每帧最多滚动一次，避免逐 token 的布局抖动。
  const scrollRafRef = useRef<number | null>(null);
  const [forceScrollTick, setForceScrollTick] = useState(0);

  // 登录/注册表单
  const [authEmail, setAuthEmail] = useState("");
  const [authPassword, setAuthPassword] = useState("");
  const [authError, setAuthError] = useState<string | null>(null);
  const [authLoading, setAuthLoading] = useState(false);

  const doLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setAuthError(null);
    setAuthLoading(true);
    try {
      const r = await fetch(`${API_AUTH}/login/`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: authEmail.trim().toLowerCase(),
          password: authPassword,
        }),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.detail || "登录失败");
      const t = data.token;
      if (t) {
        if (typeof window !== "undefined") {
          localStorage.setItem(TOKEN_KEY, t);
          localStorage.setItem(EMAIL_KEY, data.email || authEmail);
        }
        setToken(t);
      } else throw new Error("未返回 token");
    } catch (e) {
      setAuthError(e instanceof Error ? e.message : "登录失败");
    } finally {
      setAuthLoading(false);
    }
  };

  const doRegister = async (e?: React.FormEvent) => {
    e?.preventDefault();
    setAuthError(null);
    setAuthLoading(true);
    try {
      const r = await fetch(`${API_AUTH}/register/`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: authEmail.trim().toLowerCase(),
          password: authPassword,
        }),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.detail || "注册失败");
      const t = data.token;
      if (t) {
        if (typeof window !== "undefined") {
          localStorage.setItem(TOKEN_KEY, t);
          localStorage.setItem(EMAIL_KEY, data.email || authEmail);
        }
        setToken(t);
      } else throw new Error("未返回 token");
    } catch (e) {
      setAuthError(e instanceof Error ? e.message : "注册失败");
    } finally {
      setAuthLoading(false);
    }
  };

  const logout = () => {
    if (typeof window !== "undefined") {
      localStorage.removeItem(TOKEN_KEY);
      localStorage.removeItem(EMAIL_KEY);
    }
    setToken(null);
    setError(null);
  };

  const loadConversations = useCallback(async () => {
    try {
      const list = await api("GET", "/conversations/");
      setConversations(list);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    }
  }, [api]);

  const loadClients = useCallback(async () => {
    try {
      const list = await api("GET", "/clients/");
      setClients(list || []);
      return list || [];
    } catch {
      setClients([]);
      return [];
    }
  }, [api]);

  const refreshConversation = useCallback(
    async (id: string) => {
      const conv = await api("GET", `/conversations/${id}/`);
      // 回包晚到时，若用户已切到别的会话，丢弃本次结果，避免旧内容覆盖当前会话。
      if (currentConvIdRef.current !== id) return conv;
      const knownClients = clientsRef.current;
      const clientLabel =
        conv.assigned_client_id && knownClients.length
          ? (knownClients.find((c) => c.id === conv.assigned_client_id)?.name ?? conv.assigned_client_id)
          : "";
      setConvDetail({
        title: conv.title,
        cwd: conv.cwd + (clientLabel ? `  ·  PC: ${clientLabel}` : "") + `  ·  CLI: ${agentLabel(conv.agent_type)}`,
        messages: conv.messages || [],
        assigned_client_id: conv.assigned_client_id,
        force: conv.force,
        session_id: conv.session_id,
        options: conv.options || {},
        agent_type: conv.agent_type,
      });
      return conv;
    },
    [api]
  );

  const selectConv = useCallback(
    async (id: string) => {
      currentConvIdRef.current = id;   // 同步标记最新选中，先于任何 await
      setCurrentConvId(id);
      setConvDetail(null);             // 立刻清掉上一个会话的内容，避免切换时残留
      setPollTaskId(null);             // 停掉上一个会话的实时流，避免串到新会话视图
      setLiveTasks({});                // 释放上个会话的实时行，避免 liveTasks 跨会话无界泄漏
      setSidebarOpen(false);
      try {
        await refreshConversation(id);
      } catch (e) {
        if (currentConvIdRef.current !== id) return;
        setConvDetail({
          cwd: "",
          messages: [],
        });
        setError(e instanceof Error ? e.message : "加载失败");
      }
    },
    [refreshConversation]
  );

  // 让 ref 跟随 currentConvId（覆盖非 selectConv 的设置路径，如删除会话置空）。
  useEffect(() => {
    currentConvIdRef.current = currentConvId;
  }, [currentConvId]);

  // clientsRef 跟随 clients，供 refreshConversation 读取而不必把 clients 纳入依赖。
  useEffect(() => {
    clientsRef.current = clients;
  }, [clients]);

  useEffect(() => {
    if (token) {
      loadConversations();
      loadClients();
    }
  }, [token, loadConversations, loadClients]);

  useEffect(() => {
    if (!pollTaskId || !currentConvId) return;
    const currentToken = token ?? getStoredToken();
    if (!currentToken) return;
    const controller = new AbortController();
    setLiveTasks((prev) => ({
      ...prev,
      [pollTaskId]: { status: "running", lines: [] },
    }));

    const DEDUP_WINDOW = 64;     // 去重仅扫描尾部窗口，避免随事件数增长的 O(n²) 全扫描
    const MAX_LIVE_LINES = 2000; // 单任务实时行上限，超出丢弃最旧的，防止长任务内存无界增长
    const appendLine = (taskId: string, tone: LiveLine["tone"], text: string) => {
      if (!text) return;
      lineSeqRef.current += 1;
      const seqId = `${tone}-${lineSeqRef.current}`;
      setLiveTasks((prev) => {
        const current = prev[taskId] || { status: "running", lines: [] };
        const tail = current.lines.length > DEDUP_WINDOW ? current.lines.slice(-DEDUP_WINDOW) : current.lines;
        if (tail.some((line) => line.tone === tone && line.text === text)) {
          return prev;
        }
        if (tone === "assistant") {
          const lastLine = current.lines[current.lines.length - 1];
          if (lastLine?.tone === "assistant") {
            if (text.startsWith(lastLine.text)) {
              return {
                ...prev,
                [taskId]: {
                  ...current,
                  lines: [
                    ...current.lines.slice(0, -1),
                    { ...lastLine, text, id: `assistant-${text.length}` },
                  ],
                },
              };
            }
            if (lastLine.text.startsWith(text)) {
              return prev;
            }
          }
        }
        if (tone === "result") {
          const lastAssistant = [...current.lines].reverse().find((line) => line.tone === "assistant");
          if (lastAssistant?.text.trim() === text.trim()) {
            return prev;
          }
        }
        const appended = [...current.lines, { id: seqId, tone, text }];
        const lines = appended.length > MAX_LIVE_LINES ? appended.slice(-MAX_LIVE_LINES) : appended;
        return {
          ...prev,
          [taskId]: { ...current, lines },
        };
      });
    };

    const pollUntilFinished = async () => {
      appendLine(pollTaskId, "system", "正在持续获取结果…");
      while (!controller.signal.aborted) {
        try {
          const response = await fetch(`${API_BROKER}/tasks/${pollTaskId}/`, {
            method: "GET",
            headers: {
              Authorization: `Token ${currentToken}`,
              Accept: "application/json",
            },
            signal: controller.signal,
          });
          if (response.status === 401) {
            throw new Error("登录已过期，请重新登录");
          }
          if (!response.ok) {
            throw new Error(`poll failed: ${response.status}`);
          }
          const task = (await response.json()) as {
            status?: string;
            result_text?: string | null;
            events?: Array<Record<string, unknown>>;
          };
          const status = String(task.status || "running");
          setLiveTasks((prev) => ({
            ...prev,
            [pollTaskId]: {
              ...(prev[pollTaskId] || { status: "running", lines: [] }),
              status,
              resultText: task.result_text || prev[pollTaskId]?.resultText,
            },
          }));
          if (task.result_text) {
            appendLine(pollTaskId, "result", task.result_text);
          }
          if (!["queued", "running"].includes(status)) {
            await refreshConversation(currentConvId);
            setLiveTasks((prev) => {
              const next = { ...prev };
              delete next[pollTaskId];
              return next;
            });
            setPollTaskId((prev) => (prev === pollTaskId ? null : prev));
            return;
          }
        } catch (e) {
          if ((e as Error).name === "AbortError") return;
          setError(e instanceof Error ? e.message : "任务轮询失败");
          return;
        }
        await new Promise((resolve) => setTimeout(resolve, 1500));
      }
    };

    const run = async () => {
      try {
        const response = await fetch(`${API_BROKER}/tasks/${pollTaskId}/stream/`, {
          method: "GET",
          headers: {
            Authorization: `Token ${currentToken}`,
            Accept: "text/event-stream",
          },
          signal: controller.signal,
        });
        if (!response.ok || !response.body) {
          throw new Error(`stream failed: ${response.status}`);
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });

          let boundary = buffer.indexOf("\n\n");
          while (boundary !== -1) {
            const packet = buffer.slice(0, boundary);
            buffer = buffer.slice(boundary + 2);
            const raw = packet
              .split("\n")
              .filter((line) => line.startsWith("data:"))
              .map((line) => line.slice(5).trim())
              .join("\n");
            if (!raw) {
              boundary = buffer.indexOf("\n\n");
              continue;
            }

            const event = JSON.parse(raw) as Record<string, unknown>;
            const type = String(event.type || "");
            if (type === "assistant") {
              appendLine(pollTaskId, "assistant", extractAssistantText(event));
            } else if (type === "tool_call") {
              appendLine(
                pollTaskId,
                "tool",
                formatToolLine((event.tool_call as Record<string, unknown>) || {}, String(event.subtype || ""))
              );
            } else if (type === "result") {
              const resultText = String(event.result || "");
              appendLine(pollTaskId, "result", resultText);
              setLiveTasks((prev) => ({
                ...prev,
                [pollTaskId]: {
                  ...(prev[pollTaskId] || { status: "running", lines: [] }),
                  resultText,
                },
              }));
            } else if (type === "permission_request") {
              const id = String(event.id || "");
              if (id) {
                const card = {
                  id,
                  tool_name: String(event.tool_name || ""),
                  tool_input: (event.tool_input as Record<string, unknown>) || {},
                  status: "pending" as const,
                };
                setLiveTasks((prev) => {
                  const cur = prev[pollTaskId] || { status: "running", lines: [] };
                  const existing = cur.permissions || [];
                  if (existing.some((p) => p.id === id)) return prev;
                  return { ...prev, [pollTaskId]: { ...cur, permissions: [...existing, card] } };
                });
              }
            } else if (type === "permission_resolved") {
              const id = String(event.id || "");
              const decision = String(event.decision || "");
              setLiveTasks((prev) => {
                const cur = prev[pollTaskId];
                if (!cur?.permissions) return prev;
                return {
                  ...prev,
                  [pollTaskId]: {
                    ...cur,
                    permissions: cur.permissions.map((p) =>
                      p.id === id ? { ...p, status: decision === "allow" ? "allowed" : "denied" } : p
                    ),
                  },
                };
              });
            } else if (type === "system" && event.subtype === "init") {
              const sessionId = String(event.session_id || "");
              appendLine(pollTaskId, "system", `session: ${sessionId}`);
            } else if (type === "system" && event.subtype === "end") {
              const status = String(event.status || "success");
              setLiveTasks((prev) => ({
                ...prev,
                [pollTaskId]: {
                  ...(prev[pollTaskId] || { status: "running", lines: [] }),
                  status,
                },
              }));
              await refreshConversation(currentConvId);
              setLiveTasks((prev) => {
                const next = { ...prev };
                delete next[pollTaskId];
                return next;
              });
              setPollTaskId((prev) => (prev === pollTaskId ? null : prev));
              return;
            }
            boundary = buffer.indexOf("\n\n");
          }
        }
      } catch (e) {
        if ((e as Error).name === "AbortError") return;
        await pollUntilFinished();
      }
    };

    run();
    return () => controller.abort();
  }, [currentConvId, pollTaskId, refreshConversation, token]);

  // 轮询当前会话内「可下载的文件」（AI 通过 teleagent-send 发回的文件出现在这里）。
  useEffect(() => {
    if (!currentConvId || !token) {
      setConvFiles([]);
      return;
    }
    let cancelled = false;
    const tick = async () => {
      try {
        const files = await api("GET", `/conversations/${currentConvId}/files/`);
        if (!cancelled) setConvFiles(Array.isArray(files) ? files : []);
      } catch {
        /* 轮询失败忽略 */
      }
    };
    tick();
    const iv = setInterval(tick, 4000);
    return () => {
      cancelled = true;
      clearInterval(iv);
    };
  }, [currentConvId, token, api]);

  // 轮询当前会话内的工具审批（含已批准/已拒绝），作为对话流里的「对方消息」渲染。
  useEffect(() => {
    if (!currentConvId || !token) {
      setConvPerms([]);
      return;
    }
    let cancelled = false;
    const tick = async () => {
      try {
        const list = await api("GET", `/conversations/${currentConvId}/permissions/`);
        if (!cancelled) {
          setConvPerms(
            Array.isArray(list)
              ? list.map((p: { id: string; tool_name: string; tool_input?: Record<string, unknown>; status: string; created_at?: string }) => ({
                  id: p.id,
                  tool_name: p.tool_name,
                  tool_input: p.tool_input,
                  status: (p.status as "pending" | "allowed" | "denied") || "pending",
                  created_at: p.created_at,
                }))
              : []
          );
        }
      } catch {
        /* 轮询失败忽略 */
      }
    };
    tick();
    const iv = setInterval(tick, 2000);
    return () => {
      cancelled = true;
      clearInterval(iv);
    };
  }, [currentConvId, token, api]);

  const handleSend = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!currentConvId || !prompt.trim()) return;
    setLoading(true);
    setError(null);
    setForceScrollTick((value) => value + 1);
    try {
      const res = await api("POST", `/conversations/${currentConvId}/messages/`, {
        prompt: prompt.trim(),
        force: convDetail?.force ?? false,  // 继承会话级权限（与常驻进程一致）
        output_format: "stream-json",
        stream_partial: true,
        timeout_sec: 1800,
      });
      setPrompt("");
      const conv = await refreshConversation(currentConvId);
      const refreshedTaskId = findActiveTaskId(conv?.messages || []);
      if (refreshedTaskId) {
        setPollTaskId(refreshedTaskId);
      } else if (typeof res.task_id === "string") {
        setPollTaskId(res.task_id);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "发送失败");
    } finally {
      setLoading(false);
    }
  };

  // 工具审批：用户在审批卡片上点允许/总是允许/拒绝。乐观更新卡片状态，再回写后端。
  const answerPermission = async (permId: string, decision: "allow" | "deny", remember: boolean) => {
    answeredPermIdsRef.current.add(permId);  // 立刻挡住弹框，避免轮询回灌 pending 重弹
    // 乐观地把卡片标记为已决（按钮立即消失、留在对话流里），轮询稍后同步真实状态。
    setConvPerms((prev) =>
      prev.map((p) => (p.id === permId ? { ...p, status: decision === "allow" ? "allowed" : "denied" } : p))
    );
    try {
      await api("PATCH", `/permissions/${permId}/`, { decision, remember });
    } catch (e) {
      setError(e instanceof Error ? e.message : "审批失败");
    }
  };

  // 带鉴权地下载已就绪的文件传输（Token 头无法走 <a href>，故 fetch → blob → 触发下载）。
  const downloadTransfer = async (id: string, filename: string) => {
    const t = token ?? getStoredToken();
    try {
      const r = await fetch(`${API_BROKER}/files/${id}/download/`, {
        headers: t ? { Authorization: `Token ${t}` } : {},
      });
      if (!r.ok) throw new Error(`下载失败: ${r.status}`);
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename || "download";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(e instanceof Error ? e.message : "下载失败");
    }
  };

  // Web 发起下载：建传输请求 → 轮询到 ready（broker 读盘上传）→ 拉取 blob 下载。
  const requestAndDownload = async (clientId: string, path: string, name: string) => {
    try {
      const created = await api("POST", "/files/request/", {
        client_id: clientId,
        path,
        ...(currentConvId ? { conversation_id: currentConvId } : {}),
      });
      const id = created.id as string;
      const deadline = Date.now() + 60_000;
      while (Date.now() < deadline) {
        const t = await api("GET", `/files/${id}/`);
        if (t.status === "ready") {
          await downloadTransfer(id, t.filename || name);
          return;
        }
        if (t.status === "failed") throw new Error(t.error || "读取文件失败");
        await new Promise((r) => setTimeout(r, 1000));
      }
      throw new Error("超时：设备未响应（确认 Broker 在线）");
    } catch (e) {
      setError(e instanceof Error ? e.message : "下载失败");
    }
  };

  const handlePromptKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key !== "Enter" || e.shiftKey) return;
    e.preventDefault();
    if (loading || !prompt.trim() || !currentConvId) return;
    void handleSend(e);
  };

  const handleNewConv = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newCwd.trim()) return;
    try {
      // 从 schema + 用户选择构建 options（去掉空值），并由 permission_mode 派生 force。
      const schema = AGENT_OPTION_SCHEMA[newAgentType] || [];
      const options: Record<string, string> = {};
      for (const opt of schema) {
        const v = newOptions[opt.key] ?? opt.default;
        if (v) options[opt.key] = v;
      }
      const body: { cwd: string; title: string; agent_type: AgentType; client_id?: string; force: boolean; options: Record<string, string> } = {
        cwd: newCwd.trim(),
        title: newTitle.trim(),
        agent_type: newAgentType,
        force: options.permission_mode === "bypassPermissions",
        options,
      };
      if (newClientId) body.client_id = newClientId;
      const conv = await api("POST", "/conversations/", body);
      setModalOpen(false);
      setNewCwd("");
      setNewTitle("");
      setNewClientId("");
      setNewAgentType("cursor_agent");
      setNewOptions({});
      setCwdPickerOpen(true);
      setAdvancedOpen(false);
      await loadConversations();
      selectConv(conv.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "创建失败");
    }
  };

  const openNewModal = async () => {
    const list = await loadClients();
    const firstSupported = list?.[0]?.supported_agents?.[0];
    if (firstSupported) setNewAgentType(firstSupported);
    setCwdPickerOpen(true);
    setAdvancedOpen(false);
    setModalOpen(true);
  };

  const startRename = () => {
    setTitleDraft(convDetail?.title || "");
    setEditingTitle(true);
  };

  const handleRename = async () => {
    if (!currentConvId) return;
    const t = titleDraft.trim();
    setEditingTitle(false);
    if (!t || t === (convDetail?.title || "")) return;
    try {
      await api("PATCH", `/conversations/${currentConvId}/`, { title: t });
      await refreshConversation(currentConvId);
      await loadConversations();
    } catch (e) {
      setError(e instanceof Error ? e.message : "重命名失败");
    }
  };

  const sendControl = async (action: string, value: string) => {
    if (!currentConvId) return;
    // 乐观更新：立即反映到界面（权限模式同时联动 force）
    setConvDetail((prev) => {
      if (!prev) return prev;
      const options = { ...(prev.options || {}) };
      let force = prev.force;
      if (action === "set_permission_mode") {
        options.permission_mode = value;
        force = value === "bypassPermissions";
      } else if (action === "set_model") {
        options.model = value;
      }
      return { ...prev, options, force };
    });
    try {
      await api("POST", `/conversations/${currentConvId}/control/`, { action, value });
    } catch (e) {
      setError(e instanceof Error ? e.message : "控制指令失败");
    }
  };

  const handleDeleteConversation = async (convId: string) => {
    const target = conversations.find((item) => item.id === convId);
    const title = target?.title || target?.cwd || "这个会话";
    if (typeof window !== "undefined" && !window.confirm(`删除会话“${title}”？`)) {
      return;
    }
    try {
      await api("DELETE", `/conversations/${convId}/`);
      // liveTasks 只持有「当前打开会话」的任务（切换会话时已清空），故删的若是当前会话就整体清空，
      // 删别的会话则与 liveTasks 无关、无需处理。
      if (currentConvId === convId) {
        setCurrentConvId(null);
        setConvDetail(null);
        setPollTaskId(null);
        setLiveTasks({});
      }
      await loadConversations();
    } catch (e) {
      setError(e instanceof Error ? e.message : "删除会话失败");
    }
  };

  const selectedClient = clients.find((c) => c.id === newClientId) || null;
  const agentChoices = (selectedClient?.supported_agents?.length
    ? selectedClient.supported_agents
    : AGENT_OPTIONS.map((item) => item.value)) as AgentType[];
  const activeTaskId = findActiveTaskId(convDetail?.messages);
  const activeLiveTask = activeTaskId ? liveTasks[activeTaskId] : null;
  const browseClientId = convDetail?.assigned_client_id || "";
  // 主聊天流只保留消息；工具审批移到顶部「权限审批」折叠面板与全局弹框处理（见下）。
  const timeline = (convDetail?.messages || []).map((m) => ({ id: m.id, msg: m }));
  // 待批准请求：驱动顶部高亮徽标与「需要你批准」全局弹框。排除本地已应答的，避免轮询回灌重弹。
  const pendingPerms = convPerms.filter((p) => p.status === "pending" && !answeredPermIdsRef.current.has(p.id));
  // 排序后再拼 key，保证服务端返回顺序变化不会让弹框误判为「有新请求」而反复弹。
  const pendingKey = pendingPerms.map((p) => p.id).sort().join(",");
  const activeTaskServerStatus = activeTaskId
    ? convDetail?.messages?.find((m) => m.task?.id === activeTaskId)?.task?.status
    : undefined;
  const activeTaskStatus = activeTaskId
    ? taskDisplayStatus(activeTaskServerStatus, activeLiveTask)
    : "idle";
  const lastMessage = convDetail?.messages?.[convDetail.messages.length - 1];
  const currentClientName =
    convDetail?.assigned_client_id && clients.length
      ? clients.find((client) => client.id === convDetail.assigned_client_id)?.name
      : null;

  useEffect(() => {
    if (activeTaskId && activeTaskId !== pollTaskId) {
      setPollTaskId(activeTaskId);
    }
  }, [activeTaskId, pollTaskId]);

  useEffect(() => {
    setSessionMetaOpen(false);
    setPermPanelOpen(false);
    answeredPermIdsRef.current = new Set();
  }, [currentConvId]);

  // 待批集合发生变化（新请求到达，或处理掉一个还剩其它）时，强制重新弹出审批弹框，
  // 即使用户之前点了「稍后处理」——确保不会漏看需要人工确认的危险操作。
  useEffect(() => {
    setPermModalDismissed(false);
  }, [pendingKey]);

  const permModalOpen = pendingPerms.length > 0 && !permModalDismissed;

  // Warm-session 在线信号：通知后端此会话正被 Web 打开，对应 PC 据此预热常驻 Agent 进程；
  // 周期心跳维持打开状态，切换/离开会话时通知关闭（后端另有 120s 心跳过期兜底）。
  useEffect(() => {
    if (!currentConvId || !token) return;
    const convId = currentConvId;
    const ping = () => {
      if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
      api("POST", `/conversations/${convId}/open/`).catch(() => {});
    };
    const close = () => {
      api("POST", `/conversations/${convId}/close/`).catch(() => {});
    };
    ping();
    const timer = window.setInterval(ping, 45000);
    const onVisibility = () => {
      if (document.visibilityState === "visible") ping();
    };
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("beforeunload", close);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("beforeunload", close);
      close();
    };
  }, [currentConvId, token, api]);

  useEffect(() => {
    if (!currentConvId) return;
    if (scrollRafRef.current != null) return;  // 本帧已安排滚动，合并后续 token 变化
    scrollRafRef.current = requestAnimationFrame(() => {
      scrollRafRef.current = null;
      const container = messageListRef.current;
      if (!container) return;  // 已卸载/未挂载时安全退出
      const distanceToBottom = container.scrollHeight - container.scrollTop - container.clientHeight;
      if (distanceToBottom < 160) {
        container.scrollTo({ top: container.scrollHeight });  // 瞬时滚动，避免平滑动画在流式时堆叠
      }
    });
  }, [currentConvId, convDetail?.messages.length, liveTasks]);

  useEffect(() => {
    const container = messageListRef.current;
    if (!container) return;
    const timer = window.setTimeout(() => {
      container.scrollTo({ top: container.scrollHeight, behavior: "smooth" });
    }, 40);
    return () => window.clearTimeout(timer);
  }, [forceScrollTick, convDetail?.messages.length]);

  if (!authChecked) {
    return (
      <div className="min-h-screen flex items-center justify-center px-6">
        <div className="rounded-card border border-line bg-surface px-8 py-6 text-sm uppercase tracking-[0.2em] text-muted shadow-card">
          Loading TeleAgent
        </div>
      </div>
    );
  }

  if (!token) {
    return (
      <div className="min-h-screen flex flex-col">
        <header className="header">
          <h1><Link href="/">TeleAgent</Link></h1>
          <nav className="flex items-center gap-3">
            <Link href="/">首页</Link>
            <Link href="/broker" className="active">TeleAgent</Link>
            <Link href="/credentials">凭证管理</Link>
          </nav>
        </header>
        <main className="flex-1 flex items-center justify-center p-4 md:p-8">
          <div className="w-full max-w-5xl rounded-card border border-line-soft bg-panel p-4 shadow-soft md:p-6">
            <div className="grid gap-4 md:grid-cols-[1.15fr_0.85fr]">
              <section className="rounded-field bg-ink px-6 py-7 text-[#f8fafc] md:px-8 md:py-9">
                <span className="inline-flex rounded-pill border border-white/10 px-3 py-1 text-[11px] uppercase tracking-[0.24em] text-white/[0.64]">
                  Remote Agent Workspace
                </span>
                <h2 className="mt-5 max-w-xl text-3xl font-semibold leading-[1.05] tracking-[-0.02em] md:text-5xl">
                  TeleAgent lets you dispatch local coding agents from a calm web console.
                </h2>
                <p className="mt-4 max-w-lg text-sm leading-7 text-white/[0.72] md:text-base">
                  选择一台在线 PC、锁定一个 Agent CLI、从浏览器发起任务，并实时查看执行过程。
                </p>
                <div className="mt-8 grid gap-3 md:grid-cols-2">
                  <div className="rounded-card border border-white/10 bg-white/5 p-4">
                    <div className="text-[11px] uppercase tracking-[0.24em] text-white/[0.48]">Dispatch</div>
                    <div className="mt-2 text-base text-white">会话级固定 CLI，避免上下文串线。</div>
                  </div>
                  <div className="rounded-card border border-white/10 bg-white/5 p-4">
                    <div className="text-[11px] uppercase tracking-[0.24em] text-white/[0.48]">Observe</div>
                    <div className="mt-2 text-base text-white">任务事件流实时展示 assistant、tool、result。</div>
                  </div>
                </div>
              </section>
              <section className="px-5 py-6 md:px-7">
                <div className="mb-5">
                  <Eyebrow>Access</Eyebrow>
                  <h3 className="mt-2 text-2xl font-semibold text-ink">登录或注册</h3>
                  <p className="mt-2 text-sm leading-6 text-muted">使用邮箱和密码登录；未注册会自动创建账号。</p>
                </div>
                <form onSubmit={doLogin} className="space-y-4">
                  <Input
                    label="邮箱"
                    type="email"
                    value={authEmail}
                    onChange={(e) => setAuthEmail(e.target.value)}
                    placeholder="you@example.com"
                    required
                  />
                  <Input
                    label="密码"
                    type="password"
                    value={authPassword}
                    onChange={(e) => setAuthPassword(e.target.value)}
                    placeholder="密码"
                    required
                  />
                  {authError && (
                    <div className="rounded-field bg-failed-bg px-4 py-3 text-sm text-failed-fg">{authError}</div>
                  )}
                  <div className="flex gap-2">
                    <Button type="submit" variant="accent" block disabled={authLoading}>
                      {authLoading ? "…" : "登录"}
                    </Button>
                    <Button type="button" variant="secondary" block onClick={() => doRegister()} disabled={authLoading}>
                      注册
                    </Button>
                  </div>
                </form>
              </section>
            </div>
          </div>
        </main>
      </div>
    );
  }

  return (
    <div className="flex h-[100dvh] flex-col overflow-hidden">
      <header className="header">
        <div className="flex items-center gap-3">
          <button
            type="button"
            className="inline-flex h-9 w-9 items-center justify-center rounded-pill border border-line bg-panel text-ink-soft transition hover:bg-line-faint md:hidden"
            onClick={() => setSidebarOpen(true)}
            aria-label="打开会话列表"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
            </svg>
          </button>
          <h1><Link href="/">TeleAgent</Link></h1>
        </div>
        <nav className="flex items-center gap-2 md:gap-3">
          <div className="hidden items-center gap-3 md:flex">
            <Link href="/">首页</Link>
            <Link href="/broker" className="active">TeleAgent</Link>
            <Link href="/credentials">凭证管理</Link>
          </div>
          <button
            type="button"
            onClick={logout}
            className="hidden rounded-pill border border-line bg-panel px-3 py-1.5 text-[11px] uppercase tracking-[0.18em] text-muted transition hover:bg-line-faint md:inline-flex"
          >
            退出登录
          </button>
          <Link href="/credentials" className="rounded-pill border border-line bg-panel px-3 py-1.5 text-[11px] uppercase tracking-[0.16em] text-muted transition hover:bg-line-faint md:hidden">
            凭证
          </Link>
        </nav>
      </header>

      <div className="flex min-h-0 flex-1 overflow-hidden px-2 pb-2 md:px-6 md:pb-6">
        <div className="flex min-h-0 w-full flex-col gap-3 md:flex-row">
        {/* 侧边栏：移动端为抽屉 */}
        <aside
          className={`
            fixed md:relative inset-y-0 left-0 z-40 w-[84vw] max-w-[21rem] md:w-[21rem]
            flex flex-col pt-16 md:pt-0 transform transition-transform duration-200 ease-out
            rounded-card border border-line-soft bg-panel shadow-soft overflow-hidden
            ${sidebarOpen ? "translate-x-0" : "-translate-x-full md:translate-x-0"}
          `}
        >
          <div className="border-b border-line px-4 py-4 md:px-5">
            <div className="flex items-center justify-between">
              <Eyebrow>Console</Eyebrow>
              <div className="flex items-center gap-2 md:hidden">
                <button
                  type="button"
                  className="rounded-pill border border-line bg-panel px-3 py-1.5 text-[11px] uppercase tracking-[0.16em] text-muted"
                  onClick={logout}
                >
                  退出
                </button>
                <button
                  type="button"
                  className="rounded-pill p-2 text-ink-soft transition hover:bg-line-faint"
                  onClick={() => setSidebarOpen(false)}
                  aria-label="关闭"
                >
                  <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>
            </div>
            <h2 className="mt-2 text-xl font-semibold text-ink">会话</h2>
            <p className="mt-2 text-sm leading-6 text-muted">
              当前在线客户端 {clients.length} 台，会话 {conversations.length} 个。
            </p>
          </div>
          <div className="border-b border-line-faint px-4 py-4 md:px-5">
            <Button variant="primary" block onClick={openNewModal}>＋ 新建会话</Button>
          </div>
          <ul className="flex-1 overflow-y-auto px-3 py-3 space-y-2">
            {conversations.length === 0 && (
              <li className="rounded-card bg-surface p-4 text-sm text-muted">暂无会话，点击“新建会话”开始。</li>
            )}
            {conversations.map((c) => (
              <li key={c.id}>
                {(() => {
                  const isCurrent = currentConvId === c.id;
                  const currentStatus = isCurrent
                    ? activeTaskStatus === "idle"
                      ? lastMessage?.task?.status
                      : activeTaskStatus
                    : undefined;
                  const isActive = isCurrent && (currentStatus === "queued" || currentStatus === "running");
                  return (
                <div
                  className={`conversation-card rounded-field border p-3 ${
                    isCurrent
                      ? "border-accent bg-accent-soft shadow-card"
                      : "border-transparent bg-panel hover:border-line"
                  }`}
                >
                  <div className="flex items-start gap-3">
                    <button type="button" className="min-w-0 flex-1 text-left" onClick={() => selectConv(c.id)}>
                      <div className="flex items-start gap-3">
                        <Avatar label={shortPathLabel(c.cwd)} status={isCurrent ? currentStatus : undefined} />
                        <div className="min-w-0 flex-1">
                          <div className="flex items-start justify-between gap-2">
                            <div className={`truncate text-[15px] font-medium ${isCurrent ? "text-accent-hover" : "text-ink"}`}>
                              {c.title || c.cwd || c.id}
                            </div>
                            <span className="shrink-0 text-[11px] text-faint">{formatRelativeTime(c.updated_at)}</span>
                          </div>
                          <div className="mt-1 truncate font-mono text-xs text-muted">{c.cwd}</div>
                          {c.last_result && (
                            <div className="mt-1 line-clamp-2 text-xs leading-5 text-faint">{c.last_result}</div>
                          )}
                        </div>
                      </div>
                      <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
                        {isActive && <StatusBadge status={currentStatus} />}
                        <Chip tone="plain">{agentLabel(c.agent_type)}</Chip>
                        <Chip>{c.message_count || 0} 轮</Chip>
                      </div>
                    </button>
                    <button
                      type="button"
                      className="rounded-pill px-2 py-1 text-[11px] uppercase tracking-[0.16em] text-muted transition hover:bg-accent-soft hover:text-accent-hover"
                      onClick={() => handleDeleteConversation(c.id)}
                      aria-label="删除会话"
                    >
                      删除
                    </button>
                  </div>
                </div>
                  );
                })()}
              </li>
            ))}
          </ul>
        </aside>
        {sidebarOpen && (
          <div
            className="fixed inset-0 z-30 md:hidden"
            style={{ background: "var(--scrim-soft)" }}
            onClick={() => setSidebarOpen(false)}
            aria-hidden
          />
        )}

        {/* 主内容区 */}
        <main className="flex-1 flex min-h-0 min-w-0 flex-col rounded-field border border-line-soft bg-panel shadow-soft md:rounded-card">
          {!currentConvId ? (
            <div className="flex flex-1 items-center justify-center p-6">
              <div className="max-w-md text-center">
                <Eyebrow tone="faint">Ready</Eyebrow>
                <h3 className="mt-3 text-3xl font-semibold leading-tight text-ink">选择一个会话，或者创建一个新的执行工作台。</h3>
                <p className="mt-4 text-sm leading-7 text-muted">
                  你会在这里看到固定 CLI 的实时任务流、工具调用摘要和最终结果。
                </p>
              </div>
            </div>
          ) : (
            <div className="flex min-h-0 flex-1 flex-col">
              <div className="shrink-0 border-b border-line bg-panel px-4 py-3 md:px-6 md:py-4">
                <div className="flex flex-wrap items-start justify-between gap-4">
                  <div className="min-w-0">
                    <Eyebrow tone="faint">当前会话</Eyebrow>
                    {editingTitle ? (
                      <div className="mt-2 flex items-center gap-2">
                        <Input
                          autoFocus
                          value={titleDraft}
                          onChange={(e) => setTitleDraft(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") handleRename();
                            if (e.key === "Escape") setEditingTitle(false);
                          }}
                          maxLength={256}
                          placeholder="会话标题"
                          className="max-w-md text-lg font-semibold"
                          style={{ padding: "0.375rem 0.75rem" }}
                        />
                        <Button variant="accent" size="sm" onClick={handleRename}>保存</Button>
                        <Button variant="secondary" size="sm" onClick={() => setEditingTitle(false)}>取消</Button>
                      </div>
                    ) : (
                      <div className="mt-2 flex items-center gap-2">
                        <h3 className="truncate text-[1.85rem] font-semibold leading-none text-ink md:text-2xl">{convDetail?.title || "(无标题)"}</h3>
                        <button type="button" onClick={startRename} aria-label="重命名会话"
                          className="shrink-0 rounded-pill border border-line bg-panel px-2 py-1 text-[11px] text-muted transition hover:bg-accent-soft">✎ 重命名</button>
                      </div>
                    )}
                    {!sessionMetaOpen && (
                      <p className="mt-2 truncate font-mono text-xs text-muted md:block">{convDetail?.cwd}</p>
                    )}
                  </div>
                  <div className="flex flex-wrap items-center justify-end gap-2">
                    {currentConvId && (
                      <Button variant="danger" size="sm" onClick={() => handleDeleteConversation(currentConvId)}>
                        删除会话
                      </Button>
                    )}
                    <StatusBadge status={activeTaskId ? activeTaskStatus : "idle"} />
                    <Badge tone="outline">{activeTaskId ? "实时" : "就绪"}</Badge>
                    <Badge tone="accent">{convDetail?.messages.length || 0} 轮</Badge>
                    {convDetail?.session_id ? (
                      <span title="本会话已建立上下文锚点（session_id）。常驻进程断开后，下一轮会自动 --resume 恢复历史。">
                        <Badge tone="success" dot>上下文已锚定</Badge>
                      </span>
                    ) : (
                      <span title="本会话尚无上下文锚点（还没拿到 session_id）。当前从零开始，暂无可恢复的历史。">
                        <Badge tone="outline" dot>新会话</Badge>
                      </span>
                    )}
                  </div>
                </div>
                {convDetail?.agent_type === "claude_code" && (
                  <div className="mt-3 flex flex-wrap items-center gap-2 rounded-field border border-line bg-surface px-3 py-2">
                    <span className="text-[11px] uppercase tracking-[0.16em] text-faint">实时控制</span>
                    <label className="flex items-center gap-1.5">
                      <span className="text-xs text-muted">模式</span>
                      <Select
                        size="sm"
                        value={convDetail?.options?.permission_mode ?? "default"}
                        onChange={(e) => sendControl("set_permission_mode", e.target.value)}
                      >
                        <option value="default">默认</option>
                        <option value="plan">计划</option>
                        <option value="acceptEdits">接受编辑</option>
                        <option value="bypassPermissions">全放开</option>
                      </Select>
                    </label>
                    <label className="flex items-center gap-1.5">
                      <span className="text-xs text-muted">模型</span>
                      <Select
                        size="sm"
                        value={convDetail?.options?.model ?? ""}
                        onChange={(e) => sendControl("set_model", e.target.value)}
                      >
                        <option value="">默认</option>
                        <option value="opus">Opus</option>
                        <option value="sonnet">Sonnet</option>
                        <option value="haiku">Haiku</option>
                      </Select>
                    </label>
                    {(activeTaskStatus === "running" || activeTaskStatus === "queued") && (
                      <Button variant="danger" size="sm" onClick={() => sendControl("interrupt", "")}>
                        ⏹ 中断
                      </Button>
                    )}
                    <span className="text-[11px] text-faint">即时生效（需会话常驻）</span>
                  </div>
                )}
                <div className="mt-4 flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    onClick={() => setSessionMetaOpen((prev) => !prev)}
                    className="inline-flex items-center gap-2 rounded-pill border border-line bg-panel px-3 py-2 text-[11px] uppercase tracking-[0.16em] text-muted transition hover:bg-line-faint"
                  >
                    <span className="hidden md:inline">{sessionMetaOpen ? "收起会话信息" : "展开会话信息"}</span>
                    <span className="md:hidden">{sessionMetaOpen ? "收起详情" : "展开详情"}</span>
                    <span className={`transition-transform ${sessionMetaOpen ? "rotate-180" : ""}`}>⌄</span>
                  </button>
                  {convDetail?.assigned_client_id && (
                    <button
                      type="button"
                      onClick={() => setFileBrowserOpen(true)}
                      className="inline-flex items-center gap-2 rounded-pill border border-line bg-panel px-3 py-2 text-[11px] uppercase tracking-[0.16em] text-muted transition hover:bg-line-faint"
                    >
                      📁 <span className="hidden md:inline">浏览/下载文件</span><span className="md:hidden">文件</span>
                    </button>
                  )}
                  {convPerms.length > 0 && (
                    <button
                      type="button"
                      onClick={() => setPermPanelOpen((prev) => !prev)}
                      className={`inline-flex items-center gap-2 rounded-pill border px-3 py-2 text-[11px] uppercase tracking-[0.16em] transition ${
                        pendingPerms.length > 0
                          ? "border-accent bg-accent-soft text-accent hover:border-accent-border"
                          : "border-line bg-panel text-muted hover:bg-line-faint"
                      }`}
                    >
                      🛡 <span className="hidden md:inline">权限审批</span><span className="md:hidden">审批</span>
                      {pendingPerms.length > 0 ? (
                        <span className="rounded-pill bg-accent px-1.5 py-0.5 text-[10px] font-semibold text-white">
                          {pendingPerms.length} 待批
                        </span>
                      ) : (
                        <span className="text-faint">{convPerms.length}</span>
                      )}
                      <span className={`transition-transform ${permPanelOpen ? "rotate-180" : ""}`}>⌄</span>
                    </button>
                  )}
                  {permPanelOpen && convPerms.length > 0 && (
                    <div className="mt-3 flex w-full min-w-0 flex-col gap-1.5 rounded-card border border-line bg-surface p-3">
                      <div className="px-0.5 text-[11px] font-medium uppercase tracking-[0.18em] text-faint">
                        权限审批记录（{convPerms.length}）
                      </div>
                      <div className="flex max-h-[40vh] flex-col gap-1.5 overflow-y-auto">
                        {[...convPerms]
                          .sort((a, b) => ((b.created_at || "") < (a.created_at || "") ? -1 : 1))
                          .map((p) => (
                            <PermissionRow
                              key={p.id}
                              card={p}
                              onAnswer={(decision, remember) => answerPermission(p.id, decision, remember)}
                            />
                          ))}
                      </div>
                    </div>
                  )}
                  {sessionMetaOpen && (
                    <div className="mt-3 flex w-full flex-wrap gap-2">
                      <Chip>{currentClientName || "未绑定设备"}</Chip>
                      <Chip tone="accent">
                        {lastMessage?.task?.agent_type ? agentLabel(lastMessage.task.agent_type) : "等待任务"}
                      </Chip>
                      <Chip tone="plain">
                        {activeLiveTask?.lines?.length
                          ? `实时事件 ${activeLiveTask.lines.length}`
                          : activeTaskId
                            ? "任务执行中"
                            : "当前空闲"}
                      </Chip>
                      {lastMessage?.prompt && (
                        <Chip tone="plain" style={{ maxWidth: "100%", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                          {lastMessage.prompt}
                        </Chip>
                      )}
                    </div>
                  )}
                </div>
              </div>
              <div ref={messageListRef} className="min-h-0 flex-1 overflow-y-auto px-3 py-3 md:px-6 md:py-5">
                <div className="flex w-full flex-col gap-4">
                {convFiles.length > 0 && (
                  <div className="rounded-card border border-line bg-surface p-3">
                    <div className="mb-2 text-[11px] font-medium uppercase tracking-[0.18em] text-faint">收到的文件</div>
                    <div className="flex flex-col gap-2">
                      {convFiles.map((f) => (
                        <div key={f.id} className="flex items-center gap-3 rounded-field border border-line bg-white px-3 py-2">
                          <span className="text-base">📄</span>
                          <span className="min-w-0 flex-1 truncate font-mono text-xs text-ink" title={f.filename}>{f.filename}</span>
                          <span className="shrink-0 text-[10px] text-faint">{formatBytes(f.size)}</span>
                          <Button size="sm" variant="accent" onClick={() => downloadTransfer(f.id, f.filename)}>下载</Button>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
                {timeline.map((item) => {
                  const m = item.msg;
                  return (
                  <div key={m.id} className="flex flex-col gap-3">
                    <ChatBubble
                      role="user"
                      header={<><span className="flex items-center gap-2"><span>你</span><span>{m.task?.agent_type ? agentLabel(m.task.agent_type) : "待发送"}</span></span>{m.created_at && <span className="text-[11px] font-normal normal-case tracking-normal">{formatTime(m.created_at)}</span>}</>}
                    >
                      {m.prompt}
                    </ChatBubble>

                    <ChatBubble
                      role="agent"
                      header={
                        <>
                          {m.task ? <StatusBadge status={taskDisplayStatus(m.task.status, liveTasks[m.task.id])} /> : <Badge tone="accent">提交中</Badge>}
                          {m.task?.started_at && (
                            <Chip tone="plain">
                              {new Date(m.task.started_at).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })}
                            </Chip>
                          )}
                        </>
                      }
                    >
                      {m.task ? (
                        (() => {
                          const lines = liveTasks[m.task.id]?.lines || [];
                          const primaryLines = lines.filter((line) => line.tone === "assistant" || line.tone === "result");
                          const detailLines = lines.filter((line) => line.tone === "tool" || line.tone === "system");
                          const finalText = m.task.result_text?.trim();
                          const primaryText = primaryLines.map((line) => line.text.trim()).filter(Boolean);
                          const shouldShowLive = primaryLines.length > 0;
                          const showFinal = Boolean(finalText) && !primaryText.includes(finalText || "");
                          const displayStatus = taskDisplayStatus(m.task.status, liveTasks[m.task.id]);
                          return (
                            <div className="flex flex-col gap-3">
                              {shouldShowLive ? (
                                primaryLines.map((line) => (
                                  <EventLine key={line.id} tone={line.tone as "assistant" | "result"}>
                                    <Markdown text={line.text} />
                                  </EventLine>
                                ))
                              ) : displayStatus === "queued" || displayStatus === "running" ? (
                                <EventLine tone="assistant" label="状态">
                                  {displayStatus === "queued" ? "已发送，正在等待设备接收…" : "正在处理，马上就有回应…"}
                                </EventLine>
                              ) : !finalText ? (
                                <EventLine tone="system" label="状态">这轮没有返回内容。</EventLine>
                              ) : null}

                              {/* 工具审批不再混入聊天流：待批走全局弹框，历史在顶部「权限审批」面板。 */}

                              {showFinal && (
                                <EventLine tone="result" label="回复">
                                  <Markdown text={m.task.result_text || ""} />
                                </EventLine>
                              )}

                              {(detailLines.length > 0 || m.task.result_text) && (
                                <details className="rounded-field border border-line bg-surface px-4 py-3">
                                  <summary className="cursor-pointer select-none text-[11px] uppercase tracking-[0.18em] text-faint">
                                    {detailLines.length > 0 ? `执行细节 ${detailLines.length} 条` : "查看执行细节"}
                                  </summary>
                                  {detailLines.length > 0 ? (
                                    <div className="mt-3 flex flex-col gap-2">
                                      {detailLines.map((line) => (
                                        <EventLine key={line.id} tone={line.tone as "tool" | "system"}>
                                          {line.text}
                                        </EventLine>
                                      ))}
                                    </div>
                                  ) : (
                                    <div className="mt-3 text-sm text-muted">当前没有额外执行细节。</div>
                                  )}
                                </details>
                              )}
                            </div>
                          );
                        })()
                      ) : (
                        <EventLine tone="assistant" label="状态">任务还在提交中…</EventLine>
                      )}
                    </ChatBubble>
                  </div>
                  );
                })}
                <div ref={messageEndRef} />
                </div>
              </div>
              {error && (
                <div className="mx-4 mb-3 max-h-40 overflow-auto rounded-card bg-failed-bg px-4 py-3 text-sm text-failed-fg break-words md:mx-6">{error}</div>
              )}
              <form onSubmit={handleSend} className="shrink-0 border-t border-line bg-panel p-3 pb-[calc(env(safe-area-inset-bottom)+0.75rem)] md:p-5">
                <Textarea
                  value={prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                  onKeyDown={handlePromptKeyDown}
                  placeholder="输入你的下一条任务，直接发送到当前设备和 CLI..."
                  rows={3}
                  required
                />
                <div className="mt-3 flex flex-col gap-3 md:flex-row md:flex-wrap md:items-center">
                  <Chip>
                    {currentClientName || "未绑定设备"} · {lastMessage?.task?.agent_type ? agentLabel(lastMessage.task.agent_type) : "沿用当前会话 CLI"}
                  </Chip>
                  <Chip tone="plain" className="hidden md:inline-flex">Enter 发送 · Shift+Enter 换行</Chip>
                  <Button type="submit" variant="accent" disabled={loading} className="w-full md:ml-auto md:w-auto">
                    {loading ? "创建任务…" : activeTaskId ? "继续发送" : "发送"}
                  </Button>
                </div>
              </form>
            </div>
          )}
        </main>
        </div>
      </div>

      {/* 待批准请求：主动弹出，避免用户漏看需要人工确认的危险操作。处理完或点「稍后」即关闭。 */}
      {permModalOpen && (
        <div
          className="fixed inset-0 z-[60] flex items-center justify-center p-4"
          style={{ background: "var(--scrim)" }}
          onClick={() => setPermModalDismissed(true)}
        >
          <div
            className="flex max-h-[90vh] w-full max-w-lg flex-col overflow-hidden rounded-card border border-line-soft bg-panel shadow-pop"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="border-b border-line px-5 py-4">
              <Eyebrow>Approval Needed</Eyebrow>
              <h3 className="mt-2 text-xl font-semibold text-ink">
                需要你的批准{pendingPerms.length > 1 ? `（${pendingPerms.length}）` : ""}
              </h3>
              <p className="mt-1 text-xs text-muted">Agent 想执行下列操作，请确认。处理后会自动显示下一条。</p>
            </div>
            <div className="flex flex-col gap-3 overflow-y-auto p-5">
              {pendingPerms.map((p) => (
                <PermissionCard
                  key={p.id}
                  card={p}
                  onAnswer={(decision, remember) => answerPermission(p.id, decision, remember)}
                />
              ))}
            </div>
            <div className="flex justify-end border-t border-line px-5 py-3">
              <Button variant="secondary" onClick={() => setPermModalDismissed(true)}>稍后处理</Button>
            </div>
          </div>
        </div>
      )}

      {/* 新建会话弹窗 */}
      {modalOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center p-4"
          style={{ background: "var(--scrim)" }}
          onClick={() => setModalOpen(false)}
        >
          <div
            className="max-h-[90vh] w-full max-w-md overflow-y-auto rounded-card border border-line-soft bg-panel shadow-pop"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="border-b border-line px-5 py-4">
              <Eyebrow>New Session</Eyebrow>
              <h3 className="mt-2 text-xl font-semibold text-ink">新建会话</h3>
            </div>
            <form onSubmit={handleNewConv} className="space-y-4 p-5">
              <div className="grid gap-3 rounded-field border border-line bg-surface p-4">
                <div className="flex items-center gap-3">
                  <div className="flex h-8 w-8 items-center justify-center rounded-pill bg-ink text-xs font-semibold text-white">1</div>
                  <div>
                    <div className="text-sm font-medium text-ink-soft">先选择设备</div>
                    <div className="text-xs text-muted">决定任务要派发到哪台在线 PC。</div>
                  </div>
                </div>
                <Select
                  value={newClientId}
                  onChange={(e) => {
                    const value = e.target.value;
                    setNewClientId(value);
                    const nextClient = clients.find((c) => c.id === value);
                    const nextAgent = nextClient?.supported_agents?.[0];
                    if (nextAgent) setNewAgentType(nextAgent);
                  }}
                >
                  <option value="">任意（未分配时由先拉取的客户端执行）</option>
                  {clients.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                      {c.hostname ? ` · ${c.hostname}` : ""}
                    </option>
                  ))}
                </Select>
              </div>
              <div className="grid gap-3 rounded-field border border-line bg-surface p-4">
                <div className="flex items-center gap-3">
                  <div className="flex h-8 w-8 items-center justify-center rounded-pill bg-ink text-xs font-semibold text-white">2</div>
                  <div>
                    <div className="text-sm font-medium text-ink-soft">再选择目录</div>
                    <div className="text-xs text-muted">浏览该设备上的文件夹并选定，或直接手输绝对路径。</div>
                  </div>
                </div>
                {cwdPickerOpen ? (
                  <>
                    <FolderPicker
                      clientId={newClientId}
                      value={newCwd}
                      onChange={setNewCwd}
                      onPick={() => setCwdPickerOpen(false)}
                      api={api}
                    />
                    <Input
                      mono
                      value={newCwd}
                      onChange={(e) => setNewCwd(e.target.value)}
                      placeholder="/path/to/project"
                      required
                    />
                  </>
                ) : (
                  <div className="flex items-center gap-2 rounded-field border border-line bg-white px-3 py-2">
                    <span className="shrink-0 text-success-fg">✓</span>
                    <span className="min-w-0 flex-1 truncate font-mono text-xs text-ink" title={newCwd}>
                      {newCwd || "未选择目录"}
                    </span>
                    <Button size="sm" variant="secondary" onClick={() => setCwdPickerOpen(true)}>
                      重新选择
                    </Button>
                  </div>
                )}
              </div>
              <div className="grid gap-3 rounded-field border border-line bg-surface p-4">
                <div className="flex items-center gap-3">
                  <div className="flex h-8 w-8 items-center justify-center rounded-pill bg-ink text-xs font-semibold text-white">3</div>
                  <div>
                    <div className="text-sm font-medium text-ink-soft">然后选择 Agent</div>
                    <div className="text-xs text-muted">只显示这台设备实际上报的可用 CLI。</div>
                  </div>
                </div>
                <Select
                  value={newAgentType}
                  onChange={(e) => { setNewAgentType(e.target.value as AgentType); setNewOptions({}); }}
                  required
                >
                  {agentChoices.map((value) => (
                    <option key={value} value={value}>
                      {agentLabel(value)}
                    </option>
                  ))}
                </Select>
                <p className="text-xs leading-6 text-muted">
                  {selectedClient?.supported_agents?.length
                    ? "该列表来自本机 Broker 上报的本地可用 CLI。"
                    : "未绑定具体 PC 时，先展示系统支持的 CLI 类型。"}
                </p>
              </div>
              <Input
                label="标题（可选）"
                value={newTitle}
                onChange={(e) => setNewTitle(e.target.value)}
                placeholder="会话标题"
              />
              {(AGENT_OPTION_SCHEMA[newAgentType] || []).length > 0 && (
                <div className="rounded-field border border-line bg-surface">
                  <button
                    type="button"
                    onClick={() => setAdvancedOpen((v) => !v)}
                    className="flex w-full items-center justify-between gap-2 px-4 py-3 text-left"
                  >
                    <span className="text-[11px] font-medium uppercase tracking-[0.18em] text-faint">
                      高级参数（权限模式 / 模型 / 思考强度）
                    </span>
                    <span className="shrink-0 text-xs text-muted">
                      {advancedOpen ? "收起" : "默认即可 · 展开修改"}
                      <span className={`ml-1 inline-block transition-transform ${advancedOpen ? "rotate-180" : ""}`}>⌄</span>
                    </span>
                  </button>
                  {advancedOpen && (
                    <div className="space-y-3 px-4 pb-3">
                      {(AGENT_OPTION_SCHEMA[newAgentType] || []).map((opt) => (
                        <div key={opt.key}>
                          <Select
                            label={opt.label}
                            size="sm"
                            value={newOptions[opt.key] ?? opt.default}
                            onChange={(e) => setNewOptions((prev) => ({ ...prev, [opt.key]: e.target.value }))}
                          >
                            {opt.choices.map((c) => (
                              <option key={c.value} value={c.value}>{c.label}</option>
                            ))}
                          </Select>
                          {opt.hint && <span className="mt-1 block text-xs text-faint">{opt.hint}</span>}
                        </div>
                      ))}
                      <p className="text-xs text-faint">这些也可在创建后于对话页顶部「实时控制」里随时调整。</p>
                    </div>
                  )}
                </div>
              )}
              <div className="flex justify-end gap-2 pt-2">
                <Button variant="secondary" onClick={() => setModalOpen(false)}>取消</Button>
                <Button type="submit" variant="accent">创建</Button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* 文件浏览/下载弹框：浏览 Agent PC 上的文件，点「下载」经中转下载到本地。 */}
      {fileBrowserOpen && browseClientId && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center p-4"
          style={{ background: "var(--scrim)" }}
          onClick={() => setFileBrowserOpen(false)}
        >
          <div
            className="max-h-[85vh] w-full max-w-md overflow-y-auto rounded-card border border-line-soft bg-panel shadow-pop"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between border-b border-line px-5 py-4">
              <h3 className="text-lg font-semibold text-ink">浏览并下载文件</h3>
              <button type="button" onClick={() => setFileBrowserOpen(false)} className="text-sm text-muted hover:text-ink">关闭</button>
            </div>
            <div className="p-5">
              <FolderPicker
                clientId={browseClientId}
                value=""
                onChange={() => {}}
                api={api}
                conversationId={currentConvId || undefined}
                onFileSelect={(path, name) => requestAndDownload(browseClientId, path, name)}
              />
              <p className="mt-3 text-xs text-muted">仅限当前会话的工作目录内浏览下载；大文件需稍候设备读取上传。</p>
            </div>
          </div>
        </div>
      )}

      <footer className="footer hidden shrink-0 md:block">TeleAgent workspace · mobile and desktop ready</footer>
    </div>
  );
}
