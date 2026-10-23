(function () {
  "use strict";
  var el = document.getElementById("page-data");
  if (!el) return;
  var D = JSON.parse(el.textContent);
  var $ = function (id) { return document.getElementById(id); };
  var num = function (v) { var n = parseFloat(v); return isFinite(n) ? n : NaN; };
  var fmt = function (n, d) {
    if (!isFinite(n)) return "";
    return n.toLocaleString("zh-TW", { minimumFractionDigits: 0, maximumFractionDigits: d === undefined ? 2 : d });
  };
  var round = function (n, d) { var p = Math.pow(10, d); return Math.round(n * p) / p; };

  /* ------------------------------------------------------------ 發票對獎 */
  var TIERS = [[8, "頭獎", 200000], [7, "二獎", 40000], [6, "三獎", 10000], [5, "四獎", 4000], [4, "五獎", 1000], [3, "六獎", 200]];

  function suffixLen(a, b) {
    var k = 0;
    while (k < a.length && k < b.length && a[a.length - 1 - k] === b[b.length - 1 - k]) k++;
    return k;
  }

  function checkInvoice(p, n) {
    // 回傳 {cls, msg}
    var L = n.length;
    if (L < 3) return { cls: "", msg: "請輸入至少 3 碼" };
    var full = L === 8;
    if (full && n === p.special) return { cls: "win", msg: "🎉 恭喜中特別獎 1,000 萬元！" };
    if (full && n === p.grand) return { cls: "win", msg: "🎉 恭喜中特獎 200 萬元！" };
    var best = null;
    p.first.forEach(function (f) {
      var k = suffixLen(n, f);
      if (k >= 3 && (!best || k > best.k)) best = { k: k, f: f };
    });
    var extraHit = (p.extra || []).indexOf(n.slice(-3)) >= 0;
    var hints = [];
    if (!full) {
      if (p.special.slice(-L) === n) hints.push("特別獎");
      if (p.grand.slice(-L) === n) hints.push("特獎");
    }
    if (best) {
      var tier = TIERS.filter(function (t) { return t[0] <= best.k; })[0];
      if (full || best.k < L) {
        return { cls: "win", msg: "🎉 中" + tier[1] + " " + fmt(tier[2]) + " 元！（對中頭獎 " + best.f + " 末 " + best.k + " 碼）" };
      }
      return { cls: "win", msg: "🎉 至少中" + tier[1] + " " + fmt(tier[2]) + " 元！對中頭獎 " + best.f + "，請再往前比對看能不能更高" };
    }
    if (extraHit) return { cls: "win", msg: "🎉 中增開六獎 200 元！" };
    if (hints.length) return { cls: "maybe", msg: "末 " + L + " 碼與" + hints.join("、") + "相同，請核對完整 8 碼！" };
    return { cls: "lose", msg: "沒有中獎，再接再厲 🙂" };
  }

  function setupInvoice(getPeriod) {
    var input = $("inv-input"), out = $("inv-result"), logEl = $("inv-log");
    if (!input) return;
    var lastLogged = "";
    function run() {
      var n = input.value.replace(/\D/g, "").slice(0, 8);
      if (n !== input.value) input.value = n;
      if (!n) { out.className = "result"; out.textContent = "請輸入號碼"; return; }
      var r = checkInvoice(getPeriod(), n);
      out.className = "result " + r.cls;
      out.textContent = r.msg;
      if ((n.length === 3 || n.length === 8) && n !== lastLogged) {
        lastLogged = n;
        var li = document.createElement("li");
        li.textContent = n;
        if (r.cls === "win" || r.cls === "maybe") li.className = "win";
        logEl.insertBefore(li, logEl.firstChild);
        while (logEl.children.length > 30) logEl.removeChild(logEl.lastChild);
        // 3 碼對完自動清空，方便連續對獎
        if (n.length === 3 && r.cls === "lose") setTimeout(function () { if (input.value === n) { input.value = ""; } }, 700);
      }
    }
    input.addEventListener("input", run);
    input.focus({ preventScroll: true });
    return run;
  }

  if (D.type === "invoice") {
    setupInvoice(function () { return D; });
  }
  if (D.type === "invoice-multi") {
    var cur = 0;
    var rerun = setupInvoice(function () { return D.periods[cur]; });
    document.querySelectorAll(".tab").forEach(function (b) {
      b.addEventListener("click", function () {
        cur = +b.getAttribute("data-period");
        document.querySelectorAll(".tab").forEach(function (x) { x.classList.toggle("on", x === b); });
        document.querySelectorAll(".period").forEach(function (x) { x.hidden = +x.getAttribute("data-idx") !== cur; });
        $("inv-log").innerHTML = "";
        rerun();
      });
    });
  }

  /* ------------------------------------------------------------ 油價試算 */
  if (D.type === "oil") {
    var t = $("oil-type"), l = $("oil-l"), m = $("oil-money"), o = $("oil-result");
    var calc = function (src) {
      var p = num(t.value);
      if (src === "money" && m.value) {
        var liters = num(m.value) / p;
        l.value = round(liters, 2);
        o.textContent = fmt(num(m.value), 0) + " 元可以加約 " + fmt(liters, 2) + " 公升";
      } else {
        var cost = num(l.value) * p;
        if (src !== "type") m.value = "";
        o.textContent = isFinite(cost) ? fmt(num(l.value)) + " 公升 × " + p.toFixed(1) + " 元 = 約 " + fmt(cost, 0) + " 元" : "";
        if (src === "type" && m.value) calc("money");
      }
    };
    t.addEventListener("change", function () { calc(m.value ? "money" : "type"); });
    l.addEventListener("input", function () { calc("l"); });
    m.addEventListener("input", function () { calc("money"); });
    calc("l");
  }

  /* ------------------------------------------------------------ 匯率換算 */
  if (D.type === "rate") {
    var sel = $("rate-cur"), fi = $("rate-foreign"), ti = $("rate-twd");
    var rate = function () { return D.rates[sel.value]; };
    var dg = function () { return rate() < 0.05 ? 0 : 2; };
    var f2t = function () { var v = num(fi.value); ti.value = isFinite(v) ? round(v * rate(), 2) : ""; };
    var t2f = function () { var v = num(ti.value); fi.value = isFinite(v) ? round(v / rate(), dg() || 2) : ""; };
    fi.addEventListener("input", f2t);
    ti.addEventListener("input", t2f);
    sel.addEventListener("change", f2t);
    f2t();
  }

  /* ------------------------------------------------------------ 坪數換算 */
  if (D.type === "ping") {
    var M2 = 3.305785, FT2 = 35.5832;
    var inputs = document.querySelectorAll(".unit");
    var conv = function (srcEl) {
      var v = num(srcEl.value), u = srcEl.getAttribute("data-unit");
      var ping = u === "ping" ? v : u === "m2" ? v / M2 : v / FT2;
      var vals = { ping: round(ping, 4), m2: round(ping * M2, 3), ft2: round(ping * FT2, 2) };
      inputs.forEach(function (x) {
        if (x !== srcEl) x.value = isFinite(ping) ? vals[x.getAttribute("data-unit")] : "";
      });
    };
    inputs.forEach(function (x) { x.addEventListener("input", function () { conv(x); }); });
    conv($("u-ping"));
  }

  /* ------------------------------------------------------------ 民國西元 */
  if (D.type === "roc") {
    var roc = $("y-roc"), ad = $("y-ad"), yr = $("y-result");
    var AN = "鼠牛虎兔龍蛇馬羊猴雞狗豬", nowY = new Date().getFullYear();
    var show = function () {
      var y = num(ad.value);
      if (!isFinite(y)) { yr.textContent = ""; return; }
      var r = y - 1911;
      var rocTxt = r > 0 ? "民國 " + r + " 年" : "民國前 " + (1 - r) + " 年";
      var age = nowY - y;
      yr.textContent = "西元 " + y + " 年 ＝ " + rocTxt + " ・ 生肖" + AN.charAt(((y - 4) % 12 + 12) % 12) +
        (age >= 0 ? " ・ 今年滿 " + age + " 歲" : "");
    };
    roc.addEventListener("input", function () { var v = num(roc.value); ad.value = isFinite(v) ? v + 1911 : ""; show(); });
    ad.addEventListener("input", function () { var v = num(ad.value); roc.value = isFinite(v) ? v - 1911 : ""; show(); });
    show();
  }

  /* ------------------------------------------------------------ 薪資換算 */
  if (D.type === "salary") {
    var hpd = $("s-hpd"), dpm = $("s-dpm"), sals = document.querySelectorAll(".sal"), lastSrc = $("s-hour");
    var calcSal = function (src) {
      lastSrc = src;
      var v = num(src.value), k = src.getAttribute("data-k"), H = num(hpd.value) || 8, Dm = num(dpm.value) || 21.75;
      var hour = k === "hour" ? v : k === "day" ? v / H : k === "month" ? v / (H * Dm) : v / (12 * H * Dm);
      var vals = { hour: round(hour, 1), day: Math.round(hour * H), month: Math.round(hour * H * Dm), year: Math.round(hour * H * Dm * 12) };
      sals.forEach(function (x) { if (x !== src) x.value = isFinite(hour) ? vals[x.getAttribute("data-k")] : ""; });
    };
    sals.forEach(function (x) { x.addEventListener("input", function () { calcSal(x); }); });
    [hpd, dpm].forEach(function (x) { x.addEventListener("input", function () { calcSal(lastSrc); }); });
    calcSal(lastSrc);
  }
})();
