"use strict";
// jobpulse dashboard - vanilla TypeScript, zero runtime dependencies.
// Compiled by `npm run build` to src/jobpulse/web/app.js, which the publish step uploads.
const $ = (id) => {
    const el = document.getElementById(id);
    if (!el)
        throw new Error(`missing #${id}`);
    return el;
};
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c] ?? c);
const pct = (x, digits = 0) => `${((x ?? 0) * 100).toFixed(digits)}%`;
const delta = (d) => {
    if (d === null || d === undefined)
        return '<span class="muted">–</span>';
    const pp = d * 100;
    const cls = pp > 0.05 ? "up" : pp < -0.05 ? "down" : "muted";
    return `<span class="${cls}">${pp > 0 ? "+" : ""}${pp.toFixed(1)} pp</span>`;
};
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
// Dates in the data are calendar days (ISO, no time zone): format them in UTC so they never shift.
const fmtDate = (iso, opts) => {
    const dt = new Date(`${iso.slice(0, 10)}T00:00:00Z`);
    return Number.isNaN(dt.getTime()) ? iso : dt.toLocaleDateString("en-US", { ...opts, timeZone: "UTC" });
};
const shortDate = (iso) => fmtDate(iso, { month: "short", day: "numeric" });
const longDate = (iso) => fmtDate(iso, { month: "short", day: "numeric", year: "numeric" });
const dayDate = (iso) => fmtDate(iso, { weekday: "short", month: "short", day: "numeric" });
// ---------------------------------------------------------------- tooltip
const tip = () => $("tooltip");
function showTip(html, x, y) {
    const t = tip();
    t.innerHTML = html;
    t.hidden = false;
    const w = t.offsetWidth, h = t.offsetHeight;
    const left = x + 16 + w > window.innerWidth - 8 ? x - 16 - w : x + 16;
    const top = y + 16 + h > window.innerHeight - 8 ? y - 16 - h : y + 16;
    t.style.left = `${Math.max(8, left)}px`;
    t.style.top = `${Math.max(8, top)}px`;
}
const hideTip = () => { tip().hidden = true; };
// Hover (mouse, pen, touch) and keyboard focus show the same tooltip.
function bindTip(el, html) {
    el.addEventListener("pointermove", (e) => showTip(html(), e.clientX, e.clientY));
    el.addEventListener("pointerdown", (e) => showTip(html(), e.clientX, e.clientY));
    el.addEventListener("pointerleave", hideTip);
    el.addEventListener("focus", () => {
        const r = el.getBoundingClientRect();
        showTip(html(), r.left + Math.min(r.width * 0.6, 260), r.bottom - 10);
    });
    el.addEventListener("blur", hideTip);
}
// ---------------------------------------------------------------- segmented controls (ARIA tabs)
function initTabs(list, onChange) {
    const tabs = Array.from(list.querySelectorAll('[role="tab"]'));
    const select = (tab, focus) => {
        for (const t of tabs) {
            const on = t === tab;
            t.setAttribute("aria-selected", String(on));
            t.tabIndex = on ? 0 : -1;
            const panel = document.getElementById(t.getAttribute("aria-controls") ?? "");
            if (panel)
                panel.hidden = !on;
        }
        if (focus)
            tab.focus();
        hideTip();
        onChange?.(tab.getAttribute("aria-controls") ?? "");
    };
    tabs.forEach((tab, i) => {
        tab.addEventListener("click", () => select(tab, false));
        tab.addEventListener("keydown", (e) => {
            const k = e.key;
            const j = k === "ArrowRight" ? (i + 1) % tabs.length : k === "ArrowLeft" ? (i - 1 + tabs.length) % tabs.length
                : k === "Home" ? 0 : k === "End" ? tabs.length - 1 : -1;
            if (j >= 0) {
                e.preventDefault();
                select(tabs[j], true);
            }
        });
    });
}
// ---------------------------------------------------------------- sections
function renderKpis(d) {
    const last = d.runs.find((r) => r.date === d.run_date);
    const m = last?.metrics ?? {};
    const withJd = d.postings.filter((p) => p.has_jd).length;
    const totalPostings = d.series.postings.reduce((a, b) => a + b, 0);
    const kpis = [
        [String(d.postings.length), "new postings today"],
        [String(withJd), "with full JD text"],
        [String(totalPostings), "postings analyzed, 30 days"],
        [String(m["extract.llm_calls"] ?? 0), "LLM calls today"],
        [pct(m["extract.cache_hit_rate"]), "extraction cache hits"],
        [String(d.resumes.length), "tailored resumes today"],
    ];
    $("kpis").innerHTML = kpis.map(([v, l]) => `<div class="kpi"><div class="v">${esc(v)}</div><div class="l">${esc(l)}</div></div>`).join("");
    $("run-date").textContent = `Run date: ${d.run_date}`;
    $("nav-date").textContent = longDate(d.run_date);
    $("nav-date").setAttribute("title", `Run date ${d.run_date}`);
    $("generated").textContent = d.generated_at;
}
function renderDemandBars(d) {
    const rows = d.demand.slice(0, 15);
    const max = Math.max(...rows.map((r) => r.share_30d ?? 0), 0.01);
    const tipHtml = (r) => `<b>${esc(r.skill)}</b> <span class="t-sub">(${esc(r.category)})</span><br>${r.count_30d ?? 0} of ${r.postings_30d ?? 0} postings, 30 days (${pct(r.share_30d, 1)})<br>` +
        `7-day delta: ${delta(r.delta_7d)} · 30-day delta: ${delta(r.delta_30d)}`;
    $("demand-bars").innerHTML = rows.map((r) => `
    <div class="bar-row" data-skill="${esc(r.skill)}" tabindex="0">
      <span class="bar-name">${esc(r.skill)}</span>
      <span class="bar-track" aria-hidden="true"><span class="bar-fill" style="width:${(100 * (r.share_30d ?? 0)) / max}%"></span></span>
      <span class="bar-val">${pct(r.share_30d)}<span class="d">${delta(r.delta_7d)}</span></span>
    </div>`).join("") || '<p class="empty-block">No extracted postings yet.</p>';
    $("demand-bars").querySelectorAll(".bar-row").forEach((el) => {
        const r = rows.find((x) => x.skill === el.dataset.skill);
        if (!r)
            return;
        el.setAttribute("aria-label", tipHtml(r).replace(/<br>/g, ". ").replace(/<[^>]+>/g, ""));
        bindTip(el, () => tipHtml(r));
    });
}
function rolling(counts, totals, window = 7) {
    return counts.map((_, i) => {
        let c = 0, n = 0;
        for (let j = Math.max(0, i - window + 1); j <= i; j++) {
            c += counts[j];
            n += totals[j];
        }
        return n > 0 ? c / n : null;
    });
}
const SERIES_COLORS = ["var(--series-1)", "var(--series-2)", "var(--series-3)", "var(--series-4)"];
let chartWidth = 0;
function renderDemandChart(d) {
    const el = $("demand-chart");
    const skills = Object.keys(d.series.skills).slice(0, 4);
    const colors = SERIES_COLORS;
    const full = skills.map((s) => rolling(d.series.skills[s], d.series.postings));
    // Start at the first day with any data, so a young deployment does not show weeks of empty axis.
    const start = d.series.dates.findIndex((_, i) => full.some((l) => l[i] !== null));
    if (!skills.length || start < 0) {
        el.innerHTML = '<p class="empty-block">The chart appears after the first postings with a full JD are extracted.</p>';
        return;
    }
    const dates = d.series.dates.slice(start);
    const totals = d.series.postings.slice(start);
    const lines = full.map((l) => l.slice(start));
    const n = dates.length;
    const W = Math.max(300, Math.round(el.clientWidth || 560));
    chartWidth = W;
    const narrow = W < 480;
    const H = narrow ? 220 : n < 2 ? 200 : 290, L = 38, R = narrow ? 86 : 108, T = 12, B = 28;
    const ymax = Math.ceil(Math.max(0.1, ...lines.flat().map((v) => v ?? 0)) * 10) / 10; // round up to 10%
    const x = (i) => (n > 1 ? L + (i * (W - L - R)) / (n - 1) : L + (W - L - R) / 2);
    const y = (v) => T + (1 - v / ymax) * (H - T - B);
    let svg = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="7-day rolling demand share for ${esc(skills.join(", "))}">`;
    for (const t of [0, ymax / 2, ymax]) {
        svg += `<line class="${t === 0 ? "baseline" : "gridline"}" x1="${L}" x2="${W - R}" y1="${y(t)}" y2="${y(t)}"/>`;
        svg += `<text x="${L - 8}" y="${y(t) + 4}" text-anchor="end">${Math.round(t * 100)}%</text>`;
    }
    const xticks = n >= 3 ? [0, Math.floor((n - 1) / 2), n - 1] : n === 2 ? [0, 1] : [0];
    for (const i of xticks)
        svg += `<text x="${x(i)}" y="${H - 8}" text-anchor="middle">${esc(shortDate(dates[i]))}</text>`;
    const ends = [];
    lines.forEach((vals, k) => {
        let path = "";
        vals.forEach((v, i) => { if (v !== null)
            path += `${path ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`; });
        if (path.includes("L"))
            svg += `<path class="series" d="${path}" stroke="${colors[k]}"/>`;
        const lastI = vals.map((v) => v !== null).lastIndexOf(true);
        if (lastI >= 0) {
            svg += `<circle class="end" cx="${x(lastI).toFixed(1)}" cy="${y(vals[lastI] ?? 0).toFixed(1)}" r="4" fill="${colors[k]}"/>`;
            ends.push({ k, ly: y(vals[lastI] ?? 0) + 4 });
        }
    });
    // Direct labels at the line ends, spread apart so they never overlap.
    ends.sort((a, b) => a.ly - b.ly);
    for (let i = 1; i < ends.length; i++)
        ends[i].ly = Math.max(ends[i].ly, ends[i - 1].ly + 13);
    const overflow = ends.length ? ends[ends.length - 1].ly - (H - B + 4) : 0;
    if (overflow > 0)
        for (const e of ends)
            e.ly -= overflow;
    const lx = n > 1 ? W - R + 10 : x(0) + 10;
    for (const e of ends)
        svg += `<text class="lbl" x="${lx}" y="${e.ly.toFixed(1)}">${esc(skills[e.k])}</text>`;
    svg += `<line class="xhair" x1="0" x2="0" y1="${T}" y2="${H - B}" visibility="hidden"/>`;
    svg += `<g class="hover-dots" visibility="hidden">${skills.map((_, k) => `<circle class="hover-dot" r="4.5" fill="${colors[k]}"/>`).join("")}</g>`;
    svg += `<rect class="hit" x="${L - 10}" y="${T}" width="${W - L - R + 20}" height="${H - T - B}" fill="transparent"/></svg>`;
    const legend = skills.map((s, k) => `<span><i style="background:${colors[k]}"></i>${esc(s)}</span>`).join("");
    const latest = skills.map((s, k) => {
        const row = d.demand.find((r) => r.skill === s);
        return `<div class="st"><div class="st-k"><i style="background:${colors[k]}"></i>${esc(s)}</div>` +
            `<div class="st-v">${row?.share_7d === undefined ? "–" : pct(row.share_7d)}</div><div class="st-d">${delta(row?.delta_7d)}</div></div>`;
    }).join("");
    const latestHtml = `<div class="latest"><p class="latest-cap">Last 7 days · change vs the 7 days before</p>${latest}</div>`;
    const note = n < 2 ? '<p class="note">One day of history so far: each skill is a single point. Lines draw from the second daily run.</p>' : "";
    el.innerHTML = svg + `<div class="legend">${legend}</div>` + note + latestHtml;
    const svgEl = el.querySelector("svg");
    const hit = el.querySelector(".hit");
    const xhair = el.querySelector(".xhair");
    const dotsG = el.querySelector(".hover-dots");
    const dots = Array.from(dotsG.querySelectorAll("circle"));
    const move = (e) => {
        const box = svgEl.getBoundingClientRect();
        const sx = ((e.clientX - box.left) / box.width) * W;
        const i = n > 1 ? Math.max(0, Math.min(n - 1, Math.round(((sx - L) / (W - L - R)) * (n - 1)))) : 0;
        xhair.setAttribute("x1", String(x(i)));
        xhair.setAttribute("x2", String(x(i)));
        xhair.setAttribute("visibility", "visible");
        dotsG.setAttribute("visibility", "visible");
        dots.forEach((c, k) => {
            const v = lines[k][i];
            c.setAttribute("visibility", v === null ? "hidden" : "visible");
            c.setAttribute("cx", String(x(i)));
            c.setAttribute("cy", String(y(v ?? 0)));
        });
        const rowsHtml = skills.map((s, k) => `<div class="t-row"><i style="background:${colors[k]}"></i>${esc(s)}<span class="t-v">${lines[k][i] === null ? "–" : pct(lines[k][i] ?? 0, 1)}</span></div>`).join("");
        showTip(`<b>${esc(dayDate(dates[i]))}</b><div class="t-sub">${esc(dates[i])} · ${plural(totals[i], "posting")} that day</div>${rowsHtml}`, e.clientX, e.clientY);
    };
    hit.addEventListener("pointermove", move);
    hit.addEventListener("pointerdown", move);
    hit.addEventListener("pointerleave", () => {
        xhair.setAttribute("visibility", "hidden");
        dotsG.setAttribute("visibility", "hidden");
        hideTip();
    });
}
function renderDemandTable(d) {
    // Table view (accessibility: the chart's numbers without color).
    const skills = Object.keys(d.series.skills).slice(0, 4);
    $("demand-table").innerHTML = `<table class="tbl"><thead><tr><th scope="col">Date</th><th scope="col" class="num">Postings</th>${skills.map((s) => `<th scope="col" class="num">${esc(s)}</th>`).join("")}</tr></thead><tbody>` +
        d.series.dates.map((dt, i) => `<tr><td>${esc(dt)}</td><td class="num">${d.series.postings[i]}</td>${skills.map((s) => `<td class="num">${d.series.skills[s][i]}</td>`).join("")}</tr>`).join("") + "</tbody></table>";
}
function renderPostings(d) {
    $("postings-count").textContent = String(d.postings.length);
    const tags = (xs, cls) => xs.map((s) => `<span class="tag ${cls}">${esc(s)}</span>`).join("");
    $("postings").innerHTML = '<thead><tr><th scope="col">Company</th><th scope="col">Role</th><th scope="col">Required skills</th><th scope="col">Nice to have</th><th scope="col">Flags</th></tr></thead><tbody>' +
        (d.postings.map((p) => `<tr>
      <td class="lead" data-label="Company"><span class="co">${esc(p.company)}</span><span class="loc">${esc(p.locations.join(" · "))}</span></td>
      <td class="role" data-label="Role"><a class="ext" href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.title)}</a></td>
      <td data-label="Required skills">${p.has_jd ? tags(p.required, "req") || '<span class="muted">None marked required</span>' : '<span class="muted">JD not available via a public API</span>'}</td>
      <td data-label="Nice to have">${tags(p.nice, "")}</td>
      <td data-label="Flags">${p.flags.map((f) => `<span class="flag">${esc(f)}</span>`).join("")}${p.grad_window ? `<span class="grad">Grad ${esc(p.grad_window)}</span>` : ""}</td>
    </tr>`).join("") || '<tr><td colspan="5" class="empty">No new postings for this date.</td></tr>') + "</tbody>";
}
function renderTrends(d) {
    $("trends").innerHTML = '<thead><tr><th scope="col">Skill</th><th scope="col" class="num">New repos</th><th scope="col" class="num">HN stories</th><th scope="col" class="num">Score</th></tr></thead><tbody>' +
        (d.trends.slice(0, 12).map((t, i) => `<tr><td class="sk"><span class="rank">${i + 1}</span>${esc(t.skill)}</td><td class="num">${t.github_repos}</td><td class="num">${t.hn_stories}</td><td class="num">${t.score.toFixed(2)}</td></tr>`).join("") ||
            '<tr><td colspan="4" class="empty">No trend data.</td></tr>') + "</tbody>";
    $("gaps").innerHTML = '<thead><tr><th scope="col">Skill</th><th scope="col" class="num">30d share</th><th scope="col" class="num">Trend</th><th scope="col" class="num">Priority</th></tr></thead><tbody>' +
        (d.gaps.slice(0, 12).map((g, i) => `<tr><td class="sk"><span class="rank">${i + 1}</span>${esc(g.skill)}</td><td class="num">${pct(g.share_30d)} <span class="muted">(${g.count_30d})</span></td><td class="num">${g.trend_score.toFixed(2)}</td><td class="num">${g.priority.toFixed(3)}</td></tr>`).join("") ||
            '<tr><td colspan="4" class="empty">No gaps.</td></tr>') + "</tbody>";
}
// The "why" is LLM prose followed by code-filled demand numbers ("Measured demand - A: ...; B: ...").
function whyHtml(why) {
    const cut = why.indexOf("Measured demand - ");
    if (cut < 0)
        return `<p class="why">${esc(why)}</p>`;
    const prose = why.slice(0, cut).trim();
    const parts = why.slice(cut + 18).split(/(?<=\.)\s+(?=[^.:]{1,40}: in )/);
    return (prose ? `<p class="why">${esc(prose)}</p>` : "") +
        `<div class="measured"><span class="k">Measured demand</span>${parts.map((m) => `<span class="m">${esc(m)}</span>`).join("")}</div>`;
}
function ideaCard(i) {
    const targets = (i.target_skills ?? []).map((s) => `<span class="tag">${esc(s)}</span>`).join("");
    return `<article class="idea">
    ${targets ? `<div class="targets" aria-label="Target skills">${targets}</div>` : ""}
    <h4>${esc(i.title)}</h4>
    ${whyHtml(i.why)}
    <p><span class="k">Scope</span>${esc(i.scope)}</p>
    <div><span class="k">Stack</span>${i.stack.map((s) => `<span class="tag">${esc(s)}</span>`).join("")}</div>
    <div><span class="k">Success metrics</span><ul>${i.success_metrics.map((m) => `<li>${esc(m)}</li>`).join("")}</ul></div>
    <p class="meta">${esc(longDate(i.date))} · ${esc(i.source)}</p>
  </article>`;
}
function renderIdeasAndResumes(d) {
    $("ideas").innerHTML = d.ideas_today.map(ideaCard).join("") || '<p class="empty-block">No ideas today.</p>';
    $("ideas-recent").innerHTML = d.ideas_recent.map(ideaCard).join("") ||
        '<p class="empty-block">No earlier ideas yet. Each daily run adds up to three.</p>';
    $("ideas-today-n").textContent = String(d.ideas_today.length);
    $("ideas-older-n").textContent = String(d.ideas_recent.length);
    $("persona-note").textContent = `${d.persona_note} Coverage = share of the JD's extracted skills that appear on the page (untailored → tailored).`;
    $("resumes").innerHTML = '<thead><tr><th scope="col">Company</th><th scope="col">Role</th><th scope="col">Projects chosen</th><th scope="col" class="num">Coverage</th><th scope="col">PDF</th></tr></thead><tbody>' +
        (d.resumes.map((r) => {
            const gain = Math.round((r.coverage_after - r.coverage_before) * 100);
            return `<tr><td class="lead" data-label="Company"><span class="co">${esc(r.company)}</span></td>
      <td class="role" data-label="Role">${r.url ? `<a class="ext" href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.title)}</a>` : esc(r.title)}</td>
      <td data-label="Projects chosen">${esc(r.projects.join(", "))}</td>
      <td class="num cov" data-label="Coverage">${pct(r.coverage_before)} → <b>${pct(r.coverage_after)}</b>${gain > 0 ? `<span class="gain up">+${gain} pp</span>` : ""}</td>
      <td data-label="PDF"><a class="btn-sm" href="${esc(r.pdf)}" target="_blank" rel="noopener">Open PDF</a></td></tr>`;
        }).join("") ||
            '<tr><td colspan="5" class="empty">No resumes for this date.</td></tr>') + "</tbody>";
}
function timeline(items, label) {
    return `<div class="roadmap" role="list" aria-label="${esc(label)}"><div class="rm-line"></div><div class="rm-fill"></div>` + items.map((it) => {
        const cls = it.status === "todo" ? "todo" : it.status === "current" ? "done current" : "done";
        const tag = it.status === "current" ? ' <span class="rm-tag">latest</span>' : "";
        const state = it.status === "todo" ? "upcoming" : "completed";
        return `<div class="rm-item ${cls}" role="listitem"><div class="rm-dot" aria-hidden="true"></div><div class="rm-card">
      <div class="rm-date">${esc(it.date)}${tag}${it.chip ?? ""}<span class="sr">, ${state}</span></div><h3>${esc(it.title)}</h3><p>${esc(it.desc)}</p></div></div>`;
    }).join("") + "</div>";
}
function addDays(iso, n) {
    const dt = new Date(`${iso}T00:00:00Z`);
    dt.setUTCDate(dt.getUTCDate() + n);
    return dt.toISOString().slice(0, 10);
}
function statusChip(status) {
    const cls = status === "succeeded" ? "ok" : status === "failed" ? "bad" : "partial";
    return `<span class="rm-status ${cls}">${esc(status)}</span>`;
}
function renderTimelines(d) {
    const runs = d.runs.slice(-8);
    const items = runs.map((r, idx) => {
        const m = r.metrics;
        const desc = `${plural(m["ingest.postings_ingested"] ?? 0, "posting")}, ${plural(m["extract.llm_calls"] ?? 0, "LLM call")}, ` +
            `${plural(m["tailor.resumes_generated"] ?? 0, "resume")}`;
        return { status: idx === runs.length - 1 ? "current" : "done", date: dayDate(r.date), title: `Daily run ${r.date}`, desc, chip: statusChip(r.status) };
    });
    const last = runs.length ? runs[runs.length - 1].date : d.run_date;
    for (let k = 1; k <= 2; k++) {
        const day = addDays(last, k);
        items.push({ status: "todo", date: dayDate(day), title: `Scheduled run ${day}`, desc: "EventBridge Scheduler → Step Functions (daily)." });
    }
    $("runs-timeline").innerHTML = timeline(items, "Pipeline run history");
    const rm = d.roadmap.map((r, i, arr) => {
        const lastDone = arr.map((x) => x.status).lastIndexOf("done");
        const status = r.status === "todo" ? "todo" : i === lastDone ? "current" : "done";
        return { status, date: r.label, title: r.title, desc: r.desc };
    });
    $("roadmap-timeline").innerHTML = timeline(rm, "Project roadmap");
    initTimelines();
}
// Bright line grows with scroll and stops at the center of the LAST done dot (from real layout).
function initTimelines() {
    const all = Array.from(document.querySelectorAll(".roadmap"));
    const update = () => {
        const viewH = window.innerHeight || document.documentElement.clientHeight;
        for (const rm of all) {
            const fill = rm.querySelector(".rm-fill");
            const dots = rm.querySelectorAll(".rm-item.done .rm-dot");
            if (!fill)
                continue;
            let target = 0;
            if (dots.length) {
                const dot = dots[dots.length - 1];
                const item = dot.closest(".rm-item");
                target = (item?.offsetTop ?? 0) + dot.offsetTop + dot.offsetHeight / 2;
            }
            const rect = rm.getBoundingClientRect();
            const progress = Math.max(0, Math.min(1, (viewH * 0.8 - rect.top) / (rect.height * 0.9)));
            fill.style.height = `${target * progress}px`;
        }
    };
    for (const ev of ["scroll", "resize", "load"])
        window.addEventListener(ev, update, { passive: true });
    update();
}
function renderDraft(d) {
    const pre = $("linkedin");
    const btn = $("copy-draft");
    pre.textContent = d.linkedin_draft;
    let timer = 0;
    const done = (label) => {
        btn.textContent = label;
        btn.classList.toggle("done", label === "Copied");
        window.clearTimeout(timer);
        timer = window.setTimeout(() => { btn.textContent = "Copy draft"; btn.classList.remove("done"); }, 2000);
    };
    const fallback = () => {
        const range = document.createRange();
        range.selectNodeContents(pre);
        const sel = window.getSelection();
        sel?.removeAllRanges();
        sel?.addRange(range);
        done(document.execCommand?.("copy") ? "Copied" : "Selected, press Ctrl/Cmd+C");
    };
    btn.addEventListener("click", () => {
        if (navigator.clipboard)
            void navigator.clipboard.writeText(d.linkedin_draft).then(() => done("Copied"), fallback);
        else
            fallback();
    });
}
function onResize(d) {
    let frame = 0;
    window.addEventListener("resize", () => {
        hideTip();
        cancelAnimationFrame(frame);
        frame = requestAnimationFrame(() => {
            const w = $("demand-chart").clientWidth;
            if (w && Math.abs(w - chartWidth) > 2)
                renderDemandChart(d);
        });
    }, { passive: true });
    window.addEventListener("scroll", hideTip, { passive: true });
}
async function main() {
    const res = await fetch("data/dashboard.json", { cache: "no-cache" });
    const d = (await res.json());
    renderKpis(d);
    renderDemandBars(d);
    renderDemandChart(d);
    renderDemandTable(d);
    renderPostings(d);
    renderTrends(d);
    renderIdeasAndResumes(d);
    renderTimelines(d);
    renderDraft(d);
    initTabs(document.querySelector('[aria-label="Chart or table view"]'), (panel) => {
        if (panel === "demand-chart")
            renderDemandChart(d);
    });
    initTabs(document.querySelector('[aria-label="Which ideas"]'));
    onResize(d);
}
main().catch((err) => {
    document.querySelector("main").insertAdjacentHTML("afterbegin", `<p class="wrap"><span class="flag error">Failed to load data: ${esc(err)}</span></p>`);
});
