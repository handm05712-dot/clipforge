import { AppCtx, useAppState } from "./state";
import { Home } from "./components/Home";
import { Editor } from "./components/Editor";

export default function App() {
  const state = useAppState();
  return (
    <AppCtx.Provider value={state}>
      <div className="app">
        {state.route.page === "editor" && state.route.projectId ? (
          <Editor projectId={state.route.projectId} />
        ) : (
          <Home />
        )}
      </div>
      <div className="toast-wrap">
        {state.toasts.map((t) => (
          <div key={t.id} className={"toast " + t.kind}>{t.msg}</div>
        ))}
      </div>
    </AppCtx.Provider>
  );
}
