// blog-writer chat UI — connects to /ws and renders the pipeline + draft live.

(() => {
  const $ = (id) => document.getElementById(id);
  const statusDot = $("status-dot");
  const statusText = $("status-text");
  const chat = $("chat");
  const stagesEl = $("stages");
  const draftWrap = $("draft-wrap");
  const draftEl = $("draft");
  const draftIter = $("draft-iter");
  const downloadBtn = $("download-btn");
  const acceptBtn = $("accept-btn");
  const reviseForm = $("revise-form");
  const reviseInput = $("revise-input");
  const briefForm = $("brief-form");
  const startBtn = $("start-btn");
  const copyBtn = $("copy-btn");
  const newBtn = $("new-btn");
  const progressFill = $("progress-fill");
  const emptyState = $("empty-state");
  const improveForm = $("improve-form");
  const improveBtn = $("improve-btn");
  const modeToggle = $("mode-toggle");
  const modeNew = $("mode-new");
  const modeImprove = $("mode-improve");

  const STAGE_ORDER = [
    ["ideation", "Ideation"],
    ["pick_angle", "Pick angle"],
    ["internal_knowledge", "Internal knowledge"],
    ["research", "Research"],
    ["planner", "Planner"],
    ["approve_plan", "Plan approval"],
    ["poc_builder", "PoC builder"],
    ["diagrammer", "Architecture"],
    ["writer", "Writer"],
    ["fact_checker", "Fact checker"],
    ["critic", "Critic"],
    ["final_review", "Final review"],
  ];
  const STAGE_INDEX = new Map(STAGE_ORDER.map(([id], i) => [id, i]));
  const stageNodes = new Map();
  for (const [id, label] of STAGE_ORDER) {
    const li = document.createElement("li");
    li.dataset.stage = id;
    li.innerHTML = `<span class="stage-icon"></span><span>${label}</span>`;
    stagesEl.appendChild(li);
    stageNodes.set(id, li);
  }

  function setProgress(stageId, done) {
    const idx = STAGE_INDEX.get(stageId);
    if (idx == null) return;
    const pct = ((idx + (done ? 1 : 0.4)) / STAGE_ORDER.length) * 100;
    progressFill.style.width = `${Math.min(100, pct)}%`;
  }

  function setBusy(busy) {
    pipelineRunning = busy;
    startBtn.disabled = busy;
    startBtn.classList.toggle("busy", busy);
    startBtn.textContent = busy ? "Working…" : "Start writing";
    if (improveBtn) {
      improveBtn.disabled = busy;
      improveBtn.classList.toggle("busy", busy);
      improveBtn.textContent = busy ? "Working…" : "Improve draft";
    }
  }

  function hideEmptyState() {
    if (emptyState && emptyState.parentNode) emptyState.remove();
  }

  let ws = null;
  let currentDraft = "";
  let currentTitle = "blog-post";
  let downloadPath = null;
  let pipelineRunning = false;
  let improveMode = false;
  let currentExcalidraw = null;
  let currentDiagramTitle = "architecture";

  // -------------------------------------------------------------------------
  // WebSocket plumbing
  // -------------------------------------------------------------------------

  function connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws`);

    ws.onopen = () => {
      statusDot.classList.add("ok");
      statusText.textContent = "connected";
    };
    ws.onclose = () => {
      statusDot.classList.remove("ok");
      statusDot.classList.add("err");
      statusText.textContent = "disconnected — reload";
      setBusy(false);
    };
    ws.onerror = () => {
      statusDot.classList.add("err");
      statusText.textContent = "error";
    };
    ws.onmessage = (ev) => {
      let msg;
      try {
        msg = JSON.parse(ev.data);
      } catch {
        return;
      }
      handleMessage(msg);
    };
  }

  function send(obj) {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify(obj));
  }

  // -------------------------------------------------------------------------
  // Event handlers
  // -------------------------------------------------------------------------

  function handleMessage(msg) {
    switch (msg.type) {
      case "ready":
        addSystem("Connected. Fill in the brief on the left to start.");
        break;
      case "stage_start":
        markStage(msg.stage, "running");
        setProgress(msg.stage, false);
        break;
      case "stage_end":
        markStage(msg.stage, "done");
        setProgress(msg.stage, true);
        break;
      case "log":
        addLog(msg.message);
        break;
      case "angles":
        addCard("Angles", msg.angles.map((a) => `<li>${escape(a)}</li>`).join(""), "ul", {
          icon: "💡",
        });
        break;
      case "angle_picked":
        addLog(`Angle: ${msg.angle}`);
        break;
      case "outline":
        renderOutline(msg);
        break;
      case "poc_result":
        addLog(
          `PoC ${msg.id}: exit=${msg.exit_code}, attempts=${msg.attempts}` +
            (msg.stdout ? ` · stdout: ${truncate(msg.stdout, 100)}` : ""),
        );
        break;
      case "diagram":
        renderDiagram(msg);
        break;
      case "critic": {
        if (improveMode) break; // shown via the "recommendations" card instead
        const approved = String(msg.verdict || "").toLowerCase().includes("approve");
        addCard(
          `Critic · round ${msg.round}`,
          (msg.feedback || []).map((f) => `<li>${escape(f)}</li>`).join("") ||
            "<li>No feedback — looks good.</li>",
          "ul",
          {
            variant: approved ? "ok" : "warn",
            icon: approved ? "✅" : "🔁",
            badge: `${escape(msg.verdict)} · ${msg.total}`,
          },
        );
        break;
      }
      case "fact_findings":
        if (msg.items && msg.items.length) {
          addCard(
            `Fact check (${msg.items.length})`,
            msg.items
              .map((f) => `<li>[${f.status}] <em>${escape(f.section)}</em>: ${escape(f.claim)}</li>`)
              .join(""),
            "ul",
          );
        }
        break;
      case "citations":
        if (msg.items && msg.items.length) {
          addCard(
            `${msg.kind === "internal" ? "Internal (Learn)" : "External"} sources (${msg.items.length})`,
            msg.items
              .map(
                (c) =>
                  `<li><a href="${escape(c.url)}" target="_blank" rel="noopener">${escape(c.title)}</a></li>`,
              )
              .join(""),
            "ul",
            { icon: msg.kind === "internal" ? "📘" : "🌐" },
          );
        }
        break;
      case "recommendations":
        addCard(
          `Recommendations${msg.total != null ? ` · score ${msg.total}` : ""}`,
          (msg.items || []).map((f) => `<li>${escape(f)}</li>`).join("") ||
            "<li>No changes recommended — looks solid.</li>",
          "ul",
          { variant: "ok", icon: "🛠️" },
        );
        break;
      case "draft":
        renderDraft(msg.markdown, msg.iteration);
        break;
      case "ask":
        renderAsk(msg);
        break;
      case "revision_done":
        addLog(`Revision applied (iteration ${msg.iteration}).`);
        break;
      case "revision_persisted":
        if (msg.draft_path) downloadPath = msg.draft_path;
        downloadBtn.disabled = !currentDraft;
        break;
      case "improve_persisted":
        if (msg.improved_path) {
          downloadPath = msg.improved_path;
          addLog(`Improved draft saved → ${msg.improved_path}`);
        }
        if (msg.review_path) addLog(`Review saved → ${msg.review_path}`);
        if (msg.sources_path) addLog(`Sources saved → ${msg.sources_path}`);
        break;
      case "persisted":
        downloadPath = msg.draft_path;
        if (msg.draft_path) {
          addLog(`Draft saved → ${msg.draft_path}`);
        }
        break;
      case "done": {
        setBusy(false);
        progressFill.style.width = "100%";
        const hasDraft = !!currentDraft;
        downloadBtn.disabled = !hasDraft;
        copyBtn.disabled = !hasDraft;
        acceptBtn.disabled = !hasDraft;
        newBtn.hidden = false;
        if (improveMode) {
          addSystem(
            hasDraft
              ? "Draft improved — sources added and recommendations applied. Revise further below, or accept."
              : "Recommendations and sources ready. Toggle off “Recommend only” to also rewrite the draft.",
          );
        } else {
          addSystem(
            `Pipeline complete — verdict: ${msg.final_verdict}. You can now ask for revisions below, or accept.`,
          );
        }
        if (hasDraft) {
          reviseForm.classList.remove("hidden");
          reviseInput.focus();
        }
        break;
      }
      case "error":
        addError(msg.message);
        break;
      case "pong":
        break;
      default:
        addLog(`(unknown event: ${msg.type})`);
    }
  }

  // -------------------------------------------------------------------------
  // Render helpers
  // -------------------------------------------------------------------------

  function escape(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function truncate(s, n) {
    s = String(s);
    return s.length > n ? s.slice(0, n) + "…" : s;
  }

  function scroll() {
    chat.scrollTop = chat.scrollHeight;
  }

  function addSystem(text) {
    const div = document.createElement("div");
    div.className = "msg system";
    div.textContent = text;
    chat.appendChild(div);
    scroll();
  }
  function addLog(text) {
    const div = document.createElement("div");
    div.className = "msg log";
    div.textContent = `· ${text}`;
    chat.appendChild(div);
    scroll();
  }
  function addError(text) {
    const div = document.createElement("div");
    div.className = "msg error";
    div.textContent = `⚠ ${text}`;
    chat.appendChild(div);
    scroll();
  }
  function addUser(text) {
    const div = document.createElement("div");
    div.className = "msg user";
    div.textContent = text;
    chat.appendChild(div);
    scroll();
  }
  function addCard(title, bodyHtml, wrapper, opts = {}) {
    const div = document.createElement("div");
    div.className = "msg card" + (opts.variant ? ` card-${opts.variant}` : "");
    const icon = opts.icon ? `<span class="card-icon">${opts.icon}</span>` : "";
    const badge = opts.badge ? `<span class="card-badge">${opts.badge}</span>` : "";
    div.innerHTML =
      `<h3>${icon}<span>${escape(title)}</span>${badge}</h3>` +
      (wrapper ? `<${wrapper}>${bodyHtml}</${wrapper}>` : bodyHtml);
    chat.appendChild(div);
    scroll();
  }

  function markStage(id, status) {
    const li = stageNodes.get(id);
    if (!li) return;
    li.classList.remove("running", "done", "err");
    li.classList.add(status);
  }

  function renderOutline(msg) {
    const sections = (msg.sections || []).map((s) => `<li>${escape(s)}</li>`).join("");
    const pocs = (msg.pocs || [])
      .map((p) => `<li><code>${escape(p.id)}</code> · ${escape(p.description)} <span class="src">(${escape(p.language)})</span></li>`)
      .join("");
    currentTitle = msg.title || currentTitle;
    addCard(
      `Outline · ${msg.title || ""}`,
      `<div class="src">Sections:</div><ul>${sections}</ul>` +
        (pocs ? `<div class="src">PoCs:</div><ul>${pocs}</ul>` : ""),
      "",
    );
  }

  function slugify(value) {
    return (
      String(value || "")
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-|-$/g, "")
        .slice(0, 60) || "diagram"
    );
  }

  function downloadText(text, filename, mime) {
    if (!text) return;
    const blob = new Blob([text], { type: mime || "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  }

  function renderDiagram(msg) {
    hideEmptyState();
    currentExcalidraw = msg.excalidraw || null;
    currentDiagramTitle = msg.title || "architecture";
    const slug = slugify(currentDiagramTitle);
    const div = document.createElement("div");
    div.className = "msg card diagram-card";
    const titleSuffix = msg.title ? " · " + escape(msg.title) : "";
    div.innerHTML =
      `<h3><span class="card-icon">🗺️</span><span>Architecture${titleSuffix}</span></h3>` +
      `<div class="diagram-render">Rendering diagram…</div>` +
      `<div class="diagram-actions">` +
      `<button type="button" class="diagram-dl"${currentExcalidraw ? "" : " disabled"}>Download .excalidraw</button>` +
      `<a href="https://aka.ms/excalidraw" target="_blank" rel="noopener">Open in Excalidraw ↗</a>` +
      `</div>`;
    chat.appendChild(div);
    const dlBtn = div.querySelector(".diagram-dl");
    if (dlBtn) {
      dlBtn.addEventListener("click", () =>
        downloadText(currentExcalidraw, `${slug}.excalidraw`, "application/json"),
      );
    }
    const target = div.querySelector(".diagram-render");
    const code = msg.mermaid || "";
    if (window.mermaid && code) {
      const id = "mmd-" + Math.random().toString(36).slice(2);
      Promise.resolve()
        .then(() => window.mermaid.render(id, code))
        .then(({ svg }) => {
          target.innerHTML = svg;
          scroll();
        })
        .catch(() => {
          target.innerHTML = `<pre class="diagram-code">${escape(code)}</pre>`;
        });
    } else {
      target.innerHTML = `<pre class="diagram-code">${escape(code)}</pre>`;
    }
    scroll();
  }

  function renderDraft(markdown, iteration) {
    currentDraft = markdown || "";
    draftWrap.classList.remove("hidden");
    draftIter.textContent = `· iteration ${iteration ?? "?"}`;
    if (window.marked) {
      draftEl.innerHTML = window.marked.parse(currentDraft);
    } else {
      draftEl.textContent = currentDraft;
    }
    downloadBtn.disabled = false;
    copyBtn.disabled = false;
  }

  function renderAsk(msg) {
    const div = document.createElement("div");
    div.className = "msg ask";
    const prompt = document.createElement("div");
    prompt.className = "prompt";
    prompt.textContent = msg.prompt || "Please respond:";
    div.appendChild(prompt);

    if (Array.isArray(msg.choices) && msg.choices.length) {
      const choicesDiv = document.createElement("div");
      choicesDiv.className = "choices";
      for (const choice of msg.choices) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.textContent = choice;
        btn.addEventListener("click", () => {
          send({ type: "answer", value: choice });
          addUser(choice);
          div.remove();
        });
        choicesDiv.appendChild(btn);
      }
      div.appendChild(choicesDiv);
    } else {
      const wrap = document.createElement("div");
      wrap.className = "free";
      const input = document.createElement("input");
      input.type = "text";
      input.placeholder = "Type your answer and press Enter";
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && input.value.trim()) {
          send({ type: "answer", value: input.value.trim() });
          addUser(input.value.trim());
          div.remove();
        }
      });
      wrap.appendChild(input);
      div.appendChild(wrap);
      setTimeout(() => input.focus(), 0);
    }

    chat.appendChild(div);
    scroll();
  }

  // -------------------------------------------------------------------------
  // Form handlers
  // -------------------------------------------------------------------------

  briefForm.addEventListener("submit", (e) => {
    e.preventDefault();
    const topic = $("topic").value.trim();
    if (!topic) return;
    if (pipelineRunning) return;
    improveMode = false;
    hideEmptyState();
    newBtn.hidden = true;
    setBusy(true);
    addUser(topic);
    send({
      type: "start",
      topic,
      toc: $("toc").value.trim() || null,
      instructions: $("instructions").value.trim() || null,
      autonomous: $("autonomous").checked,
      stub: $("stub").checked,
    });
  });

  // Mode toggle: switch the left pane between "New post" and "Improve a draft".
  if (modeToggle) {
    modeToggle.addEventListener("click", (e) => {
      const btn = e.target.closest(".mode-btn");
      if (!btn || pipelineRunning) return;
      const mode = btn.dataset.mode;
      for (const b of modeToggle.querySelectorAll(".mode-btn")) {
        b.classList.toggle("active", b === btn);
      }
      if (modeNew) modeNew.hidden = mode !== "new";
      if (modeImprove) modeImprove.hidden = mode !== "improve";
    });
  }

  if (improveForm) {
    improveForm.addEventListener("submit", (e) => {
      e.preventDefault();
      if (pipelineRunning) return;
      const draft = $("improve-draft").value;
      const path = $("improve-path").value.trim();
      if (!draft.trim() && !path) {
        addError("Paste a draft or give a server file path first.");
        return;
      }
      improveMode = true;
      hideEmptyState();
      newBtn.hidden = true;
      setBusy(true);
      const recommendOnly = $("improve-recommend").checked;
      addUser(
        path
          ? `Improve draft from: ${path}`
          : `Improve pasted draft${recommendOnly ? " (recommend only)" : ""}`,
      );
      send({
        type: "improve",
        draft,
        path: path || null,
        topic: $("improve-topic").value.trim() || null,
        deep_research: $("improve-deep").checked,
        recommend_only: recommendOnly,
        stub: $("improve-stub").checked,
      });
    });
  }

  reviseForm.addEventListener("submit", (e) => {
    e.preventDefault();
    const instruction = reviseInput.value.trim();
    if (!instruction) return;
    addUser(instruction);
    reviseInput.value = "";
    send({ type: "revise", instruction });
  });

  downloadBtn.addEventListener("click", () => {
    if (!currentDraft) return;
    const slug = (currentTitle || "blog-post")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-|-$/g, "")
      .slice(0, 60) || "blog-post";
    const blob = new Blob([currentDraft], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${slug}.md`;
    a.click();
    URL.revokeObjectURL(url);
  });

  acceptBtn.addEventListener("click", () => {
    send({ type: "done" });
    acceptBtn.disabled = true;
    reviseForm.classList.add("hidden");
    addSystem("Draft accepted. ✓");
  });

  copyBtn.addEventListener("click", async () => {
    if (!currentDraft) return;
    try {
      await navigator.clipboard.writeText(currentDraft);
      const prev = copyBtn.textContent;
      copyBtn.textContent = "Copied ✓";
      setTimeout(() => (copyBtn.textContent = prev), 1500);
    } catch {
      addError("Clipboard copy failed — use Download instead.");
    }
  });

  newBtn.addEventListener("click", () => {
    if (pipelineRunning) return;
    chat.innerHTML = "";
    draftWrap.classList.add("hidden");
    reviseForm.classList.add("hidden");
    draftEl.innerHTML = "";
    currentDraft = "";
    downloadPath = null;
    currentExcalidraw = null;
    downloadBtn.disabled = true;
    copyBtn.disabled = true;
    acceptBtn.disabled = true;
    newBtn.hidden = true;
    progressFill.style.width = "0%";
    for (const li of stageNodes.values()) li.classList.remove("running", "done", "err");
    addSystem("Started a new post. Fill in the brief on the left.");
    $("topic").focus();
  });

  connect();
})();
