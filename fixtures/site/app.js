/* Microblog fixture - plain JS, no dependencies. The API the page calls
   is the same one the agent sees: reads work, the write endpoint 404s. */

async function apiGet(path) {
  const r = await fetch(path, { credentials: "include" });
  return r.json();
}

async function refreshFeed() {
  const data = await apiGet("/api/feed");
  const ul = document.getElementById("feed");
  ul.textContent = "";
  for (const p of data.posts) {
    const li = document.createElement("li");
    li.className = "post";
    li.dataset.postId = p.id;
    const b = document.createElement("b");
    b.textContent = p.author;
    const span = document.createElement("span");
    span.textContent = " " + p.text;
    li.append(b, span);
    ul.append(li);
  }
}

async function checkSession() {
  // session cookie presence decides which view to show
  const r = await fetch("/api/feed", { credentials: "include" });
  const hasSid = document.cookie.includes("sid=");
  if (hasSid) {
    document.getElementById("login-view").hidden = true;
    document.getElementById("main-view").hidden = false;
    document.getElementById("whoami").textContent = "signed in as testuser";
  } else {
    document.getElementById("login-view").hidden = false;
    document.getElementById("main-view").hidden = true;
    document.getElementById("whoami").textContent = "not logged in";
  }
}

async function tryPost(text) {
  // mirrors the agent contract: try API first, expect 404, fall back
  let r;
  try {
    r = await fetch("/api/posts", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
  } catch (e) {
    r = { status: 0 };
  }
  if (r.status === 404) {
    // browser fallback: form-post to the site endpoint
    const form = document.createElement("form");
    form.method = "POST";
    form.action = "/site/api/post";
    const ta = document.createElement("input");
    ta.name = "text";
    ta.value = text;
    form.append(ta);
    document.body.append(form);
    form.submit();
    return { via: "form-fallback" };
  }
  return { via: "api", status: r.status };
}

document.getElementById("composer").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const text = document.getElementById("composer-text").value.trim();
  if (!text) return;
  const btn = document.getElementById("post-btn");
  btn.disabled = true;
  const res = await tryPost(text);
  if (res.via === "form-fallback") return; // page navigates
  const msg = document.getElementById("composer-msg");
  msg.hidden = false;
  msg.textContent = res.status === 201 ? "Posted (via API)" : "Post failed";
  btn.disabled = false;
  refreshFeed();
});

checkSession();
refreshFeed();
