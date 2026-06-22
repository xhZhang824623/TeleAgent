"use client";

import Link from "next/link";
import { useEffect, useRef, useState, useCallback } from "react";
import { Markdown } from "./Markdown";

import {
  API_BROKER, API_AUTH, AGENT_OPTIONS, AGENT_OPTION_SCHEMA, TOKEN_KEY, EMAIL_KEY,
  getStoredToken, agentLabel, extractAssistantText, formatToolLine, findActiveTaskId,
  statusLabel, statusTone, shortPathLabel, lineToneLabel, formatRelativeTime, conversationStatusDot,
} from "./lib";
import type { AgentType, Client, Conversation, Task, Message, LiveLine, LiveTask } from "./lib";

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
  const [pollTaskId, setPollTaskId] = useState<string | null>(null);
  const [liveTasks, setLiveTasks] = useState<Record<string, LiveTask>>({});
  const [sessionMetaOpen, setSessionMetaOpen] = useState(false);
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
      const clientLabel =
        conv.assigned_client_id && clients.length
          ? (clients.find((c) => c.id === conv.assigned_client_id)?.name ?? conv.assigned_client_id)
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
    [api, clients]
  );

  const selectConv = useCallback(
    async (id: string) => {
      setCurrentConvId(id);
      setSidebarOpen(false);
      try {
        await refreshConversation(id);
      } catch (e) {
        setConvDetail({
          cwd: "",
          messages: [],
        });
        setError(e instanceof Error ? e.message : "加载失败");
      }
    },
    [refreshConversation]
  );

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

    const appendLine = (taskId: string, tone: LiveLine["tone"], text: string) => {
      if (!text) return;
      setLiveTasks((prev) => {
        const current = prev[taskId] || { status: "running", lines: [] };
        if (current.lines.some((line) => line.tone === tone && line.text === text)) {
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
        const nextId = `${tone}-${current.lines.length}-${text.length}`;
        return {
          ...prev,
          [taskId]: {
            ...current,
            lines: [...current.lines, { id: nextId, tone, text }],
          },
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
      if (currentConvId === convId) {
        setCurrentConvId(null);
        setConvDetail(null);
        setPollTaskId(null);
      }
      setLiveTasks((prev) => {
        const next = { ...prev };
        const taskIds =
          convDetail?.messages
            ?.filter((message) => message.task?.id && currentConvId === convId)
            .map((message) => message.task!.id) || [];
        for (const taskId of taskIds) delete next[taskId];
        return next;
      });
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
  const activeTaskStatus = activeLiveTask?.status || (activeTaskId ? "running" : "idle");
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
  }, [currentConvId]);

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
    const container = messageListRef.current;
    const anchor = messageEndRef.current;
    if (!container || !anchor || !currentConvId) return;
    const distanceToBottom = container.scrollHeight - container.scrollTop - container.clientHeight;
    const shouldStick = distanceToBottom < 160;
    if (shouldStick) {
      container.scrollTo({ top: container.scrollHeight, behavior: "smooth" });
    }
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
        <div className="rounded-2xl border border-[#e5e7eb] bg-[#f9fafb] px-8 py-6 text-sm tracking-[0.2em] uppercase text-[#6b7280] shadow-sm">
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
          <div className="w-full max-w-5xl rounded-2xl border border-[#e5e7eb] bg-[#ffffff] p-4 shadow-sm md:p-6">
            <div className="grid gap-4 md:grid-cols-[1.15fr_0.85fr]">
              <section className="rounded-xl bg-[#111827] px-6 py-7 text-[#f8fafc] md:px-8 md:py-9">
                <div className="inline-flex rounded-full border border-white/10 px-3 py-1 text-[11px] uppercase tracking-[0.24em] text-white/64">
                  Remote Agent Workspace
                </div>
                <h2 className="mt-5 max-w-xl text-3xl font-semibold leading-tight md:text-5xl">
                  TeleAgent lets you dispatch local coding agents from a calm web console.
                </h2>
                <p className="mt-4 max-w-lg text-sm leading-7 text-white/72 md:text-base">
                  选择一台在线 PC、锁定一个 Agent CLI、从浏览器发起任务，并实时查看执行过程。
                </p>
                <div className="mt-8 grid gap-3 text-sm text-white/78 md:grid-cols-2">
                  <div className="rounded-2xl border border-white/10 bg-white/5 p-4">
                    <div className="text-[11px] uppercase tracking-[0.24em] text-white/48">Dispatch</div>
                    <div className="mt-2 text-base text-white">会话级固定 CLI，避免上下文串线。</div>
                  </div>
                  <div className="rounded-2xl border border-white/10 bg-white/5 p-4">
                    <div className="text-[11px] uppercase tracking-[0.24em] text-white/48">Observe</div>
                    <div className="mt-2 text-base text-white">任务事件流实时展示 assistant、tool、result。</div>
                  </div>
                </div>
              </section>
              <section className="rounded-xl bg-[#ffffff] px-5 py-6 md:px-7">
                <div className="mb-5">
                  <div className="text-[11px] uppercase tracking-[0.22em] text-[#6b7280]">Access</div>
                  <h3 className="mt-2 text-2xl font-semibold text-[#111827]">登录或注册</h3>
                  <p className="mt-2 text-sm leading-6 text-[#6b7280]">使用邮箱和密码登录；未注册会自动创建账号。</p>
                </div>
            <form onSubmit={doLogin} className="space-y-4">
              <label className="block">
                <span className="text-sm font-medium text-[#374151]">邮箱</span>
                <input
                  type="email"
                  value={authEmail}
                  onChange={(e) => setAuthEmail(e.target.value)}
                  placeholder="you@example.com"
                  className="mt-1 block w-full rounded-2xl border border-[#e5e7eb] bg-white px-4 py-3 text-sm outline-none transition focus:border-[#4f46e5] focus:ring-2 focus:ring-[#4f46e5]/15"
                  required
                />
              </label>
              <label className="block">
                <span className="text-sm font-medium text-[#374151]">密码</span>
                <input
                  type="password"
                  value={authPassword}
                  onChange={(e) => setAuthPassword(e.target.value)}
                  placeholder="密码"
                  className="mt-1 block w-full rounded-2xl border border-[#e5e7eb] bg-white px-4 py-3 text-sm outline-none transition focus:border-[#4f46e5] focus:ring-2 focus:ring-[#4f46e5]/15"
                  required
                />
              </label>
              {authError && (
                <div className="rounded-2xl bg-[#fef2f2] px-4 py-3 text-sm text-[#b91c1c]">{authError}</div>
              )}
              <div className="flex gap-2">
                <button
                  type="submit"
                  disabled={authLoading}
                  className="flex-1 rounded-2xl bg-[#4f46e5] py-3 text-sm font-medium text-white transition hover:bg-[#4338ca] disabled:opacity-50"
                >
                  {authLoading ? "…" : "登录"}
                </button>
                <button
                  type="button"
                  onClick={doRegister}
                  disabled={authLoading}
                  className="flex-1 rounded-2xl border border-[#e5e7eb] py-3 text-sm text-[#4b5563] transition hover:bg-[#f3f4f6] disabled:opacity-50"
                >
                  注册
                </button>
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
            className="rounded-full border border-white/10 p-2 transition hover:bg-white/10 md:hidden"
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
            className="hidden rounded-full border border-white/12 px-3 py-1.5 text-xs uppercase tracking-[0.18em] text-white/78 transition hover:border-white/24 hover:text-white md:inline-flex"
          >
            退出登录
          </button>
          <Link href="/credentials" className="rounded-full border border-white/10 px-3 py-1.5 text-xs uppercase tracking-[0.16em] text-white/72 transition hover:border-white/24 hover:text-white md:hidden">
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
            rounded-2xl border border-[#eceef1] bg-[#ffffff] shadow-soft             ${sidebarOpen ? "translate-x-0" : "-translate-x-full md:translate-x-0"}
          `}
        >
          <div className="border-b border-[#e5e7eb]/80 px-4 py-4 md:px-5">
            <div className="text-[11px] uppercase tracking-[0.24em] text-[#6b7280]">Console</div>
            <div className="mt-2 flex items-center justify-between">
              <h2 className="text-xl font-semibold text-[#111827]">会话</h2>
              <div className="flex items-center gap-2 md:hidden">
                <button
                  type="button"
                  className="rounded-full border border-[#e5e7eb] px-3 py-1.5 text-[11px] uppercase tracking-[0.16em] text-[#6b7280]"
                  onClick={logout}
                >
                  退出
                </button>
                <button
                  type="button"
                  className="rounded-full p-2 transition hover:bg-black/5"
                  onClick={() => setSidebarOpen(false)}
                  aria-label="关闭"
                >
                  <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>
            </div>
            <p className="mt-2 text-sm leading-6 text-[#6b7280]">
              当前在线客户端 {clients.length} 台，会话 {conversations.length} 个。
            </p>
          </div>
          <div className="border-b border-[#f3f4f6] px-4 py-4 md:px-5">
            <button
              type="button"
              className="w-full rounded-xl bg-[#111827] px-4 py-3 text-sm font-medium text-white transition hover:bg-[#4338ca]"
              onClick={openNewModal}
            >
              ＋ 新建会话
            </button>
          </div>
          <ul className="flex-1 overflow-y-auto px-3 py-3 space-y-2">
            {conversations.length === 0 && (
              <li className="rounded-2xl bg-[#f9fafb] p-4 text-sm text-[#6b7280]">暂无会话，点击“新建会话”开始。</li>
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
                  className={`conversation-card rounded-xl border px-3 py-3 text-sm transition ${
                    isCurrent
                      ? "border-[#a5b4fc] bg-[#eef2ff] text-[#4338ca] shadow-sm"
                      : "border-transparent bg-[#ffffff] text-[#374151] hover:border-[#e5e7eb] hover:bg-white"
                  }`}
                >
                  <div className="flex items-start gap-3">
                    <button
                      type="button"
                      className="flex-1 text-left"
                      onClick={() => selectConv(c.id)}
                    >
                      <div className="flex items-start gap-3">
                        <div className="relative mt-0.5">
                          <span className="inline-flex h-9 w-9 items-center justify-center rounded-2xl bg-[#f3f4f6] text-[11px] font-semibold uppercase tracking-[0.14em] text-[#6b7280]">
                            {shortPathLabel(c.cwd).slice(0, 2)}
                          </span>
                          <span className={`absolute -right-0.5 -top-0.5 h-2.5 w-2.5 rounded-full ring-2 ring-white ${conversationStatusDot(currentStatus)}`} />
                        </div>
                        <div className="min-w-0 flex-1">
                          <div className="flex items-start justify-between gap-2">
                            <div className="truncate font-medium text-[15px]">{c.title || c.cwd || c.id}</div>
                            <span className="shrink-0 text-[11px] text-[#9ca3af]">{formatRelativeTime(c.updated_at)}</span>
                          </div>
                          <div className="mt-1 truncate text-sm text-[#6b7280]">{c.cwd}</div>
                          {c.last_result && (
                            <div className="mt-1 line-clamp-2 text-xs leading-5 text-[#9ca3af]">{c.last_result}</div>
                          )}
                        </div>
                      </div>
                      <div className="mt-3 flex flex-wrap items-center gap-2 text-[11px] uppercase tracking-[0.16em]">
                        {isActive && (
                          <span className="rounded-full bg-[#eef2ff] px-2.5 py-1 text-[#4f46e5]">
                            {currentStatus === "running" ? "执行中" : "排队中"}
                          </span>
                        )}
                        <span className="rounded-full bg-white px-2.5 py-1 text-[#9ca3af]">
                          {agentLabel(c.agent_type)}
                        </span>
                        <span className="rounded-full bg-[#f3f4f6] px-2.5 py-1 text-[#6b7280]">
                          {c.message_count || 0} 轮
                        </span>
                        <span className="truncate text-[#9ca3af]">{shortPathLabel(c.cwd)}</span>
                      </div>
                    </button>
                    <button
                      type="button"
                      className="rounded-full px-2 py-1 text-[11px] uppercase tracking-[0.16em] text-[#6b7280] transition hover:bg-[#eef2ff] hover:text-[#4338ca]"
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
            className="fixed inset-0 z-30 bg-black/30 md:hidden"
            onClick={() => setSidebarOpen(false)}
            aria-hidden
          />
        )}

        {/* 主内容区 */}
        <main className="flex-1 flex min-h-0 flex-col rounded-xl border border-[#eceef1] bg-[#ffffff] shadow-soft md:rounded-2xl">
          {!currentConvId ? (
            <div className="flex flex-1 items-center justify-center p-6">
              <div className="max-w-md text-center">
                <div className="text-[11px] uppercase tracking-[0.26em] text-[#9ca3af]">Ready</div>
                <h3 className="mt-3 text-3xl font-semibold text-[#111827]">选择一个会话，或者创建一个新的执行工作台。</h3>
                <p className="mt-4 text-sm leading-7 text-[#6b7280]">
                  你会在这里看到固定 CLI 的实时任务流、工具调用摘要和最终结果。
                </p>
              </div>
            </div>
          ) : (
            <div className="flex min-h-0 flex-1 flex-col">
              <div className="shrink-0 border-b border-[#e5e7eb] bg-[#ffffff] px-4 py-3 md:px-6 md:py-4">
                <div className="flex flex-wrap items-start justify-between gap-4">
                  <div className="min-w-0">
                    <div className="text-[11px] uppercase tracking-[0.22em] text-[#9ca3af]">当前会话</div>
                    {editingTitle ? (
                      <div className="mt-2 flex items-center gap-2">
                        <input
                          autoFocus
                          value={titleDraft}
                          onChange={(e) => setTitleDraft(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") handleRename();
                            if (e.key === "Escape") setEditingTitle(false);
                          }}
                          maxLength={256}
                          placeholder="会话标题"
                          className="w-full max-w-md rounded-xl border border-[#e5e7eb] bg-white px-3 py-1.5 text-lg font-semibold text-[#111827] outline-none focus:border-[#4f46e5]"
                        />
                        <button type="button" onClick={handleRename}
                          className="shrink-0 rounded-full bg-[#4f46e5] px-3 py-1.5 text-[11px] uppercase tracking-[0.16em] text-white transition hover:bg-[#4338ca]">保存</button>
                        <button type="button" onClick={() => setEditingTitle(false)}
                          className="shrink-0 rounded-full border border-[#e5e7eb] px-3 py-1.5 text-[11px] uppercase tracking-[0.16em] text-[#4b5563] transition hover:bg-[#f3f4f6]">取消</button>
                      </div>
                    ) : (
                      <div className="mt-2 flex items-center gap-2">
                        <h3 className="truncate text-[1.85rem] font-semibold leading-none text-[#111827] md:text-2xl">{convDetail?.title || "(无标题)"}</h3>
                        <button type="button" onClick={startRename} aria-label="重命名会话"
                          className="shrink-0 rounded-full border border-[#e5e7eb] bg-white px-2 py-1 text-[11px] text-[#6b7280] transition hover:bg-[#eef2ff]">✎ 重命名</button>
                      </div>
                    )}
                    {!sessionMetaOpen && (
                      <p className="mt-2 truncate text-xs font-mono text-[#6b7280] md:block">{convDetail?.cwd}</p>
                    )}
                  </div>
                  <div className="flex flex-wrap items-center justify-end gap-2">
                    {currentConvId && (
                      <button
                        type="button"
                        onClick={() => handleDeleteConversation(currentConvId)}
                        className="rounded-full border border-[#e5e7eb] bg-white px-3 py-1 text-[11px] uppercase tracking-[0.18em] text-[#b91c1c] transition hover:bg-[#eef2ff]"
                      >
                        删除会话
                      </button>
                    )}
                    <div className={`inline-flex items-center gap-2 rounded-full px-3 py-1 text-[11px] uppercase tracking-[0.18em] ${statusTone(activeTaskStatus)}`}>
                      {(activeTaskStatus === "running" || activeTaskStatus === "queued") && (
                        <span className="status-pulse h-2 w-2 rounded-full bg-current opacity-70" />
                      )}
                      {activeTaskId ? statusLabel(activeTaskStatus) : "空闲"}
                    </div>
                    <div className="rounded-full border border-[#e5e7eb] bg-white px-3 py-1 text-[11px] uppercase tracking-[0.18em] text-[#6b7280]">
                      {activeTaskId ? "实时" : "就绪"}
                    </div>
                    <div className="rounded-full bg-[#eef2ff] px-3 py-1 text-[11px] uppercase tracking-[0.18em] text-[#4f46e5]">
                      {convDetail?.messages.length || 0} 轮
                    </div>
                    {convDetail?.session_id ? (
                      <div
                        title="本会话已建立上下文锚点（session_id）。常驻进程断开后，下一轮会自动 --resume 恢复历史。"
                        className="inline-flex items-center gap-1.5 rounded-full bg-emerald-50 px-3 py-1 text-[11px] uppercase tracking-[0.18em] text-emerald-700"
                      >
                        <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" /> 上下文已锚定
                      </div>
                    ) : (
                      <div
                        title="本会话尚无上下文锚点（还没拿到 session_id）。当前从零开始，暂无可恢复的历史。"
                        className="inline-flex items-center gap-1.5 rounded-full border border-[#e5e7eb] bg-white px-3 py-1 text-[11px] uppercase tracking-[0.18em] text-[#9ca3af]"
                      >
                        <span className="h-1.5 w-1.5 rounded-full bg-[#d1d5db]" /> 新会话
                      </div>
                    )}
                  </div>
                </div>
                {convDetail?.agent_type === "claude_code" && (
                  <div className="mt-3 flex flex-wrap items-center gap-2 rounded-xl border border-[#e5e7eb] bg-[#f9fafb] px-3 py-2">
                    <span className="text-[11px] uppercase tracking-[0.16em] text-[#9ca3af]">实时控制</span>
                    <label className="flex items-center gap-1.5">
                      <span className="text-xs text-[#6b7280]">模式</span>
                      <select
                        value={convDetail?.options?.permission_mode ?? "default"}
                        onChange={(e) => sendControl("set_permission_mode", e.target.value)}
                        className="select-clean rounded-lg border border-[#e5e7eb] bg-white px-2 py-1 text-xs text-[#374151] outline-none focus:border-[#4f46e5]"
                      >
                        <option value="default">默认</option>
                        <option value="plan">计划</option>
                        <option value="acceptEdits">接受编辑</option>
                        <option value="bypassPermissions">全放开</option>
                      </select>
                    </label>
                    <label className="flex items-center gap-1.5">
                      <span className="text-xs text-[#6b7280]">模型</span>
                      <select
                        value={convDetail?.options?.model ?? ""}
                        onChange={(e) => sendControl("set_model", e.target.value)}
                        className="select-clean rounded-lg border border-[#e5e7eb] bg-white px-2 py-1 text-xs text-[#374151] outline-none focus:border-[#4f46e5]"
                      >
                        <option value="">默认</option>
                        <option value="opus">Opus</option>
                        <option value="sonnet">Sonnet</option>
                        <option value="haiku">Haiku</option>
                      </select>
                    </label>
                    {(activeTaskStatus === "running" || activeTaskStatus === "queued") && (
                      <button
                        type="button"
                        onClick={() => sendControl("interrupt", "")}
                        className="rounded-lg border border-[#fecaca] bg-[#fef2f2] px-2.5 py-1 text-xs text-[#b91c1c] transition hover:bg-[#fee2e2]"
                      >
                        ⏹ 中断
                      </button>
                    )}
                    <span className="text-[11px] text-[#9ca3af]">即时生效（需会话常驻）</span>
                  </div>
                )}
                <div className="mt-4">
                  <button
                    type="button"
                    onClick={() => setSessionMetaOpen((prev) => !prev)}
                    className="inline-flex items-center gap-2 rounded-full border border-[#e5e7eb] bg-white px-3 py-2 text-[11px] uppercase tracking-[0.16em] text-[#6b7280] transition hover:bg-white"
                  >
                    <span className="hidden md:inline">{sessionMetaOpen ? "收起会话信息" : "展开会话信息"}</span>
                    <span className="md:hidden">{sessionMetaOpen ? "收起详情" : "展开详情"}</span>
                    <span className={`transition-transform ${sessionMetaOpen ? "rotate-180" : ""}`}>⌄</span>
                  </button>
                  {sessionMetaOpen && (
                    <div className="mt-3 flex flex-wrap gap-2 text-[11px] uppercase tracking-[0.16em] text-[#6b7280]">
                      <span className="rounded-full bg-[#f3f4f6] px-3 py-2">{currentClientName || "未绑定设备"}</span>
                      <span className="rounded-full bg-[#eef2ff] px-3 py-2">
                        {lastMessage?.task?.agent_type ? agentLabel(lastMessage.task.agent_type) : "等待任务"}
                      </span>
                      <span className="rounded-full bg-white px-3 py-2">
                        {activeLiveTask?.lines?.length
                          ? `实时事件 ${activeLiveTask.lines.length}`
                          : activeTaskId
                            ? "任务执行中"
                            : "当前空闲"}
                      </span>
                      {lastMessage?.prompt && (
                        <span className="max-w-full truncate rounded-full bg-white px-3 py-2 normal-case tracking-normal text-[#6b7280]">
                          {lastMessage.prompt}
                        </span>
                      )}
                    </div>
                  )}
                </div>
              </div>
              <div ref={messageListRef} className="min-h-0 flex-1 overflow-y-auto px-3 py-3 md:px-6 md:py-5">
                <div className="flex w-full flex-col gap-4">
                {convDetail?.messages.map((m) => (
                  <div key={m.id} className="message-enter flex flex-col gap-3">
                      <div className="flex justify-end">
                        <div className="max-w-[92%] md:max-w-[78%] rounded-xl rounded-br-md bg-[#111827] px-4 py-3 text-white shadow-sm">
                          <div className="mb-2 flex items-center justify-between gap-3 text-[10px] uppercase tracking-[0.18em] text-white/58">
                            <span>你</span>
                            <span>{m.task?.agent_type ? agentLabel(m.task.agent_type) : "待发送"}</span>
                          </div>
                          <div className="whitespace-pre-wrap break-words text-sm leading-6 md:text-[15px]">{m.prompt}</div>
                        </div>
                      </div>

                      <div className="flex justify-start">
                        <div className="w-full max-w-[96%] md:max-w-[84%] rounded-xl rounded-bl-md border border-[#e5e7eb] bg-white px-4 py-3 shadow-sm">
                          <div className="flex flex-wrap items-center gap-2 text-[10px] uppercase tracking-[0.18em] text-[#6b7280]">
                            <span className={`rounded-full px-3 py-1 ${m.task ? statusTone(m.task.status) : "bg-[#eef2ff] text-[#4f46e5]"}`}>
                              {m.task ? statusLabel(m.task.status) : "提交中"}
                            </span>
                            {m.task?.started_at && (
                              <span className="rounded-full bg-[#f3f4f6] px-3 py-1 text-[#6b7280] normal-case tracking-normal">
                                {new Date(m.task.started_at).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })}
                              </span>
                            )}
                          </div>

                          {m.task ? (
                            <>
                              {(() => {
                                const lines = liveTasks[m.task.id]?.lines || [];
                                const primaryLines = lines.filter((line) => line.tone === "assistant" || line.tone === "result");
                                const detailLines = lines.filter((line) => line.tone === "tool" || line.tone === "system");
                                const finalText = m.task.result_text?.trim();
                                const primaryText = primaryLines.map((line) => line.text.trim()).filter(Boolean);
                                const shouldShowLive = primaryLines.length > 0;
                                const showFinal =
                                  Boolean(finalText) &&
                                  !primaryText.includes(finalText || "");
                                return (
                                  <>
                                    {shouldShowLive ? (
                                      <div className="mt-3 space-y-3">
                                        {primaryLines.map((line) => (
                                          <div
                                            key={line.id}
                                            className={`rounded-xl px-4 py-3 text-sm leading-6 ${
                                              line.tone === "result"
                                                ? "bg-[#f9fafb] text-[#374151]"
                                                : "bg-[#eef2ff] text-[#3730a3]"
                                            }`}
                                          >
                                            <div className="mb-1 text-[10px] uppercase tracking-[0.18em] opacity-70">
                                              {lineToneLabel(line.tone)}
                                            </div>
                                            <Markdown text={line.text} />
                                          </div>
                                        ))}
                                      </div>
                                    ) : m.task.status === "queued" || m.task.status === "running" ? (
                                      <div className="mt-3 rounded-xl border border-[#e0e7ff] bg-[#eef2ff] px-4 py-3 text-sm text-[#4f46e5]">
                                        {m.task.status === "queued" ? "已发送，正在等待设备接收…" : "正在处理，马上就有回应…"}
                                      </div>
                                    ) : !finalText ? (
                                      <div className="mt-3 rounded-xl border border-[#e5e7eb] bg-[#f9fafb] px-4 py-3 text-sm text-[#6b7280]">
                                        这轮没有返回内容。
                                      </div>
                                    ) : null}

                                    {showFinal && (
                                      <div className="mt-3 rounded-xl bg-[#f9fafb] px-4 py-3 text-sm leading-6 text-[#374151]">
                                        <div className="mb-1 text-[10px] uppercase tracking-[0.18em] text-[#9ca3af]">回复</div>
                                        <Markdown text={m.task.result_text || ""} />
                                      </div>
                                    )}

                                    {(detailLines.length > 0 || m.task.result_text) && (
                                      <details className="mt-3 rounded-xl border border-[#e5e7eb] bg-[#f9fafb] px-4 py-3">
                                        <summary className="cursor-pointer select-none text-[11px] uppercase tracking-[0.18em] text-[#9ca3af]">
                                          {detailLines.length > 0 ? `执行细节 ${detailLines.length} 条` : "查看执行细节"}
                                        </summary>
                                        {detailLines.length > 0 ? (
                                          <div className="mt-3 space-y-2">
                                            {detailLines.map((line) => (
                                              <div
                                                key={line.id}
                                                className={`rounded-[1rem] px-3 py-2 text-sm leading-6 ${
                                                  line.tone === "tool"
                                                    ? "bg-[#eef2ff] font-mono text-[#4f46e5]"
                                                    : "bg-[#f9fafb] font-mono text-[#6b7280]"
                                                }`}
                                              >
                                                <div className="mb-1 text-[10px] uppercase tracking-[0.18em] opacity-70">
                                                  {lineToneLabel(line.tone)}
                                                </div>
                                                <div className="whitespace-pre-wrap break-words">{line.text}</div>
                                              </div>
                                            ))}
                                          </div>
                                        ) : (
                                          <div className="mt-3 text-sm text-[#6b7280]">当前没有额外执行细节。</div>
                                        )}
                                      </details>
                                    )}
                                  </>
                                );
                              })()}
                            </>
                          ) : (
                            <div className="mt-3 rounded-xl bg-[#eef2ff] px-4 py-3 text-sm text-[#4f46e5]">任务还在提交中…</div>
                          )}
                        </div>
                      </div>
                  </div>
                ))}
                <div ref={messageEndRef} />
                </div>
              </div>
              {error && (
                <div className="mx-4 mb-3 rounded-2xl bg-[#fef2f2] px-4 py-3 text-sm text-[#b91c1c] md:mx-6">{error}</div>
              )}
              <form onSubmit={handleSend} className="shrink-0 border-t border-[#e5e7eb] bg-[#ffffff] p-3 pb-[calc(env(safe-area-inset-bottom)+0.75rem)] md:p-5">
                <textarea
                  value={prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                  onKeyDown={handlePromptKeyDown}
                  placeholder="输入你的下一条任务，直接发送到当前设备和 CLI..."
                  rows={3}
                  className="w-full resize-y rounded-xl border border-[#e5e7eb] bg-white px-4 py-3 text-sm text-[#111827] outline-none transition focus:border-[#4f46e5] focus:ring-2 focus:ring-[#4f46e5]/15"
                  required
                />
                <div className="mt-3 flex flex-col gap-3 md:flex-row md:flex-wrap md:items-center">
                  <div className="rounded-full bg-[#f3f4f6] px-3 py-2 text-[11px] uppercase tracking-[0.16em] text-[#6b7280]">
                    {currentClientName || "未绑定设备"} · {lastMessage?.task?.agent_type ? agentLabel(lastMessage.task.agent_type) : "沿用当前会话 CLI"}
                  </div>
                  <div className="hidden rounded-full bg-white px-3 py-2 text-[11px] text-[#6b7280] md:block">
                    Enter 发送 · Shift+Enter 换行
                  </div>
                  <button
                    type="submit"
                    disabled={loading}
                    className="w-full rounded-full bg-[#4f46e5] px-5 py-3 text-sm font-medium text-white transition hover:bg-[#4338ca] disabled:opacity-50 md:w-auto"
                  >
                    {loading ? "创建任务…" : activeTaskId ? "继续发送" : "发送"}
                  </button>
                </div>
              </form>
            </div>
          )}
        </main>
        </div>
      </div>

      {/* 新建会话弹窗 */}
      {modalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-[rgba(0,0,0,0.45)] p-4">
          <div className="max-h-[90vh] w-full max-w-md overflow-y-auto rounded-2xl border border-[#eceef1] bg-[#ffffff] shadow-pop">
            <div className="border-b border-[#e5e7eb] px-5 py-4">
              <div className="text-[11px] uppercase tracking-[0.22em] text-[#6b7280]">New Session</div>
              <h3 className="mt-2 text-xl font-semibold text-[#111827]">新建会话</h3>
            </div>
            <form onSubmit={handleNewConv} className="space-y-4 p-5">
              <div className="grid gap-3 rounded-xl border border-[#e5e7eb] bg-[#f9fafb] p-4">
                <div className="flex items-center gap-3">
                  <div className="flex h-8 w-8 items-center justify-center rounded-full bg-[#111827] text-xs font-semibold text-white">1</div>
                  <div>
                    <div className="text-sm font-medium text-[#374151]">先选择设备</div>
                    <div className="text-xs text-[#6b7280]">决定任务要派发到哪台在线 PC。</div>
                  </div>
                </div>
                <select
                  value={newClientId}
                  onChange={(e) => {
                    const value = e.target.value;
                    setNewClientId(value);
                    const nextClient = clients.find((c) => c.id === value);
                    const nextAgent = nextClient?.supported_agents?.[0];
                    if (nextAgent) setNewAgentType(nextAgent);
                  }}
                  className="select-clean mt-1 block w-full rounded-2xl border border-[#e5e7eb] bg-white px-4 py-3 text-sm outline-none transition focus:border-[#4f46e5] focus:ring-2 focus:ring-[#4f46e5]/15"
                >
                  <option value="">任意（未分配时由先拉取的客户端执行）</option>
                  {clients.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                      {c.hostname ? ` · ${c.hostname}` : ""}
                    </option>
                  ))}
                </select>
              </div>
              <div className="grid gap-3 rounded-xl border border-[#e5e7eb] bg-[#f9fafb] p-4">
                <div className="flex items-center gap-3">
                  <div className="flex h-8 w-8 items-center justify-center rounded-full bg-[#111827] text-xs font-semibold text-white">2</div>
                  <div>
                    <div className="text-sm font-medium text-[#374151]">再选择目录</div>
                    <div className="text-xs text-[#6b7280]">使用这台机器上的绝对路径。</div>
                  </div>
                </div>
                <input
                  type="text"
                  value={newCwd}
                  onChange={(e) => setNewCwd(e.target.value)}
                  placeholder="/path/to/project"
                  className="mt-1 block w-full rounded-2xl border border-[#e5e7eb] bg-white px-4 py-3 text-sm outline-none transition focus:border-[#4f46e5] focus:ring-2 focus:ring-[#4f46e5]/15"
                  required
                />
              </div>
              <div className="grid gap-3 rounded-xl border border-[#e5e7eb] bg-[#f9fafb] p-4">
                <div className="flex items-center gap-3">
                  <div className="flex h-8 w-8 items-center justify-center rounded-full bg-[#111827] text-xs font-semibold text-white">3</div>
                  <div>
                    <div className="text-sm font-medium text-[#374151]">然后选择 Agent</div>
                    <div className="text-xs text-[#6b7280]">只显示这台设备实际上报的可用 CLI。</div>
                  </div>
                </div>
                <select
                  value={newAgentType}
                  onChange={(e) => { setNewAgentType(e.target.value as AgentType); setNewOptions({}); }}
                  className="select-clean mt-1 block w-full rounded-2xl border border-[#e5e7eb] bg-white px-4 py-3 text-sm outline-none transition focus:border-[#4f46e5] focus:ring-2 focus:ring-[#4f46e5]/15"
                  required
                >
                  {agentChoices.map((value) => (
                    <option key={value} value={value}>
                      {agentLabel(value)}
                    </option>
                  ))}
                </select>
                <p className="mt-2 text-xs leading-6 text-[#6b7280]">
                  {selectedClient?.supported_agents?.length
                    ? "该列表来自本机 Broker 上报的本地可用 CLI。"
                    : "未绑定具体 PC 时，先展示系统支持的 CLI 类型。"}
                </p>
              </div>
              <label className="block">
                <span className="text-sm font-medium text-[#374151]">标题（可选）</span>
                <input
                  type="text"
                  value={newTitle}
                  onChange={(e) => setNewTitle(e.target.value)}
                  placeholder="会话标题"
                  className="mt-1 block w-full rounded-2xl border border-[#e5e7eb] bg-white px-4 py-3 text-sm outline-none transition focus:border-[#4f46e5] focus:ring-2 focus:ring-[#4f46e5]/15"
                />
              </label>
              {(AGENT_OPTION_SCHEMA[newAgentType] || []).length > 0 && (
                <div className="space-y-3 rounded-2xl border border-[#e5e7eb] bg-[#f9fafb] px-4 py-3">
                  <div className="text-[11px] font-medium uppercase tracking-[0.18em] text-[#9ca3af]">Agent 参数</div>
                  {(AGENT_OPTION_SCHEMA[newAgentType] || []).map((opt) => (
                    <label key={opt.key} className="block">
                      <span className="text-sm font-medium text-[#374151]">{opt.label}</span>
                      <select
                        value={newOptions[opt.key] ?? opt.default}
                        onChange={(e) => setNewOptions((prev) => ({ ...prev, [opt.key]: e.target.value }))}
                        className="select-clean mt-1 block w-full rounded-xl border border-[#e5e7eb] bg-white px-3 py-2 text-sm outline-none transition focus:border-[#4f46e5] focus:ring-2 focus:ring-[#4f46e5]/15"
                      >
                        {opt.choices.map((c) => (
                          <option key={c.value} value={c.value}>{c.label}</option>
                        ))}
                      </select>
                      {opt.hint && <span className="mt-1 block text-xs text-[#9ca3af]">{opt.hint}</span>}
                    </label>
                  ))}
                </div>
              )}
              <div className="flex justify-end gap-2 pt-2">
                <button
                  type="button"
                  onClick={() => setModalOpen(false)}
                  className="rounded-full border border-[#e5e7eb] px-4 py-2 text-sm text-[#4b5563] transition hover:bg-[#f3f4f6]"
                >
                  取消
                </button>
                <button
                  type="submit"
                  className="rounded-full bg-[#4f46e5] px-5 py-2 text-sm font-medium text-white transition hover:bg-[#4338ca]"
                >
                  创建
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      <footer className="footer hidden shrink-0 md:block">TeleAgent workspace · mobile and desktop ready</footer>
    </div>
  );
}
