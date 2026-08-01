/* POV-1 Study Dashboard — vanilla JS, no dependencies, no network beyond the
 * optional local JSON fetch. Renders the dashboard_payload contract produced by
 * gap/stats.dashboard_payload.
 *
 * Data source, in order:
 *   1. window.DASHBOARD_DATA  (injected by the Anki Qt webview)
 *   2. fetch('dashboard_data.json')  (standalone browser / static server)
 */
(function () {
  "use strict";

  // ---------------------------------------------------------------- helpers
  var DASH = "—"; // em dash for missing values

  function isNum(v) {
    return typeof v === "number" && isFinite(v);
  }

  function num(v, digits) {
    if (!isNum(v)) return DASH;
    return v.toFixed(digits == null ? 1 : digits);
  }

  function pct(v, digits) {
    if (!isNum(v)) return DASH;
    return v.toFixed(digits == null ? 1 : digits) + "%";
  }

  // Accuracy that may arrive as a 0-1 fraction OR an already-scaled percent.
  // Fields without a _pct suffix (abstain means) are ambiguous in the contract;
  // treat anything in [0,1] as a fraction.
  function pctAuto(v, digits) {
    if (!isNum(v)) return DASH;
    var scaled = (v >= 0 && v <= 1) ? v * 100 : v;
    return scaled.toFixed(digits == null ? 1 : digits) + "%";
  }

  // signed percentage-points, e.g. "+7.0 pp" / "-9.0 pp"
  function pp(v, digits) {
    if (!isNum(v)) return DASH;
    var d = digits == null ? 1 : digits;
    var s = (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v).toFixed(d);
    return s + " pp";
  }

  function signClass(v) {
    if (!isNum(v)) return "";
    return v > 0 ? "delta-pos" : v < 0 ? "delta-neg" : "";
  }

  function esc(s) {
    if (s == null) return "";
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function el(id) {
    return document.getElementById(id);
  }

  var ARM_LABELS = { gate: "Gate (A)", nogate: "No-gate (B)", vanilla: "Vanilla (C)" };
  function armLabel(a) {
    return ARM_LABELS[a] || (a ? esc(a) : DASH);
  }

  function fmtTimestamp(ms) {
    if (!isNum(ms)) return DASH;
    try {
      var d = new Date(ms);
      if (isNaN(d.getTime())) return DASH;
      return d.toLocaleString(undefined, {
        year: "numeric", month: "short", day: "numeric",
        hour: "2-digit", minute: "2-digit"
      });
    } catch (e) {
      return DASH;
    }
  }

  // ---------------------------------------------------------------- header
  function renderHeader(data) {
    el("generated-ts").textContent = fmtTimestamp(data.generated_ms);
  }

  // ---------------------------------------------------------- crossover SVG
  function renderCrossover(data) {
    var host = el("crossover");
    host.innerHTML = "";
    var ep = (data.endpoints && data.endpoints.crossover) || [];

    if (!ep.length) {
      host.innerHTML = '<p class="muted">No crossover data.</p>';
      return;
    }

    // Geometry
    var W = 640, H = 300;
    var mL = 46, mR = 18, mT = 22, mB = 50;
    var innerW = W - mL - mR;
    var innerH = H - mT - mB;
    var baseY = mT + innerH;
    var yMax = 100;

    var svg = [];
    svg.push('<svg viewBox="0 0 ' + W + ' ' + H + '" role="img" ' +
      'aria-label="Grouped bar chart of novel-item accuracy for gate and no-gate arms at exposure buckets 1-4 and 5+">');

    // Y gridlines + ticks
    var ticks = [0, 25, 50, 75, 100];
    for (var t = 0; t < ticks.length; t++) {
      var yy = baseY - (ticks[t] / yMax) * innerH;
      svg.push('<line class="svg-tick" x1="' + mL + '" y1="' + yy.toFixed(1) +
        '" x2="' + (mL + innerW) + '" y2="' + yy.toFixed(1) + '"/>');
      svg.push('<text class="svg-axis" x="' + (mL - 8) + '" y="' + (yy + 4).toFixed(1) +
        '" text-anchor="end">' + ticks[t] + '</text>');
    }
    // Baseline (emphasised)
    svg.push('<line class="svg-base" x1="' + mL + '" y1="' + baseY +
      '" x2="' + (mL + innerW) + '" y2="' + baseY + '"/>');

    // Y axis title
    svg.push('<text class="svg-axis" transform="translate(14,' + (mT + innerH / 2) +
      ') rotate(-90)" text-anchor="middle">Novel accuracy (%)</text>');

    var n = ep.length;
    var slotW = innerW / n;
    var barW = Math.min(58, slotW * 0.28);
    var gap = 16;

    for (var i = 0; i < n; i++) {
      var b = ep[i];
      var cx = mL + slotW * (i + 0.5);
      var startX = cx - barW - gap / 2;

      var series = [
        { x: startX, v: b.acc_gate_pct, nn: b.n_gate, fill: "var(--arm-gate)" },
        { x: startX + barW + gap, v: b.acc_nogate_pct, nn: b.n_nogate, fill: "var(--arm-nogate)" }
      ];

      for (var s = 0; s < series.length; s++) {
        var sv = series[s];
        if (isNum(sv.v)) {
          var h = (Math.max(0, Math.min(yMax, sv.v)) / yMax) * innerH;
          var y = baseY - h;
          svg.push('<rect x="' + sv.x.toFixed(1) + '" y="' + y.toFixed(1) +
            '" width="' + barW.toFixed(1) + '" height="' + h.toFixed(1) +
            '" rx="4" fill="' + sv.fill + '"><title>' +
            (s === 0 ? "Gate" : "No-gate") + " · bucket " + esc(b.bucket) +
            ": " + pct(sv.v) + " (n=" + (isNum(sv.nn) ? sv.nn : DASH) + ")</title></rect>");
          svg.push('<text class="svg-value" x="' + (sv.x + barW / 2).toFixed(1) +
            '" y="' + (y - 6).toFixed(1) + '" text-anchor="middle">' +
            sv.v.toFixed(0) + '</text>');
        } else {
          svg.push('<text class="svg-axis" x="' + (sv.x + barW / 2).toFixed(1) +
            '" y="' + (baseY - 6) + '" text-anchor="middle">' + DASH + '</text>');
        }
      }

      // Bucket label
      svg.push('<text class="svg-blabel" x="' + cx.toFixed(1) + '" y="' + (baseY + 22) +
        '" text-anchor="middle">Exposures ' + esc(b.bucket) + '</text>');
    }

    svg.push("</svg>");

    var fig = document.createElement("div");
    fig.className = "crossover__figure";
    fig.innerHTML = svg.join("");
    host.appendChild(fig);

    // Legend
    var legend = document.createElement("div");
    legend.className = "crossover__legend";
    legend.innerHTML =
      '<span class="legend-item"><span class="legend-swatch" style="background:var(--arm-gate)"></span>Gate (Arm A)</span>' +
      '<span class="legend-item"><span class="legend-swatch" style="background:var(--arm-nogate)"></span>No-gate (Arm B)</span>' +
      '<span class="legend-item muted">Diff = Arm A − Arm B</span>';
    host.appendChild(legend);

    // Per-bucket diff verdict cards (make the sign flip obvious)
    var buckets = document.createElement("div");
    buckets.className = "crossover__buckets";
    for (var j = 0; j < ep.length; j++) {
      var bb = ep[j];
      var card = document.createElement("div");
      card.className = "bucket-card";
      var arrow = isNum(bb.diff_pp) ? (bb.diff_pp > 0 ? "▲ A ahead" : bb.diff_pp < 0 ? "▼ A behind" : "tied") : DASH;
      card.innerHTML =
        '<div class="bucket-card__label">Exposures ' + esc(bb.bucket) + ' &middot; diff (A − B)</div>' +
        '<div class="bucket-card__diff ' + signClass(bb.diff_pp) + '">' + pp(bb.diff_pp) + '</div>' +
        '<div class="bucket-card__sub">' + arrow + ' &middot; gate ' + pct(bb.acc_gate_pct) +
        ' vs no-gate ' + pct(bb.acc_nogate_pct) + '</div>';
      buckets.appendChild(card);
    }
    host.appendChild(buckets);
  }

  // ---------------------------------------------------------------- tiles
  function renderTiles(data) {
    var host = el("tiles");
    host.innerHTML = "";
    var ep = data.endpoints || {};

    // Terminal A-B
    var term = ep.terminal || {};
    host.appendChild(tile({
      label: "Terminal novel accuracy · A − B",
      value: pp(term.diff_pp),
      valueClass: signClass(term.diff_pp),
      sub: "gate " + pct(term.acc_gate_pct) + " vs no-gate " + pct(term.acc_nogate_pct) +
           "  (n " + (isNum(term.n_gate) ? term.n_gate : DASH) + "/" +
           (isNum(term.n_nogate) ? term.n_nogate : DASH) + ")"
    }));

    // Throughput A vs B
    var thr = ep.throughput || {};
    host.appendChild(tile({
      label: "Throughput · A vs B",
      value: isNum(thr.pct_diff_A_vs_B) ? pp0(thr.pct_diff_A_vs_B) : DASH,
      valueClass: signClass(thr.pct_diff_A_vs_B),
      sub: "retired: gate " + (isNum(thr.gate_retired) ? thr.gate_retired : DASH) +
           " vs no-gate " + (isNum(thr.nogate_retired) ? thr.nogate_retired : DASH)
    }));

    // Latency r + lockstep badge
    var lat = ep.latency || {};
    var lockstep = !!lat.lockstep;
    var badge = lockstep
      ? '<span class="badge badge--bad"><span class="badge__dot"></span>Lockstep — dead</span>'
      : '<span class="badge badge--good"><span class="badge__dot"></span>Dissociated</span>';
    host.appendChild(tile({
      label: "Latency dissociation · r",
      value: num(lat.r, 2),
      sub: "revlog time vs novel latency" + (isNum(lat.r) ? " (threshold r ≤ 0.80)" : ""),
      badge: badge
    }));
  }

  function pp0(v) {
    if (!isNum(v)) return DASH;
    return (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v).toFixed(1) + "%";
  }

  function tile(o) {
    var d = document.createElement("div");
    d.className = "tile";
    var html = '<div class="tile__label">' + esc(o.label) + '</div>' +
      '<div class="tile__value ' + (o.valueClass || "") + '">' + o.value + '</div>';
    if (o.sub) html += '<div class="tile__sub">' + o.sub + '</div>';
    if (o.badge) html += '<div class="tile__badge-row">' + o.badge + '</div>';
    d.innerHTML = html;
    return d;
  }

  // ---------------------------------------------------------------- arms
  function renderArms(data) {
    var host = el("arms");
    host.innerHTML = "";
    var arms = data.arms || {};
    var order = ["gate", "nogate", "vanilla"];
    for (var i = 0; i < order.length; i++) {
      var key = order[i];
      var a = arms[key] || {};
      var d = document.createElement("div");
      d.className = "arm";
      d.setAttribute("data-arm", key);
      d.innerHTML =
        '<div class="arm__name">' + armLabel(key) +
        '<span class="arm__code">' + esc(key) + '</span></div>' +
        '<div class="arm__stats">' +
          '<div><div class="arm__stat-value">' + (isNum(a.concepts) ? a.concepts : DASH) +
          '</div><div class="arm__stat-label">Concepts</div></div>' +
          '<div><div class="arm__stat-value">' + (isNum(a.retired) ? a.retired : DASH) +
          '</div><div class="arm__stat-label">Retired</div></div>' +
        '</div>';
      host.appendChild(d);
    }
  }

  // ---------------------------------------------------------------- abstain
  function renderAbstain(data) {
    var ab = data.abstain || {};
    el("abstain-threshold").textContent = isNum(ab.threshold) ? ab.threshold : "8";
    var host = el("abstain");
    host.innerHTML = "";

    var cells = [
      { value: isNum(ab.scored) ? ab.scored : DASH, label: "Scored", sub: "≥ " + (isNum(ab.threshold) ? ab.threshold : 8) + " novel attempts" },
      { value: isNum(ab.abstained) ? ab.abstained : DASH, label: "Abstained", sub: "below the line" },
      { value: pctAuto(ab.below_line_mean_acc), label: "Below-line acc.", sub: "mean novel accuracy" },
      { value: pctAuto(ab.above_line_mean_acc), label: "Above-line acc.", sub: "mean novel accuracy" },
      { value: pp(ab.diff_pp), label: "Difference", sub: "above − below", cls: signClass(ab.diff_pp) }
    ];
    for (var i = 0; i < cells.length; i++) {
      var c = cells[i];
      var d = document.createElement("div");
      d.className = "abstain__cell";
      d.innerHTML =
        '<div class="abstain__value ' + (c.cls || "") + '">' + c.value + '</div>' +
        '<div class="abstain__label">' + esc(c.label) + '</div>' +
        '<div class="abstain__sub">' + esc(c.sub) + '</div>';
      host.appendChild(d);
    }
  }

  // ---------------------------------------------------------------- table
  function chip(arm) {
    return '<span class="chip" data-arm="' + esc(arm) + '">' +
      '<span class="chip__dot"></span>' + armLabel(arm) + '</span>';
  }

  function renderTable(data) {
    var tbody = el("concept-tbody");
    tbody.innerHTML = "";
    var concepts = data.concepts || [];

    if (!concepts.length) {
      var tr0 = document.createElement("tr");
      tr0.innerHTML = '<td colspan="7" class="muted">No concepts.</td>';
      tbody.appendChild(tr0);
      return;
    }

    for (var i = 0; i < concepts.length; i++) {
      var c = concepts[i];
      var tr = document.createElement("tr");
      var abstained = !c.has_score;
      if (abstained) tr.className = "row-abstain";

      // Performance cell
      var perfCell;
      if (c.has_score && isNum(c.performance)) {
        perfCell = '<span>' + num(c.performance, 1) + '</span>';
      } else {
        var cov = isNum(c.coverage_pct) ? Math.round(c.coverage_pct) : null;
        perfCell = '<span class="abstain-tag">ABSTAIN — coverage ' +
          (cov == null ? DASH : cov + "%") + '</span>';
      }

      // Retired cell
      var retiredCell;
      if (c.retired) {
        retiredCell = '<span class="retired-yes">Yes' +
          (c.retired_trigger ? ' <small>(' + esc(c.retired_trigger) + ')</small>' : "") +
          '</span>';
      } else {
        retiredCell = '<span class="retired-no">' + DASH + '</span>';
      }

      tr.innerHTML =
        '<td class="code">' + esc(c.code != null ? c.code : DASH) + '</td>' +
        '<td class="name">' + esc(c.name != null ? c.name : DASH) + '</td>' +
        '<td>' + chip(c.arm) + '</td>' +
        '<td class="num">' + (isNum(c.novel_attempts) ? c.novel_attempts : DASH) + '</td>' +
        '<td class="num">' + perfCell + '</td>' +
        '<td class="num">' + (isNum(c.card_mastery) ? num(c.card_mastery, 2) : DASH) + '</td>' +
        '<td>' + retiredCell + '</td>';
      tbody.appendChild(tr);
    }
  }

  // ---------------------------------------------------------------- driver
  function render(data) {
    if (!data || typeof data !== "object") {
      throw new Error("dashboard data is empty or not an object");
    }
    renderHeader(data);
    renderCrossover(data);
    renderTiles(data);
    renderArms(data);
    renderAbstain(data);
    renderTable(data);
    document.body.setAttribute("data-ready", "1");
  }

  function showError(msg) {
    var b = el("error-banner");
    if (b) {
      b.hidden = false;
      b.textContent = "Could not render dashboard: " + msg;
    }
  }

  function boot() {
    try {
      if (typeof window.DASHBOARD_DATA !== "undefined" && window.DASHBOARD_DATA) {
        render(window.DASHBOARD_DATA);
        return;
      }
    } catch (e) {
      showError(e && e.message ? e.message : String(e));
      return;
    }

    // Standalone: fetch the JSON next to the HTML.
    fetch("dashboard_data.json", { cache: "no-store" })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status + " loading dashboard_data.json");
        return r.json();
      })
      .then(function (json) { render(json); })
      .catch(function (e) { showError(e && e.message ? e.message : String(e)); });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
