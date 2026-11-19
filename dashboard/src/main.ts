// jobpulse dashboard - vanilla TypeScript, zero runtime dependencies.
// Compiled by `npm run build` to src/jobpulse/web/app.js, which the publish step uploads.

interface DemandRow {
  skill: string; category: string; share_30d?: number; delta_7d?: number | null; delta_30d?: number | null;
  share_7d?: number; count_30d?: number; postings_30d?: number;
}
interface Posting {
  company: string; title: string; url: string; locations: string[]; date_posted: string; has_jd: boolean;
  required: string[]; nice: string[]; method: string | null; flags: string[]; grad_window: string | null;
}
interface Trend { skill: string; github_repos: number; github_stars: number; hn_stories: number; hn_points: number; score: number }
interface Gap { skill: string; share_30d: number; count_30d: number; trend_score: number; priority: number }
interface Idea {
  title: string; why: string; scope: string; stack: string[]; success_metrics: string[]; target_skills: string[];
  date: string; source: string;
}
interface Resume {
  company: string; title: string; url: string; pdf: string; coverage_before: number; coverage_after: number;
  projects: string[]; fits_one_page: boolean;
}
interface Run { date: string; status: string; metrics: Record<string, number> }
interface RoadmapItem { status: string; label: string; title: string; desc: string }
interface Dashboard {
  run_date: string; generated_at: string; persona_note: string; postings: Posting[]; demand: DemandRow[];
  series: { dates: string[]; postings: number[]; skills: Record<string, number[]> };
  trends: Trend[]; gaps: Gap[]; ideas_today: Idea[]; ideas_recent: Idea[]; resumes: Resume[]; runs: Run[];
  roadmap: RoadmapItem[]; linkedin_draft: string;
}

const $ = (id: string): HTMLElement => {
  const el = document.getElementById(id);
  if (!el) throw new Error(`missing #${id}`);
  return el;
};
const esc = (s: unknown): string =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c] ?? c);
const pct = (x: number | undefined, digits = 0): string => `${((x ?? 0) * 100).toFixed(digits)}%`;
const delta = (d: number | null | undefined): string => {
  if (d === null || d === undefined) return '<span class="muted">–</span>';
  const pp = d * 100;
  const cls = pp > 0.05 ? "up" : pp < -0.05 ? "down" : "muted";
  return `<span class="${cls}">${pp > 0 ? "+" : ""}${pp.toFixed(1)} pp</span>`;
};

// ---------------------------------------------------------------- tooltip
const tip = (): HTMLElement => $("tooltip");
function showTip(html: string, x: number, y: number): void {
  const t = tip();
  t.innerHTML = html;
  t.hidden = false;
  const w = t.offsetWidth;
  t.style.left = `${Math.min(x + 14, window.innerWidth - w - 8)}px`;
  t.style.top = `${y + 14}px`;
}
const hideTip = (): void => { tip().hidden = true; };

// ---------------------------------------------------------------- sections
function renderKpis(d: Dashboard): void {
  const last = d.runs.find((r) => r.date === d.run_date);
  const m = last?.metrics ?? {};
  const withJd = d.postings.filter((p) => p.has_jd).length;
  const totalPostings = d.series.postings.reduce((a, b) => a + b, 0);
  const kpis: [string, string][] = [
    [String(d.postings.length), "new postings today"],
    [String(withJd), "with full JD text"],
    [String(totalPostings), "postings analyzed, 30 days"],
    [String(m["extract.llm_calls"] ?? 0), "LLM calls today"],
    [pct(m["extract.cache_hit_rate"]), "extraction cache hits"],
    [String(d.resumes.length), "tailored resumes today"],
  ];
  $("kpis").innerHTML = kpis.map(([v, l]) => `<div class="kpi"><div class="v">${esc(v)}</div><div class="l">${esc(l)}</div></div>`).join("");
  $("run-date").textContent = `Run date: ${d.run_date}.`;
  $("generated").textContent = d.generated_at;
}

function renderDemandBars(d: Dashboard): void {
  const rows = d.demand.slice(0, 15);
  const max = Math.max(...rows.map((r) => r.share_30d ?? 0), 0.01);
  $("demand-bars").innerHTML = rows.map((r) => `
    <div class="bar-row" data-skill="${esc(r.skill)}">
      <span>${esc(r.skill)}</span>
      <span class="bar-track"><span class="bar-fill" style="width:${(100 * (r.share_30d ?? 0)) / max}%"></span></span>
      <span class="bar-val">${pct(r.share_30d)} · ${delta(r.delta_7d)}</span>
    </div>`).join("") || '<p class="muted">No extracted postings yet.</p>';
  $("demand-bars").querySelectorAll<HTMLElement>(".bar-row").forEach((el) => {
    const r = rows.find((x) => x.skill === el.dataset.skill);
    if (!r) return;
    el.addEventListener("mousemove", (e) => showTip(
      `<b>${esc(r.skill)}</b> (${esc(r.category)})<br>${r.count_30d ?? 0} of ${r.postings_30d ?? 0} postings, 30 days (${pct(r.share_30d, 1)})<br>` +
      `7-day delta: ${delta(r.delta_7d)} · 30-day delta: ${delta(r.delta_30d)}`, e.clientX, e.clientY));
    el.addEventListener("mouseleave", hideTip);
  });
}

function rolling(counts: number[], totals: number[], window = 7): (number | null)[] {
  return counts.map((_, i) => {
    let c = 0, n = 0;
    for (let j = Math.max(0, i - window + 1); j <= i; j++) { c += counts[j]; n += totals[j]; }
    return n > 0 ? c / n : null;
  });
}

function renderDemandChart(d: Dashboard): void {
  const skills = Object.keys(d.series.skills).slice(0, 4);
  const colors = ["var(--series-1)", "var(--series-2)", "var(--series-3)", "var(--series-4)"];
  const lines = skills.map((s) => rolling(d.series.skills[s], d.series.postings));
  const W = 520, H = 240, L = 36, R = 92, T = 10, B = 26;
  const n = d.series.dates.length;
  const ymax = Math.max(0.1, ...lines.flat().map((v) => v ?? 0));
  const x = (i: number): number => L + (i * (W - L - R)) / Math.max(1, n - 1);
  const y = (v: number): number => T + (1 - v / ymax) * (H - T - B);
  const ticks = [0, ymax / 2, ymax];
  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="7-day rolling demand share for ${esc(skills.join(", "))}">`;
  for (const t of ticks) {
    svg += `<line x1="${L}" x2="${W - R}" y1="${y(t)}" y2="${y(t)}" stroke="var(--grid)"/>`;
    svg += `<text x="${L - 6}" y="${y(t) + 4}" text-anchor="end">${Math.round(t * 100)}%</text>`;
  }
  [0, Math.floor((n - 1) / 2), n - 1].forEach((i) => {
    svg += `<text x="${x(i)}" y="${H - 6}" text-anchor="middle">${esc(d.series.dates[i].slice(5))}</text>`;
  });
  const labelYs: number[] = [];
  lines.forEach((vals, k) => {
    let path = "";
    vals.forEach((v, i) => { if (v !== null) path += `${path ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`; });
    svg += `<path d="${path}" fill="none" stroke="${colors[k]}" stroke-width="2" stroke-linejoin="round"/>`;
    const lastV = [...vals].reverse().find((v) => v !== null) ?? 0;
    let ly = y(lastV) + 4;
    for (const prev of labelYs) if (Math.abs(prev - ly) < 12) ly = prev + 12; // avoid label collisions
    labelYs.push(ly);
    svg += `<text class="lbl" x="${W - R + 6}" y="${ly}">${esc(skills[k])}</text>`;
  });
  svg += `<line id="xhair" x1="0" x2="0" y1="${T}" y2="${H - B}" stroke="var(--muted)" stroke-dasharray="3 3" visibility="hidden"/>`;
  svg += `<rect id="hit" x="${L}" y="${T}" width="${W - L - R}" height="${H - T - B}" fill="transparent"/></svg>`;
  const legend = skills.map((s, k) => `<span><i style="background:${colors[k]}"></i>${esc(s)}</span>`).join("");
  const el = $("demand-chart");
  el.innerHTML = svg + `<div class="legend">${legend}</div>`;

  const svgEl = el.querySelector("svg") as SVGSVGElement;
  const hit = el.querySelector("#hit") as SVGRectElement;
  const xhair = el.querySelector("#xhair") as SVGLineElement;
  hit.addEventListener("mousemove", (e) => {
    const box = svgEl.getBoundingClientRect();
    const sx = ((e.clientX - box.left) / box.width) * W;
    const i = Math.max(0, Math.min(n - 1, Math.round(((sx - L) / (W - L - R)) * (n - 1))));
    xhair.setAttribute("x1", String(x(i)));
    xhair.setAttribute("x2", String(x(i)));
    xhair.setAttribute("visibility", "visible");
    const rowsHtml = skills.map((s, k) => `<span style="color:${colors[k]}">■</span> ${esc(s)}: ${lines[k][i] === null ? "–" : pct(lines[k][i] ?? 0, 1)}`).join("<br>");
    showTip(`<b>${esc(d.series.dates[i])}</b> (${d.series.postings[i]} postings that day)<br>${rowsHtml}`, e.clientX, e.clientY);
  });
  hit.addEventListener("mouseleave", () => { xhair.setAttribute("visibility", "hidden"); hideTip(); });

  // Table view (accessibility: the chart's numbers without color).
  $("demand-table").innerHTML = `<table class="tbl"><tr><th>Date</th><th class="num">Postings</th>${skills.map((s) => `<th class="num">${esc(s)}</th>`).join("")}</tr>` +
    d.series.dates.map((dt, i) => `<tr><td>${esc(dt)}</td><td class="num">${d.series.postings[i]}</td>${skills.map((s) => `<td class="num">${d.series.skills[s][i]}</td>`).join("")}</tr>`).join("") + "</table>";
}

function renderPostings(d: Dashboard): void {
  $("postings-count").textContent = String(d.postings.length);
  const tags = (xs: string[], cls: string): string => xs.map((s) => `<span class="tag ${cls}">${esc(s)}</span>`).join("");
  $("postings").innerHTML = "<tr><th>Company</th><th>Role</th><th>Required skills</th><th>Nice to have</th><th>Flags</th></tr>" +
    (d.postings.map((p) => `<tr>
      <td><b>${esc(p.company)}</b><br><span class="muted">${esc(p.locations.join(" · "))}</span></td>
      <td><a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.title)}</a></td>
      <td>${p.has_jd ? tags(p.required, "req") : '<span class="muted">JD not available via a public API</span>'}</td>
      <td>${tags(p.nice, "")}</td>
      <td>${p.flags.map((f) => `<span class="flag">${esc(f)}</span>`).join("")}${p.grad_window ? `<span class="muted">grad ${esc(p.grad_window)}</span>` : ""}</td>
    </tr>`).join("") || '<tr><td colspan="5" class="muted">No new postings for this date.</td></tr>');
}

function renderTrends(d: Dashboard): void {
  $("trends").innerHTML = '<tr><th>Skill</th><th class="num">New repos</th><th class="num">HN stories</th><th class="num">Score</th></tr>' +
    (d.trends.slice(0, 12).map((t) => `<tr><td>${esc(t.skill)}</td><td class="num">${t.github_repos}</td><td class="num">${t.hn_stories}</td><td class="num">${t.score.toFixed(2)}</td></tr>`).join("") ||
      '<tr><td colspan="4" class="muted">No trend data.</td></tr>');
  $("gaps").innerHTML = '<tr><th>Skill</th><th class="num">30d share</th><th class="num">Trend</th><th class="num">Priority</th></tr>' +
    (d.gaps.slice(0, 12).map((g) => `<tr><td>${esc(g.skill)}</td><td class="num">${pct(g.share_30d)} <span class="muted">(${g.count_30d})</span></td><td class="num">${g.trend_score.toFixed(2)}</td><td class="num">${g.priority.toFixed(3)}</td></tr>`).join("") ||
      '<tr><td colspan="4" class="muted">No gaps.</td></tr>');
}

function ideaCard(i: Idea): string {
  return `<article class="idea">
    <h4>${esc(i.title)}</h4>
    <p class="why">${esc(i.why)}</p>
    <p><b>Scope:</b> ${esc(i.scope)}</p>
    <p><b>Stack:</b> ${i.stack.map((s) => `<span class="tag">${esc(s)}</span>`).join("")}</p>
    <p><b>Success metrics:</b></p><ul>${i.success_metrics.map((m) => `<li>${esc(m)}</li>`).join("")}</ul>
    <p class="muted">${esc(i.date)} · ${esc(i.source)}</p>
  </article>`;
}

function renderIdeasAndResumes(d: Dashboard): void {
  $("ideas").innerHTML = d.ideas_today.map(ideaCard).join("") || '<p class="muted">No ideas today.</p>';
  $("ideas-recent").innerHTML = d.ideas_recent.map(ideaCard).join("");
  $("persona-note").textContent = `${d.persona_note} Coverage = share of the JD's extracted skills that appear on the page (untailored → tailored).`;
  $("resumes").innerHTML = '<tr><th>Company</th><th>Role</th><th>Projects chosen</th><th class="num">Coverage</th><th>PDF</th></tr>' +
    (d.resumes.map((r) => `<tr><td><b>${esc(r.company)}</b></td><td>${esc(r.title)}</td><td>${esc(r.projects.join(", "))}</td>
      <td class="num cov">${pct(r.coverage_before)} → <b>${pct(r.coverage_after)}</b></td>
      <td><a href="${esc(r.pdf)}" target="_blank" rel="noopener">PDF</a></td></tr>`).join("") ||
      '<tr><td colspan="5" class="muted">No resumes for this date.</td></tr>');
}

// ---------------------------------------------------------------- roadmap timelines
interface TimelineItem { status: "done" | "current" | "todo"; date: string; title: string; desc: string }

function timeline(items: TimelineItem[]): string {
  return `<div class="roadmap"><div class="rm-line"></div><div class="rm-fill"></div>` + items.map((it) => {
    const cls = it.status === "todo" ? "todo" : it.status === "current" ? "done current" : "done";
    const tag = it.status === "current" ? ' <span class="rm-tag">latest</span>' : "";
    return `<div class="rm-item ${cls}"><div class="rm-dot"></div><div class="rm-card">
      <div class="rm-date">${esc(it.date)}${tag}</div><h3>${esc(it.title)}</h3><p>${esc(it.desc)}</p></div></div>`;
  }).join("") + "</div>";
}

function addDays(iso: string, n: number): string {
  const dt = new Date(`${iso}T00:00:00Z`);
  dt.setUTCDate(dt.getUTCDate() + n);
  return dt.toISOString().slice(0, 10);
}

function renderTimelines(d: Dashboard): void {
  const runs = d.runs.slice(-8);
  const items: TimelineItem[] = runs.map((r, idx) => {
    const m = r.metrics;
    const desc = `${m["ingest.postings_ingested"] ?? 0} postings, ${m["extract.llm_calls"] ?? 0} LLM calls, ` +
      `${m["tailor.resumes_generated"] ?? 0} resumes · ${r.status}`;
    return { status: idx === runs.length - 1 ? "current" : "done", date: r.date, title: `Daily run ${r.date}`, desc };
  });
  const last = runs.length ? runs[runs.length - 1].date : d.run_date;
  for (let k = 1; k <= 2; k++) {
    items.push({ status: "todo", date: addDays(last, k), title: "Scheduled run", desc: "EventBridge Scheduler → Step Functions (daily)." });
  }
  $("runs-timeline").innerHTML = timeline(items);
  const rm = d.roadmap.map((r, i, arr) => {
    const lastDone = arr.map((x) => x.status).lastIndexOf("done");
    const status: TimelineItem["status"] = r.status === "todo" ? "todo" : i === lastDone ? "current" : "done";
    return { status, date: r.label, title: r.title, desc: r.desc };
  });
  $("roadmap-timeline").innerHTML = timeline(rm);
  initTimelines();
}

// Bright line grows with scroll and stops at the center of the LAST done dot (from real layout).
function initTimelines(): void {
  const all = Array.from(document.querySelectorAll<HTMLElement>(".roadmap"));
  const update = (): void => {
    const viewH = window.innerHeight || document.documentElement.clientHeight;
    for (const rm of all) {
      const fill = rm.querySelector<HTMLElement>(".rm-fill");
      const dots = rm.querySelectorAll<HTMLElement>(".rm-item.done .rm-dot");
      if (!fill) continue;
      let target = 0;
      if (dots.length) {
        const dot = dots[dots.length - 1];
        const item = dot.closest<HTMLElement>(".rm-item");
        target = (item?.offsetTop ?? 0) + dot.offsetTop + dot.offsetHeight / 2;
      }
      const rect = rm.getBoundingClientRect();
      const progress = Math.max(0, Math.min(1, (viewH * 0.8 - rect.top) / (rect.height * 0.9)));
      fill.style.height = `${target * progress}px`;
    }
  };
  for (const ev of ["scroll", "resize", "load"]) window.addEventListener(ev, update, { passive: true });
  update();
}

function renderDraft(d: Dashboard): void {
  $("linkedin").textContent = d.linkedin_draft;
  $("copy-draft").addEventListener("click", () => {
    void navigator.clipboard?.writeText(d.linkedin_draft).then(() => { $("copy-draft").textContent = "Copied"; });
  });
}

async function main(): Promise<void> {
  const res = await fetch("data/dashboard.json", { cache: "no-cache" });
  const d = (await res.json()) as Dashboard;
  renderKpis(d);
  renderDemandBars(d);
  renderDemandChart(d);
  renderPostings(d);
  renderTrends(d);
  renderIdeasAndResumes(d);
  renderTimelines(d);
  renderDraft(d);
}

main().catch((err: unknown) => {
  document.querySelector("main")!.insertAdjacentHTML("afterbegin", `<p class="flag">Failed to load data: ${esc(err)}</p>`);
});
