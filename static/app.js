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
    // 按来源拆分到两个独立列表
    const emailList = data.resumes.filter((r) => (r.source || "manual") === "email");
    const manualList = data.resumes.filter((r) => (r.source || "manual") !== "email");

    $("manual-count").textContent = `(${manualList.length})`;
    $("email-count").textContent = `(${emailList.length})`;

    const renderItems = (items) => items.map((r) => {
      // 技能标签预览（最多 5 个）
      const skillTags = (r.skills || []).slice(0, 5)
        .map((s) => `<span class="tag">${escapeHtml(s)}</span>`).join("");
      const more = (r.skills || []).length > 5
        ? `<span class="muted">+${(r.skills || []).length - 5}</span>` : "";
      // 解析状态徽章
      const statusBadge = r.status === "ready"
        ? '<span class="badge badge-ok">已就绪</span>'
        : r.status === "error"
          ? '<span class="badge badge-err">解析失败</span>'
          : '<span class="badge badge-unknown">解析中</span>';
      // 文本质量警告徽章（扫描件等低质量 PDF）
      const warnBadge = r.warning
        ? '<span class="badge badge-err" title="' + escapeHtml(r.warning) + '">文本质量差</span>'
        : "";
      // 预分类徽章（入库即通用评估：不针对特定岗位，仅按独立负责/真实用户/可量化结果判断）
      const pre = r.preclassification || null;
      const preBadge = pre
        ? `<span class="badge class-badge-${pre.classification || "review"}" title="通用评估（不针对特定岗位）：${escapeHtml(pre.reason || "")}">通用评估:${CLASS_LABELS[pre.classification] || pre.classification}</span>`
        : "";
      // 上传时间（ISO 字符串 → 本地时间显示）
      let timeStr = "";
      if (r.created_at) {
        const t = new Date(r.created_at);
        if (!isNaN(t)) timeStr = t.toLocaleString("zh-CN", { hour12: false }).replace(/\//g, "-");
      }
      return `
      <li data-resume-id="${escapeHtml(r.resume_id)}">
        <div class="fn">
          <label class="sel-cb" title="选择删除"><input type="checkbox" data-sel="${escapeHtml(r.resume_id)}" /></label>
          ${escapeHtml(r.name || r.filename || "(未命名)")}
          <span>${statusBadge} ${preBadge} ${warnBadge}</span>
          <button class="btn-mini del-btn" title="删除简历" data-name="${escapeHtml(r.name || r.filename || "")}">🗑 删除</button>
        </div>
        <div class="rid">${escapeHtml(r.filename || "")}${timeStr ? ` · 🕐 ${escapeHtml(timeStr)}` : ""} · ${escapeHtml(r.resume_id.slice(0, 8))}（点击查看详情）</div>
        ${skillTags ? `<div class="skills">${skillTags}${more}</div>` : ""}
        <div class="resume-detail" hidden></div>
      </li>`;
    }).join("");

    $("manual-resume-list").innerHTML = manualList.length
      ? renderItems(manualList)
      : '<li class="empty">暂无手动上传的简历</li>';
    $("email-resume-list").innerHTML = emailList.length
      ? renderItems(emailList)
      : '<li class="empty">暂无邮箱抓取的简历</li>';
    resetSelectionUI();
  } catch (e) {
    $("manual-resume-list").innerHTML = `<li class="empty">加载失败：${escapeHtml(e.message)}</li>`;
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

// ---------------- 批量删除 ----------------
function resetSelectionUI() {
  const cbs = document.querySelectorAll('#manual-resume-list input[data-sel]');
  const checked = document.querySelectorAll('#manual-resume-list input[data-sel]:checked').length;
  $("sel-count").textContent = checked > 0 ? `已选 ${checked} 份` : "";
  $("batch-del-btn").disabled = checked === 0;
  $("sel-all-cb").checked = cbs.length > 0 && checked === cbs.length;
}

async function batchDeleteResumes() {
  const ids = Array.from(document.querySelectorAll('#manual-resume-list input[data-sel]:checked'))
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
    <div class="candidate" data-resume-id="${escapeHtml(c.id)}" data-cls="${escapeHtml(cls)}">
      <div class="candidate-head">
        <div><span class="rank">${escapeHtml(c.rank)}</span><span class="name">${escapeHtml(c.name || "(未命名)")}</span></div>
        <div class="score">
          ${clsBadge} ${correctedMark}
          <span class="score-num">${escapeHtml(scorePercent)}%</span>
        </div>
      </div>
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
    </div>`;
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

// ---------------- 自动筛选面板 ----------------
async function loadAutoScreenPanel() {
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
    const statusEl = $("auto-screen-status");
    if (!res.ok) throw new Error(st.detail || "加载失败");
    let statusText = st.running ? "⏳ 自动筛选中…" : "";
    if (!st.default_query_set) {
      statusText = "未设置默认岗位要求";
    } else if (st.last_run && st.last_run.status === "completed") {
      const d = st.last_run.distributions || {};
      statusText = `上次自动筛选：${escapeHtml(st.last_run.screened_count)} 份 · ` +
        `🟢${d.interview || 0} 🟠${d.review || 0} 🔴${d.reject || 0}`;
    }
    statusEl.textContent = statusText;
  } catch (e) { /* 忽略 */ }

  // 最近一次结果
  try {
    const res = await fetch(`${API}/auto-screen/results?limit=1`);
    const data = await res.json();
    const box = $("auto-screen-results");
    const summary = $("auto-screen-summary");
    if (!res.ok || !data.runs || data.runs.length === 0) {
      box.innerHTML = "";
      summary.innerHTML = "";
      return;
    }
    const run = data.runs[0];
    const d = run.distributions || {};
    summary.innerHTML = `最近一次：${escapeHtml(run.status === "completed" ? `筛了 ${run.screened_count} 份（🟢${d.interview || 0} 🟠${d.review || 0} 🔴${d.reject || 0}）` : `状态：${escapeHtml(run.status)}`)} · 规则 v${run.rules_version_used || 0} · ${escapeHtml((run.finished_at || "").replace("T", " ").slice(0, 16))}`;
    if (run.candidates && run.candidates.length) {
      box.innerHTML = `<details open>
        <summary class="auto-run-title">候选人列表（${run.candidates.length}）</summary>
        ${run.candidates.map(candidateCardHtml).join("")}
      </details>`;
    } else {
      box.innerHTML = "";
    }
  } catch (e) { /* 忽略 */ }
}

async function saveDefaultQuery() {
  const log = $("auto-screen-log");
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
    loadAutoScreenPanel();
  } catch (e) {
    log.innerHTML = `<span class="err">保存失败：${escapeHtml(e.message)}</span>`;
  }
}

async function runAutoScreen() {
  const log = $("auto-screen-log");
  log.innerHTML = '<span class="wait">▶ 一键工作流：抓取邮箱 → 自动筛选（可能需要几分钟）…</span>';
  try {
    const res = await fetch(`${API}/workflow/run`, { method: "POST" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "触发失败");
    if (data.status === "already_running") {
      log.innerHTML = '<span class="err">自动筛选正在运行中，请稍后查看结果</span>';
      return;
    }
    log.innerHTML = `<span class="ok">✓ ${escapeHtml(data.message || "工作流完成")}（筛选 ${data.screened_count} 份）</span>`;
    loadAutoScreenPanel();
    loadWorkbench();
  } catch (e) {
    log.innerHTML = `<span class="err">${escapeHtml(e.message)}</span>`;
  }
}

// ---------------- 手动筛选工作流（独立于邮箱自动筛选） ----------------
async function runManualScreen() {
  const log = $("upload-log");
  log.innerHTML = '<span class="wait">▶ 正在筛选手动上传的简历（可能需要几分钟）…</span>';
  saveLogs();
  try {
    const res = await fetch(`${API}/manual-screen/run`, { method: "POST" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "触发失败");
    if (data.status === "already_running") {
      log.innerHTML = '<span class="err">筛选正在运行中，请稍后查看结果</span>';
      saveLogs();
      return;
    }
    log.innerHTML = `<span class="ok">✓ ${escapeHtml(data.message || "手动筛选完成")}</span>`;
    saveLogs();
    loadManualScreenPanel();
    loadWorkbench();
  } catch (e) {
    log.innerHTML = `<span class="err">${escapeHtml(e.message)}</span>`;
    saveLogs();
  }
}

async function loadManualScreenPanel() {
  // 从自动筛选结果中筛选 trigger=manual_screen 的最近一次
  try {
    const res = await fetch(`${API}/auto-screen/results?limit=20`);
    const data = await res.json();
    const runs = (data.runs || []).filter((r) => r.trigger === "manual_screen");
    const box = $("manual-screen-results");
    const summary = $("manual-screen-summary");
    $("manual-screen-status").textContent = "";
    if (!runs.length) {
      box.innerHTML = "";
      summary.innerHTML = "";
      return;
    }
    const run = runs[0];
    const d = run.distributions || {};
    summary.innerHTML = `最近一次手动筛选：${run.screened_count || 0} 份（🟢${d.interview || 0} 🟠${d.review || 0} 🔴${d.reject || 0}）· ${escapeHtml((run.finished_at || "").replace("T", " ").slice(0, 16))}`;
    box.innerHTML = run.candidates && run.candidates.length
      ? run.candidates.map(candidateCardHtml).join("")
      : '<div class="empty">无筛选结果（先上传简历，或点「开始筛选手动简历」）</div>';
  } catch (e) { /* 忽略 */ }
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

// ---------------- 候选人处理工作台 ----------------
const WB_LABELS = {
  pending: "待处理",
  interview: "约面试",
  review: "待核实",
  archived: "已归档",
};
const WB_CLASS = {
  pending: "badge-unknown",
  interview: "badge-ok",
  review: "badge class-badge-review",
  archived: "badge-err",
};

async function loadWorkbench() {
  try {
    const res = await fetch(`${API}/workbench/candidates`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "加载失败");

    $("workbench-meta").textContent = `(共 ${data.total} 人)`;
    $("workbench-pending").textContent = data.pending_count > 0
      ? `🔴 ${data.pending_count} 人待处理` : "";

    const list = $("workbench-list");
    if (!data.candidates || data.candidates.length === 0) {
      list.innerHTML = '<div class="empty">暂无已筛选候选人（运行自动筛选后出现）</div>';
      return;
    }
    list.innerHTML = data.candidates.map((c) => `
      <div class="workbench-card" data-wb-id="${escapeHtml(c.resume_id)}">
        <div class="wb-head">
          <span class="badge ${WB_CLASS[c.work_status] || "badge-unknown"}">${WB_LABELS[c.work_status] || c.work_status}</span>
          ${c.corrected_by_human ? '<span class="badge badge-unknown">已人工纠正</span>' : ""}
        </div>
        ${candidateCardHtml({
          ...c,
          id: c.resume_id,
          classification: c.classification,
          classification_reason: c.classification_reason,
          overall_score: c.overall_score,
          skills: c.skills,
          analysis: c.analysis,
          assessment: {},
          corrected_by_human: c.corrected_by_human,
        })}
        <div class="wb-actions">
          <button data-wb-action="interview" class="btn wb-interview">🟢 约面试</button>
          <button data-wb-action="review" class="btn wb-review">🟠 待核实</button>
          <button data-wb-action="archived" class="btn wb-archived">🔴 归档淘汰</button>
        </div>
      </div>`).join("");
  } catch (e) {
    $("workbench-list").innerHTML = `<div class="empty">加载失败：${escapeHtml(e.message)}</div>`;
  }
}

async function updateWorkbenchStatus(resumeId, status) {
  try {
    const res = await fetch(`${API}/workbench/candidates/${resumeId}/status`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "更新失败");
    loadWorkbench(); // 刷新工作台
  } catch (e) {
    alert("更新失败：" + e.message);
  }
}

function exportInterviewList() {
  fetch(`${API}/workbench/export`)
    .then((res) => {
      if (!res.ok) throw new Error("没有可导出的候选人");
      return res.blob();
    })
    .then((blob) => {
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "面试名单.csv";
      a.click();
      URL.revokeObjectURL(url);
    })
    .catch((e) => alert("导出失败：" + e.message));
}

// ---------------- 事件绑定 ----------------
$("upload-btn").addEventListener("click", uploadResumes);
$("refresh-btn").addEventListener("click", loadResumeList);
$("summarize-rules-btn").addEventListener("click", summarizeRules);
$("compare-rules-btn").addEventListener("click", compareRules);
$("save-default-query-btn").addEventListener("click", saveDefaultQuery);
$("run-auto-screen-btn").addEventListener("click", runAutoScreen);
$("export-interview-btn").addEventListener("click", exportInterviewList);
$("workbench-refresh-btn").addEventListener("click", loadWorkbench);
$("email-save-btn").addEventListener("click", saveEmailConfig);
$("email-test-btn").addEventListener("click", testEmailConfig);
$("email-type").addEventListener("change", applyEmailProvider);
$("run-manual-screen-btn").addEventListener("click", runManualScreen);

// 工作台候选人操作按钮（事件委托）
$("workbench-list").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-wb-action]");
  if (!btn) return;
  const card = btn.closest(".workbench-card");
  if (card) updateWorkbenchStatus(card.dataset.wbId, btn.dataset.wbAction);
});

// 简历列表：点击展开详情 / 删除按钮 / 选择框（事件委托）
$("manual-resume-list").addEventListener("click", (e) => {
  bindResumeListClick(e);
});
$("email-resume-list").addEventListener("click", (e) => {
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

// 全选 / 批量删除
$("sel-all-cb").addEventListener("change", (e) => {
  document.querySelectorAll('#manual-resume-list input[data-sel]').forEach((cb) => {
    cb.checked = e.target.checked;
  });
  resetSelectionUI();
});
$("batch-del-btn").addEventListener("click", batchDeleteResumes);

// 结果卡片内的"提交纠正"按钮（事件委托）
$("results").addEventListener("click", (e) => {
  const submitBtn = e.target.closest('[data-fb="submit"]');
  if (!submitBtn) return;
  const candidateEl = submitBtn.closest(".candidate");
  const fbClass = candidateEl.querySelector('[data-fb="class"]').value;
  const reason = candidateEl.querySelector('[data-fb="reason"]').value.trim();
  submitFeedback(candidateEl, fbClass, reason);
});

checkHealth();
loadResumeList();
loadRules();
restoreState();
loadAutoScreenPanel();
loadManualScreenPanel();
loadWorkbench();
loadEmailConfig();
setInterval(checkHealth, 30000);
setInterval(() => { loadAutoScreenPanel(); loadWorkbench(); }, 60000); // 每分钟刷新自动筛选 + 工作台
