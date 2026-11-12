// ==UserScript==
// @name         jobmarket — lưu trang TopCV đang xem
// @namespace    jobmarket-crawl.local
// @version      1.0
// @description  Lưu trang danh sách / chi tiết tin TopCV BẠN đang xem thành file jobmarket-topcv-*.html trong thư mục Downloads; jobmarket (start/watch/import-html) tự chuyển vào data/inbox/topcv và nạp. Không gửi request nào, không tự mở trang.
// @match        https://www.topcv.vn/*
// @grant        none
// @run-at       document-idle
// ==/UserScript==

// Mức "Vàng" trong quy tắc thu thập (config.toml): chỉ ghi lại trang bạn tự mở trong trình duyệt của bạn — script
// không duyệt, không bấm, không tải thêm trang nào. Cài bằng Tampermonkey/Violentmonkey (Chrome/Edge/Firefox).
(function () {
  "use strict";

  const PREFIX = "jobmarket-topcv-";
  const AUTO_KEY = "jobmarket-topcv-auto";

  function pageKind() {
    if (document.querySelector(".job-item-search-result")) return "listing";
    for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
      if (s.textContent.includes("JobPosting")) return "detail";
    }
    return null;
  }

  function fileName(kind) {
    const now = new Date();
    const pad = (n) => String(n).padStart(2, "0");
    const stamp =
      `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-` +
      `${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
    const slug = (location.pathname + location.search)
      .replace(/[^A-Za-z0-9-]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 80);
    return `${PREFIX}${kind}-${stamp}-${slug || "trang"}.html`;
  }

  function save(kind) {
    const html = "<!DOCTYPE html>\n" + document.documentElement.outerHTML;
    const url = URL.createObjectURL(new Blob([html], { type: "text/html;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = fileName(kind);
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
    toast(`Đã lưu ${kind === "listing" ? "trang danh sách" : "trang chi tiết"} cho jobmarket`);
  }

  function readAuto() {
    try {
      return localStorage.getItem(AUTO_KEY) === "1";
    } catch (e) {
      return false;
    }
  }

  function writeAuto(on) {
    try {
      localStorage.setItem(AUTO_KEY, on ? "1" : "0");
    } catch (e) {
      /* trình duyệt chặn lưu cục bộ → chỉ dùng nút bấm */
    }
  }

  let box;
  function toast(text) {
    const note = document.createElement("div");
    note.textContent = text;
    note.style.cssText = "margin-top:6px;padding:6px 10px;background:#1b5e20;color:#fff;border-radius:6px;font:13px sans-serif";
    box.appendChild(note);
    setTimeout(() => note.remove(), 3000);
  }

  const kind = pageKind();
  if (!kind) return;

  box = document.createElement("div");
  box.style.cssText = "position:fixed;right:16px;bottom:16px;z-index:2147483647;text-align:right";
  const button = document.createElement("button");
  button.textContent = "Lưu cho jobmarket";
  button.style.cssText = "padding:8px 12px;border:0;border-radius:6px;background:#00b14f;color:#fff;font:600 13px sans-serif;cursor:pointer";
  button.addEventListener("click", () => save(kind));
  const label = document.createElement("label");
  label.style.cssText = "display:block;margin-top:4px;font:12px sans-serif;color:#333;background:#fff;padding:2px 6px;border-radius:4px";
  const auto = document.createElement("input");
  auto.type = "checkbox";
  auto.checked = readAuto();
  auto.addEventListener("change", () => writeAuto(auto.checked));
  label.append(auto, " tự lưu mỗi trang tin tôi mở");
  box.append(button, label);
  document.body.appendChild(box);

  if (auto.checked) save(kind);
})();
