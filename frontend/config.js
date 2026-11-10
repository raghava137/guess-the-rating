// Where the web page finds the backend API and the monitoring tools.
// The page never talks to the model directly: everything goes through apiBase (REST).
window.APP_CONFIG = {
  apiBase: "",            // "" = same address as this page; nginx forwards /api to the backend
  requestTimeoutMs: 150000,
  tools: {
    mlflow: "http://localhost:5050",
    grafana: "http://localhost:3000",
    prometheus: "http://localhost:9090",
    alertmanager: "http://localhost:9093",
    ray: "http://localhost:8265",
  },
  grafanaDashboard: "chess-rating",
};
