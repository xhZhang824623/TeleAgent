"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Card, Eyebrow, Badge, Button, Input } from "../components/ui";

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
    const authToken = currentToken ?? token ?? getStoredToken();
    try {
      const response = await fetch(`${API_BROKER}/credentials/`, {
        headers: authToken ? { Authorization: `Token ${authToken}` } : {},
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
    return <div className="min-h-screen flex items-center justify-center text-sm text-muted">Loading…</div>;
  }

  if (!token) {
    return (
      <div className="min-h-screen flex items-center justify-center px-6">
        <div className="rounded-card border border-line-soft bg-panel px-8 py-7 text-center shadow-soft">
          <h1 className="text-2xl font-semibold text-ink">请先登录 TeleAgent</h1>
          <p className="mt-3 text-sm text-muted">这个页面用于生成 LocalBroker 登录凭证。</p>
          <Link href="/broker" className="mt-5 inline-flex rounded-pill bg-accent px-5 py-2.5 text-sm font-medium text-white transition hover:bg-accent-hover">
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
          <Card>
            <Eyebrow>Credential Studio</Eyebrow>
            <h2 className="mt-2 text-3xl font-semibold text-ink">生成 LocalBroker 登录凭证</h2>
            <p className="mt-3 text-sm leading-7 text-muted">
              创建后会返回一个自动生成的 UUID 和 <code className="font-mono">secret_key</code>。<strong className="text-ink-soft">secret_key 仅在创建时显示一次、不会保存在服务端</strong>，请立即复制保存；UUID 之后可随时查看。
            </p>
          </Card>

          <div className="grid gap-4 lg:grid-cols-[0.95fr_1.05fr]">
            <Card>
              <Eyebrow>Create</Eyebrow>
              <h3 className="mt-2 text-xl font-semibold text-ink">新建设备凭证</h3>
              <form onSubmit={createCredential} className="mt-5 space-y-4">
                <Input
                  label="设备名称"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="例如：Office Mac mini"
                  required
                />
                {error && <div className="max-h-40 overflow-auto rounded-field bg-failed-bg px-4 py-3 text-sm text-failed-fg break-words">{error}</div>}
                <Button type="submit" variant="accent" disabled={loading}>
                  {loading ? "生成中…" : "生成凭证"}
                </Button>
              </form>
            </Card>

            <Card>
              <Eyebrow>Latest</Eyebrow>
              <h3 className="mt-2 text-xl font-semibold text-ink">最新生成结果</h3>
              {latest ? (
                <div className="mt-5 rounded-field border border-line bg-surface p-4">
                  <div className="space-y-3 text-sm text-ink-soft">
                    <div>
                      <div className="text-[11px] uppercase tracking-[0.18em] text-faint">UUID</div>
                      <div className="mt-1 break-all font-mono">{latest.id}</div>
                    </div>
                    <div>
                      <div className="text-[11px] uppercase tracking-[0.18em] text-faint">Name</div>
                      <div className="mt-1">{latest.name}</div>
                    </div>
                    <div>
                      <div className="text-[11px] uppercase tracking-[0.18em] text-faint">Secret Key</div>
                      <div className="mt-1 break-all font-mono">{latest.secret_key}</div>
                    </div>
                  </div>
                  <div className="mt-4 flex flex-wrap gap-2">
                    <Button variant="secondary" size="sm" onClick={() => copyText(latest.id)}>复制 UUID</Button>
                    <Button variant="secondary" size="sm" onClick={() => copyText(latest.secret_key || "")}>复制 Secret</Button>
                  </div>
                  <p className="mt-4 text-xs leading-6 text-failed-fg">
                    ⚠️ 请立即复制保存：secret_key 仅此一次显示，服务端只保存哈希，离开本页后无法再次查看。
                  </p>
                </div>
              ) : (
                <div className="mt-5 rounded-field bg-surface p-5 text-sm leading-7 text-muted">
                  还没有新生成的凭证。创建后，这里会立即显示 UUID 和 secret_key。
                </div>
              )}
            </Card>
          </div>

          <Card>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <Eyebrow>Inventory</Eyebrow>
                <h3 className="mt-2 text-xl font-semibold text-ink">已有凭证</h3>
              </div>
              <Badge tone="accent">{credentials.length} Items</Badge>
            </div>
            <div className="mt-5 space-y-3">
              {credentials.length === 0 ? (
                <div className="rounded-field bg-surface p-4 text-sm text-muted">暂无凭证。</div>
              ) : (
                credentials.map((credential) => (
                  <div key={credential.id} className="rounded-field border border-line bg-panel p-4">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <div className="min-w-0">
                        <div className="font-medium text-ink">{credential.name}</div>
                        <div className="mt-1 break-all font-mono text-xs text-muted">{credential.id}</div>
                      </div>
                      <div className="text-[11px] uppercase tracking-[0.18em] text-faint">
                        {new Date(credential.created_at).toLocaleString()}
                      </div>
                    </div>
                  </div>
                ))
              )}
            </div>
          </Card>
        </div>
      </main>
    </div>
  );
}
