/* SupportPilot web chat widget v2. One file, no dependencies.
   Embed:
     <script src="chat-widget.js" data-api="https://YOUR-N8N/webhook"
             data-title="ShopEase Support" data-color="#1a6ef5"></script>
   Needs three n8n webhooks under data-api:
     POST /web-verify   {session, email, order}  -> {verified, locked, name}
     POST /web-chat     {session, text}         -> (any 200)
     GET  /web-poll?session=..&after=ID          -> {messages:[{id, role, content}]}   */
(function () {
  var script = document.currentScript;
  var API = (script.getAttribute("data-api") || "").replace(/\/$/, "");
  var TITLE = script.getAttribute("data-title") || "Support";
  var COLOR = script.getAttribute("data-color") || "#1a6ef5";
  var K_SESSION = "sp_session", K_NAME = "sp_name";
  var MAX_WAIT_MS = 90000;
  // ngrok free domains can show a browser warning page that breaks fetch(); this header skips it.
  var H = /ngrok/i.test(API) ? { "ngrok-skip-browser-warning": "1" } : {};
  function postHeaders() { var h = { "Content-Type": "application/json" }; for (var k in H) h[k] = H[k]; return h; }

  function store(k, v) { try { if (v === null) localStorage.removeItem(k); else if (v !== undefined) localStorage.setItem(k, v); return localStorage.getItem(k); } catch (e) { return null; } }
  function uuid() { return (window.crypto && crypto.randomUUID) ? crypto.randomUUID() : "s" + Date.now().toString(16) + Math.random().toString(16).slice(2) + Math.random().toString(16).slice(2); }
  var session = store(K_SESSION) || store(K_SESSION, uuid());
  var name = store(K_NAME);
  var lastId = 0, waiting = false, waitStart = 0, timer = null, started = false, unread = false;

  /* ---------- styles ---------- */
  var css = document.createElement("style");
  css.textContent =
    "#sp-btn{position:fixed;right:20px;bottom:20px;width:58px;height:58px;border-radius:50%;border:0;background:" + COLOR + ";color:#fff;font-size:26px;cursor:pointer;box-shadow:0 4px 14px rgba(0,0,0,.28);z-index:99999}" +
    "#sp-dot{position:absolute;top:4px;right:4px;width:13px;height:13px;border-radius:50%;background:#e5484d;border:2px solid #fff;display:none}" +
    "#sp-box{position:fixed;right:20px;bottom:92px;width:350px;max-width:calc(100vw - 32px);height:480px;max-height:calc(100vh - 124px);display:none;flex-direction:column;background:#fff;color:#1b1f24;border-radius:14px;box-shadow:0 10px 34px rgba(0,0,0,.3);overflow:hidden;font:14px/1.45 system-ui,-apple-system,Segoe UI,sans-serif;z-index:99999}" +
    "#sp-box.open{display:flex}" +
    "#sp-head{background:" + COLOR + ";color:#fff;padding:12px 14px;display:flex;align-items:center;justify-content:space-between;font-weight:600}" +
    "#sp-x{background:none;border:0;color:#fff;font-size:22px;line-height:1;cursor:pointer;padding:0 2px}" +
    "#sp-verify{flex:1;padding:18px 16px;overflow-y:auto}" +
    "#sp-verify p{margin:0 0 12px;color:#4a5562}" +
    "#sp-verify label{display:block;font-size:12px;font-weight:600;margin:10px 0 4px;color:#4a5562}" +
    "#sp-verify input{width:100%;box-sizing:border-box;padding:10px;border:1px solid #cfd6de;border-radius:8px;font:inherit}" +
    "#sp-vbtn{margin-top:16px;width:100%;padding:11px;border:0;border-radius:8px;background:" + COLOR + ";color:#fff;font:inherit;font-weight:600;cursor:pointer}" +
    "#sp-vbtn[disabled]{opacity:.6;cursor:default}" +
    "#sp-err{color:#c62d32;font-size:13px;margin-top:10px;min-height:18px}" +
    "#sp-chat{flex:1;display:none;flex-direction:column;min-height:0}" +
    "#sp-msgs{flex:1;overflow-y:auto;padding:12px;background:#f5f7fa}" +
    ".sp-m{max-width:84%;margin:6px 0;padding:8px 11px;border-radius:12px;white-space:pre-wrap;word-wrap:break-word;overflow-wrap:anywhere}" +
    ".sp-u{background:" + COLOR + ";color:#fff;margin-left:auto;border-bottom-right-radius:3px}" +
    ".sp-a{background:#fff;border:1px solid #e1e5ea;border-bottom-left-radius:3px}" +
    ".sp-t{color:#8a94a0;font-style:italic}" +
    "#sp-form{display:flex;border-top:1px solid #e1e5ea;background:#fff}" +
    "#sp-in{flex:1;border:0;padding:12px;font:inherit;outline:none;min-width:0}" +
    "#sp-send{border:0;background:none;color:" + COLOR + ";font-weight:600;padding:0 14px;cursor:pointer;font:inherit}" +
    "#sp-foot{padding:6px 12px;font-size:11px;color:#8a94a0;background:#fff;border-top:1px solid #eef1f4;text-align:center}" +
    "#sp-foot a{color:#8a94a0;cursor:pointer;text-decoration:underline}";
  document.head.appendChild(css);

  /* ---------- markup ---------- */
  var btn = document.createElement("button");
  btn.id = "sp-btn"; btn.setAttribute("aria-label", "Open chat");
  btn.innerHTML = '💬<span id="sp-dot"></span>';
  var box = document.createElement("div");
  box.id = "sp-box"; box.setAttribute("role", "dialog"); box.setAttribute("aria-label", TITLE);
  box.innerHTML =
    '<div id="sp-head"><span id="sp-title"></span><button id="sp-x" aria-label="Close">×</button></div>' +
    '<form id="sp-verify" novalidate>' +
      '<p>To check your orders, please confirm who you are.</p>' +
      '<label for="sp-email">Email used on your order</label><input id="sp-email" type="email" autocomplete="email" maxlength="120" placeholder="you@example.com">' +
      '<label for="sp-order">Any order number</label><input id="sp-order" type="text" maxlength="20" placeholder="SE-1001">' +
      '<button id="sp-vbtn" type="submit">Start chat</button><div id="sp-err" role="alert"></div>' +
    '</form>' +
    '<div id="sp-chat"><div id="sp-msgs" aria-live="polite"></div>' +
      '<form id="sp-form"><input id="sp-in" maxlength="1000" autocomplete="off" dir="auto" placeholder="Type your message..."><button id="sp-send" type="submit">Send</button></form>' +
      '<div id="sp-foot"><span id="sp-who"></span> · <a id="sp-reset">Not you?</a></div></div>';
  document.body.appendChild(btn); document.body.appendChild(box);
  function $(id) { return box.querySelector("#" + id); }
  $("sp-title").textContent = TITLE;
  var msgs = $("sp-msgs"), input = $("sp-in"), dot = btn.querySelector("#sp-dot");

  /* ---------- helpers ---------- */
  function first(d) { return Array.isArray(d) ? d[0] : d; }
  function add(text, cls) {
    var d = document.createElement("div");
    d.className = "sp-m " + cls; d.setAttribute("dir", "auto");
    d.textContent = text;                       // textContent only: never inject HTML
    msgs.appendChild(d); msgs.scrollTop = msgs.scrollHeight; return d;
  }
  var typing = null;
  function showTyping(on) {
    if (on && !typing) typing = add("typing…", "sp-a sp-t");
    if (!on && typing) { typing.remove(); typing = null; }
  }
  function view(which) {
    $("sp-verify").style.display = which === "verify" ? "block" : "none";
    $("sp-chat").style.display = which === "chat" ? "flex" : "none";
  }
  function isOpen() { return box.classList.contains("open"); }
  function setUnread(on) { unread = on; dot.style.display = on ? "block" : "none"; }

  /* ---------- polling ---------- */
  function poll() {
    clearTimeout(timer);
    fetch(API + "/web-poll?session=" + encodeURIComponent(session) + "&after=" + lastId, { headers: H })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var list = (first(d) || {}).messages || [];
        list.forEach(function (m) {
          lastId = Math.max(lastId, m.id);
          if (m.role === "agent") {
            showTyping(false); waiting = false; add(m.content, "sp-a");
            if (!isOpen()) setUnread(true);
          }
        });
        if (waiting && Date.now() - waitStart > MAX_WAIT_MS) {
          showTyping(false); waiting = false;
          add("This is taking longer than usual. Please try again in a moment.", "sp-a");
        }
      })
      .catch(function () {})
      .then(function () { timer = setTimeout(poll, waiting ? 1500 : (isOpen() ? 5000 : 15000)); });
  }
  function loadHistory() {
    msgs.innerHTML = ""; lastId = 0;
    return fetch(API + "/web-poll?session=" + encodeURIComponent(session) + "&after=0", { headers: H })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        ((first(d) || {}).messages || []).forEach(function (m) {
          lastId = Math.max(lastId, m.id);
          add(m.content, m.role === "agent" ? "sp-a" : "sp-u");
        });
        if (!msgs.children.length) add("Hi " + (name || "there") + "! Ask me about your orders, delivery, returns or payments.", "sp-a");
      })
      .catch(function () { add("Hi! Ask me about your orders, delivery, returns or payments.", "sp-a"); });
  }
  function enterChat() {
    view("chat"); $("sp-who").textContent = name ? "Signed in as " + name : "";
    loadHistory().then(function () { started = true; poll(); input.focus(); });
  }

  /* ---------- verify ---------- */
  $("sp-verify").addEventListener("submit", function (e) {
    e.preventDefault();
    var email = $("sp-email").value.trim(), order = $("sp-order").value.trim().toUpperCase().replace(/^SE[\s_-]*(\d+)$/, "SE-$1"), err = $("sp-err"), vb = $("sp-vbtn");
    err.textContent = "";
    if (!/^\S+@\S+\.\S+$/.test(email) || !order) { err.textContent = "Please enter your email and an order number."; return; }
    vb.disabled = true; vb.textContent = "Checking…";
    fetch(API + "/web-verify", { method: "POST", headers: postHeaders(),
      body: JSON.stringify({ session: session, email: email, order: order }) })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        d = first(d) || {};
        if (d.verified) { name = d.name || "customer"; store(K_NAME, name); enterChat(); }
        else err.textContent = d.locked ? "Too many attempts. Please try again in an hour." : "We couldn't match that email and order number. Please check and try again.";
      })
      .catch(function () { err.textContent = "Connection problem. Please try again."; })
      .then(function () { vb.disabled = false; vb.textContent = "Start chat"; });
  });

  /* ---------- sending ---------- */
  $("sp-form").addEventListener("submit", function (e) {
    e.preventDefault();
    var t = input.value.trim(); if (!t) return;
    input.value = ""; add(t, "sp-u");
    waiting = true; waitStart = Date.now(); showTyping(true);
    fetch(API + "/web-chat", { method: "POST", headers: postHeaders(),
      body: JSON.stringify({ session: session, text: t }) })
      .catch(function () { showTyping(false); waiting = false; add("Connection problem. Please send that again.", "sp-a"); });
    poll();
  });

  /* ---------- open / close / reset ---------- */
  btn.addEventListener("click", function () {
    box.classList.toggle("open"); setUnread(false);
    if (isOpen()) {
      if (name && !started) enterChat();
      else if (!name) view("verify");
      else input.focus();
    }
  });
  $("sp-x").addEventListener("click", function () { box.classList.remove("open"); });
  $("sp-reset").addEventListener("click", function () {
    clearTimeout(timer); started = false; waiting = false; name = null;
    store(K_NAME, null); session = uuid(); store(K_SESSION, session);
    msgs.innerHTML = ""; $("sp-email").value = ""; $("sp-order").value = ""; $("sp-err").textContent = ""; view("verify");
  });
  view("verify");
})();
