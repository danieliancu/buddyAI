import { useState, type FormEvent, type ReactNode } from "react";
import { AlertTriangle, CheckCircle2, KeyRound, PlugZap, Server, XCircle } from "lucide-react";
import { api, type KeyName, type TestResult, type TestTarget } from "../api";
import { LanguagePicker } from "../components/LanguageBits";
import { useLanguages } from "../languages";
import { Badge, Button, Card, ErrorBox, Field, Input, PageHeader, Spinner, useAsync } from "../components/ui";

const KEYS: { name: KeyName; label: string; secret: boolean; placeholder: string }[] = [
  { name: "openai_api_key", label: "OpenAI API key", secret: true, placeholder: "sk-…" },
  { name: "dashscope_api_key", label: "DashScope API key (Qwen)", secret: true, placeholder: "sk-…" },
  { name: "azure_speech_key", label: "Azure Speech key", secret: true, placeholder: "Azure key" },
  { name: "azure_speech_region", label: "Azure Speech region", secret: false, placeholder: "westeurope" },
];

const PROFILE_LABEL: Record<string, string> = { openai: "OpenAI", qwen: "Qwen + Azure" };

type TestKey = "llm" | "stt" | "tts";
type TestState = TestResult | "running";

const TESTS: { key: Exclude<TestKey, "tts">; label: string }[] = [
  { key: "llm", label: "LLM" },
  { key: "stt", label: "STT" },
];

export default function SystemPage() {
  const info = useAsync(api.system.info, []);
  const [keys, setKeys] = useState<Partial<Record<KeyName, string>>>({});
  const [saving, setSaving] = useState(false);
  const [saveErr, setSaveErr] = useState<unknown>(null);
  const [saved, setSaved] = useState(false);
  const [tests, setTests] = useState<Partial<Record<TestKey, TestState>>>({});
  const [ttsLang, setTtsLang] = useState("en");
  const langMap = useLanguages();
  const languages = [...langMap.values()];

  const saveKeys = async (e: FormEvent) => {
    e.preventDefault();
    const body = Object.fromEntries(Object.entries(keys).filter(([, v]) => v && v.trim())) as Partial<Record<KeyName, string>>;
    if (!Object.keys(body).length) return;
    setSaving(true);
    setSaveErr(null);
    try {
      info.setData(await api.system.setKeys(body));
      setKeys({});
      setSaved(true);
      setTests({});
      window.setTimeout(() => setSaved(false), 2000);
    } catch (err) {
      setSaveErr(err);
    } finally {
      setSaving(false);
    }
  };

  const runTest = async (key: TestKey, target: TestTarget) => {
    setTests((m) => ({ ...m, [key]: "running" }));
    try {
      const r = await api.system.test(target);
      setTests((m) => ({ ...m, [key]: r }));
    } catch (err) {
      setTests((m) => ({ ...m, [key]: { ok: false, detail: err instanceof Error ? err.message : String(err) } }));
    }
  };

  if (info.error) return <ErrorBox error={info.error} onRetry={info.reload} />;
  if (!info.data) return <Spinner />;
  const i = info.data;
  const hasInput = Object.values(keys).some((v) => v && v.trim());

  return (
    <>
      <PageHeader title="System" subtitle={`olá server v${i.version}`} />
      {i.mock_providers && (
        <div className="mb-4 flex items-start gap-3 rounded-xl border border-warn/30 bg-warn-bg px-4 py-3 text-sm text-warn">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" />
          <span>
            <b>MOCK mode is on.</b> The server does not call real providers (STT/LLM/TTS); replies and costs are simulated. Disable
            <code className="mx-1 font-mono">BUDDYAI_MOCK_PROVIDERS</code> for production.
          </span>
        </div>
      )}
      <div className="space-y-4">
        <Card title={<span className="inline-flex items-center gap-2"><Server className="size-4 text-accent" /> Server</span>}>
          <dl className="grid gap-3 text-sm sm:grid-cols-2">
            <Row label="Watch WebSocket URL (server_url)">
              <code className="font-mono text-xs break-all select-all">{i.device_ws_url}</code>
            </Row>
            <Row label="Web URL">
              <code className="font-mono text-xs break-all">{i.base_url}</code>
            </Row>
            <Row label="mDNS discovery">{i.mdns_enabled ? <Badge tone="ok">on</Badge> : <Badge>off</Badge>}</Row>
            <Row label="Providers">{i.mock_providers ? <Badge tone="warn">mock</Badge> : <Badge tone="ok">real</Badge>}</Row>
            <Row label="Active AI profile">
              <span className="inline-flex flex-wrap items-center gap-2">
                <Badge tone="accent">{PROFILE_LABEL[i.ai_profile ?? ""] ?? i.ai_profile ?? "—"}</Badge>
                <span className="text-xs text-muted">
                  Switch it with <code className="font-mono">BUDDYAI_AI_PROFILE</code> in <code className="font-mono">server/.env</code> (restart required).
                </span>
              </span>
            </Row>
          </dl>
        </Card>

        <Card title={<span className="inline-flex items-center gap-2"><KeyRound className="size-4 text-accent" /> API keys</span>}>
          <form onSubmit={saveKeys} className="space-y-4">
            <p className="text-sm text-muted">
              Keys stay on the server and are never shown in full. Fill in only the fields you want to change. Environment variables (.env)
              take precedence.
            </p>
            <div className="grid gap-4 sm:grid-cols-2">
              {KEYS.map((k) => (
                <Field
                  key={k.name}
                  label={k.label}
                  htmlFor={k.name}
                  hint={i.keys[k.name] ? <>Current: <span className="font-mono">{i.keys[k.name]}</span></> : "Not set"}
                >
                  <Input
                    id={k.name}
                    type={k.secret ? "password" : "text"}
                    autoComplete="off"
                    placeholder={i.keys[k.name] ? "unchanged" : k.placeholder}
                    value={keys[k.name] ?? ""}
                    onChange={(e) => setKeys((m) => ({ ...m, [k.name]: e.target.value }))}
                  />
                </Field>
              ))}
            </div>
            <ErrorBox error={saveErr} />
            <div className="flex items-center gap-3">
              <Button type="submit" variant="primary" loading={saving} disabled={!hasInput}>
                Save keys
              </Button>
              {saved && <span className="text-sm text-ok">Saved</span>}
            </div>
          </form>
        </Card>

        <Card title={<span className="inline-flex items-center gap-2"><PlugZap className="size-4 text-accent" /> Connection test</span>}>
          <div className="grid gap-3 sm:grid-cols-2">
            {TESTS.map(({ key, label }) => (
              <div key={key} className="flex items-start gap-3 rounded-lg border border-border px-3 py-3">
                <Button size="sm" loading={tests[key] === "running"} onClick={() => runTest(key, key)} className="w-24 shrink-0">
                  {label}
                </Button>
                <TestOutcome r={tests[key]} />
              </div>
            ))}
            <div className="flex flex-col gap-3 rounded-lg border border-border px-3 py-3 sm:col-span-2">
              <span className="text-sm font-medium">Test voice output</span>
              <div className="flex flex-wrap items-center gap-2">
                <div className="min-w-48 flex-1">
                  <LanguagePicker
                    ariaLabel="Voice test language"
                    value={ttsLang}
                    onChange={(v) => v && setTtsLang(v)}
                    languages={languages}
                  />
                </div>
                <Button
                  size="sm"
                  loading={tests.tts === "running"}
                  onClick={() => runTest("tts", `tts_${ttsLang}`)}
                  className="h-10 w-24 shrink-0"
                >
                  Test
                </Button>
              </div>
              <TestOutcome r={tests.tts} />
            </div>
          </div>
        </Card>
      </div>
    </>
  );
}

function TestOutcome({ r }: { r: TestState | undefined }) {
  return (
    <div className="min-w-0 flex-1 pt-1 text-sm">
      {r === undefined && <span className="text-muted">Not tested</span>}
      {r === "running" && <span className="text-muted">Testing…</span>}
      {r && r !== "running" && (
        <span className={r.ok ? "text-ok" : "text-danger"}>
          {r.ok ? <CheckCircle2 className="mr-1 inline size-4" /> : <XCircle className="mr-1 inline size-4" />}
          <span className="break-words">{r.detail || (r.ok ? "OK" : "Failed")}</span>
        </span>
      )}
    </div>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="mb-0.5 text-xs text-muted">{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}
