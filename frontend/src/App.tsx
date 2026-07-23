import AdminApp from "./AdminApp";
import LanguageBoundary from "./LanguageBoundary";

export default function App() {
  return (
    <LanguageBoundary>
      <AdminApp />
    </LanguageBoundary>
  );
}