// 智能简历筛选系统 - 前端逻辑
const API = "/api/v1";

const $ = (id) => document.getElementById(id);

// 三分类标签与徽章样式（interview=绿 / review=橙 / reject=红）
const CLASS_LABELS = {
  interview: "值得面试",
  review: "HR审核",
  reject: "直接淘汰",
};

// 当前查询 id（提交查询成功后记录，供反馈提交使用）
// ---------------- 状态持久化（localStorage）----------------
// 刷新页面后恢复上传日志（手动筛选流程已移除，仅保留上传日志）
const LS = {
  uploadLog: "rs_upload_log",
};

function saveLogs() {
  localStorage.setItem(LS.uploadLog, $("upload-log").innerHTML);
}

function restoreState() {
  // 上传日志恢复
  const ul = localStorage.getItem(LS.uploadLog);
  if (ul) {
    $("upload-log").innerHTML = ul;
    // 日志里若还停留在"正在解析"，说明是刷新前的中间快照：
    // 实际解析状态以左侧列表徽章为准（服务端会持续更新）
    if (ul.includes("正在 AI 解析")) {
      $("upload-log").innerHTML +=
        '\n<span class="err">（以上为刷新前的解析过程记录，实际状态请看左侧列表徽章）</span>';
    }
  }
}

// 转义 HTML，防止 XSS
function escapeHtml(text) {
  if (text == null) return "";
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

// 简单 Markdown 渲染：标题、加粗、斜体、列表、代码块
function markdownToHtml(md) {
  if (!md) return "";
  const lines = md.split("\n");
  let html = "";
  let inList = false;
  let listType = null;

  const closeList = () => {
    if (inList) {
      html += listType === "ol" ? "</ol>" : "</ul>";
      inList = false;
      listType = null;
    }
  };

  const inline = (text) => {
    return escapeHtml(text)
      .replace(/\*\*\*(.+?)\*\*\*/g, "<strong><em>$1</em></strong>")
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)/g, "<em>$1</em>")
      .replace(/`([^`]+)`/g, "<code>$1</code>");
  };

  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) { closeList(); continue; }
    const headerMatch = trimmed.match(/^(#{1,6})\s+(.*)$/);
    if (headerMatch) {
      closeList();
      const level = Math.min(headerMatch[1].length + 2, 6);
      html += `<h${level}>${inline(headerMatch[2])}</h${level}>`;
      continue;
    }
    const ulMatch = trimmed.match(/^[-*]\s+(.*)$/);
    if (ulMatch) {
      if (!inList || listType !== "ul") { closeList(); html += "<ul>"; inList = true; listType = "ul"; }
      html += `<li>${inline(ulMatch[1])}</li>`;
      continue;
    }
    const olMatch = trimmed.match(/^\d+\.\s+(.*)$/);
    if (olMatch) {
      if (!inList || listType !== "ol") { closeList(); html += "<ol>"; inList = true; listType = "ol"; }
      html += `<li>${inline(olMatch[1])}</li>`;
      continue;
    }
    closeList();
    html += `<p>${inline(line)}</p>`;
  }

  closeList();
  return html;
}

// ---------------- 健康检查 ----------------
async function checkHealth() {
  const badge = $("health-badge");
  try {
    const res = await fetch(`${API}/health`);
    const data = await res.json();
    if (res.ok && data.status === "ok") {
      badge.textContent = "正常";
      badge.className = "badge badge-ok";
    } else {
      throw new Error("异常");
    }
  } catch (e) {
    badge.textContent = "无法连接";
    badge.className = "badge badge-err";
  }
}

// ---------------- 上传简历（并行提交 + 后台解析轮询）----------------
function escapeRegExp(text) {
  return String(text).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

async function uploadResumes() {
  const input = $("resume-files");
  const log = $("upload-log");
  const btn = $("upload-btn");
  const files = Array.from(input.files || []);
  if (files.length === 0) {
    log.innerHTML = '<span class="err">请先选择文件</span>';
    return;
  }
  btn.disabled = true;
  log.textContent = `开始提交 ${files.length} 个文件…\n（提交后立即返回，简历在后台并行解析，可继续其他操作）`;
  saveLogs();

  // 并行提交所有文件
  const submitted = await Promise.all(files.map(async (file) => {
    const fd = new FormData();
    fd.append("file", file);
    try {
      const res = await fetch(`${API}/resumes`, { method: "POST", body: fd });
      const data = await res.json();
      if (res.ok) {
        return { file, resumeId: data.resume_id, status: data.status || "parsing",
                 error: null, warning: data.warning || null };
      }
      return { file, resumeId: null, status: "error", error: data.detail || "提交失败" };
    } catch (e) {
      return { file, resumeId: null, status: "error", error: e.message };
    }
  }));

  let ok = 0, fail = 0;
  const pending = [];
  for (const s of submitted) {
    if (s.status === "error" || !s.resumeId) {
      fail++;
      log.innerHTML += `\n<span class="err">✗ ${escapeHtml(s.file.name)}: ${escapeHtml(s.error || "失败")}</span>`;
    } else {
      log.innerHTML += `\n<span class="wait">⏳ ${escapeHtml(s.file.name)} 后台解析中…</span>`;
      pending.push(s);
    }
  }

  // 轮询所有待解析简历的状态
  const deadline = Date.now() + 5 * 60 * 1000; // 最多等 5 分钟
  const replaceLine = (s, html) => {
    log.innerHTML = log.innerHTML.replace(
      new RegExp(`<span class="wait">⏳ ${escapeRegExp(s.file.name)} 后台解析中…</span>`),
      html);
  };
  while (pending.length > 0 && Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 3000));
    for (let i = pending.length - 1; i >= 0; i--) {
      const s = pending[i];
      try {
        const res = await fetch(`${API}/resumes/${s.resumeId}/status`);
        const data = await res.json();
        if (data.status === "ready") {
          ok++;
          pending.splice(i, 1);
          const warn = data.warning || s.warning;
          const warnHtml = warn
            ? `<span class="err">⚠ ${escapeHtml(warn)}</span>`
            : "";
          replaceLine(s, `<span class="ok">✓ ${escapeHtml(s.file.name)} 解析完成</span>${warnHtml}`);
        } else if (data.status === "error") {
          fail++;
          pending.splice(i, 1);
          replaceLine(s, `<span class="err">✗ ${escapeHtml(s.file.name)}: ${escapeHtml(data.error || "解析失败")}</span>`);
        }
      } catch (e) { /* 网络抖动，下轮重试 */ }
    }
  }
  // 超时仍未完成的保持"解析中"标记
  if (pending.length > 0) {
    log.innerHTML += `\n<span class="err">还有 ${pending.length} 份仍在后台解析，可稍后刷新页面查看</span>`;
  }

  // 汇总提示
  if (fail === 0) {
    log.innerHTML += '\n\n<span class="upload-done">✅ 全部上传并解析完成，可以开始筛选了！</span>';
  } else {
    log.innerHTML += `\n\n<span class="upload-done upload-done-warn">⚠️ 完成：成功 ${ok}，失败 ${fail} 份（失败的请重新选择上传）</span>`;
  }
  btn.disabled = false;
  input.value = "";
  saveLogs();
  loadResumeList();
}

// ---------------- 简历列表 ----------------
async function loadResumeList() {
  try {
    const res = await fetch(`${API}/resumes`);
    const data = await res.json();

    $("resume-count").textContent = `(${data.resumes.length})`;

    const renderItems = (items) => items.map((r) => {
      // 紧凑一行：状态简标 + 预分类色点 + 文件名 + 时间 + 删除
      const statusBadge = r.status === "ready"
        ? '<span class="badge badge-ok">✓</span>'
        : r.status === "error"
          ? '<span class="badge badge-err">✗</span>'
          : '<span class="badge badge-unknown">…</span>';
      const warnMark = r.warning
        ? '<span class="muted" title="' + escapeHtml(r.warning) + '">⚠</span>'
        : "";
      const pre = r.preclassification || null;
      const preMark = pre
        ? `<span class="cls-dot class-badge-${pre.classification || "review"}" title="通用评估：${escapeHtml(pre.reason || "")}"></span>`
        : "";
      let timeStr = "";
      if (r.created_at) {
        const t = new Date(r.created_at);
        if (!isNaN(t)) timeStr = t.toLocaleString("zh-CN", { hour12: false, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
      }
      return `
      <li data-resume-id="${escapeHtml(r.resume_id)}">
        <div class="fn">
          <label class="sel-cb" title="选择删除"><input type="checkbox" data-sel="${escapeHtml(r.resume_id)}" /></label>
          <span class="rid">${statusBadge} ${preMark} ${warnMark} ${escapeHtml(r.filename || "(未命名)")}${timeStr ? ` · ${escapeHtml(timeStr)}` : ""}</span>
          <button class="btn-mini del-btn" title="删除简历" data-name="${escapeHtml(r.name || r.filename || "")}">🗑</button>
        </div>
        <div class="resume-detail" hidden></div>
      </li>`;
    }).join("");

    $("resume-list").innerHTML = data.resumes.length
      ? renderItems(data.resumes)
      : '<li class="empty">暂无简历（手动上传或邮箱抓取后显示）</li>';
    resetSelectionUI();
  } catch (e) {
    $("resume-list").innerHTML = `<li class="empty">加载失败：${escapeHtml(e.message)}</li>`;
  }
}

// 展开/收起简历详情（AI 解析出的完整信息）
async function toggleResumeDetail(li, resumeId) {
  const detail = li.querySelector(".resume-detail");
  if (!detail.hidden) {
    detail.hidden = true;
    return;
  }
  detail.hidden = false;
  detail.innerHTML = '<span class="muted">加载中…</span>';
  try {
    const res = await fetch(`${API}/resumes/${resumeId}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "加载失败");
    const m = data.metadata || {};
    const workHtml = (m.work_experience || []).map((w) => `
      <li><b>${escapeHtml(w.company)}</b>（${escapeHtml(w.title)}）${escapeHtml(w.start_date)} - ${escapeHtml(w.end_date)}
      ${w.description ? `<br>${escapeHtml(w.description)}` : ""}</li>`).join("");
    const projHtml = (m.projects || []).map((p) => `
      <li><b>${escapeHtml(p.name)}</b>${p.period ? `（${escapeHtml(p.period)}）` : ""}
      ${p.description ? `<br>${escapeHtml(p.description)}` : ""}</li>`).join("");
    const eduHtml = (m.education || []).map((e) =>
      `${escapeHtml(e.institution)} ${escapeHtml(e.degree || "")} ${escapeHtml(e.major || "")}`).join("；");
    detail.innerHTML = `
      <div><b>技能：</b>${(m.skills || []).map((s) => escapeHtml(s)).join("、") || "无"}</div>
      <div><b>期望薪资：</b>${escapeHtml(m.expected_salary || "未填写")}　<b>期望地点：</b>${escapeHtml((m.preferred_locations || []).join("、") || "未填写")}</div>
      <div><b>工作经历：</b></div>
      <ul>${workHtml || "<li>无</li>"}</ul>
      <div><b>项目经历：</b></div>
      <ul>${projHtml || "<li>无</li>"}</ul>
      <div><b>教育背景：</b>${eduHtml || "无"}</div>`;
  } catch (e) {
    detail.innerHTML = `加载失败：${escapeHtml(e.message)}`;
  }
}

// 删除简历（内存 + 向量库）
async function deleteResume(resumeId, name) {
  if (!confirm(`确定删除「${name || "该简历"}」吗？将从服务器和向量库中删除，无法恢复。`)) return;
  try {
    const res = await fetch(`${API}/resumes/${resumeId}`, { method: "DELETE" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "删除失败");
    loadResumeList();
  } catch (e) {
    alert("删除失败：" + e.message);
  }
}

// ---------------- 批量删除（手动 + 邮箱两个列表共用）----------------
// 复选框/计数/全选/删除同时作用于两个分组，避免邮箱列表勾选无反应
function resetSelectionUI() {
  const cbs = document.querySelectorAll('input[data-sel]');
  const checked = document.querySelectorAll('input[data-sel]:checked').length;
  $("sel-count").textContent = checked > 0 ? `已选 ${checked} 份` : "";
  $("batch-del-btn").disabled = checked === 0;
  $("sel-all-cb").checked = cbs.length > 0 && checked === cbs.length;
}

async function batchDeleteResumes() {
  const ids = Array.from(document.querySelectorAll('input[data-sel]:checked'))
    .map((cb) => cb.dataset.sel);
  if (ids.length === 0) return;
  if (!confirm(`确定删除选中的 ${ids.length} 份简历吗？将从服务器和向量库中删除，无法恢复。`)) return;
  try {
    const res = await fetch(`${API}/resumes/batch-delete`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "删除失败");
    const delLog = $("upload-log");
    delLog.innerHTML += `\n<span class="ok">✓ 已删除 ${data.deleted} 份简历${data.not_found && data.not_found.length ? `（${data.not_found.length} 份不存在）` : ""}</span>`;
    saveLogs();
    loadResumeList();
  } catch (e) {
    alert("批量删除失败：" + e.message);
  }
}

function candidateCardHtml(c) {
  const skills = (c.skills || []).map((s) => `<span class="tag">${escapeHtml(s)}</span>`).join("");
  const locations = escapeHtml((c.preferred_locations || []).join("、"));
  const scorePercent = (c.overall_score != null) ? Math.round(c.overall_score * 100) : "-";
  const email = c.email ? "📧 " + escapeHtml(c.email) + "　" : "";
  const phone = c.phone ? "📱 " + escapeHtml(c.phone) : "";
  const salary = c.expected_salary ? "<br>💰 期望薪资：" + escapeHtml(c.expected_salary) : "";
  const analysis = c.analysis ? `<div class="analysis">${markdownToHtml(c.analysis)}</div>` : "";

  // 三分类徽章 + 判定理由 + 人工纠正标记
  const cls = c.classification || "review";
  const clsBadge = `<span class="badge class-badge-${cls}">${CLASS_LABELS[cls] || cls}</span>`;
  const correctedMark = c.corrected_by_human
    ? '<span class="badge badge-unknown">已人工纠正</span>' : "";
  const reasonLine = c.classification_reason
    ? `<div class="cls-reason">判定理由：${escapeHtml(c.classification_reason)}</div>` : "";

  // 6 维评估展示（含独立负责/真实用户/可量化结果）
  let assessmentHtml = "";
  const assess = c.assessment || {};
  const assessKeys = [
    ["skill_match", "技能匹配"],
    ["experience_match", "经验匹配"],
    ["education_match", "教育匹配"],
    ["ownership", "独立负责"],
    ["real_users", "真实用户"],
    ["quantified_results", "可量化结果"],
  ];
  const assessItems = assessKeys
    .filter(([k]) => assess[k] != null)
    .map(([k, label]) => `${label} ${Math.round(assess[k] * 100)}%`);
  if (assessItems.length) {
    assessmentHtml = `<div class="cls-reason">评估：${assessItems.join(" · ")}</div>`;
  }

  // 人工纠正表单（折叠）
  const feedbackForm = `
    <details class="feedback-box">
      <summary>纠正分类</summary>
      <select data-fb="class">
        <option value="interview">值得面试</option>
        <option value="review">HR审核</option>
        <option value="reject">直接淘汰</option>
      </select>
      <input data-fb="reason" type="text" placeholder="纠正原因（选填，建议填写以帮助总结规则）" />
      <button data-fb="submit" class="btn">提交纠正</button>
    </details>`;

  return `
    <details class="candidate" data-resume-id="${escapeHtml(c.id)}" data-cls="${escapeHtml(cls)}">
      <summary class="candidate-head">
        <span class="cand-name"><span class="rank">${escapeHtml(c.rank)}</span><span class="name">${escapeHtml(c.name || "(未命名)")}</span></span>
        <span class="score">
          ${clsBadge} ${correctedMark}
          <span class="score-num">${escapeHtml(scorePercent)}%</span>
        </span>
      </summary>
      <div class="candidate-body">
        ${reasonLine}
        ${assessmentHtml}
        <div class="meta">
          ${email}${phone}
          ${locations ? "<br>📍 期望地点：" + locations : ""}
          ${salary}
        </div>
        ${skills ? `<div class="skills">${skills}</div>` : ""}
        ${analysis}
        ${feedbackForm}
      </div>
    </details>`;
}

// ---------------- 人工纠正反馈 ----------------
async function submitFeedback(candidateEl, fbClass, reason) {
  const resumeId = candidateEl.dataset.resumeId;
  const nameEl = candidateEl.querySelector(".name");
  const scoreEl = candidateEl.querySelector(".score-num");

  const body = {
    resume_id: resumeId,
    // 自动筛选场景无 query_id，用 "auto" 标识（后端不强校验 query_id）
    query_id: "auto",
    candidate_name: nameEl ? nameEl.textContent : "",
    ai_classification: candidateEl.dataset.cls || "review",
    human_classification: fbClass,
    human_reason: reason || "",
    overall_score: scoreEl ? parseFloat(scoreEl.textContent) / 100 : null,
  };
  try {
    const res = await fetch(`${API}/feedback`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "提交失败");
    // 更新该卡片徽章为人工分类
    const head = candidateEl.querySelector(".candidate-head .score");
    const newBadge = `<span class="badge class-badge-${fbClass}">${CLASS_LABELS[fbClass] || fbClass}</span>`;
    const correctedMark = '<span class="badge badge-unknown">已人工纠正</span>';
    head.innerHTML = newBadge + " " + correctedMark + " " + (scoreEl ? scoreEl.outerHTML : "");

    // 提示显示在卡片内（纠正表单上方），而非顶部日志区
    const fbBox = candidateEl.querySelector(".feedback-box");
    const candidateName = nameEl ? nameEl.textContent.trim() : "该候选人";
    const fbResult = document.createElement("div");
    fbResult.className = "fb-result";
    fbResult.innerHTML = `✓ 已纠正：${escapeHtml(candidateName)} → ${escapeHtml(CLASS_LABELS[fbClass] || fbClass)}${reason ? `（原因：${escapeHtml(reason)}）` : ""}`;
    if (fbBox) fbBox.before(fbResult);
    // 收起到期自动消失（保留 6 秒后淡出）
    setTimeout(() => { fbResult.style.opacity = "0"; }, 6000);

    loadRules(); // 刷新规则面板（待总结数量变化）
  } catch (e) {
    // 失败提示显示在卡片内
    const fbBox = candidateEl.querySelector(".feedback-box");
    const fbResult = document.createElement("div");
    fbResult.className = "fb-result fb-result-err";
    fbResult.textContent = `提交纠正失败：${e.message}`;
    if (fbBox) fbBox.before(fbResult);
    setTimeout(() => { fbResult.style.opacity = "0"; }, 6000);
  }
}

// ---------------- 筛选规则面板 ----------------
async function loadRules() {
  const box = $("rules-box");
  const meta = $("rules-meta");
  try {
    const res = await fetch(`${API}/rules`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "加载失败");

    const versionInfo = data.version > 0 ? `v${data.version}` : "尚未总结";
    meta.textContent = versionInfo +
      (data.pending_feedback_count > 0 ? ` · ${data.pending_feedback_count} 条新反馈待总结` : "");

    if (data.version > 0) {
      const items = (data.rules || []).map((r) => `<li>${escapeHtml(r)}</li>`).join("");
      const updated = data.updated_at ? `（更新于 ${escapeHtml(data.updated_at)}）` : "";
      const summary = data.summary ? `<div class="rules-summary">${escapeHtml(data.summary)}</div>` : "";
      box.innerHTML = `<ul class="rules-list">${items}</ul>${summary}${updated}`;
    } else {
      box.innerHTML = data.feedback_total > 0
        ? "尚未总结规则。已收集 " + data.feedback_total + " 条反馈，可点击下方按钮总结。"
        : "尚未总结筛选规则";
    }
  } catch (e) {
    box.innerHTML = `加载失败：${escapeHtml(e.message)}`;
  }
}

async function summarizeRules() {
  const log = $("rules-log");
  const btn = $("summarize-rules-btn");
  btn.disabled = true;
  log.innerHTML = '<span class="ok">正在用 AI 总结纠正规律，请稍候…</span>';
  try {
    const res = await fetch(`${API}/rules/summarize`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "总结失败");
    // 迭代引导：提示换批验证
    log.innerHTML =
      `<span class="ok">✓ 规则已总结为 v${data.version}，共 ${data.rules.length} 条规则</span>\n` +
      `<span class="ok">💡 建议：换一批简历重新筛选，或点击「对比验证」查看新旧规则的效果差异。</span>`;
    loadRules();
  } catch (e) {
    log.innerHTML = `<span class="err">${escapeHtml(e.message)}</span>`;
  } finally {
    btn.disabled = false;
  }
}

// ---------------- 规则版本对比验证 ----------------
async function compareRules() {
  const box = $("compare-box");
  const log = $("rules-log");
  const btn = $("compare-rules-btn");
  box.hidden = false;
  box.innerHTML = '<span class="muted">正在对比新旧规则（需两轮 AI 分析，可能耗时较长）…</span>';

  // 对比对象：最近一次自动筛选结果里的候选人
  let body = null;
  try {
    const res = await fetch(`${API}/auto-screen/results?limit=1`);
    const data = await res.json();
    const run = data.runs && data.runs[0];
    if (run && run.candidates && run.candidates.length) {
      body = { resume_ids: run.candidates.map((c) => c.id) };
    }
  } catch (e) { /* ignore */ }
  if (!body) {
    box.innerHTML = '<span class="err">请先运行一次自动筛选，再对比新旧规则</span>';
    return;
  }

  btn.disabled = true;
  try {
    const res = await fetch(`${API}/rules/compare`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "对比失败");

    if (data.compared_count === 0) {
      box.innerHTML = `<span class="err">${escapeHtml(data.note || "没有可对比的候选人")}</span>`;
      return;
    }

    // 分布对比表
    const rows = [["interview", "值得面试"], ["review", "HR审核"], ["reject", "直接淘汰"]]
      .map(([key, label]) => {
        const b = (data.distributions.base || {})[key] || 0;
        const c = (data.distributions.current || {})[key] || 0;
        const diff = c - b;
        const diffHtml = diff === 0 ? "" : (diff > 0 ? ` <span class="up">▲${diff}</span>` : ` <span class="down">▼${Math.abs(diff)}</span>`);
        return `<tr><td>${label}</td><td>${b}</td><td>${c}${diffHtml}</td></tr>`;
      }).join("");

    // 分类变化的候选人
    const changed = (data.deltas || []).filter((d) => d.changed);
    const deltaHtml = changed.length
      ? `<div class="compare-deltas"><b>分类变化的候选人（${changed.length}）：</b><ul>` +
        changed.map((d) => {
          const from = CLASS_LABELS[d.base_classification] || d.base_classification;
          const to = CLASS_LABELS[d.current_classification] || d.current_classification;
          return `<li>${escapeHtml(d.name || d.resume_id)}：<span class="badge class-badge-${d.base_classification}">${from}</span> → <span class="badge class-badge-${d.current_classification}">${to}</span></li>`;
        }).join("") + "</ul></div>"
      : '<div class="muted">本次对比无分类变化</div>';

    box.innerHTML = `
      <h3>规则版本对比：v${data.base_version} → v${data.current_version}</h3>
      <table class="compare-table">
        <tr><th>分类</th><th>上一版本 (v${data.base_version})</th><th>当前版本 (v${data.current_version})</th></tr>
        ${rows}
      </table>
      ${deltaHtml}
      <div class="muted">${escapeHtml(data.note)}</div>`;
    log.innerHTML = `<span class="ok">✓ 对比完成：${data.changed_count} 位候选人分类发生变化</span>`;
  } catch (e) {
    box.innerHTML = `<span class="err">${escapeHtml(e.message)}</span>`;
  } finally {
    btn.disabled = false;
  }
}

// ---------------- 筛选面板（统一流水线，结果按来源分组） ----------------
async function loadScreenPanel() {
  // 默认岗位要求（仅当用户未在编辑时回填，避免覆盖输入）
  const editing = document.activeElement === $("default-query-text");
  if (!editing) {
    try {
      const res = await fetch(`${API}/auto-screen/query`);
      const data = await res.json();
      if (res.ok && data.query_text) {
        if ($("default-query-text").value !== data.query_text) {
          $("default-query-text").value = data.query_text;
        }
      }
    } catch (e) { /* 忽略 */ }
  }

  // 状态行
  try {
    const res = await fetch(`${API}/auto-screen/status`);
    const st = await res.json();
    const statusEl = $("screen-status");
    if (!res.ok) throw new Error(st.detail || "加载失败");
    let statusText = st.running ? "⏳ 筛选中…" : "";
    if (!st.default_query_set) {
      statusText = "未设置默认岗位要求";
    } else if (st.last_run && st.last_run.status === "completed") {
      const d = st.last_run.distributions || {};
      statusText = `上次筛选：${escapeHtml(st.last_run.screened_count)} 份 · ` +
        `🟢${d.interview || 0} 🟠${d.review || 0} 🔴${d.reject || 0}`;
    }
    statusEl.textContent = statusText;
  } catch (e) { /* 忽略 */ }

  // 结果：聚合最近几次 run 的候选人（按 resume_id 去重，保留最新），按来源分组展示
  try {
    const res = await fetch(`${API}/auto-screen/results?limit=20`);
    const data = await res.json();
    const byId = {};
    for (const run of (data.runs || [])) {
      for (const c of (run.candidates || [])) {
        if (c && c.id) {
          byId[c.id] = { ...c, screened_at: run.finished_at || "" };
        }
      }
    }
    const all = Object.values(byId);
    const manual = all.filter((c) => (c.source || "manual") !== "email");
    const email = all.filter((c) => (c.source || "manual") === "email");

    const renderGroup = (items, boxId, summaryId, emptyText) => {
      const box = $(boxId);
      const summary = $(summaryId);
      if (!items.length) {
        box.innerHTML = emptyText
          ? `<div class="empty">${emptyText}</div>`
          : "";
        summary.innerHTML = "";
        return;
      }
      const clsCount = (cls) => items.filter((c) => c.classification === cls).length;
      summary.innerHTML = `共 ${items.length} 人 · 🟢${clsCount("interview")} 🟠${clsCount("review")} 🔴${clsCount("reject")}`;
      // 候选人列表默认折叠，点击标题展开，避免全部卡片占满屏幕
      box.innerHTML = `<details class="results-collapse">
        <summary class="auto-run-title">👥 候选人列表（${items.length}）</summary>
        ${items.map(candidateCardHtml).join("")}
      </details>`;
    };
    renderGroup(manual, "manual-screen-results", "manual-screen-summary", "暂无手动上传的筛选结果");
    renderGroup(email, "auto-screen-results", "auto-screen-summary", "暂无邮箱抓取的筛选结果");
  } catch (e) { /* 忽略 */ }
}

async function runScreen() {
  const log = $("screen-log");
  log.innerHTML = '<span class="wait">▶ 智能体正在筛选所有简历（手动 + 邮箱，可能需要几分钟）…</span>';
  try {
    const res = await fetch(`${API}/screen/run`, { method: "POST" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "触发失败");
    if (data.status === "already_running") {
      log.innerHTML = '<span class="err">筛选正在运行中，请稍后查看结果</span>';
      return;
    }
    log.innerHTML = `<span class="ok">✓ ${escapeHtml(data.message || "筛选完成")}</span>`;
    loadScreenPanel();
    loadResumeList(); // 刷新简历列表（抓取的新简历会出现在列表）
  } catch (e) {
    log.innerHTML = `<span class="err">${escapeHtml(e.message)}</span>`;
  }
}

async function saveDefaultQuery() {
  const log = $("default-query-log");
  const text = $("default-query-text").value.trim();
  if (!text) {
    log.innerHTML = '<span class="err">请输入默认岗位要求</span>';
    return;
  }
  try {
    const res = await fetch(`${API}/auto-screen/query`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query_text: text }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "保存失败");
    log.innerHTML = '<span class="ok">✓ 默认岗位要求已保存，邮箱抓取新简历后会自动筛选</span>';
    loadScreenPanel();
  } catch (e) {
    log.innerHTML = `<span class="err">保存失败：${escapeHtml(e.message)}</span>`;
  }
}

// ---------------- 邮箱配置（界面切换账号） ----------------
// 常见邮箱 IMAP 预设（选择类型自动填服务器/端口，用户只需填账号+授权码）
const EMAIL_PROVIDERS = {
  qq:      { host: "imap.qq.com",         port: 993 },
  "163":   { host: "imap.163.com",        port: 993 },
  "126":   { host: "imap.126.com",        port: 993 },
  gmail:   { host: "imap.gmail.com",      port: 993 },
  outlook: { host: "outlook.office365.com", port: 993 },
  custom:  { host: "", port: 993 },
};

function applyEmailProvider() {
  const type = $("email-type").value;
  const provider = EMAIL_PROVIDERS[type] || EMAIL_PROVIDERS.custom;
  $("email-host").value = provider.host;
  $("email-port").value = provider.port;
  // 自定义类型时显示服务器/端口输入框
  $("email-custom-fields").hidden = type !== "custom";
}

async function loadEmailConfig() {
  try {
    const res = await fetch(`${API}/email-config`);
    const cfg = await res.json();
    if (!res.ok) throw new Error(cfg.detail || "加载失败");
    // 根据已存 host 回显邮箱类型
    const type = Object.keys(EMAIL_PROVIDERS).find(
      (k) => EMAIL_PROVIDERS[k].host === cfg.host) || "custom";
    $("email-type").value = type;
    applyEmailProvider();
    $("email-host").value = cfg.host || "";
    $("email-user").value = cfg.user || "";
    $("email-port").value = cfg.port || 993;
    $("email-password").value = cfg.password && cfg.password !== "******" ? cfg.password : "";
    $("email-password").placeholder = cfg.password === "******" ? "已保存（留空保持不变）" : "授权码";
  } catch (e) { /* 忽略 */ }
}

// 取当前邮箱配置（host 兜底：类型为预设时用预设值，避免异步回填未完成时提交空 host）
function currentEmailConfig() {
  const type = $("email-type").value;
  const preset = EMAIL_PROVIDERS[type] || EMAIL_PROVIDERS.custom;
  return {
    enabled: true,
    host: $("email-host").value.trim() || preset.host || "",
    user: $("email-user").value.trim(),
    password: $("email-password").value.trim(),
    port: parseInt($("email-port").value) || preset.port || 993,
  };
}

async function saveEmailConfig() {
  const log = $("email-config-log");
  try {
    const res = await fetch(`${API}/email-config`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(currentEmailConfig()),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "保存失败");
    log.innerHTML = '<span class="ok">✓ 邮箱配置已保存</span>';
    loadEmailConfig();
  } catch (e) {
    log.innerHTML = `<span class="err">保存失败：${escapeHtml(e.message)}</span>`;
  }
}

async function testEmailConfig() {
  const log = $("email-config-log");
  log.innerHTML = '<span class="wait">正在测试邮箱连接…</span>';
  try {
    // 先用输入框内容保存（测试 = 测你刚填的配置，而不是旧配置）
    const saveRes = await fetch(`${API}/email-config`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(currentEmailConfig()),
    });
    const saveData = await saveRes.json();
    if (!saveRes.ok) throw new Error(saveData.detail || "保存失败");

    // 再测试连接
    const res = await fetch(`${API}/email-config/test`, { method: "POST" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "测试失败");
    log.innerHTML = `<span class="ok">✓ ${escapeHtml(data.message)}</span>`;
  } catch (e) {
    log.innerHTML = `<span class="err">${escapeHtml(e.message)}</span>`;
  }
}

// ---------------- 事件绑定 ----------------
$("upload-btn").addEventListener("click", uploadResumes);
$("refresh-btn").addEventListener("click", loadResumeList);
$("summarize-rules-btn").addEventListener("click", summarizeRules);
$("compare-rules-btn").addEventListener("click", compareRules);
$("save-default-query-btn").addEventListener("click", saveDefaultQuery);
$("run-screen-btn").addEventListener("click", runScreen);
$("email-save-btn").addEventListener("click", saveEmailConfig);
$("email-test-btn").addEventListener("click", testEmailConfig);
$("email-type").addEventListener("change", applyEmailProvider);

// 简历列表：点击展开详情 / 删除按钮 / 选择框（事件委托）
$("resume-list").addEventListener("click", (e) => {
  bindResumeListClick(e);
});

function bindResumeListClick(e) {
  const selCb = e.target.closest('input[data-sel]');
  if (selCb) {
    e.stopPropagation();
    resetSelectionUI();
    return;
  }
  const delBtn = e.target.closest(".del-btn");
  if (delBtn) {
    e.stopPropagation();
    const li = delBtn.closest("li");
    deleteResume(li.dataset.resumeId, delBtn.dataset.name);
    return;
  }
  const li = e.target.closest("li[data-resume-id]");
  if (li) toggleResumeDetail(li, li.dataset.resumeId);
}

// 全选 / 批量删除（手动 + 邮箱两个列表共用）
$("sel-all-cb").addEventListener("change", (e) => {
  document.querySelectorAll('input[data-sel]').forEach((cb) => {
    cb.checked = e.target.checked;
  });
  resetSelectionUI();
});
$("batch-del-btn").addEventListener("click", batchDeleteResumes);

// 结果卡片内的"提交纠正"按钮（事件委托）
// 注意：候选卡片会出现在多个容器（手动筛选结果/自动筛选结果/工作台），
// 且早期版本曾用 $("results")（该元素已不存在）导致脚本中断，
// 故改为 document 级委托，任何容器内的纠正按钮都能生效。
document.addEventListener("click", (e) => {
  const submitBtn = e.target.closest('[data-fb="submit"]');
  if (!submitBtn) return;
  const candidateEl = submitBtn.closest(".candidate");
  if (!candidateEl) return;
  const fbClass = candidateEl.querySelector('[data-fb="class"]').value;
  const reason = candidateEl.querySelector('[data-fb="reason"]').value.trim();
  submitFeedback(candidateEl, fbClass, reason);
});

checkHealth();
loadResumeList();
loadRules();
restoreState();
loadScreenPanel();
loadEmailConfig();
setInterval(checkHealth, 30000);
setInterval(() => { loadScreenPanel(); loadResumeList(); }, 60000); // 每分钟刷新筛选面板 + 简历列表
