/*
 * HCMS browser check. Keeps this browser's proof current by signing a server challenge with the
 * browser's private key. The key is non-exportable and lives in IndexedDB, so it cannot be copied.
 */
(function () {
  var DB_NAME = "hcms-access", STORE = "keys", KEY_ID = "browser";
  var MAX_CHECK_TRIES = 2;

  function openDb() {
    return new Promise(function (resolve, reject) {
      var req = indexedDB.open(DB_NAME, 1);
      req.onupgradeneeded = function () { req.result.createObjectStore(STORE); };
      req.onsuccess = function () { resolve(req.result); };
      req.onerror = function () { reject(req.error); };
    });
  }

  function storeKey(privateKey) {
    return openDb().then(function (db) {
      return new Promise(function (resolve, reject) {
        var tx = db.transaction(STORE, "readwrite");
        tx.objectStore(STORE).put(privateKey, KEY_ID);
        tx.oncomplete = function () { resolve(true); };
        tx.onerror = function () { reject(tx.error); };
      });
    });
  }

  function loadKey() {
    return openDb().then(function (db) {
      return new Promise(function (resolve, reject) {
        var req = db.transaction(STORE, "readonly").objectStore(STORE).get(KEY_ID);
        req.onsuccess = function () { resolve(req.result || null); };
        req.onerror = function () { reject(req.error); };
      });
    });
  }

  function base64url(buffer) {
    var bytes = new Uint8Array(buffer), text = "";
    for (var i = 0; i < bytes.length; i++) { text += String.fromCharCode(bytes[i]); }
    return btoa(text).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  function refresh() {
    return loadKey().then(function (key) {
      if (!key) { return false; }
      return fetch("/access/challenge/", { credentials: "same-origin", cache: "no-store" })
        .then(function (r) { return r.ok ? r.text() : Promise.reject(new Error("challenge")); })
        .then(function (nonce) {
          return crypto.subtle.sign({ name: "ECDSA", hash: "SHA-256" }, key,
                                    new TextEncoder().encode(nonce))
            .then(function (sig) {
              var body = new URLSearchParams({ nonce: nonce, signature: base64url(sig) });
              return fetch("/access/proof/", { method: "POST", body: body, credentials: "same-origin" });
            });
        })
        .then(function (r) { return r.ok; })
        .catch(function () { return false; });
    });
  }

  window.HCMSProof = { refresh: refresh, storeKey: storeKey };

  var body = document.body;
  if (!body) { return; }
  if (body.hasAttribute("data-hcms-check")) {
    // The server answered 403 because the proof was missing or expired. Refresh and reload. If this
    // browser has no key, or the refresh fails, say so at once rather than leaving the page spinning.
    function refused() {
      body.innerHTML = "<p>This browser could not be verified. Ask an administrator to check it.</p>";
    }
    var tries = Number(sessionStorage.getItem("hcms-tries") || 0);
    if (tries < MAX_CHECK_TRIES) {
      sessionStorage.setItem("hcms-tries", String(tries + 1));
      refresh().then(function (ok) { if (ok) { location.replace(location.href); } else { refused(); } });
    } else {
      refused();
    }
    return;
  }
  sessionStorage.removeItem("hcms-tries");
  refresh();
  setInterval(refresh, 2 * 60 * 1000);
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") { refresh(); }
  });
})();
