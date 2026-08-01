import { createRoot } from "react-dom/client";
import { App } from "./App";
import { BundleProvider } from "./lib/BundleContext";
import "./design/tokens.css";

createRoot(document.getElementById("root")!).render(
  <BundleProvider>
    <App />
  </BundleProvider>,
);
