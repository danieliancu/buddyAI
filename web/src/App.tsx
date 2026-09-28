import { lazy, Suspense, useCallback, useEffect, useState } from "react";
import { Navigate, Route, Routes } from "react-router";
import { api, setUnauthorizedHandler } from "./api";
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

// recharts is heavy: load the usage/diagnostics page on demand.
const UsagePage = lazy(() => import("./pages/UsagePage"));

interface AuthState {
  loading: boolean;
  needsSetup: boolean;
  user: string | null;
  error: unknown;
}

export default function App() {
  const [auth, setAuth] = useState<AuthState>({ loading: true, needsSetup: false, user: null, error: null });

  const load = useCallback(() => {
    setAuth((a) => ({ ...a, loading: true, error: null }));
    api.auth
      .status()
      .then((s) => setAuth({ loading: false, needsSetup: s.needs_setup, user: s.user, error: null }))
      .catch((e) => setAuth((a) => ({ ...a, loading: false, error: e })));
  }, []);

  useEffect(() => {
    load();
    // Any 401 from a protected endpoint drops the session and shows the login form.
    setUnauthorizedHandler(() => setAuth((a) => ({ ...a, user: null })));
    return () => setUnauthorizedHandler(null);
  }, [load]);

  if (auth.loading && !auth.user) {
    return (
      <div className="grid min-h-screen place-items-center">
        <Spinner />
      </div>
    );
  }
  if (auth.error) {
    return (
      <div className="mx-auto max-w-md p-6">
        <ErrorBox error={auth.error} onRetry={load} />
      </div>
    );
  }
  if (auth.needsSetup || !auth.user) {
    return (
      <AuthPage
        mode={auth.needsSetup ? "setup" : "login"}
        onDone={(user) => setAuth({ loading: false, needsSetup: false, user, error: null })}
      />
    );
  }

  const logout = async () => {
    await api.auth.logout().catch(() => undefined);
    setAuth((a) => ({ ...a, user: null }));
  };

  return (
    <LiveProvider>
      <Layout user={auth.user} onLogout={logout}>
        <Routes>
          <Route path="/" element={<Navigate to="/devices" replace />} />
          <Route path="/devices" element={<DevicesPage />} />
          <Route path="/devices/:id" element={<DeviceSettingsPage />} />
          <Route path="/conversations" element={<ConversationsPage />} />
          <Route path="/personas" element={<PersonasPage />} />
          <Route
            path="/usage"
            element={
              <Suspense fallback={<Spinner />}>
                <UsagePage />
              </Suspense>
            }
          />
          <Route path="/firmware" element={<FirmwarePage />} />
          <Route path="/system" element={<SystemPage />} />
          <Route path="*" element={<Navigate to="/devices" replace />} />
        </Routes>
      </Layout>
    </LiveProvider>
  );
}
