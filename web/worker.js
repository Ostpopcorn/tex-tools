// Runs textools with Pyodide, so that the page stays responsive while Python
// works. Only the latest request to run is processed, after the zip files to
// read or write.
import { loadPyodide } from "./pyodide/pyodide.mjs";

let bridge = null;
let latest = null;
let scheduled = false;
const jobs = []; // zip files to read ("unzip") or write ("zip")

async function init() {
  postMessage({ type: "progress", text: "Starting Python…" });
  const pyodide = await loadPyodide({ indexURL: new URL("./pyodide/", import.meta.url).href });
  postMessage({ type: "progress", text: "Loading textools…" });
  const response = await fetch(new URL("./textools.zip", import.meta.url));
  if (!response.ok) throw new Error(`Could not download textools (${response.status})`);
  const sitePackages = pyodide.runPython("import site; site.getsitepackages()[0]");
  pyodide.unpackArchive(await response.arrayBuffer(), "zip", { extractDir: sitePackages });
  pyodide.runPython("import importlib; importlib.invalidate_caches()");
  bridge = pyodide.pyimport("textools.web");
  const version = pyodide.runPython("import textools; textools.__version__");
  postMessage({ type: "ready", defaults: JSON.parse(bridge.defaults()), version,
                pyodide: pyodide.version });
}

function process() {
  scheduled = false;
  if (!bridge) return;
  while (jobs.length) {
    const job = jobs.shift();
    try {
      if (job.type === "unzip") {
        postMessage({ type: "unzip", id: job.id, response: bridge.read_zip(job.data) });
      } else {
        const proxy = bridge.zip_files(JSON.stringify({ files: job.files }), ...job.archives);
        const data = proxy.toJs();
        proxy.destroy();
        postMessage({ type: "zip", id: job.id, data }, [data.buffer]);
      }
    } catch (err) {
      postMessage({ type: job.type, id: job.id, error: pythonError(err) });
    }
  }
  if (!latest) return;
  const { id, request } = latest;
  latest = null;
  try {
    const response = bridge.run(JSON.stringify(request));
    postMessage({ type: "result", id, response });
  } catch (err) {
    postMessage({ type: "error", id, message: pythonError(err) });
  }
}

function pythonError(err) {
  const text = String(err && err.message || err);
  // Show the last line of a Python traceback, e.g., "ValueError: ..."
  const lines = text.trim().split("\n");
  return lines[lines.length - 1];
}

function schedule() {
  if (!scheduled) {
    scheduled = true;
    setTimeout(process, 0);
  }
}

self.onmessage = (event) => {
  if (event.data.type === "run") latest = event.data;
  else if (event.data.type === "zip" || event.data.type === "unzip") jobs.push(event.data);
  else return;
  schedule();
};

init().then(schedule, (err) => {
  postMessage({ type: "fatal", message: String(err && err.message || err) });
});
