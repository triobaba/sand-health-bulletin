/* Quarterly Health Bulletin dashboard.

   Reads window.BULLETIN, written by build_site.py from the same pipeline run as
   the PDF, and draws every chart as plain SVG.

   Choices worth knowing before changing this file:
   - No libraries and no network calls. The page is static files, so it is cheap
     to host, loads over 3G and has nothing to keep patched.
   - No arithmetic on the figures beyond display maths (for example deliveries
     per month in the Q4 note). Every published number comes from the pipeline.
   - Every quarterly count is shown with its months-present figure, and only
     complete quarters are compared. That rule is the reason the bulletin exists.
   - Each chart has a table view and hover details, so no reading depends on
     colour alone. Browser storage only remembers the chosen quarter and
     indicator, inside try/catch, so the page works when storage is blocked. */
(function () {
  "use strict";
  var B = window.BULLETIN;
  if (!B) {
    document.getElementById("main").innerHTML =
      '<div class="banner"><strong>No data found.</strong> Run <code>python build_site.py</code> to build the site.</div>';
    return;
  }

  // ---------- helpers ----------
  var $ = function (id) { return document.getElementById(id); };
  var NS = "http://www.w3.org/2000/svg";
  var fmt = function (v, d) {
    if (v === null || v === undefined || isNaN(v)) return "n/a";
    return Number(v).toLocaleString("en-GB", { minimumFractionDigits: d || 0, maximumFractionDigits: d || 0 });
  };
  var esc = function (s) {
    return String(s === null || s === undefined ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  };
  var qLabel = function (q) { return q.replace(/^(\d{4})Q(\d)$/, "Q$2 $1"); };
  var store = {
    get: function (k) { try { return window.localStorage.getItem("qhb:" + k); } catch (e) { return null; } },
    set: function (k, v) { try { window.localStorage.setItem("qhb:" + k, v); } catch (e) { /* storage blocked */ } }
  };
  function svgEl(tag, attrs, parent) {
    var el = document.createElementNS(NS, tag);
    for (var k in attrs) el.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(el);
    return el;
  }
  function svgText(parent, x, y, text, attrs) {
    var t = svgEl("text", Object.assign({ x: x, y: y }, attrs || {}), parent);
    t.textContent = text;
    return t;
  }
  // Charts are drawn at the width of their box, so text stays 12px on a phone.
  function boxW(box) { return Math.max(280, Math.round(box.clientWidth || 520)); }
  function niceMax(v) {
    if (v <= 0) return 1;
    var p = Math.pow(10, Math.floor(Math.log10(v)));
    var n = v / p;
    var step = n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10;
    return step * p;
  }
  function ticks(max, count) {
    var out = [];
    for (var i = 0; i <= count; i++) out.push((max / count) * i);
    return out;
  }
  function table(cols, rows) {
    var h = "<table><thead><tr>";
    cols.forEach(function (c) { h += '<th class="' + (c.n ? "n" : "") + '">' + esc(c.label) + "</th>"; });
    h += "</tr></thead><tbody>";
    rows.forEach(function (r) {
      h += "<tr>";
      cols.forEach(function (c) {
        var v = c.html ? c.html(r) : esc(c.f ? c.f(r) : r[c.key]);
        h += '<td class="' + (c.n ? "n" : "") + (c.cls ? " " + c.cls : "") + '">' + v + "</td>";
      });
      h += "</tr>";
    });
    return h + "</tbody></table>";
  }

  // ---------- tooltip ----------
  var tip = $("tip");
  function showTip(evt, title, lines) {
    tip.innerHTML = "<b>" + esc(title) + "</b>" + lines.map(esc).join("<br>");
    tip.hidden = false;
    moveTip(evt);
  }
  function moveTip(evt) {
    var pad = 14, w = tip.offsetWidth, h = tip.offsetHeight;
    var x = evt.clientX + pad, y = evt.clientY + pad;
    if (x + w > window.innerWidth - 8) x = evt.clientX - w - pad;
    if (y + h > window.innerHeight - 8) y = evt.clientY - h - pad;
    tip.style.left = Math.max(8, x) + "px";
    tip.style.top = Math.max(8, y) + "px";
  }
  function hideTip() { tip.hidden = true; }
  function bindHover(chart, hit, mark, title, lines) {
    var on = function (e) {
      chart.classList.add("dim");
      (Array.isArray(mark) ? mark : [mark]).forEach(function (m) { m.classList.add("on"); });
      showTip(e, title, lines);
    };
    var off = function () {
      chart.classList.remove("dim");
      (Array.isArray(mark) ? mark : [mark]).forEach(function (m) { m.classList.remove("on"); });
      hideTip();
    };
    hit.addEventListener("pointerenter", on);
    hit.addEventListener("pointermove", moveTip);
    hit.addEventListener("pointerleave", off);
    hit.addEventListener("pointerdown", on);
  }
  document.addEventListener("scroll", hideTip, { passive: true });

  // Rounded top bar: 4px radius at the data end, square at the baseline.
  function barPath(x, y, w, h, r) {
    r = Math.min(r, w / 2, h);
    return "M" + x + "," + (y + h) + "V" + (y + r) + "Q" + x + "," + y + " " + (x + r) + "," + y +
      "H" + (x + w - r) + "Q" + (x + w) + "," + y + " " + (x + w) + "," + (y + r) + "V" + (y + h) + "Z";
  }
  // Rounded right end for horizontal bars (or left end if negative).
  function hbarPath(x0, y, len, h, r) {
    var neg = len < 0, L = Math.abs(len);
    r = Math.min(r, h / 2, L);
    if (!neg) {
      return "M" + x0 + "," + y + "H" + (x0 + L - r) + "Q" + (x0 + L) + "," + y + " " + (x0 + L) + "," + (y + r) +
        "V" + (y + h - r) + "Q" + (x0 + L) + "," + (y + h) + " " + (x0 + L - r) + "," + (y + h) + "H" + x0 + "Z";
    }
    return "M" + x0 + "," + y + "H" + (x0 - L + r) + "Q" + (x0 - L) + "," + y + " " + (x0 - L) + "," + (y + r) +
      "V" + (y + h - r) + "Q" + (x0 - L) + "," + (y + h) + " " + (x0 - L + r) + "," + (y + h) + "H" + x0 + "Z";
  }

  // ---------- state ----------
  var quarters = B.meta.quarters;
  var saved = store.get("quarter");
  var state = {
    q: quarters.indexOf(saved) >= 0 ? saved : B.meta.latest_quarter,
    indicator: store.get("indicator") || "nmr_per_1000"
  };
  var cov = {}; B.coverage.forEach(function (c) { cov[c.quarter] = c; });
  var nat = {}; B.national.forEach(function (n) { nat[n.quarter] = n; });
  var trend = {}; B.trends.forEach(function (t) { trend[t.quarter] = t; });
  var full = function (q) { return cov[q] && !cov[q].low_coverage; };
  var monthsChip = function (q) {
    var c = cov[q];
    return c.months_present + " of " + c.months_expected + " months";
  };

  var INDICATORS = [
    { key: "nmr_per_1000", label: "Neonatal mortality", unit: "per 1,000 live births", d: 1 },
    { key: "stillbirth_rate_per_1000", label: "Stillbirth rate", unit: "per 1,000 births", d: 1 },
    { key: "preterm_pct", label: "Preterm births", unit: "% of live births", d: 1 },
    { key: "lbw_pct", label: "Low birth weight", unit: "% of live births", d: 1 },
    { key: "apgar_lt7_pct", label: "Apgar score below 7", unit: "% of live births", d: 1 },
    { key: "deliveries", label: "Deliveries", unit: "count, depends on months in data", d: 0 }
  ];
  if (!INDICATORS.some(function (i) { return i.key === state.indicator; })) state.indicator = "nmr_per_1000";

  // ---------- quarter switch ----------
  function buildSwitch() {
    var box = $("quarterSwitch");
    box.innerHTML = "";
    quarters.forEach(function (q) {
      var b = document.createElement("button");
      b.type = "button";
      b.setAttribute("role", "radio");
      b.setAttribute("aria-checked", q === state.q ? "true" : "false");
      b.tabIndex = q === state.q ? 0 : -1;
      b.innerHTML = esc(qLabel(q).split(" ")[0]) + "<small>" + cov[q].months_present + "/" + cov[q].months_expected + " mo</small>";
      b.title = qLabel(q) + ": " + monthsChip(q) + " in the data";
      b.addEventListener("click", function () { setQuarter(q); });
      b.addEventListener("keydown", function (e) {
        var i = quarters.indexOf(state.q);
        if (e.key === "ArrowRight" || e.key === "ArrowDown") { e.preventDefault(); setQuarter(quarters[Math.min(quarters.length - 1, i + 1)], true); }
        if (e.key === "ArrowLeft" || e.key === "ArrowUp") { e.preventDefault(); setQuarter(quarters[Math.max(0, i - 1)], true); }
      });
      box.appendChild(b);
    });
  }
  function setQuarter(q, focus) {
    state.q = q;
    store.set("quarter", q);
    buildSwitch();
    if (focus) {
      var on = document.querySelector('#quarterSwitch [aria-checked="true"]');
      if (on) on.focus();
    }
    renderQuarter();
  }

  // ---------- overview ----------
  function renderTiles() {
    var q = state.q, n = nat[q], t = trend[q], c = cov[q];
    var chip = c.low_coverage
      ? '<span class="chip">' + esc(monthsChip(q)) + "</span>"
      : '<span class="chip ok">Full quarter</span>';
    var change = function (pctKey, isCount) {
      if (!t || t[pctKey] === null) return "First quarter in the data";
      if (isCount && !t.comparable) return "Not comparable with the quarter before";
      var v = t[pctKey];
      return (v > 0 ? "Up " : v < 0 ? "Down " : "No change ") + (v !== 0 ? fmt(Math.abs(v), 1) + "%" : "") + " on the quarter before";
    };
    var tiles = [
      { l: "Deliveries", v: fmt(n.deliveries), d: change("deliveries_change_pct", true), chip: chip },
      { l: "Live births", v: fmt(n.live_births), d: fmt(n.stillbirths) + " stillbirths", chip: chip },
      { l: "Stillbirth rate", v: fmt(n.stillbirth_rate_per_1000, 1), u: "per 1,000", d: change("stillbirth_rate_per_1000_change_pct"), chip: "" },
      { l: "Neonatal mortality", v: fmt(n.nmr_per_1000, 1), u: "per 1,000", d: change("nmr_per_1000_change_pct"), chip: "" }
    ];
    $("tiles").innerHTML = tiles.map(function (x) {
      return '<div class="tile"><span class="l">' + esc(x.l) + '</span><span class="v">' + x.v +
        (x.u ? "<small>" + esc(x.u) + "</small>" : "") + '</span><span class="d">' + esc(x.d) + "</span>" + x.chip + "</div>";
    }).join("");
    $("coverageNote").textContent = qLabel(q) + " has " + monthsChip(q) + " in the data (" + c.months_in_data + "), from " +
      fmt(c.facilities_reporting) + " facilities." + (c.low_coverage ? " Counts are partial; rates are still comparable." : "");
    document.querySelectorAll("[data-qlabel]").forEach(function (el) { el.textContent = qLabel(q); });
  }

  function renderDeliveries() {
    var box = $("chartDeliveries");
    box.innerHTML = "";
    var W = boxW(box), H = 280, m = { t: 26, r: 8, b: 44, l: 52 };
    var iw = W - m.l - m.r, ih = H - m.t - m.b;
    var max = niceMax(Math.max.apply(null, B.national.map(function (n) { return n.deliveries; })));
    var svg = svgEl("svg", { viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": "Bar chart of deliveries by quarter" }, box);
    var g = svgEl("g", { class: "grid" }, svg);
    ticks(max, 4).forEach(function (v) {
      var y = m.t + ih - (v / max) * ih;
      svgEl("line", { x1: m.l, x2: W - m.r, y1: y, y2: y }, g);
      svgText(svg, m.l - 8, y + 4, fmt(v), { "text-anchor": "end" });
    });
    var band = iw / quarters.length, bw = Math.min(64, band * 0.56);
    B.national.forEach(function (n) {
      var i = quarters.indexOf(n.quarter);
      var x = m.l + band * i + (band - bw) / 2;
      var h = (n.deliveries / max) * ih, y = m.t + ih - h;
      var col = full(n.quarter) ? "var(--series-1)" : "var(--series-2)";
      var p = svgEl("path", { d: barPath(x, y, bw, h, 4), fill: col, class: "mark" }, svg);
      svgText(svg, x + bw / 2, y - 7, fmt(n.deliveries), { "text-anchor": "middle", class: "v" });
      svgText(svg, x + bw / 2, m.t + ih + 18, qLabel(n.quarter).split(" ")[0], { "text-anchor": "middle", class: n.quarter === state.q ? "lab" : "" });
      svgText(svg, x + bw / 2, m.t + ih + 34, cov[n.quarter].months_present + (band < 110 ? "/" + cov[n.quarter].months_expected + " mo" : " of " + cov[n.quarter].months_expected + " months"), { "text-anchor": "middle", "font-size": "11" });
      var hit = svgEl("rect", { x: m.l + band * i, y: m.t, width: band, height: ih, class: "hit" }, svg);
      bindHover(box, hit, p, qLabel(n.quarter), [
        fmt(n.deliveries) + " deliveries",
        cov[n.quarter].months_present + " of " + cov[n.quarter].months_expected + " months in the data (" + cov[n.quarter].months_in_data + ")",
        "Monthly average: " + fmt(n.deliveries / cov[n.quarter].months_present)
      ]);
    });
    svgEl("line", { x1: m.l, x2: W - m.r, y1: m.t + ih, y2: m.t + ih, class: "axis" }, svg);
    $("tableDeliveries").innerHTML = table([
      { label: "Quarter", f: function (r) { return qLabel(r.quarter); } },
      { label: "Months in data", f: function (r) { return cov[r.quarter].months_present + " of " + cov[r.quarter].months_expected; }, n: 1 },
      { label: "Deliveries", f: function (r) { return fmt(r.deliveries); }, n: 1 },
      { label: "Per month", f: function (r) { return fmt(r.deliveries / cov[r.quarter].months_present); }, n: 1 }
    ], B.national);
  }

  function renderRates() {
    var box = $("chartRates");
    box.innerHTML = "";
    var series = [
      { key: "nmr_per_1000", name: "Neonatal mortality", color: "var(--series-1)" },
      { key: "stillbirth_rate_per_1000", name: "Stillbirth rate", color: "var(--series-2)" }
    ];
    $("legendRates").innerHTML = series.map(function (s) {
      return '<span><i style="background:' + s.color + '"></i>' + esc(s.name) + "</span>";
    }).join("");
    var W = boxW(box), narrow = W < 460;
    var H = 280, m = { t: 20, r: narrow ? 44 : 150, b: 32, l: 36 };
    var iw = W - m.l - m.r, ih = H - m.t - m.b;
    var max = niceMax(Math.max.apply(null, B.national.map(function (n) { return Math.max(n.nmr_per_1000, n.stillbirth_rate_per_1000); })) * 1.1);
    var svg = svgEl("svg", { viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": "Line chart of neonatal mortality and stillbirth rates by quarter" }, box);
    var g = svgEl("g", { class: "grid" }, svg);
    ticks(max, 4).forEach(function (v) {
      var y = m.t + ih - (v / max) * ih;
      svgEl("line", { x1: m.l, x2: m.l + iw, y1: y, y2: y }, g);
      svgText(svg, m.l - 8, y + 4, fmt(v), { "text-anchor": "end" });
    });
    var step = iw / (quarters.length - 1);
    var X = function (i) { return m.l + step * i; };
    var Y = function (v) { return m.t + ih - (v / max) * ih; };
    quarters.forEach(function (q, i) {
      svgText(svg, X(i), m.t + ih + 20, qLabel(q).split(" ")[0], { "text-anchor": "middle", class: q === state.q ? "lab" : "" });
    });
    svgEl("line", { x1: m.l, x2: m.l + iw, y1: m.t + ih, y2: m.t + ih, class: "axis" }, svg);
    var dots = quarters.map(function () { return []; });
    series.forEach(function (s) {
      var d = B.national.map(function (n, i) { return (i ? "L" : "M") + X(i) + "," + Y(n[s.key]); }).join("");
      svgEl("path", { d: d, fill: "none", stroke: s.color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }, svg);
      B.national.forEach(function (n, i) {
        dots[i].push(svgEl("circle", { cx: X(i), cy: Y(n[s.key]), r: 4.5, fill: s.color, stroke: "var(--panel)", "stroke-width": 2, class: "mark" }, svg));
      });
      var last = B.national[B.national.length - 1];
      svgText(svg, X(quarters.length - 1) + 10, Y(last[s.key]) + 4, (narrow ? "" : s.name + " ") + fmt(last[s.key], 1), { class: "v", "font-size": "12" });
    });
    var cross = svgEl("line", { x1: 0, x2: 0, y1: m.t, y2: m.t + ih, stroke: "var(--axis)", "stroke-dasharray": "3 3", visibility: "hidden" }, svg);
    quarters.forEach(function (q, i) {
      var hit = svgEl("rect", { x: X(i) - step / 2, y: m.t, width: step, height: ih, class: "hit" }, svg);
      var n = nat[q];
      bindHover(box, hit, dots[i], qLabel(q), [
        "Neonatal mortality: " + fmt(n.nmr_per_1000, 1) + " per 1,000",
        "Stillbirth rate: " + fmt(n.stillbirth_rate_per_1000, 1) + " per 1,000",
        cov[q].months_present + " of " + cov[q].months_expected + " months in the data"
      ]);
      hit.addEventListener("pointerenter", function () { cross.setAttribute("x1", X(i)); cross.setAttribute("x2", X(i)); cross.setAttribute("visibility", "visible"); });
      hit.addEventListener("pointerleave", function () { cross.setAttribute("visibility", "hidden"); });
    });
    $("tableRates").innerHTML = table([
      { label: "Quarter", f: function (r) { return qLabel(r.quarter); } },
      { label: "Neonatal mortality", f: function (r) { return fmt(r.nmr_per_1000, 1); }, n: 1 },
      { label: "Early (0 to 7 days)", f: function (r) { return fmt(r.early_nmr_per_1000, 1); }, n: 1 },
      { label: "Late (8 to 28 days)", f: function (r) { return fmt(r.late_nmr_per_1000, 1); }, n: 1 },
      { label: "Stillbirth rate", f: function (r) { return fmt(r.stillbirth_rate_per_1000, 1); }, n: 1 }
    ], B.national);
  }

  function renderFalseDrop() {
    var last = B.national[B.national.length - 1];
    var prevFull = null;
    for (var i = B.national.length - 2; i >= 0; i--) { if (full(B.national[i].quarter)) { prevFull = B.national[i]; break; } }
    var lq = last.quarter, lc = cov[lq];
    if (!lc.low_coverage || !prevFull) {
      $("falseDrop").parentElement.hidden = true;
      return;
    }
    var drop = (last.deliveries - prevFull.deliveries) / prevFull.deliveries * 100;
    var perL = last.deliveries / lc.months_present, perP = prevFull.deliveries / cov[prevFull.quarter].months_present;
    var perChange = (perL - perP) / perP * 100;
    $("falseDrop").parentElement.querySelector("h3").textContent = "Why " + qLabel(lq).split(" ")[0] + " seems to fall by a third";
    $("falseDrop").textContent = qLabel(lq) + " shows " + fmt(last.deliveries) + " deliveries against " + fmt(prevFull.deliveries) +
      " in " + qLabel(prevFull.quarter) + ", a change of " + fmt(drop, 1) + "%. But only " + lc.months_present +
      " of its " + lc.months_expected + " months are in the data (" + lc.months_in_data + "). Per month, deliveries went from " + fmt(perP) + " to " + fmt(perL) +
      ", a change of " + (perChange > 0 ? "+" : "") + fmt(perChange, 1) + "%. The fall is missing data, not fewer births.";
  }

  // ---------- indicators ----------
  function buildIndicatorSelect() {
    var sel = $("indicatorSelect");
    sel.innerHTML = INDICATORS.map(function (i) {
      return '<option value="' + i.key + '"' + (i.key === state.indicator ? " selected" : "") + ">" + esc(i.label) + "</option>";
    }).join("");
    sel.addEventListener("change", function () {
      state.indicator = sel.value;
      store.set("indicator", sel.value);
      renderProvince();
    });
  }
  function hbarChart(box, rows, opts) {
    box.innerHTML = "";
    var W = boxW(box), stack = W < 460;
    var rowH = stack ? 44 : 34, m = { t: 6, r: 60, b: 6, l: stack ? 0 : (opts.labelW || 110) };
    var H = m.t + m.b + rowH * rows.length, iw = W - m.l - m.r;
    var max = opts.max || niceMax(Math.max.apply(null, rows.map(function (r) { return r.v; })));
    var svg = svgEl("svg", { viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": opts.aria }, box);
    rows.forEach(function (r, i) {
      var y = m.t + rowH * i, bh = 18, by = stack ? y + 20 : y + (rowH - bh) / 2;
      var len = Math.max(0, (r.v / max) * iw);
      if (stack) svgText(svg, 0, y + 13, r.label, { class: "lab" });
      else svgText(svg, m.l - 10, by + 13, r.label, { "text-anchor": "end" });
      svgEl("rect", { x: m.l, y: by, width: iw, height: bh, rx: 4, fill: "var(--soft)" }, svg);
      var p = svgEl("path", { d: hbarPath(m.l, by, len, bh, 4), fill: r.color || "var(--series-1)", class: "mark" }, svg);
      svgText(svg, m.l + len + 8, by + 13, r.text, { class: "v" });
      var hit = svgEl("rect", { x: 0, y: y, width: W, height: rowH, class: "hit" }, svg);
      bindHover(box, hit, p, r.label, r.tip);
    });
  }
  function renderProvince() {
    var ind = INDICATORS.filter(function (i) { return i.key === state.indicator; })[0];
    var rows = B.by_province.filter(function (p) { return p.quarter === state.q; })
      .sort(function (a, b) { return (b[ind.key] || 0) - (a[ind.key] || 0); });
    $("provinceTitle").textContent = ind.label + " by province";
    var natV = nat[state.q][ind.key];
    $("provinceSub").textContent = qLabel(state.q) + ", " + ind.unit + ". National figure: " + fmt(natV, ind.d) + ".";
    hbarChart($("chartProvince"), rows.map(function (p) {
      return {
        label: p.province, v: p[ind.key] || 0, text: fmt(p[ind.key], ind.d),
        tip: [ind.label + ": " + fmt(p[ind.key], ind.d) + " (" + ind.unit + ")",
          fmt(p.deliveries) + " deliveries, " + fmt(p.live_births) + " live births",
          p.months_present + " of " + cov[state.q].months_expected + " months in the data"]
      };
    }), { aria: ind.label + " by province, " + qLabel(state.q), labelW: 96 });
    $("tableProvince").innerHTML = table([
      { label: "Province", key: "province" },
      { label: "Deliveries", f: function (r) { return fmt(r.deliveries); }, n: 1 },
      { label: "Stillbirth rate", f: function (r) { return fmt(r.stillbirth_rate_per_1000, 1); }, n: 1 },
      { label: "Neonatal mortality", f: function (r) { return fmt(r.nmr_per_1000, 1); }, n: 1 },
      { label: "Preterm %", f: function (r) { return fmt(r.preterm_pct, 1); }, n: 1 },
      { label: "Low birth weight %", f: function (r) { return fmt(r.lbw_pct, 1); }, n: 1 },
      { label: "Apgar < 7 %", f: function (r) { return fmt(r.apgar_lt7_pct, 1); }, n: 1 }
    ], rows);
  }
  function renderCauses() {
    var n = nat[state.q];
    var causes = [
      { k: "cause_birth_asphyxia_pct", l: "Birth asphyxia" },
      { k: "cause_prematurity_pct", l: "Prematurity" },
      { k: "cause_sepsis_pct", l: "Sepsis" },
      { k: "cause_congenital_pct", l: "Congenital" },
      { k: "cause_other_pct", l: "Other or unknown" }
    ].map(function (c) { return { label: c.l, v: n[c.k] || 0 }; });
    var named = causes.slice(0, 4).sort(function (a, b) { return b.v - a.v; });
    var rows = named.concat([causes[4]]);
    hbarChart($("chartCauses"), rows.map(function (c) {
      return { label: c.label, v: c.v, text: fmt(c.v, 1) + "%", tip: [fmt(c.v, 1) + "% of neonatal deaths, " + qLabel(state.q)] };
    }), { aria: "Cause of neonatal death, " + qLabel(state.q), max: 50, labelW: 128 });
    $("tableCauses").innerHTML = table([
      { label: "Cause", key: "label" },
      { label: "Share of deaths", f: function (r) { return fmt(r.v, 1) + "%"; }, n: 1 }
    ], rows);
  }

  // ---------- facilities ----------
  function renderTop() {
    var rows = B.top[state.q] || [];
    var max = Math.max.apply(null, rows.map(function (r) { return r.deliveries; }).concat([1]));
    $("tableTop").innerHTML = table([
      { label: "#", html: function (r) { return '<span class="rank">' + r.rank + "</span>"; } },
      { label: "Facility", key: "facility_name", cls: "name" },
      { label: "District", key: "district" },
      { label: "Province", key: "province" },
      { label: "Deliveries", n: 1, html: function (r) {
        return '<div class="bar-cell"><span class="track"><span class="fill" style="width:' + (r.deliveries / max * 100).toFixed(1) + '%"></span></span>' + fmt(r.deliveries) + "</div>";
      } },
      { label: "Months", f: function (r) { return r.months_present + " of " + cov[state.q].months_expected; }, n: 1 },
      { label: "Flag", html: function (r) {
        return r.conflicting_in_year ? '<span class="tag warn" title="This facility sent two different reports for at least one month this year">Provisional</span>' : '<span class="tag ok">Clean</span>';
      } }
    ], rows);
  }

  // ---------- reporting ----------
  function renderPerformance() {
    var P = B.performance;
    $("perfTiles").innerHTML = [
      { l: "Completeness seen in the data", v: fmt(P.mean_observed, 1) + "%", d: "Average across " + fmt(B.meta.facilities) + " facilities: months with a report out of 12" },
      { l: "Completeness facilities report", v: fmt(P.mean_self, 1) + "%", d: "Average of the figure in the governance file" },
      { l: "Gap above 10 points", v: fmt(P.n_over_10), d: "Facilities where the two figures differ by more than 10 points" }
    ].map(function (x) {
      return '<div class="tile"><span class="l">' + esc(x.l) + '</span><span class="v">' + x.v + '</span><span class="d">' + esc(x.d) + "</span></div>";
    }).join("");
    $("perfTiles").classList.add("three");

    var rows = P.rows.filter(function (r) { return r.gap !== null; })
      .sort(function (a, b) { return Math.abs(b.gap) - Math.abs(a.gap); }).slice(0, 10);
    var box = $("chartGaps");
    box.innerHTML = "";
    var W = boxW(box), stack = W < 460;
    var rowH = stack ? 42 : 32, m = { t: 22, r: 44, b: 6, l: stack ? 0 : 190 };
    var H = m.t + m.b + rowH * rows.length, iw = W - m.l - m.r;
    var lim = niceMax(Math.max.apply(null, rows.map(function (r) { return Math.abs(r.gap); })));
    var hasNeg = rows.some(function (r) { return r.gap < 0; });
    var x0 = hasNeg ? m.l + iw / 2 : m.l, span = hasNeg ? iw / 2 : iw;
    var svg = svgEl("svg", { viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": "Largest gaps between observed and self-reported completeness" }, box);
    svgText(svg, x0 + 4, 12, "Data shows more", { "font-size": "11" });
    if (hasNeg) svgText(svg, x0 - 4, 12, "Data shows less", { "font-size": "11", "text-anchor": "end" });
    rows.forEach(function (r, i) {
      var y = m.t + rowH * i, bh = 16, by = stack ? y + 20 : y + (rowH - bh) / 2;
      var len = (r.gap / lim) * span;
      if (stack) svgText(svg, 0, y + 13, r.facility_name, { class: "lab" });
      else svgText(svg, m.l - 10, by + 12, r.facility_name, { "text-anchor": "end" });
      var p = svgEl("path", { d: hbarPath(x0, by, len, bh, 4), fill: r.gap >= 0 ? "var(--series-1)" : "var(--series-2)", class: "mark" }, svg);
      svgText(svg, len >= 0 ? x0 + len + 6 : x0 + len - 6, by + 12, (r.gap > 0 ? "+" : "") + fmt(r.gap, 0), { class: "v", "text-anchor": len >= 0 ? "start" : "end" });
      var hit = svgEl("rect", { x: 0, y: y, width: W, height: rowH, class: "hit" }, svg);
      bindHover(box, hit, p, r.facility_name, [
        r.district + ", " + r.province,
        "Seen in the data: " + fmt(r.observed_completeness, 1) + "%",
        "Facility's own figure: " + fmt(r.self_reported_completeness, 1) + "%",
        "Gap: " + (r.gap > 0 ? "+" : "") + fmt(r.gap, 1) + " points"
      ]);
    });
    svgEl("line", { x1: x0, x2: x0, y1: m.t - 4, y2: H - m.b, class: "axis" }, svg);
  }

  var statusUI = { search: "", province: "", status: "" };
  function buildStatusControls() {
    var S = B.status;
    $("statusProvince").innerHTML = '<option value="">All provinces</option>' +
      B.meta.provinces.map(function (p) { return '<option value="' + esc(p) + '">' + esc(p) + "</option>"; }).join("");
    $("statusFilter").innerHTML = '<option value="">All statuses</option>' +
      ["Reporting", "Partial", "Silent"].map(function (s) {
        return '<option value="' + s + '">' + s + " (" + (S.counts[s] || 0) + ")</option>";
      }).join("");
    var counts = ["Reporting", "Partial", "Silent"].map(function (s) { return (S.counts[s] || 0) + " " + s.toLowerCase(); }).join(", ");
    var allPartial = (S.counts.Partial || 0) === S.rows.length;
    $("statusNote").textContent = counts + "." + (allPartial
      ? " Every facility is partial because February and December are missing for all of them. That is an export gap, not 117 separate problems."
      : "");
    $("statusSearch").addEventListener("input", function (e) { statusUI.search = e.target.value.trim().toLowerCase(); renderStatus(); });
    $("statusProvince").addEventListener("change", function (e) { statusUI.province = e.target.value; renderStatus(); });
    $("statusFilter").addEventListener("change", function (e) { statusUI.status = e.target.value; renderStatus(); });
  }
  function renderStatus() {
    var rows = B.status.rows.filter(function (r) {
      if (statusUI.province && r.province !== statusUI.province) return false;
      if (statusUI.status && r.status !== statusUI.status) return false;
      if (statusUI.search) {
        var hay = (r.facility_name + " " + r.district + " " + r.facility_id).toLowerCase();
        if (hay.indexOf(statusUI.search) < 0) return false;
      }
      return true;
    });
    $("tableStatus").innerHTML = rows.length ? table([
      { label: "Facility", key: "facility_name", cls: "name" },
      { label: "District", key: "district" },
      { label: "Last month", key: "last_month_reported" },
      { label: "Months", f: function (r) { return r.months_present + "/12"; }, n: 1 },
      { label: "Status", html: function (r) {
        var cls = r.status === "Reporting" ? "ok" : r.status === "Silent" ? "warn" : "neutral";
        return '<span class="tag ' + cls + '">' + esc(r.status) + "</span>";
      } }
    ], rows) : '<p class="fine" style="padding:12px">No facility matches these filters.</p>';
    $("statusCount").textContent = "Showing " + rows.length + " of " + B.status.rows.length + " facilities.";
  }

  // ---------- data quality ----------
  function renderIssues() {
    $("issues").innerHTML = B.issues.map(function (i) {
      var ids = i.affected_ids ? "Examples: " + i.affected_ids + (i.affected_ids_total > 10 ? " and " + fmt(i.affected_ids_total - 10) + " more" : "") : "";
      return '<article class="issue"><div class="ih"><b>' + esc(i.issue) + '</b><span class="cnt">' + fmt(i.count) + "</span></div>" +
        "<p>" + esc(i.detail) + "</p>" + (ids ? '<div class="ids">' + esc(ids) + "</div>" : "") + "</article>";
    }).join("");
    $("passed").textContent = B.checks_passed.length
      ? "Checks with no findings: " + B.checks_passed.join("; ") + "."
      : "";
  }

  // ---------- links, nav ----------
  function wireLinks() {
    ["pdfLink", "dlPdf"].forEach(function (id) { $(id).setAttribute("href", B.meta.pdf); });
    $("generated").textContent = "Built " + B.meta.generated_at + ". Repeated reports are handled with the rule " + B.meta.duplicate_rule + " until the M&E unit sets the real rule.";
    var links = Array.prototype.slice.call(document.querySelectorAll(".tabs a"));
    var sections = links.map(function (a) { return document.querySelector(a.getAttribute("href")); });
    if ("IntersectionObserver" in window) {
      var io = new IntersectionObserver(function (entries) {
        entries.forEach(function (en) {
          if (en.isIntersecting) {
            links.forEach(function (a) { a.classList.toggle("on", a.getAttribute("href") === "#" + en.target.id); });
          }
        });
      }, { rootMargin: "-40% 0px -55% 0px" });
      sections.forEach(function (s) { if (s) io.observe(s); });
    }
  }

  function renderQuarter() {
    renderTiles();
    renderDeliveries();
    renderRates();
    renderProvince();
    renderCauses();
    renderTop();
  }

  var lastW = window.innerWidth, rt = null;
  window.addEventListener("resize", function () {
    if (window.innerWidth === lastW) return;
    lastW = window.innerWidth;
    clearTimeout(rt);
    rt = setTimeout(function () { renderQuarter(); renderPerformance(); }, 150);
  });

  buildSwitch();
  buildIndicatorSelect();
  buildStatusControls();
  wireLinks();
  renderFalseDrop();
  renderQuarter();
  renderPerformance();
  renderStatus();
  renderIssues();
})();
