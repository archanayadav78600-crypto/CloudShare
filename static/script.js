/*
 * CloudShare - script.js
 *
 * Frontend logic. It talks to the Flask JSON API with fetch() / XMLHttpRequest:
 *   GET /api/stats, GET /api/files, POST /api/upload,
 *   GET /api/download/<id>, DELETE /api/files/<id>, POST /api/share/<id>
 *
 * Security note: file names come from users, so we NEVER put them into
 * innerHTML. We always use textContent, which prevents XSS attacks.
 * The browser checks below (size / extension) are only for convenience:
 * the Flask server validates everything again.
 */
"use strict";

(function () {
  // ---------- Element references ----------
  const $ = (id) => document.getElementById(id);

  const dropzone = $("dropzone");
  const fileInput = $("fileInput");
  const uploadQueue = $("uploadQueue");
  const searchInput = $("searchInput");
  const filterChips = $("filterChips");
  const fileTableBody = $("fileTableBody");
  const tableWrap = $("tableWrap");
  const loadingState = $("loadingState");
  const emptyState = $("emptyState");
  const confirmDialog = $("confirmDialog");
  const shareDialog = $("shareDialog");

  const MAX_BYTES = Number(dropzone.dataset.maxBytes);
  const MAX_LABEL = dropzone.dataset.maxLabel;
  const ALLOWED_EXTENSIONS = new Set(dropzone.dataset.allowed.split(","));

  const CATEGORY_LABELS = {
    images: "Images",
    documents: "Documents",
    spreadsheets: "Spreadsheets",
    presentations: "Presentations",
    archives: "Archives",
  };

  const SHARE_INFO_TEXT =
    "Sharing is not available in the local version. It will be added with AWS S3 pre-signed URLs.";

  const state = {
    search: "",
    type: "all",
    pendingDelete: null,
    listLoaded: false,
  };

  // ---------- Small helpers ----------
  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function formatDate(isoString) {
    const date = new Date(isoString);
    if (Number.isNaN(date.getTime())) return isoString;
    return date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
  }

  function showToast(message, kind) {
    const toast = el("div", "toast toast-" + (kind || "info"), message);
    toast.addEventListener("click", () => toast.remove());
    $("toastContainer").appendChild(toast);
    setTimeout(() => toast.remove(), 5000);
  }

  // Wrapper around fetch() that gives a friendly message when the server is down.
  async function request(url, options) {
    try {
      return await fetch(url, options);
    } catch (networkError) {
      throw new Error("Cannot reach the server. Is the Flask app running?");
    }
  }

  async function readJson(response) {
    try {
      return await response.json();
    } catch (parseError) {
      return null;
    }
  }

  // Calls the API and returns parsed JSON, or throws Error(message).
  async function api(url, options) {
    const response = await request(url, options);
    const data = await readJson(response);
    if (!response.ok) {
      throw new Error((data && data.error) || "Request failed (" + response.status + ").");
    }
    return data;
  }

  function getExtension(fileName) {
    const parts = fileName.split(".");
    return parts.length > 1 ? parts.pop().toLowerCase() : "";
  }

  function createBadge(file) {
    return el("span", "file-badge cat-" + file.category, file.extension.toUpperCase().slice(0, 4));
  }

  // ---------- Dashboard rendering ----------
  function renderStats(stats) {
    $("statTotalFiles").textContent = String(stats.total_files);
    $("statStorage").textContent = stats.total_size_readable;

    const list = $("categoryList");
    list.replaceChildren();
    Object.keys(CATEGORY_LABELS).forEach((key) => {
      const info = stats.categories[key] || { count: 0 };
      const percent = stats.total_files ? Math.round((info.count / stats.total_files) * 100) : 0;

      const row = el("li", "category-row");
      const bar = el("div", "category-bar");
      const fill = el("div", "category-bar-fill");
      fill.style.width = percent + "%";
      bar.appendChild(fill);

      row.append(el("span", "", CATEGORY_LABELS[key]), bar, el("span", "category-count", String(info.count)));
      list.appendChild(row);
    });
  }

  function renderRecent(recentFiles) {
    const list = $("recentList");
    list.replaceChildren();
    $("recentEmpty").hidden = recentFiles.length > 0;

    recentFiles.forEach((file) => {
      const item = el("li", "recent-item");
      const info = el("div", "recent-info");
      info.append(
        el("div", "recent-name", file.name),
        el("div", "recent-meta", file.size_readable + " · " + formatDate(file.uploaded_at))
      );
      item.append(createBadge(file), info);
      list.appendChild(item);
    });
  }

  function renderFiles(payload) {
    fileTableBody.replaceChildren();
    loadingState.hidden = true;
    $("fileCountLabel").textContent = "Showing " + payload.count + " of " + payload.total_count;

    if (payload.files.length === 0) {
      tableWrap.hidden = true;
      emptyState.hidden = false;
      if (payload.total_count === 0) {
        $("emptyTitle").textContent = "No files yet";
        $("emptyText").textContent = "Upload your first file using the upload area above.";
      } else {
        $("emptyTitle").textContent = "No matching files";
        $("emptyText").textContent = "Try a different search or choose another file type.";
      }
      return;
    }

    emptyState.hidden = true;
    tableWrap.hidden = false;
    payload.files.forEach((file) => fileTableBody.appendChild(createFileRow(file)));
  }

  function showListError(message) {
    loadingState.hidden = true;
    tableWrap.hidden = true;
    emptyState.hidden = false;
    $("emptyTitle").textContent = "Could not load files";
    $("emptyText").textContent = message;
  }

  function createCell(label, className) {
    const cell = el("td", className || "");
    cell.dataset.label = label;
    return cell;
  }

  function createFileRow(file) {
    const row = el("tr");

    const nameCell = createCell("Name", "name-cell");
    const nameWrap = el("div", "file-name-cell");
    nameWrap.append(createBadge(file), el("span", "file-name", file.name));
    nameCell.appendChild(nameWrap);

    const typeCell = createCell("Type");
    typeCell.textContent = CATEGORY_LABELS[file.category] || file.extension.toUpperCase();
    const sizeCell = createCell("Size");
    sizeCell.textContent = file.size_readable;
    const dateCell = createCell("Uploaded");
    dateCell.textContent = formatDate(file.uploaded_at);

    const actionsCell = createCell("Actions");
    const actions = el("div", "action-buttons");

    const downloadButton = el("button", "btn btn-ghost btn-small", "Download");
    downloadButton.type = "button";
    downloadButton.setAttribute("aria-label", "Download " + file.name);
    downloadButton.addEventListener("click", () => downloadFile(file, downloadButton));

    const shareButton = el("button", "btn btn-ghost btn-small", "Share");
    shareButton.type = "button";
    shareButton.setAttribute("aria-label", "Share " + file.name);
    shareButton.addEventListener("click", () => openShareDialog(file));

    const deleteButton = el("button", "btn btn-small btn-danger-outline", "Delete");
    deleteButton.type = "button";
    deleteButton.setAttribute("aria-label", "Delete " + file.name);
    deleteButton.addEventListener("click", () => openDeleteDialog(file));

    actions.append(downloadButton, shareButton, deleteButton);
    actionsCell.appendChild(actions);

    row.append(nameCell, typeCell, sizeCell, dateCell, actionsCell);
    return row;
  }

  // ---------- Loading data ----------
  async function loadStats() {
    const stats = await api("/api/stats");
    renderStats(stats);
    renderRecent(stats.recent);
  }

  async function loadFiles() {
    if (!state.listLoaded) loadingState.hidden = false;
    const params = new URLSearchParams({ search: state.search, type: state.type });
    const payload = await api("/api/files?" + params.toString());
    state.listLoaded = true;
    renderFiles(payload);
  }

  // Refresh the dashboard cards, recent list and file list together.
  async function refreshAll() {
    try {
      await Promise.all([loadStats(), loadFiles()]);
    } catch (error) {
      showListError(error.message);
      showToast(error.message, "error");
    }
  }

  // ---------- Upload ----------
  function createQueueItem(fileName) {
    const item = el("div", "queue-item");
    const top = el("div", "queue-top");
    const name = el("span", "queue-name", fileName);
    const status = el("span", "queue-status", "Waiting...");
    top.append(name, status);

    const progress = el("div", "progress");
    const bar = el("div", "progress-bar");
    progress.appendChild(bar);
    item.append(top, progress);
    uploadQueue.appendChild(item);
    return { item, status, bar };
  }

  function markQueueError(ui, message) {
    ui.item.classList.add("error");
    ui.status.textContent = message;
    ui.bar.style.width = "0%";
  }

  function markQueueSuccess(ui) {
    ui.item.classList.add("success");
    ui.status.textContent = "Uploaded";
    ui.bar.style.width = "100%";
    setTimeout(() => ui.item.remove(), 3000);
  }

  // Quick checks in the browser. The server repeats them - never skip those!
  function checkFileInBrowser(file) {
    const extension = getExtension(file.name);
    if (!ALLOWED_EXTENSIONS.has(extension)) {
      return extension
        ? "Files of type ." + extension + " are not allowed."
        : "The file needs an extension, for example .pdf.";
    }
    if (file.size === 0) return "The file is empty.";
    if (file.size > MAX_BYTES) return "The file is larger than the " + MAX_LABEL + " limit.";
    return "";
  }

  // Uploads one file with XMLHttpRequest because it reports upload progress.
  function uploadFile(file, ui) {
    return new Promise((resolve) => {
      const xhr = new XMLHttpRequest();
      const formData = new FormData();
      formData.append("file", file);

      xhr.upload.addEventListener("progress", (event) => {
        if (event.lengthComputable) {
          const percent = Math.round((event.loaded / event.total) * 100);
          ui.bar.style.width = percent + "%";
          ui.status.textContent = percent + "%";
        }
      });

      xhr.addEventListener("load", () => {
        let data = null;
        try {
          data = JSON.parse(xhr.responseText);
        } catch (parseError) {
          data = null;
        }
        if (xhr.status >= 200 && xhr.status < 300) {
          markQueueSuccess(ui);
          showToast((data && data.message) || file.name + " uploaded.", "success");
          resolve(true);
        } else {
          const message = (data && data.error) || "Upload failed (" + xhr.status + ").";
          markQueueError(ui, message);
          showToast(file.name + ": " + message, "error");
          resolve(false);
        }
      });

      xhr.addEventListener("error", () => {
        const message = "Upload failed. The server may be offline or the file too large.";
        markQueueError(ui, message);
        showToast(file.name + ": " + message, "error");
        resolve(false);
      });

      xhr.addEventListener("abort", () => {
        markQueueError(ui, "Upload cancelled.");
        resolve(false);
      });

      xhr.open("POST", "/api/upload");
      xhr.send(formData);
    });
  }

  async function handleFiles(fileList) {
    const files = Array.from(fileList);
    if (files.length === 0) return;

    // Remove old failed items from a previous batch.
    uploadQueue.querySelectorAll(".queue-item.error").forEach((item) => item.remove());

    let uploadedCount = 0;
    for (const file of files) {
      const ui = createQueueItem(file.name);
      const problem = checkFileInBrowser(file);
      if (problem) {
        markQueueError(ui, problem);
        showToast(file.name + ": " + problem, "error");
        continue;
      }
      ui.status.textContent = "0%";
      if (await uploadFile(file, ui)) uploadedCount += 1;
    }

    if (uploadedCount > 0) await refreshAll();
  }

  // ---------- Download ----------
  async function downloadFile(file, button) {
    button.disabled = true;
    try {
      const response = await request("/api/download/" + encodeURIComponent(file.id));
      if (!response.ok) {
        const data = await readJson(response);
        if (response.status === 404) refreshAll();
        throw new Error((data && data.error) || "Download failed (" + response.status + ").");
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = file.name;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      showToast("Downloading " + file.name, "success");
    } catch (error) {
      showToast(error.message, "error");
    } finally {
      button.disabled = false;
    }
  }

  // ---------- Delete (with confirmation dialog) ----------
  function openDeleteDialog(file) {
    state.pendingDelete = file;
    $("confirmFileName").textContent = file.name;
    confirmDialog.showModal();
  }

  async function confirmDelete() {
    const file = state.pendingDelete;
    confirmDialog.close();
    state.pendingDelete = null;
    if (!file) return;
    try {
      const data = await api("/api/files/" + encodeURIComponent(file.id), { method: "DELETE" });
      showToast(data.message, "success");
    } catch (error) {
      showToast(error.message, "error");
    }
    await refreshAll();
  }

  // ---------- Share (not available in the local version) ----------
  async function openShareDialog(file) {
    $("shareMessage").textContent = "Checking...";
    shareDialog.showModal();
    try {
      const response = await request("/api/share/" + encodeURIComponent(file.id), { method: "POST" });
      const data = await readJson(response);
      $("shareMessage").textContent = (data && data.error) || SHARE_INFO_TEXT;
    } catch (error) {
      $("shareMessage").textContent = error.message;
    }
  }

  // ---------- Event wiring ----------
  // Upload area: click, keyboard (via the button) and file picker.
  dropzone.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("click", (event) => event.stopPropagation());
  fileInput.addEventListener("change", () => {
    handleFiles(fileInput.files);
    fileInput.value = ""; // lets the user pick the same file again
  });

  // Drag and drop.
  ["dragenter", "dragover"].forEach((name) => {
    dropzone.addEventListener(name, (event) => {
      event.preventDefault();
      dropzone.classList.add("dragover");
    });
  });
  ["dragleave", "drop"].forEach((name) => {
    dropzone.addEventListener(name, (event) => {
      event.preventDefault();
      dropzone.classList.remove("dragover");
    });
  });
  dropzone.addEventListener("drop", (event) => handleFiles(event.dataTransfer.files));

  // If a file is dropped outside the drop zone, do not let the browser open it.
  window.addEventListener("dragover", (event) => event.preventDefault());
  window.addEventListener("drop", (event) => event.preventDefault());

  // Search (waits 250 ms after typing stops, to avoid too many requests).
  let searchTimer = null;
  searchInput.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      state.search = searchInput.value.trim();
      loadFiles().catch((error) => showToast(error.message, "error"));
    }, 250);
  });

  // File type filter chips.
  filterChips.addEventListener("click", (event) => {
    const chip = event.target.closest(".chip");
    if (!chip) return;
    filterChips.querySelectorAll(".chip").forEach((other) => {
      const isActive = other === chip;
      other.classList.toggle("active", isActive);
      other.setAttribute("aria-pressed", String(isActive));
    });
    state.type = chip.dataset.type;
    loadFiles().catch((error) => showToast(error.message, "error"));
  });

  // Dialogs.
  $("confirmCancel").addEventListener("click", () => {
    state.pendingDelete = null;
    confirmDialog.close();
  });
  $("confirmDelete").addEventListener("click", confirmDelete);
  confirmDialog.addEventListener("cancel", () => {
    state.pendingDelete = null;
  });
  $("shareClose").addEventListener("click", () => shareDialog.close());
  $("navShare").addEventListener("click", () => {
    $("shareMessage").textContent = SHARE_INFO_TEXT;
    shareDialog.showModal();
    document.body.classList.remove("sidebar-open");
  });

  // Mobile sidebar.
  $("menuToggle").addEventListener("click", () => document.body.classList.toggle("sidebar-open"));
  $("sidebarBackdrop").addEventListener("click", () => document.body.classList.remove("sidebar-open"));
  document.querySelectorAll("a.nav-link").forEach((link) => {
    link.addEventListener("click", () => document.body.classList.remove("sidebar-open"));
  });

  // ---------- Start ----------
  refreshAll();
})();
