import { lazy, Suspense, useCallback, useEffect, useState } from "react";
import { Navigate, Route, Routes, useLocation, useNavigate, useSearchParams } from "react-router";
import { api, onUnauthorized } from "./api";
import { AreaContext } from "./area";
import { LiveProvider } from "./live";
import Layout from "./components/Layout";
import { ErrorBox, Spinner } from "./components/ui";
import AuthPage from "./pages/AuthPage";
import DevicesPage from "./pages/DevicesPage";
import DeviceSettingsPage from "./pages/DeviceSettingsPage";
import PersonasPage from "./pages/PersonasPage";
import ConversationsPage from "./pages/ConversationsPage";
import SystemPage from "./pages/SystemPage";
import FirmwarePage from "./pages/FirmwarePage";
import IssuesPage from "./pages/IssuesPage";
import CustomersPage from "./pages/CustomersPage";
import CustomerDetailPage from "./pages/CustomerDetailPage";
import OrdersPage from "./pages/OrdersPage";
import { safeNext } from "./pages/my/session";

// recharts is heavy: load the usage/diagnostics page on demand.
const UsagePage = lazy(() => import("./pages/UsagePage"));

type State = { status: "loading" } | { status: "in"; user: string } | { status: "out" } | { status: "error"; error: unknown };

/** The /admin/* area: requires an operator session, otherwise redirects to /admin/login?next=… */
export default function OperatorApp() {
  const [state, setState] = useState<State>({ status: "loading" });
  const location = useLocation();
  const navigate = useNavigate();

  const load = useCallback(() => {
    setState({ status: "loading" });
    api.auth
      .status()
      .then((s) => setState(s.user ? { status: "in", user: s.user } : { status: "out" }))
      .catch((error) => setState({ status: "error", error }));
  }, []);

  useEffect(() => {
    load();
    // Any 401 from an operator endpoint (or 4401 on /api/live) → operator sign-in.
    return onUnauthorized("admin", () => setState({ status: "out" }));
  }, [load]);

  if (state.status === "loading") {
    return (
      <div className="grid min-h-screen place-items-center">
        <Spinner />
      </div>
    );
  }
  if (state.status === "error") {
    return (
      <div className="mx-auto max-w-md p-6">
        <ErrorBox error={state.error} onRetry={load} />
      </div>
    );
  }
  if (state.status === "out") {
    return <Navigate to={`/admin/login?next=${encodeURIComponent(location.pathname + location.search)}`} replace />;
  }

  const logout = async () => {
    await api.auth.logout().catch(() => undefined);
    navigate("/admin/login", { replace: true });
  };

  return (
    <AreaContext.Provider value="admin">
      <LiveProvider area="admin">
        <Layout user={state.user} onLogout={logout}>
          <Routes>
            <Route index element={<Navigate to="/admin/devices" replace />} />
            <Route path="devices" element={<DevicesPage />} />
            <Route path="devices/:id" element={<DeviceSettingsPage />} />
            <Route path="customers" element={<CustomersPage />} />
            <Route path="customers/:id" element={<CustomerDetailPage />} />
            <Route path="orders" element={<OrdersPage />} />
            <Route path="conversations" element={<ConversationsPage />} />
            <Route path="personas" element={<PersonasPage />} />
            <Route
              path="usage"
              element={
                <Suspense fallback={<Spinner />}>
                  <UsagePage />
                </Suspense>
              }
            />
            <Route path="issues" element={<IssuesPage />} />
            <Route path="firmware" element={<FirmwarePage />} />
            <Route path="system" element={<SystemPage />} />
            <Route path="*" element={<Navigate to="/admin/devices" replace />} />
          </Routes>
        </Layout>
      </LiveProvider>
    </AreaContext.Provider>
  );
}

/** /admin/login: first-run setup (no operator yet) or operator sign-in. */
export function OperatorLoginPage() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const next = safeNext(params.get("next"), "/admin/devices");
  const [state, setState] = useState<{ loading: boolean; needsSetup: boolean; webSetup: boolean; error: unknown }>({
    loading: true,
    needsSetup: false,
    webSetup: true,
    error: null,
  });

  const load = useCallback(() => {
    setState((s) => ({ ...s, loading: true, error: null }));
    api.auth
      .status()
      .then((s) => {
        if (s.user && !s.needs_setup) navigate(next, { replace: true });
        else setState({ loading: false, needsSetup: s.needs_setup, webSetup: s.web_setup_allowed !== false, error: null });
      })
      .catch((error) => setState((s) => ({ ...s, loading: false, error })));
  }, [navigate, next]);

  useEffect(load, [load]);

  if (state.loading) {
    return (
      <div className="grid min-h-screen place-items-center">
        <Spinner />
      </div>
    );
  }
  if (state.error) {
    return (
      <div className="mx-auto max-w-md p-6">
        <ErrorBox error={state.error} onRetry={load} />
      </div>
    );
  }
  if (state.needsSetup && !state.webSetup) {
    return (
      <div className="grid min-h-screen place-items-center px-4 py-10">
        <div className="w-full max-w-md rounded-2xl border border-border bg-surface p-6 shadow-xl">
          <h1 className="text-lg font-semibold">Operator account not set up</h1>
          <p className="mt-2 text-sm text-muted">The operator account is created on the server:</p>
          <pre className="mt-3 overflow-x-auto rounded-lg bg-surface-2 px-3 py-2 font-mono text-xs select-all">
            docker compose exec server python -m app.cli create-operator &lt;name&gt;
          </pre>
          <p className="mt-3 text-sm text-muted">Then reload this page and sign in.</p>
          <button onClick={load} className="mt-4 text-sm text-accent hover:underline">
            Reload
          </button>
        </div>
      </div>
    );
  }
  return (
    <AreaContext.Provider value="admin">
      <AuthPage mode={state.needsSetup ? "setup" : "login"} onDone={() => navigate(next, { replace: true })} />
    </AreaContext.Provider>
  );
}
