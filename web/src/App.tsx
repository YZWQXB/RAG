import { BrowserRouter, Link, Navigate, Outlet, Route, Routes, useNavigate } from "react-router-dom";
import { Chat } from "./pages/Chat";
import { KbDocuments } from "./pages/KbDocuments";
import { KbList } from "./pages/KbList";
import { Login } from "./pages/Login";
import { Quota } from "./pages/Quota";
import { Register } from "./pages/Register";
import { ModeBadge } from "./components/ModeBadge";
import { clearToken, getTenant, getToken } from "./lib/auth";

function Shell() {
  const navigate = useNavigate();
  const tenant = getTenant();

  return (
    <div className="min-h-screen bg-zinc-50">
      <header className="flex h-14 items-center justify-between border-b border-zinc-200 bg-white px-6">
        <div className="flex items-center gap-6">
          <Link to="/kbs" className="text-sm font-medium text-zinc-900">
            知识库问答
          </Link>
          <nav className="flex gap-4 text-sm text-zinc-600">
            <Link to="/kbs" className="hover:text-zinc-900">
              知识库
            </Link>
            <Link to="/chat" className="hover:text-zinc-900">
              问答
            </Link>
            <Link to="/quota" className="hover:text-zinc-900">
              额度
            </Link>
          </nav>
        </div>
        <div className="flex items-center gap-3 text-sm text-zinc-600">
          <ModeBadge />
          <span className="max-w-40 truncate">{tenant?.name ?? ""}</span>
          <button
            type="button"
            onClick={() => {
              clearToken();
              navigate("/login", { replace: true });
            }}
            className="text-xs text-zinc-500 underline"
          >
            退出
          </button>
        </div>
      </header>
      <Outlet />
    </div>
  );
}

function Protected() {
  if (!getToken()) return <Navigate to="/login" replace />;
  return <Shell />;
}

export function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/register" element={<Register />} />
        <Route element={<Protected />}>
          <Route path="/kbs" element={<KbList />} />
          <Route path="/kbs/:kbId/documents" element={<KbDocuments />} />
          <Route path="/chat" element={<Chat />} />
          <Route path="/quota" element={<Quota />} />
        </Route>
        <Route path="*" element={<Navigate to="/kbs" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
