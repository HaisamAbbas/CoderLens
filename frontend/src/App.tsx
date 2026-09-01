import { Route, Routes } from "react-router-dom";
import RequireAuth from "./components/RequireAuth";
import Shell from "./components/Shell";
import Overview from "./pages/Overview";
import Graph from "./pages/Graph";
import Reader from "./pages/Reader";
import Investigate from "./pages/Investigate";
import Flow from "./pages/Flow";
import Impact from "./pages/Impact";
import Codemap from "./pages/Codemap";
import Explorer from "./pages/Explorer";
import Tour from "./pages/Tour";
import Landing from "./pages/Landing";
import Login from "./pages/Login";
import Search from "./pages/Search";
import NotFound from "./pages/NotFound";
import DeadCode from "./pages/DeadCode";
import Communities from "./pages/Communities";
import ArchDelta from "./pages/ArchDelta";
import Weaknesses from "./pages/Weaknesses";
import Settings from "./pages/Settings";

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route element={<RequireAuth />}>
        <Route path="/" element={<Landing />} />
        <Route element={<Shell />}>
          <Route path="/tour" element={<Tour />} />
          <Route path="/tour/:sectionKey" element={<Tour />} />
          <Route path="/explorer" element={<Explorer />} />
          <Route path="/overview" element={<Overview />} />
          <Route path="/codemap" element={<Codemap />} />
          <Route path="/graph" element={<Graph />} />
          <Route path="/flow" element={<Flow />} />
          <Route path="/impact" element={<Impact />} />
          <Route path="/reader" element={<Reader />} />
          <Route path="/investigate" element={<Investigate />} />
          <Route path="/search" element={<Search />} />
          <Route path="/dead-code" element={<DeadCode />} />
          <Route path="/weaknesses" element={<Weaknesses />} />
          <Route path="/communities" element={<Communities />} />
          <Route path="/arch-delta" element={<ArchDelta />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="*" element={<NotFound />} />
        </Route>
      </Route>
    </Routes>
  );
}
