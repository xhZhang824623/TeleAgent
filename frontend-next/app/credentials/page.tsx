"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

const API_BROKER = "/api/broker";
const TOKEN_KEY = "broker_token";

type Credential = {
  id: string;
  name: string;
  created_at: string;
  secret_key?: string;
};

function getStoredToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(TOKEN_KEY);
}

export default function CredentialPage() {
  const [token, setToken] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [credentials, setCredentials] = useState<Credential[]>([]);
  const [name, setName] = useState("");
  const [latest, setLatest] = useState<Credential | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setToken(getStoredToken());
    setLoaded(true);
  }, []);

  useEffect(() => {
    if (!token) return;
    void loadCredentials(token);
  }, [token]);

  async function api(method: string, path: string, body?: object) {
    const currentToken = token ?? getStoredToken();
    const response = await fetch(`${API_BROKER}${path}`, {
      method,
      headers: {
        "Content-Type": "application/json",
        ...(currentToken ? { Authorization: `Token ${currentToken}` } : {}),
      },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!response.ok) {
      throw new Error(await response.text());
    }
    return response.status === 204 ? null : response.json();
  }

  async function loadCredentials(currentToken?: string) {
    try {
      const response = await fetch(`${API_BROKER}/credentials/`, {
        headers: currentToken ? { Authorization: `Token ${currentToken}` } : {},
      });
      if (!response.ok) throw new Error(await response.text());
      setCredentials(await response.json());
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载凭证失败");
    }
  }

  async function createCredential(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    setError(null);
    setLoading(true);
    try {
      const created = (await api("POST", "/credentials/", { name: name.trim() })) as Credential;
      setLatest(created);
      setName("");
      await loadCredentials();
    } catch (e) {
      setError(e instanceof Error ? e.message : "创建失败");
    } finally {
      setLoading(false);
    }
  }

  async function copyText(value: string) {
    await navigator.clipboard.writeText(value);
  }

  if (!loaded) {
    return <div className="min-h-screen flex items-center justify-center">Loading…</div>;
  }

  if (!token) {
    return (
      <div className="min-h-screen flex items-center justify-center px-6">
        <div className="rounded-2xl border border-[#e5e7eb] bg-[#ffffff] px-8 py-7 text-center shadow-sm">
          <h1 className="text-2xl font-semibold text-[#111827]">请先登录 TeleAgent</h1>
          <p className="mt-3 text-sm text-[#6b7280]">这个页面用于生成 LocalBroker 登录凭证。</p>
          <Link
            href="/broker"
            className="mt-5 inline-flex rounded-full bg-[#4f46e5] px-5 py-2.5 text-sm font-medium text-white"
          >
            前往主界面
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen px-3 pb-6 md:px-6">
      <header className="header">
        <h1><Link href="/">TeleAgent</Link></h1>
        <nav className="flex items-center gap-3">
          <Link href="/broker">TeleAgent</Link>
          <Link href="/credentials" className="active">凭证管理</Link>
        </nav>
      </header>

      <main className="main">
        <div className="mx-auto max-w-4xl space-y-4">
          <section className="rounded-2xl border border-[#e5e7eb] bg-[#ffffff] p-5 shadow-sm">
            <div className="text-[11px] uppercase tracking-[0.24em] text-[#6b7280]">Credential Studio</div>
            <h2 className="mt-2 text-3xl font-semibold text-[#111827]">生成 LocalBroker 登录凭证</h2>
            <p className="mt-3 text-sm leading-7 text-[#6b7280]">
              创建后会返回一个自动生成的 UUID 和 `secret_key`。<strong>secret_key 仅在创建时显示一次、不会保存在服务端</strong>，请立即复制保存；UUID 之后可随时查看。
            </p>
          </section>

          <div className="grid gap-4 lg:grid-cols-[0.95fr_1.05fr]">
            <section className="rounded-2xl border border-[#e5e7eb] bg-[#ffffff] p-5 shadow-sm">
              <div className="text-[11px] uppercase tracking-[0.24em] text-[#6b7280]">Create</div>
              <h3 className="mt-2 text-xl font-semibold text-[#111827]">新建设备凭证</h3>
              <form onSubmit={createCredential} className="mt-5 space-y-4">
                <label className="block">
                  <span className="text-sm font-medium text-[#374151]">设备名称</span>
                  <input
                    type="text"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="例如：Office Mac mini"
                    className="mt-1 block w-full rounded-2xl border border-[#e5e7eb] bg-white px-4 py-3 text-sm outline-none transition focus:border-[#4f46e5] focus:ring-2 focus:ring-[#4f46e5]/15"
                    required
                  />
                </label>
                {error && <div className="rounded-2xl bg-[#fef2f2] px-4 py-3 text-sm text-[#b91c1c]">{error}</div>}
                <button
                  type="submit"
                  disabled={loading}
                  className="inline-flex rounded-full bg-[#4f46e5] px-5 py-2.5 text-sm font-medium text-white transition hover:bg-[#4338ca] disabled:opacity-50"
                >
                  {loading ? "生成中…" : "生成凭证"}
                </button>
              </form>
            </section>

            <section className="rounded-2xl border border-[#e5e7eb] bg-[#ffffff] p-5 shadow-sm">
              <div className="text-[11px] uppercase tracking-[0.24em] text-[#6b7280]">Latest</div>
              <h3 className="mt-2 text-xl font-semibold text-[#111827]">最新生成结果</h3>
              {latest ? (
                <div className="mt-5 rounded-xl border border-[#e5e7eb] bg-[#f9fafb] p-4">
                  <div className="space-y-3 text-sm text-[#374151]">
                    <div>
                      <div className="text-[11px] uppercase tracking-[0.18em] text-[#9ca3af]">UUID</div>
                      <div className="mt-1 break-all font-mono">{latest.id}</div>
                    </div>
                    <div>
                      <div className="text-[11px] uppercase tracking-[0.18em] text-[#9ca3af]">Name</div>
                      <div className="mt-1">{latest.name}</div>
                    </div>
                    <div>
                      <div className="text-[11px] uppercase tracking-[0.18em] text-[#9ca3af]">Secret Key</div>
                      <div className="mt-1 break-all font-mono">{latest.secret_key}</div>
                    </div>
                  </div>
                  <div className="mt-4 flex flex-wrap gap-2">
                    <button
                      type="button"
                      onClick={() => copyText(latest.id)}
                      className="rounded-full border border-[#e5e7eb] px-4 py-2 text-sm text-[#4b5563] transition hover:bg-[#f3f4f6]"
                    >
                      复制 UUID
                    </button>
                    <button
                      type="button"
                      onClick={() => copyText(latest.secret_key || "")}
                      className="rounded-full border border-[#e5e7eb] px-4 py-2 text-sm text-[#4b5563] transition hover:bg-[#f3f4f6]"
                    >
                      复制 Secret
                    </button>
                  </div>
                  <p className="mt-4 text-xs leading-6 text-[#b91c1c]">
                    ⚠️ 请立即复制保存：secret_key 仅此一次显示，服务端只保存哈希，离开本页后无法再次查看。
                  </p>
                </div>
              ) : (
                <div className="mt-5 rounded-xl bg-[#f9fafb] p-5 text-sm leading-7 text-[#6b7280]">
                  还没有新生成的凭证。创建后，这里会立即显示 UUID 和 secret_key。
                </div>
              )}
            </section>
          </div>

          <section className="rounded-2xl border border-[#e5e7eb] bg-[#ffffff] p-5 shadow-sm">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <div className="text-[11px] uppercase tracking-[0.24em] text-[#6b7280]">Inventory</div>
                <h3 className="mt-2 text-xl font-semibold text-[#111827]">已有凭证</h3>
              </div>
              <div className="rounded-full bg-[#eef2ff] px-3 py-1 text-[11px] uppercase tracking-[0.18em] text-[#4f46e5]">
                {credentials.length} Items
              </div>
            </div>
            <div className="mt-5 space-y-3">
              {credentials.length === 0 ? (
                <div className="rounded-xl bg-[#f9fafb] p-4 text-sm text-[#6b7280]">暂无凭证。</div>
              ) : (
                credentials.map((credential) => (
                  <div
                    key={credential.id}
                    className="rounded-xl border border-[#e5e7eb] bg-white p-4"
                  >
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <div>
                        <div className="font-medium text-[#111827]">{credential.name}</div>
                        <div className="mt-1 break-all font-mono text-xs text-[#6b7280]">{credential.id}</div>
                      </div>
                      <div className="text-xs uppercase tracking-[0.18em] text-[#9ca3af]">
                        {new Date(credential.created_at).toLocaleString()}
                      </div>
                    </div>
                  </div>
                ))
              )}
            </div>
          </section>
        </div>
      </main>
    </div>
  );
}
