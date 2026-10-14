import { Suspense, lazy, useEffect } from "react";
import { Route, Routes, useNavigate, useParams } from "react-router-dom";
import { Layout } from "./components/Layout";
import { EmptyState, Button, Spinner } from "./components/ui";
import { useStore, autosaveLoop } from "./lib/store";

const Dashboard = lazy(() => import("./pages/Dashboard"));
const Setup = lazy(() => import("./pages/Setup"));
const Studio = lazy(() => import("./pages/Studio"));
const Plan2D = lazy(() => import("./pages/Plan2D"));
const Viewer3D = lazy(() => import("./pages/Viewer3D"));
const Interior = lazy(() => import("./pages/Interior"));
const Exterior = lazy(() => import("./pages/Exterior"));
const Budget = lazy(() => import("./pages/Budget"));
const History = lazy(() => import("./pages/History"));
const Exports = lazy(() => import("./pages/Exports"));
const SettingsPage = lazy(() => import("./pages/SettingsPage"));
const Shared = lazy(() => import("./pages/Shared"));

function ProjectGate({ children }: { children: React.ReactNode }) {
  const data = useStore((s) => s.data);
  const loading = useStore((s) => s.loading);
  const navigate = useNavigate();
  if (loading) return <div className="p-10"><Spinner label="Opening project…" /></div>;
  if (!data) {
    return (
      <div className="p-10">
        <EmptyState title="No project open"
          body="Create your first house design from your plot size, or upload a photo of an existing home. Images are always optional."
          action={<Button onClick={() => navigate("/setup")}>Start a project</Button>} />
      </div>
    );
  }
  return <>{children}</>;
}

export default function App() {
  useEffect(() => { autosaveLoop(); }, []);
  return (
    <Routes>
      <Route path="/shared/:token" element={<Shared />} />
      <Route path="*" element={
        <Layout>
          <Suspense fallback={<div className="p-10"><Spinner /></div>}>
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/setup" element={<Setup />} />
              <Route path="/project/:id" element={<OpenProject />} />
              <Route path="/studio" element={<ProjectGate><Studio /></ProjectGate>} />
              <Route path="/plan" element={<ProjectGate><Plan2D /></ProjectGate>} />
              <Route path="/viewer" element={<ProjectGate><Viewer3D /></ProjectGate>} />
              <Route path="/interior" element={<ProjectGate><Interior /></ProjectGate>} />
              <Route path="/exterior" element={<ProjectGate><Exterior /></ProjectGate>} />
              <Route path="/budget" element={<ProjectGate><Budget /></ProjectGate>} />
              <Route path="/history" element={<ProjectGate><History /></ProjectGate>} />
              <Route path="/exports" element={<ProjectGate><Exports /></ProjectGate>} />
              <Route path="/settings" element={<SettingsPage />} />
            </Routes>
          </Suspense>
        </Layout>
      } />
    </Routes>
  );
}

function OpenProject() {
  const { id } = useParams();
  const navigate = useNavigate();
  const loadProject = useStore((s) => s.loadProject);
  useEffect(() => {
    if (id) loadProject(id).then(() => navigate("/studio", { replace: true }));
  }, [id, loadProject, navigate]);
  return <div className="p-10"><Spinner label="Opening project…" /></div>;
}
