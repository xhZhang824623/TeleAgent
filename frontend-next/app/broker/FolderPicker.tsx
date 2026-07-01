"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { formatBytes } from "./lib";

// 远程文件夹树选择器：目录在 Agent 那台 PC（LocalBroker）上，经「请求/响应轮询」中转拉取。
// 每展开一层 = 一次 POST /fs/browse/ 建请求 + 轮询 GET /fs/browse/<id>/ 取结果（设备约 2s 一拍）。
// 传入 onFileSelect 时进入「文件浏览」模式：连文件一起列，文件行带下载按钮。

type FsEntry = { name: string; path: string; is_dir: boolean; size?: number };
type FsResult = {
  id: string;
  status: "pending" | "done" | "failed";
  listed_path?: string;
  parent_path?: string | null;
  entries?: FsEntry[];
  error?: string;
};
type Node = {
  entries: FsEntry[];
  loaded: boolean;
  loading: boolean;
  expanded: boolean;
  error?: string;
};

type ApiFn = (method: string, path: string, body?: object) => Promise<unknown>;

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export function FolderPicker({
  clientId,
  value,
  onChange,
  api,
  onFileSelect,
  onPick,
  conversationId,
}: {
  clientId: string;
  value: string;
  onChange: (path: string) => void;
  api: ApiFn;
  onFileSelect?: (path: string, name: string) => void;
  // 选定一个目录时回调（目录选择器用）：点目录名即选定该目录，父级据此收起整棵树。
  // 传入后，点目录名 = 选定（展开/收起改由三角图标负责），避免选定后还停在大树里。
  onPick?: (path: string) => void;
  // 传入则把浏览约束在该会话的工作目录内（下载浏览器用）。
  conversationId?: string;
}) {
  const includeFiles = Boolean(onFileSelect);
  const [rootPath, setRootPath] = useState<string | null>(null);
  const [nodes, setNodes] = useState<Record<string, Node>>({});
  const [rootError, setRootError] = useState<string | null>(null);
  const [rootLoading, setRootLoading] = useState(false);
  // 每次 clientId 变化递增，丢弃过期请求的回包，避免切设备后串数据。
  const genRef = useRef(0);
  // 卸载标记：组件卸载（如关闭文件浏览弹框）后停止轮询、不再 setState，避免内存泄漏与告警。
  const mountedRef = useRef(true);
  useEffect(() => () => { mountedRef.current = false; }, []);
  // onChange 以 ref 持有：调用方常传内联函数（每次渲染新建），若进依赖会让加载 effect
  // 反复触发、文件树不停刷新。用 ref 避免它成为依赖。
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  // 发起一次目录浏览并轮询到终态。
  const browse = useCallback(
    async (path: string): Promise<FsResult> => {
      const created = (await api("POST", "/fs/browse/", {
        client_id: clientId,
        path,
        include_files: includeFiles,
        ...(conversationId ? { conversation_id: conversationId } : {}),
      })) as FsResult;
      const id = created.id;
      const deadline = Date.now() + 30_000;
      while (Date.now() < deadline) {
        if (!mountedRef.current) throw new Error("已取消");  // 卸载后立即停止轮询
        const r = (await api("GET", `/fs/browse/${id}/`)) as FsResult;
        if (r.status === "done") return r;
        if (r.status === "failed") throw new Error(r.error || "列目录失败");
        await sleep(800);
      }
      throw new Error("超时：设备未响应（请确认该 PC 的 Broker 在线）");
    },
    [api, clientId, includeFiles, conversationId]
  );

  const loadRoot = useCallback(
    async (startPath: string) => {
      if (!clientId) return;
      const gen = ++genRef.current;
      setRootLoading(true);
      setRootError(null);
      try {
        const r = await browse(startPath);
        if (gen !== genRef.current || !mountedRef.current) return;
        const lp = r.listed_path || "";
        setRootPath(lp);
        setNodes({
          [lp]: { entries: r.entries || [], loaded: true, loading: false, expanded: true },
        });
        onChangeRef.current(lp);
      } catch (e) {
        if (gen !== genRef.current || !mountedRef.current) return;
        setRootPath(null);
        setRootError(e instanceof Error ? e.message : "加载失败");
      } finally {
        if (gen === genRef.current && mountedRef.current) setRootLoading(false);
      }
    },
    [browse, clientId]
  );

  // 选定设备后自动加载其默认起点（用户主目录）。
  useEffect(() => {
    setNodes({});
    setRootPath(null);
    setRootError(null);
    genRef.current++;
    if (clientId) loadRoot("");
  }, [clientId, loadRoot]);

  const toggle = useCallback(
    async (path: string) => {
      const existing = nodes[path];
      if (existing?.loaded) {
        setNodes((prev) => ({ ...prev, [path]: { ...prev[path], expanded: !prev[path].expanded } }));
        return;
      }
      const gen = genRef.current;
      setNodes((prev) => ({
        ...prev,
        [path]: { entries: [], loaded: false, loading: true, expanded: true },
      }));
      try {
        const r = await browse(path);
        if (gen !== genRef.current || !mountedRef.current) return;
        setNodes((prev) => ({
          ...prev,
          [path]: { entries: r.entries || [], loaded: true, loading: false, expanded: true },
        }));
      } catch (e) {
        if (gen !== genRef.current || !mountedRef.current) return;
        setNodes((prev) => ({
          ...prev,
          [path]: { entries: [], loaded: false, loading: false, expanded: true, error: e instanceof Error ? e.message : "加载失败" },
        }));
      }
    },
    [browse, nodes]
  );

  const renderNode = (entry: FsEntry, depth: number) => {
    // 文件叶子节点：不可展开，带下载按钮（仅文件浏览模式）。
    if (!entry.is_dir) {
      return (
        <div
          key={entry.path}
          className="flex items-center gap-2 rounded-md py-1 pr-2 text-sm hover:bg-surface"
          style={{ paddingLeft: `${depth * 14 + 24}px` }}
        >
          <span className="min-w-0 flex-1 truncate font-mono text-xs text-ink-soft" title={entry.path}>
            {entry.name}
          </span>
          {typeof entry.size === "number" && (
            <span className="shrink-0 text-[10px] text-faint">{formatBytes(entry.size)}</span>
          )}
          {onFileSelect && (
            <button
              type="button"
              onClick={() => onFileSelect(entry.path, entry.name)}
              className="shrink-0 text-xs font-medium text-accent hover:underline"
            >
              下载
            </button>
          )}
        </div>
      );
    }
    const node = nodes[entry.path];
    const expanded = node?.expanded;
    const selected = value === entry.path;
    return (
      <div key={entry.path}>
        <div
          className={`flex items-center gap-1 rounded-md py-1 pr-2 text-sm ${selected ? "bg-ink text-white" : "hover:bg-surface"}`}
          style={{ paddingLeft: `${depth * 14 + 4}px` }}
        >
          <button
            type="button"
            onClick={() => toggle(entry.path)}
            className={`flex h-5 w-5 shrink-0 items-center justify-center rounded ${selected ? "text-white/80" : "text-muted hover:text-ink"}`}
            aria-label={expanded ? "收起" : "展开"}
          >
            {node?.loading ? "…" : expanded ? "▾" : "▸"}
          </button>
          <button
            type="button"
            onClick={() => { onChange(entry.path); if (onPick) onPick(entry.path); else toggle(entry.path); }}
            className="min-w-0 flex-1 truncate text-left font-mono text-xs"
            title={entry.path}
          >
            {entry.name}
          </button>
        </div>
        {expanded && node?.error && (
          <div className="py-0.5 text-xs text-rose-600" style={{ paddingLeft: `${depth * 14 + 28}px` }}>
            {node.error}
          </div>
        )}
        {expanded && node?.loaded && node.entries.length === 0 && (
          <div className="py-0.5 text-xs text-faint" style={{ paddingLeft: `${depth * 14 + 28}px` }}>
            （无子目录）
          </div>
        )}
        {expanded && node?.loaded && node.entries.map((child) => renderNode(child, depth + 1))}
      </div>
    );
  };

  if (!clientId) {
    return <div className="rounded-field border border-line bg-surface px-3 py-4 text-xs text-muted">请先在上一步选择设备，再浏览其目录。</div>;
  }

  const root = rootPath ? nodes[rootPath] : null;

  return (
    <div className="rounded-field border border-line bg-white">
      <div className="flex items-center justify-between border-b border-line px-3 py-2">
        <span className="truncate font-mono text-xs text-muted" title={rootPath || ""}>
          {rootLoading ? "正在读取设备目录…" : rootPath || "—"}
        </span>
        <button
          type="button"
          onClick={() => loadRoot(rootPath || "")}
          className="shrink-0 text-xs text-muted hover:text-ink"
          disabled={rootLoading}
        >
          刷新
        </button>
      </div>
      <div className="max-h-64 overflow-auto p-2">
        {rootError && <div className="px-1 py-2 text-xs text-rose-600">{rootError}</div>}
        {root && (
          <div
            className={`flex items-center gap-1 rounded-md py-1 pr-2 text-sm ${value === rootPath ? "bg-ink text-white" : "hover:bg-surface"}`}
            style={{ paddingLeft: "4px" }}
          >
            <button
              type="button"
              onClick={() => rootPath && setNodes((prev) => ({ ...prev, [rootPath]: { ...prev[rootPath], expanded: !prev[rootPath].expanded } }))}
              className={`flex h-5 w-5 shrink-0 items-center justify-center rounded ${value === rootPath ? "text-white/80" : "text-muted"}`}
            >
              {root.expanded ? "▾" : "▸"}
            </button>
            <button
              type="button"
              onClick={() => {
                if (!rootPath) return;
                onChange(rootPath);
                if (onPick) { onPick(rootPath); return; }
                setNodes((prev) => ({ ...prev, [rootPath]: { ...prev[rootPath], expanded: !prev[rootPath].expanded } }));
              }}
              className="min-w-0 flex-1 truncate text-left font-mono text-xs"
              title={rootPath || ""}
            >
              {rootPath}
            </button>
          </div>
        )}
        {root?.expanded && root.entries.map((child) => renderNode(child, 1))}
        {root && root.expanded && root.entries.length === 0 && (
          <div className="px-2 py-1 text-xs text-faint">（无子目录）</div>
        )}
      </div>
    </div>
  );
}
