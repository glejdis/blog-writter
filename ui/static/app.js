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

  const STAGE_ORDER = [
    ["ideation", "Ideation"],
    ["pick_angle", "Pick angle"],
    ["internal_knowledge", "Internal knowledge"],
    ["research", "Research"],
    ["planner", "Planner"],
    ["approve_plan", "Plan approval"],
    ["poc_builder", "PoC builder"],
    ["writer", "Writer"],
    ["fact_checker", "Fact checker"],
    ["critic", "Critic"],
    ["final_review", "Final review"],
  ];
  const stageNodes = new Map();
  for (const [id, label] of STAGE_ORDER) {
    const li = document.createElement("li");
    li.dataset.stage = id;
    li.innerHTML = `<span class="stage-icon"></span><span>${label}</span>`;
    stagesEl.appendChild(li);
    stageNodes.set(id, li);
  }

  let ws = null;
  let currentDraft = "";
  let currentTitle = "blog-post";
  let downloadPath = null;
  let pipelineRunning = false;

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
      pipelineRunning = false;
      startBtn.disabled = false;
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
        break;
      case "stage_end":
        markStage(msg.stage, "done");
        break;
      case "log":
        addLog(msg.message);
        break;
      case "angles":
        addCard("Angles", msg.angles.map((a) => `<li>${escape(a)}</li>`).join(""), "ul");
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
      case "critic":
        addCard(
          `Critic round ${msg.round} → ${msg.verdict} (${msg.total})`,
          (msg.feedback || []).map((f) => `<li>${escape(f)}</li>`).join("") ||
            "<li>No feedback.</li>",
          "ul",
        );
        break;
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
          );
        }
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
      case "persisted":
        downloadPath = msg.draft_path;
        if (msg.draft_path) {
          addLog(`Draft saved → ${msg.draft_path}`);
        }
        break;
      case "done":
        pipelineRunning = false;
        startBtn.disabled = false;
        downloadBtn.disabled = !currentDraft;
        acceptBtn.disabled = false;
        addSystem(
          `Pipeline complete — verdict: ${msg.final_verdict}. You can now ask for revisions below, or accept.`,
        );
        reviseForm.classList.remove("hidden");
        reviseInput.focus();
        break;
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
  function addCard(title, bodyHtml, wrapper) {
    const div = document.createElement("div");
    div.className = "msg card";
    div.innerHTML = `<h3>${escape(title)}</h3>` + (wrapper ? `<${wrapper}>${bodyHtml}</${wrapper}>` : bodyHtml);
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
    pipelineRunning = true;
    startBtn.disabled = true;
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

  connect();
})();
