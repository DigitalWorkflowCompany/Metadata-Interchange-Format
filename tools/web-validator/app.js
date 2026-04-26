// DWC web validator — stateless, client-only.
// Loads Pyodide on first drop, installs the dwc-sidecar wheel, runs
// validate_as_json against whatever the user drops. No network egress
// after the Pyodide download + wheel fetch on first visit; after that the
// service worker / edge cache keeps subsequent visits near-instant.
//
// Concurrency: Pyodide has a single shared CWD and a single /work/ dir,
// so we gate with a mutex — drops queued while a validation is running
// are ignored (plan edge-cases review #12).

const PYODIDE_VERSION = "0.27.3";
const PYODIDE_INDEX   = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;
const MAX_FILE_BYTES  = 2 * 1024 * 1024 * 1024;  // 2 GB per file; §4.8

const dropZone  = document.getElementById("drop-zone");
const fileInput = document.getElementById("file-input");
const statusEl  = document.getElementById("status");
const reportEl  = document.getElementById("report");
const stagesEl  = document.getElementById("stages");
const summaryEl = document.getElementById("summary");
const copyBtn   = document.getElementById("copy-report");

let pyodidePromise = null;
let validating     = false;
let currentReport  = null;


// ── UI helpers ───────────────────────────────────────────────────────────

function setStatus(msg, isError = false) {
  statusEl.textContent = msg;
  statusEl.classList.toggle("error", isError);
}

function escape(s) {
  return String(s).replace(/[&<>"']/g, ch => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[ch]));
}

function topStatus(stages) {
  if (stages.some(s => s.status === "fail")) return "fail";
  if (stages.some(s => s.status === "warn")) return "warn";
  return "pass";
}

function renderReport(report) {
  currentReport = report;
  const top = topStatus(report.stages);
  summaryEl.className = "summary " + top;
  summaryEl.textContent = report.summary
    || (report.errors > 0 ? `FAIL (${report.errors} error(s))` : "OK");

  stagesEl.innerHTML = "";
  for (const stage of report.stages) {
    const li = document.createElement("li");
    li.className = "stage " + stage.status;

    const details = document.createElement("details");
    if (stage.status !== "pass") details.setAttribute("open", "");

    const summary = document.createElement("summary");
    summary.className = "stage-summary";
    const label   = `Stage ${escape(stage.stage)} — ${escape(stage.title)}`;
    const meta    = stage.errors
      ? `${stage.status.toUpperCase()} (${stage.errors} err)`
      : stage.status.toUpperCase();
    summary.innerHTML = `
      <span class="dot" aria-hidden="true"></span>
      <span class="stage-title">${label}</span>
      <span class="stage-meta">${escape(meta)}</span>
    `;
    details.appendChild(summary);

    const pre = document.createElement("pre");
    pre.className = "stage-details";
    pre.textContent = (stage.lines && stage.lines.length)
      ? stage.lines.join("\n")
      : "(no output)";
    details.appendChild(pre);

    li.appendChild(details);
    stagesEl.appendChild(li);
  }

  reportEl.classList.remove("hidden");
}


// ── Pyodide lifecycle ────────────────────────────────────────────────────

function loadPyodideScript() {
  return new Promise((resolve, reject) => {
    if (window.loadPyodide) return resolve();
    const s = document.createElement("script");
    s.src = `${PYODIDE_INDEX}pyodide.js`;
    s.onload  = () => resolve();
    s.onerror = () => reject(new Error("failed to load pyodide.js"));
    document.head.appendChild(s);
  });
}

async function loadPyodideCached() {
  if (pyodidePromise) return pyodidePromise;
  pyodidePromise = (async () => {
    setStatus("Loading Pyodide runtime…");
    await loadPyodideScript();
    const pyodide = await window.loadPyodide({ indexURL: PYODIDE_INDEX });

    setStatus("Installing packages…");
    // pyyaml is needed by mhl.py (Stage 8 — MHL v2 is YAML); blake3 is
    // intentionally not pre-loaded — Pyodide has no wheel for it, and
    // canonical.py imports it lazily so other algorithms still work.
    await pyodide.loadPackage(["micropip", "jsonschema", "cryptography", "xxhash", "pyyaml"]);

    setStatus("Installing DWC validator…");
    const manifest = await (await fetch("manifest.json")).json();
    const wheelResp = await fetch(manifest.wheel);
    if (!wheelResp.ok) throw new Error(`wheel fetch failed: ${wheelResp.status}`);
    const wheelBytes = new Uint8Array(await wheelResp.arrayBuffer());
    pyodide.FS.writeFile(`/tmp/${manifest.wheel}`, wheelBytes);

    // deps=False on the wheel install skips transitive resolution.
    // The wheel declares blake3>=0.4, which has no Pyodide wheel; all
    // other deps are pre-loaded above or installed explicitly here.
    await pyodide.runPythonAsync(`
      import micropip
      await micropip.install('rfc8785')
      await micropip.install('emfs:/tmp/${manifest.wheel}', deps=False)
    `);

    setStatus("Ready. Drop a sidecar to validate.");
    return pyodide;
  })();
  return pyodidePromise;
}


// ── Driver (Python glue; runs inside Pyodide) ────────────────────────────
// Extracts any .zip files dropped in /work/, locates the sidecar, calls
// dwc_sidecar.web_remap for artifact-path rewriting (tested in pytest), and
// runs validate_as_json with missing_is_skip=True so artifacts not present
// in the zip report as SKIP rather than FAIL.
const DRIVER_SCRIPT = `
import json, zipfile, traceback
from pathlib import Path

WORK = Path("/work")

def run():
    for z in list(WORK.rglob("*.zip")):
        try:
            with zipfile.ZipFile(z) as zf:
                zf.extractall(WORK)
        except Exception as e:
            return {"error": f"bad zip {z.name}: {e}"}

    sidecars = list(WORK.rglob("*.omc.json"))
    if not sidecars:
        return {"error": "No *.omc.json found in the dropped files."}
    sidecars.sort(key=lambda p: (len(p.parts), str(p)))
    sidecar_path = sidecars[0]

    from dwc_sidecar.web_remap import build_basename_index, remap_artifact_paths
    from dwc_sidecar.validate   import validate_as_json

    index = build_basename_index(WORK)
    doc   = json.loads(sidecar_path.read_text())
    remap_artifact_paths(doc, index)
    sidecar_path.write_text(json.dumps(doc))

    keyring = next(iter(WORK.rglob("keyring.json")), None)
    return validate_as_json(
        sidecar_path,
        base_dir         = WORK,
        keyring_path     = keyring,
        missing_is_skip  = True,
    )

try:
    _result = run()
except Exception as _e:
    _result = {
        "error": f"validator crashed: {_e}",
        "trace": traceback.format_exc(),
    }

json.dumps(_result)
`;


// ── Drop/validate flow ───────────────────────────────────────────────────

async function validate(files) {
  if (validating) return;
  validating = true;
  dropZone.classList.add("disabled");
  reportEl.classList.add("hidden");
  currentReport = null;

  try {
    for (const file of files) {
      if (file.size > MAX_FILE_BYTES) {
        throw new Error(
          `${file.name} is larger than 2 GB — too big for in-browser hashing. Use the CLI instead.`
        );
      }
    }

    const pyodide = await loadPyodideCached();
    setStatus("Preparing bundle…");

    // Fresh /work/ per validation — prevents cross-contamination between drops.
    await pyodide.runPythonAsync(`
import shutil, os
if os.path.exists("/work"): shutil.rmtree("/work")
os.makedirs("/work", exist_ok=True)
    `);

    for (const file of files) {
      const buf = new Uint8Array(await file.arrayBuffer());
      pyodide.FS.writeFile(`/work/${file.name}`, buf);
    }

    setStatus("Validating…");
    const reportJson = await pyodide.runPythonAsync(DRIVER_SCRIPT);
    const report = JSON.parse(reportJson);

    if (report.error) {
      setStatus(report.error, true);
      return;
    }

    renderReport(report);
    const top = topStatus(report.stages);
    const tone = top === "fail" ? "error"
               : top === "warn" ? "warn"
               : "ok";
    setStatus(`Done — ${report.summary || top.toUpperCase()}`,
              tone === "error");
  } catch (e) {
    setStatus(`Error: ${e.message || e}`, true);
    // Leave any previous report visible? No — hide it to avoid confusion.
    reportEl.classList.add("hidden");
  } finally {
    validating = false;
    dropZone.classList.remove("disabled");
  }
}


// ── Event wiring ─────────────────────────────────────────────────────────

dropZone.addEventListener("dragover", (e) => {
  e.preventDefault();
  if (!validating) dropZone.classList.add("drag-over");
});
dropZone.addEventListener("dragleave", () => {
  dropZone.classList.remove("drag-over");
});
dropZone.addEventListener("drop", async (e) => {
  e.preventDefault();
  dropZone.classList.remove("drag-over");
  const files = Array.from(e.dataTransfer.files || []);
  if (files.length > 0) await validate(files);
});

fileInput.addEventListener("change", async (e) => {
  const files = Array.from(e.target.files || []);
  if (files.length > 0) await validate(files);
  // Reset so re-picking the same file re-triggers 'change'.
  e.target.value = "";
});

copyBtn.addEventListener("click", async () => {
  if (!currentReport) return;
  try {
    await navigator.clipboard.writeText(
      JSON.stringify(currentReport, null, 2)
    );
    copyBtn.classList.add("copied");
    copyBtn.textContent = "Copied!";
    setTimeout(() => {
      copyBtn.classList.remove("copied");
      copyBtn.textContent = "Copy report";
    }, 1500);
  } catch (_) {
    setStatus(
      "Clipboard unavailable — select the report text and copy manually.",
      true
    );
  }
});


// Initial prompt. Pyodide loads on first drop, not on page load, to keep
// the page interactive on slow connections.
setStatus("Ready. Drop a sidecar or zip to validate.");
