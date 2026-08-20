import { useNavigate } from "react-router-dom";

export default function NotFound() {
  const nav = useNavigate();
  return (
    <div className="page">
      <div className="eyebrow">404</div>
      <h1 className="h1" style={{ marginTop: 6 }}>Page not found</h1>
      <p className="lede">
        That page doesn't exist — the map only goes so far. Head back to the overview and keep exploring.
      </p>
      <button className="btn primary" style={{ marginTop: 18 }} onClick={() => nav("/overview")}>
        Back to the overview
      </button>
    </div>
  );
}
