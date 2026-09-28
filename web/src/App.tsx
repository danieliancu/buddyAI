import { useEffect } from "react";
import { Navigate, Route, Routes, useLocation, useNavigate, useParams } from "react-router";
import { api } from "./api";
import { Spinner } from "./components/ui";
import OperatorApp, { OperatorLoginPage } from "./OperatorApp";
import CustomerApp from "./pages/my/CustomerApp";
import { ForgotPasswordPage, LoginPage, ResetPasswordPage, SignupPage, VerifyEmailPage } from "./pages/my/AuthPages";

/**
 * One SPA, two roles:
 *   - customers: /login, /signup, /forgot-password, /reset-password, /verify-email, /my/*
 *   - operator:  /admin/login, /admin/*
 * The pre-M7 operator paths (/devices, …) redirect to /admin/… so bookmarks keep working.
 */
export default function App() {
  return (
    <Routes>
      <Route path="/" element={<RootRedirect />} />

      {/* customer */}
      <Route path="/login" element={<LoginPage />} />
      <Route path="/signup" element={<SignupPage />} />
      <Route path="/forgot-password" element={<ForgotPasswordPage />} />
      <Route path="/reset-password" element={<ResetPasswordPage />} />
      <Route path="/verify-email" element={<VerifyEmailPage />} />
      <Route path="/my/*" element={<CustomerApp />} />

      {/* operator */}
      <Route path="/admin/login" element={<OperatorLoginPage />} />
      <Route path="/admin/*" element={<OperatorApp />} />

      {/* legacy operator paths */}
      {["devices", "conversations", "personas", "usage", "firmware", "system"].map((p) => (
        <Route key={p} path={`/${p}`} element={<LegacyRedirect to={`/admin/${p}`} />} />
      ))}
      <Route path="/devices/:id" element={<LegacyDeviceRedirect />} />

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

/** "/" → /my when signed in as a customer, else /login. */
function RootRedirect() {
  const navigate = useNavigate();
  useEffect(() => {
    let alive = true;
    api.me
      .get({ no401: true })
      .then(() => alive && navigate("/my", { replace: true }))
      .catch(() => alive && navigate("/login", { replace: true }));
    return () => {
      alive = false;
    };
  }, [navigate]);
  return (
    <div className="grid min-h-screen place-items-center">
      <Spinner />
    </div>
  );
}

function LegacyRedirect({ to }: { to: string }) {
  const { search } = useLocation();
  return <Navigate to={to + search} replace />;
}

function LegacyDeviceRedirect() {
  const { id = "" } = useParams();
  return <Navigate to={`/admin/devices/${encodeURIComponent(id)}`} replace />;
}
