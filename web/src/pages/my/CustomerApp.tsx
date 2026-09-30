import { useCallback, useEffect, useMemo, useState } from "react";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router";
import { api, ApiError, onUnauthorized, type Account } from "../../api";
import { AreaContext } from "../../area";
import { LiveProvider } from "../../live";
import CustomerLayout from "../../components/CustomerLayout";
import { ErrorBox, Spinner } from "../../components/ui";
import DeviceSettingsPage from "../DeviceSettingsPage";
import { CustomerCtx, type CustomerSession } from "./session";
import MyWatchesPage from "./MyWatchesPage";
import AddWatchPage from "./AddWatchPage";
import WatchHistoryPage from "./WatchHistoryPage";
import { MyNotesPage, MyRemindersPage } from "./MyItemsPages";
import MyPersonasPage from "./MyPersonasPage";
import AccountPage from "./AccountPage";

type State = { status: "loading" } | { status: "in"; account: Account } | { status: "out" } | { status: "error"; error: unknown };

/** The /my/* area: requires a signed-in customer, otherwise redirects to /login?next=… */
export default function CustomerApp() {
  const [state, setState] = useState<State>({ status: "loading" });
  const location = useLocation();
  const navigate = useNavigate();

  const load = useCallback(async () => {
    try {
      const account = await api.me.get({ no401: true });
      setState({ status: "in", account });
    } catch (e) {
      setState(e instanceof ApiError && e.status === 401 ? { status: "out" } : { status: "error", error: e });
    }
  }, []);

  useEffect(() => {
    void load();
    // Any 401 from /api/me/... (session expired, password changed elsewhere, account suspended) → sign-in page.
    return onUnauthorized("me", () => setState({ status: "out" }));
  }, [load]);

  const session = useMemo<CustomerSession | null>(
    () =>
      state.status === "in"
        ? {
            account: state.account,
            setAccount: (account) => setState({ status: "in", account }),
            reload: load,
            signOut: async () => {
              await api.me.logout().catch(() => undefined);
              navigate("/login", { replace: true });
            },
          }
        : null,
    [state, load, navigate],
  );

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
        <ErrorBox error={state.error} onRetry={() => void load()} />
      </div>
    );
  }
  if (state.status === "out" || !session) {
    const next = location.pathname + location.search;
    return <Navigate to={`/login?next=${encodeURIComponent(next)}`} replace />;
  }

  return (
    <AreaContext.Provider value="me">
      <CustomerCtx.Provider value={session}>
        <LiveProvider area="me">
          <CustomerLayout>
            <Routes>
              <Route index element={<MyWatchesPage />} />
              <Route path="add-watch" element={<AddWatchPage />} />
              <Route path="watch/:id" element={<DeviceSettingsPage mode="customer" />} />
              <Route path="watch/:id/history" element={<WatchHistoryPage />} />
              <Route path="notes" element={<MyNotesPage />} />
              <Route path="reminders" element={<MyRemindersPage />} />
              <Route path="personas" element={<MyPersonasPage />} />
              <Route path="account" element={<AccountPage />} />
              <Route path="*" element={<Navigate to="/my" replace />} />
            </Routes>
          </CustomerLayout>
        </LiveProvider>
      </CustomerCtx.Provider>
    </AreaContext.Provider>
  );
}
