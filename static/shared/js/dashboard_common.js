/*
 * FoundIT dashboards: code shared by the student, faculty / staff and admin pages.
 *
 * Authentication, reports, claims, notifications, and direct messages are backed by MySQL.
 * The signed-in account is the only recipient whose rows are returned by the APIs.
 */
window.FoundIT = (() => {
  "use strict";

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

  /* ------------------------------------------------------------------ Helpers */
  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>'"]/g, (character) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
    }[character]));
  }

  function plural(count, word) {
    return `${count} ${word}${count === 1 ? "" : "s"}`;
  }

  function formatDate(value) {
    if (!value) return "Unknown date";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return new Intl.DateTimeFormat(undefined, {
      month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit"
    }).format(date);
  }

  /* -------------------------------------------------------- Public board */
  async function loadCategories(selects = []) {
    const targets = selects.length ? selects : [
      "#item-category",
      "#found-item-category",
      "#edit-item-category"
    ];
    const elements = targets.map((selector) => $(selector)).filter(Boolean);
    if (!elements.length) return [];

    try {
      const response = await fetch("/api/categories", {
        headers: { Accept: "application/json" },
        cache: "no-store"
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.message || "Could not load categories.");
      const categories = Array.isArray(data.categories) ? data.categories : [];

      elements.forEach((select) => {
        const current = select.value;
        select.replaceChildren();

        const placeholder = document.createElement("option");
        placeholder.value = "";
        placeholder.textContent = categories.length ? "Select a category" : "No categories available";
        placeholder.disabled = true;
        placeholder.selected = !current;
        select.append(placeholder);

        categories.forEach((category) => {
          const option = document.createElement("option");
          option.value = category.name;
          option.textContent = category.name;
          select.append(option);
        });

        if (current && categories.some((category) => category.name === current)) {
          select.value = current;
        } else if (current) {
          const legacy = document.createElement("option");
          legacy.value = current;
          legacy.textContent = `${current} (inactive / legacy)`;
          select.append(legacy);
          select.value = current;
        }
      });

      return categories;
    } catch (error) {
      console.warn("Could not load categories.", error);
      elements.forEach((select) => {
        select.replaceChildren();
        const option = document.createElement("option");
        option.value = "";
        option.textContent = "Categories unavailable";
        option.disabled = true;
        option.selected = true;
        select.append(option);
      });
      throw error;
    }
  }

  async function loadPublicLostReports() {
    const response = await fetch("/api/public/lost-reports", { headers: { Accept: "application/json" } });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.message || "Could not load recently posted items.");
    const reports = data.reports || [];
    reports.totalCount = Number(data.count ?? reports.length);
    return reports;
  }

  async function loadPublicRecentPosts() {
    const response = await fetch("/api/public/recent-posts", { headers: { Accept: "application/json" } });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.message || "Could not load recently posted items.");
    const reports = data.reports || [];
    reports.totalCount = Number(data.count ?? reports.length);
    reports.lostCount = Number(data.lostCount ?? 0);
    reports.foundCount = Number(data.foundCount ?? 0);
    return reports;
  }

  function ensureFoundDetailsModal() {
    let modal = document.getElementById("public-item-details-modal");
    if (modal) return modal;
    modal = document.createElement("div");
    modal.id = "public-item-details-modal";
    modal.className = "public-item-modal";
    modal.hidden = true;
    modal.innerHTML = `
      <div class="public-item-modal-backdrop" data-modal-close></div>
      <section class="public-item-modal-dialog" role="dialog" aria-modal="true" aria-labelledby="public-item-modal-title">
        <button type="button" class="public-item-modal-close" data-modal-close aria-label="Close">×</button>
        <div id="public-item-modal-body"></div>
      </section>`;
    document.body.append(modal);
    modal.addEventListener("click", (event) => {
      if (event.target.matches("[data-modal-close]")) modal.hidden = true;
    });
    return modal;
  }

  async function submitClaimRequest(foundReportId, message, button) {
    const response = await fetch(`/api/found-reports/${foundReportId}/claims`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ message })
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(data.message || "The claim request could not be submitted.");
      error.status = response.status;
      error.code = data.code || null;
      throw error;
    }
    button.disabled = true;
    button.textContent = "Claim Request Submitted";
    return data;
  }

  function showAlreadyRequestedClaim(body) {
    const area = document.getElementById("claim-request-area");
    if (!area) return;
    area.innerHTML = `
      <div class="claim-request-heading"><h3>Claim Request</h3></div>
      <div class="claim-already-requested" role="status" aria-live="polite">Already requested a claim for this item.</div>`;
  }

  function isOwnPublicPost(item) {
    const user = currentUser();
    const accountId = String(document.body.dataset.userAccountId || "");
    const reporterId = String(item.reporter_id || "");
    if (!accountId || !reporterId || accountId !== reporterId) return false;

    const userRole = String(user.role || "").toLowerCase();
    const reporterRole = String(item.reporter_role || "").toLowerCase();
    const isStudent = userRole === "student" && reporterRole === "student";
    const isFacultyStaff = (userRole === "faculty_staff" || userRole.includes("faculty"))
      && reporterRole === "faculty_staff";

    // Students and Faculty/Staff cannot open their own Lost Item posts.
    // Faculty/Staff also cannot open their own Found Item posts.
    return (item.post_type === "lost" && (isStudent || isFacultyStaff))
      || (item.post_type === "found" && isFacultyStaff);
  }

  function openPublicItemDetails(item) {
    if (isOwnPublicPost(item)) return;
    const modal = ensureFoundDetailsModal();
    const body = document.getElementById("public-item-modal-body");
    const isFound = item.post_type === "found";
    const firstLetter = String(item.item_name || "?").trim().charAt(0).toUpperCase() || "?";
    const photoUrls = Array.isArray(item.photo_urls) && item.photo_urls.length
      ? item.photo_urls
      : (item.photo_url ? [item.photo_url] : []);
    const image = photoUrls.length
      ? `<div class="public-item-detail-gallery">${photoUrls.map((url, index) => `<img class="public-item-detail-photo" src="${escapeHtml(url)}" alt="Photo ${index + 1} of ${escapeHtml(item.item_name || "item")}">`).join("")}</div>`
      : `<div class="public-item-detail-fallback ${isFound ? "is-found" : ""}">${escapeHtml(firstLetter)}</div>`;
    const date = isFound ? item.date_found : item.date_lost;
    const location = isFound ? item.found_location : item.last_seen;
    const user = currentUser();
    const isOwnFoundReport = isFound && user.role === "faculty_staff" && String(document.body.dataset.userAccountId || "") === String(item.reporter_id || "");
    const claimStatus = String(item.claim_status || "").toLowerCase();
    const alreadyRequested = isFound && !isOwnFoundReport && ["pending", "accepted", "returned"].includes(claimStatus);
    const wasRejected = isFound && !isOwnFoundReport && claimStatus === "rejected";
    const claimArea = isFound && !isOwnFoundReport
      ? `<div class="claim-request-area" id="claim-request-area">
          ${alreadyRequested
            ? `<div class="claim-request-heading"><h3>Claim Request</h3></div><div class="claim-already-requested" role="status" aria-live="polite">Already requested a claim for this item.</div>`
            : `<div class="claim-request-heading"><h3>${wasRejected ? "Request a Claim Again" : "Claim this item"}</h3><span>Required</span></div>
               ${wasRejected ? `<p class="claim-retry-notice">Your previous claim request was rejected. You may submit a new claim request with additional or clearer proof.</p>` : `<p>Explain why you believe this item belongs to you. Include identifying details that only the rightful owner would reasonably know.</p>`}
               <textarea id="claim-request-message" maxlength="2000" rows="5" placeholder="Example: I believe this is my USB Dongle because..." minlength="20" required></textarea>
               <div class="claim-request-footer"><small>Minimum 20 characters.</small><button type="button" class="primary-action found-claim-button" id="submit-claim-request">Submit Claim Request</button></div>`}
        </div>` : "";
    body.innerHTML = `
      <div class="public-item-detail-grid">
        <div class="public-item-detail-media">${image}</div>
        <div class="public-item-detail-content">
          <span class="public-detail-type ${isFound ? "is-found" : ""}">${isFound ? "FOUND ITEM" : "LOST ITEM"}</span>
          <h2 id="public-item-modal-title">${escapeHtml(item.item_name || "Reported item")}</h2>
          <p class="public-item-detail-meta">${escapeHtml(item.category || "Other")} · ${isFound ? "Found" : "Last seen"}: ${escapeHtml(location || "Not specified")} · Date: ${escapeHtml(date || "Not specified")}</p>
          <div class="public-item-detail-fields">
            <div><strong>Description</strong><p>${escapeHtml(item.description || "No description provided.")}</p></div>
            ${isFound ? `<div><strong>Found by</strong><p>${escapeHtml(item.reporter_name || "Faculty / Staff")}</p></div>` : `<div><strong>Posted</strong><p>${escapeHtml(formatDate(item.createdAt))}</p></div>`}
          </div>
        </div>
      </div>
      ${claimArea}`;
    if (isFound && !isOwnFoundReport && !alreadyRequested) {
      const button = document.getElementById("submit-claim-request");
      const textarea = document.getElementById("claim-request-message");
      button.addEventListener("click", async () => {
        const message = textarea.value.trim();
        if (message.length < 20) {
          textarea.setCustomValidity("Please provide at least 20 characters proving why you believe you are the owner.");
          textarea.reportValidity();
          textarea.setCustomValidity("");
          return;
        }
        button.disabled = true;
        button.textContent = "Submitting...";
        try {
          await submitClaimRequest(item.id, message, button);
          // Keep the in-memory public-post object synchronized immediately.
          // Without this, closing and reopening the modal before a page refresh
          // would still see the old claim_status and allow a second request.
          item.claim_status = "pending";
          textarea.disabled = true;
          showAlreadyRequestedClaim(body);
        } catch (error) {
          if (error.status === 409 && error.code === "CLAIM_ALREADY_REQUESTED") {
            showAlreadyRequestedClaim(body);
            return;
          }
          button.disabled = false;
          button.textContent = "Submit Claim Request";
          alert(error.message);
        }
      });
    }
    modal.hidden = false;
  }

  function renderPublicRecentPosts(list, reports, countElement) {
    if (!list) return;
    const items = (reports || []).filter((report) => report.status === "verified");
    if (countElement) countElement.textContent = plural(Number(reports?.totalCount ?? items.length), "item");
    list.replaceChildren();
    if (!items.length) {
      list.innerHTML = `<div class="empty-state"><div class="empty-illustration" aria-hidden="true"><div class="empty-illustration-back"></div><div class="empty-illustration-front"><span>+</span></div></div><h3>No public posts yet</h3><p>Verified lost and found items will appear here.</p></div>`;
      return;
    }
    items.forEach((item) => {
      const isFound = item.post_type === "found";
      const isOwnReport = isOwnPublicPost(item);
      const card = document.createElement("article");
      card.className = `public-lost-item ${isFound ? "public-found-item" : "public-lost-item"}${isOwnReport ? " is-own-report" : ""}${isFound && isOwnReport ? " is-own-found-item" : ""}`;
      if (!isOwnReport) {
        card.tabIndex = 0;
        card.setAttribute("role", "button");
        card.setAttribute("aria-label", `View details for ${item.item_name || (isFound ? "found item" : "lost item")}`);
      } else {
        card.setAttribute("aria-label", `Your submitted ${isFound ? "found" : "lost"} item: ${item.item_name || "item"}`);
        card.title = `This is your submitted ${isFound ? "found" : "lost"} item.`;
      }
      const firstLetter = String(item.item_name || "?").trim().charAt(0).toUpperCase() || "?";
      const image = item.photo_url
        ? `<img class="public-lost-image" src="${escapeHtml(item.photo_url)}" alt="Photo of ${escapeHtml(item.item_name || "item")}">`
        : `<span class="public-lost-fallback ${isFound ? "is-found" : ""}" aria-label="No photo available">${escapeHtml(firstLetter)}</span>`;
      const location = isFound ? item.found_location : item.last_seen;
      const date = isFound ? item.date_found : item.date_lost;
      card.innerHTML = `<div class="public-lost-media">${image}</div><div class="public-lost-content"><div class="public-lost-top"><span class="activity-type ${isFound ? "is-found" : ""}">${isFound ? "FOUND ITEM" : "LOST ITEM"}</span><time>${escapeHtml(formatDate(item.createdAt))}</time></div><h3>${escapeHtml(item.item_name || "Reported item")}</h3><p class="public-lost-meta">${escapeHtml(item.category || "Other")} · ${isFound ? "Found" : "Last seen"}: ${escapeHtml(location || "Not specified")} · Date: ${escapeHtml(date || "Not specified")}</p><p class="public-lost-description">${escapeHtml(item.description || "No description provided.")}</p></div>`;
      if (!isOwnReport) {
        card.addEventListener("click", () => openPublicItemDetails(item));
        card.addEventListener("keydown", (event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            openPublicItemDetails(item);
          }
        });
      }
      list.append(card);
    });
  }

  function renderPublicLostReports(list, reports, countElement) {
    const lostOnly = (reports || []).filter((r) => !r.post_type || r.post_type === "lost");
    renderPublicRecentPosts(list, lostOnly, countElement);
  }

  function setupPublicRecentSearch(input, list, countElement, getReports) {
    if (!input || !list || typeof getReports !== "function") return;

    const applySearch = () => {
      const reports = getReports() || [];
      const query = String(input.value || "").trim().toLowerCase();
      const filtered = query
        ? reports.filter((item) => {
            const location = item.post_type === "found" ? item.found_location : item.last_seen;
            return [item.item_name, location]
              .some((value) => String(value || "").toLowerCase().includes(query));
          })
        : [...reports];

      filtered.totalCount = filtered.length;
      renderPublicRecentPosts(list, filtered, countElement);
    };

    input.addEventListener("input", applySearch);
    return applySearch;
  }

  function getInitials(name) {
    return String(name || "User").trim().split(/\s+/).slice(0, 2)
      .map((part) => part[0]?.toUpperCase() || "").join("") || "U";
  }

  function byNewest(a, b) {
    return new Date(b.createdAt || 0) - new Date(a.createdAt || 0);
  }

  /** The signed-in user, as rendered by Flask onto the <body> element. */
  function currentUser() {
    const { userName, userEmail, userRole } = document.body.dataset;
    return { name: userName, email: userEmail, role: userRole };
  }

  /* ------------------------------------------------------- Page chrome (shell) */
  function startClock() {
    const dateElement = $("#current-date");
    const timeElement = $("#current-time");
    if (!dateElement || !timeElement) return;

    const dateFormat = new Intl.DateTimeFormat(undefined, { month: "long", day: "numeric", year: "numeric" });
    const timeFormat = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: true });
    const tick = () => {
      const now = new Date();
      dateElement.textContent = dateFormat.format(now);
      timeElement.textContent = timeFormat.format(now);
    };
    tick();
    setInterval(tick, 1000);
  }

  function setupSidebarToggle() {
    const shell = $("#app-shell");
    const sidebar = $("#main-sidebar");
    const toggle = $("#sidebar-toggle");
    if (!shell || !sidebar || !toggle) return;

    toggle.addEventListener("click", () => {
      const collapsed = shell.classList.toggle("sidebar-collapsed");
      const label = collapsed ? "Expand sidebar" : "Collapse sidebar";
      sidebar.classList.toggle("collapsed", collapsed);
      toggle.setAttribute("aria-expanded", String(!collapsed));
      toggle.setAttribute("aria-label", label);
      toggle.title = label;
    });
  }

  /* ------------------------------------------------------------- My Profile */
  function setupProfileMenu() {
    const trigger = $("#profile-trigger");
    const menu = $("#profile-menu");
    const profileButton = $("#my-profile-button");
    const modal = $("#profile-modal");
    if (!trigger || !menu || !profileButton || !modal) return;

    const positionMenu = () => {
      if (menu.hidden) return;
      const rect = trigger.getBoundingClientRect();
      const gap = 8;
      const viewportPadding = 10;
      const menuWidth = Math.min(248, Math.max(210, rect.width));
      menu.style.width = `${menuWidth}px`;

      // The menu normally opens above the profile row. If the available space
      // is too small, place it below instead of letting it run off-screen.
      const menuHeight = menu.offsetHeight;
      let top = rect.top - menuHeight - gap;
      if (top < viewportPadding) top = rect.bottom + gap;
      top = Math.max(viewportPadding, Math.min(top, window.innerHeight - menuHeight - viewportPadding));

      let left = rect.left;
      left = Math.max(viewportPadding, Math.min(left, window.innerWidth - menuWidth - viewportPadding));

      menu.style.left = `${left}px`;
      menu.style.top = `${top}px`;
    };

    const setMenuOpen = (open) => {
      menu.hidden = !open;
      trigger.setAttribute("aria-expanded", String(open));
      if (open) requestAnimationFrame(positionMenu);
    };

    trigger.addEventListener("click", (event) => {
      event.stopPropagation();
      const shell = $("#app-shell");
      const sidebar = $("#main-sidebar");
      if (sidebar?.classList.contains("collapsed")) {
        sidebar.classList.remove("collapsed");
        shell?.classList.remove("sidebar-collapsed");
      }
      setMenuOpen(menu.hidden);
    });

    window.addEventListener("resize", positionMenu);
    window.addEventListener("scroll", positionMenu, true);

    document.addEventListener("click", (event) => {
      if (!menu.hidden && !menu.contains(event.target) && !trigger.contains(event.target)) {
        setMenuOpen(false);
      }
    });

    profileButton.addEventListener("click", () => {
      setMenuOpen(false);
      modal.hidden = false;
      loadProfileDetails();
    });

    $$('[data-profile-close]', modal).forEach((element) => {
      element.addEventListener("click", () => { modal.hidden = true; });
    });

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        setMenuOpen(false);
        modal.hidden = true;
      }
    });

    const photoInput = $("#profile-picture-input");
    if (photoInput) {
      photoInput.addEventListener("change", () => uploadProfilePicture(photoInput));
    }

    loadSidebarProfilePhoto();
  }

  function applyProfilePhoto(url, imageSelector, fallbackSelector) {
    const image = $(imageSelector);
    const fallback = $(fallbackSelector);
    if (!image || !fallback || !url) return;
    image.hidden = true;
    image.onload = () => {
      fallback.hidden = true;
      image.hidden = false;
    };
    image.onerror = () => {
      image.hidden = true;
      fallback.hidden = false;
    };
    image.src = `${url}${url.includes("?") ? "&" : "?"}v=${Date.now()}`;
  }

  function loadSidebarProfilePhoto() {
    applyProfilePhoto("/api/profile/photo", "#sidebar-profile-photo", ".profile-avatar-fallback");
  }

  async function loadProfileDetails() {
    const status = $("#profile-upload-status");
    try {
      const response = await fetch("/api/profile", { headers: { Accept: "application/json" }, cache: "no-store" });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.message || "Could not load your profile.");
      const profile = data.profile || {};

      const setText = (selector, value) => {
        const element = $(selector);
        if (element) element.textContent = value || "—";
      };
      setText("#profile-display-name", profile.fullName);
      setText("#profile-display-role", profile.role);
      setText("#profile-full-name", profile.fullName);
      setText("#profile-account-id", profile.accountId);
      setText("#profile-email", profile.email);
      setText("#profile-role", profile.role);
      setText("#profile-status", profile.status);
      setText("#profile-created", formatDate(profile.createdAt));

      const initials = getInitials(profile.fullName);
      const fallback = $("#profile-large-avatar-fallback");
      if (fallback) fallback.textContent = initials;
      applyProfilePhoto("/api/profile/photo", "#profile-large-photo", "#profile-large-avatar-fallback");
      if (status && !status.classList.contains("is-error")) status.textContent = "";
    } catch (error) {
      if (status) {
        status.textContent = error.message || "Could not load your profile.";
        status.classList.add("is-error");
      }
    }
  }

  async function uploadProfilePicture(input) {
    const file = input.files?.[0];
    const status = $("#profile-upload-status");
    if (!file) return;

    if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) {
      status.textContent = "Choose a JPG, PNG, or WEBP image.";
      status.classList.add("is-error");
      input.value = "";
      return;
    }
    if (file.size > 2 * 1024 * 1024) {
      status.textContent = "Profile picture must not exceed 2 MB.";
      status.classList.add("is-error");
      input.value = "";
      return;
    }

    const formData = new FormData();
    formData.append("profile_picture", file);
    status.classList.remove("is-error");
    status.textContent = "Uploading...";
    input.disabled = true;
    try {
      const response = await fetch("/api/profile/photo", { method: "POST", body: formData, headers: { Accept: "application/json" } });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.message || "Could not update your profile picture.");

      const url = data.profilePictureUrl || "/api/profile/photo";
      applyProfilePhoto(url, "#sidebar-profile-photo", ".profile-avatar-fallback");
      applyProfilePhoto(url, "#profile-large-photo", "#profile-large-avatar-fallback");
      status.textContent = "Profile picture updated successfully.";
    } catch (error) {
      status.textContent = error.message || "Could not update your profile picture.";
      status.classList.add("is-error");
    } finally {
      input.disabled = false;
      input.value = "";
    }
  }

  function setupModals() {
    $$("[data-close-modal]").forEach((button) => {
      button.addEventListener("click", () => { $(`#${button.dataset.closeModal}`).hidden = true; });
    });
    $$(".modal-backdrop").forEach((backdrop) => {
      backdrop.addEventListener("click", () => { backdrop.parentElement.hidden = true; });
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") $$(".modal").forEach((modal) => { modal.hidden = true; });
    });
  }

  function initChrome() {
    startClock();
    setupSidebarToggle();
    setupProfileMenu();
    setupModals();
  }

  /* ------------------------------------------------------------------- Router */
  /**
   * Shows one panel at a time and keeps the sidebar highlight and URL hash in sync.
   * views: { viewName: { panel: "#panel-id", nav: "#nav-link-id", render: () => {} } }
   */
  function createRouter(views, defaultView = "dashboard") {
    function navigate(name, updateHash = true) {
      const view = views[name] ? name : defaultView;

      Object.entries(views).forEach(([viewName, config]) => {
        const active = viewName === view;
        $(config.panel).hidden = !active;
        const link = $(config.nav);
        link.classList.toggle("active", active);
        if (active) link.setAttribute("aria-current", "page");
        else link.removeAttribute("aria-current");
      });

      if (updateHash) history.replaceState(null, "", `#${view}`);
      views[view].render?.();
      window.scrollTo({ top: 0, behavior: "smooth" });
    }

    Object.entries(views).forEach(([name, config]) => {
      $(config.nav).addEventListener("click", (event) => {
        event.preventDefault();
        navigate(name);
      });
    });

    window.addEventListener("hashchange", () => navigate(location.hash.slice(1), false));
    navigate(location.hash.slice(1), false);
    return { navigate };
  }

  /* -------------------------------------------------------------- Photo picker */
  /**
   * Manage a five-image report picker. The selected File objects are kept in
   * memory so a user can remove one wrong image without clearing the others.
   * The backend applies the same five-image and file-signature rules.
   */
  function setupPhotoPicker(input, preview, message) {
    if (!input || !preview || !message) return;

    let selectedFiles = [];
    const allowedTypes = new Set(["image/jpeg", "image/png", "image/webp"]);
    const allowedExtensions = /\.(jpe?g|png|webp)$/i;

    const syncInput = () => {
      const transfer = new DataTransfer();
      selectedFiles.forEach((file) => transfer.items.add(file));
      input.files = transfer.files;
    };

    const render = () => {
      preview.replaceChildren();
      selectedFiles.forEach((file, index) => {
        const card = document.createElement("div");
        card.className = "photo-preview-item";

        const image = document.createElement("img");
        image.alt = file.name;
        const objectUrl = URL.createObjectURL(file);
        image.src = objectUrl;
        image.addEventListener("load", () => URL.revokeObjectURL(objectUrl), { once: true });
        image.addEventListener("error", () => URL.revokeObjectURL(objectUrl), { once: true });

        const footer = document.createElement("div");
        footer.className = "photo-preview-footer";
        const name = document.createElement("span");
        name.textContent = file.name;
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "photo-remove-button";
        remove.setAttribute("aria-label", `Remove ${file.name}`);
        remove.textContent = "Remove";
        remove.addEventListener("click", () => {
          selectedFiles.splice(index, 1);
          syncInput();
          render();
          updateMessage();
        });

        footer.append(name, remove);
        card.append(image, footer);
        preview.append(card);
      });
    };

    const updateMessage = (customMessage = "", isError = false) => {
      message.classList.toggle("is-error", isError);
      if (customMessage) {
        message.textContent = customMessage;
        return;
      }
      message.textContent = selectedFiles.length
        ? `${plural(selectedFiles.length, "image")} selected. You can upload up to 5.`
        : "";
    };

    input.addEventListener("change", () => {
      const incoming = [...input.files];
      const invalid = incoming.find((file) => {
        const typeOkay = allowedTypes.has(file.type);
        const extensionOkay = allowedExtensions.test(file.name);
        return !(typeOkay || extensionOkay);
      });

      if (invalid) {
        input.value = "";
        updateMessage(`${invalid.name} is not supported. Use PNG, JPG, JPEG, or WEBP.`, true);
        return;
      }

      const merged = [...selectedFiles, ...incoming];
      if (merged.length > 5) {
        input.value = "";
        updateMessage("You can upload a maximum of 5 images per report.", true);
        return;
      }

      selectedFiles = merged;
      syncInput();
      render();
      updateMessage();
    });

    // Expose a small reset hook on the input so the existing shared reset API
    // can clear the in-memory File list as well as the visual preview.
    input.__founditResetPhotos = () => {
      selectedFiles = [];
      input.value = "";
      render();
      updateMessage();
    };
  }

  function resetPhotoPicker(preview, message, input = null) {
    if (input?.__founditResetPhotos) input.__founditResetPhotos();
    else if (input) input.value = "";
    preview?.replaceChildren();
    if (message) {
      message.textContent = "";
      message.classList.remove("is-error");
    }
  }

  /* ------------------------------------------------------------- Message center */
  /** Inbox backed by MySQL; only the signed-in account's messages are returned. */
  function createMessageCenter() {
    const list = $("#messages-list");

    async function render() {
      try {
        const response = await fetch("/api/messages", { headers: { Accept: "application/json" }, cache: "no-store" });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.message || "Could not load messages.");

        const messages = Array.isArray(data.messages) ? data.messages : [];
        const unread = messages.filter((message) => message.read !== true).length;
        $("#messages-count").textContent = plural(messages.length, "message");
        $("#messages-unread-count").textContent = `${unread} unread`;
        $("#messages-unread-count").classList.toggle("has-unread", unread > 0);
        $("#messages-nav-count").textContent = unread;
        list.replaceChildren();

        if (!messages.length) {
          list.innerHTML = `<div class="communication-empty"><strong>No messages yet</strong><p>Messages sent to this account will appear here.</p></div>`;
          return;
        }

        messages.forEach((message) => {
          const item = document.createElement("article");
          item.className = `message-item ${message.read === true ? "" : "unread"}`;
          item.tabIndex = 0;
          item.setAttribute("role", "button");
          item.innerHTML = `
            <div class="message-item-top">
              <div class="message-sender">
                <span class="message-avatar">${escapeHtml(getInitials(message.senderName || message.senderRole))}</span>
                <span class="message-sender-copy">
                  <span class="message-sender-name">${message.read === true ? "" : "<span class='message-unread-dot'></span>"}${escapeHtml(message.senderName || "User")}</span>
                  <span class="message-sender-role">${escapeHtml(message.senderRole || "User")}</span>
                </span>
              </div>
              <time class="message-time">${escapeHtml(formatDate(message.createdAt))}</time>
            </div>
            <div class="message-subject">${escapeHtml(message.subject || "No subject")}</div>
            <p class="message-preview">${escapeHtml(message.body || "")}</p>
            <div class="message-actions">
              <span>From: ${escapeHtml(message.senderEmail || message.senderName || "User")}</span>
              <button type="button" class="message-delete">Delete</button>
            </div>`;

          const open = async () => {
            if (message.read !== true) {
              try {
                const response = await fetch(`/api/messages/${encodeURIComponent(message.id)}/read`, { method: "PATCH" });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(data.message || "Could not mark the message as read.");
                message.read = true;
                message.readAt = new Date().toISOString();
              } catch (error) {
                console.warn("Could not mark message as read.", error);
              }
            }
            openMessage(message);
            render();
          };

          item.addEventListener("click", (event) => {
            if (!event.target.closest(".message-delete")) open();
          });
          item.addEventListener("keydown", (event) => {
            if (event.target === item && (event.key === "Enter" || event.key === " ")) {
              event.preventDefault();
              open();
            }
          });
          $(".message-delete", item).addEventListener("click", async (event) => {
            event.stopPropagation();
            if (!confirm("Delete this message?")) return;
            try {
              const response = await fetch(`/api/messages/${encodeURIComponent(message.id)}`, { method: "DELETE" });
              const data = await response.json().catch(() => ({}));
              if (!response.ok) throw new Error(data.message || "Could not delete this message.");
              await render();
            } catch (error) {
              alert(error.message || "Could not delete this message.");
            }
          });
          list.append(item);
        });
      } catch (error) {
        console.warn("Could not load database messages.", error);
        $("#messages-count").textContent = "0 messages";
        $("#messages-unread-count").textContent = "0 unread";
        $("#messages-nav-count").textContent = "0";
        list.innerHTML = `<div class="communication-empty"><strong>Messages unavailable</strong><p>${escapeHtml(error.message || "Could not load messages right now.")}</p></div>`;
      }
    }

    let activeMessage = null;

    function openMessage(message) {
      activeMessage = message;
      $("#message-modal-title").textContent = message.subject || "No subject";
      $("#message-modal-meta").textContent =
        `From ${message.senderName || "User"} · ${message.senderRole || "User"} · ${formatDate(message.createdAt)}`;
      $("#message-modal-body").textContent = message.body || "";
      $("#reply-recipient-name").textContent = message.senderName || "User";
      const replyBody = $("#reply-message-body");
      if (replyBody) {
        replyBody.value = "";
        $("#reply-character-count").textContent = "0 / 3000";
      }
      const replyButton = $("#message-reply-button");
      const replyForm = $("#reply-message-form");
      if (replyButton) replyButton.hidden = false;
      if (replyForm) { replyForm.hidden = true; replyForm.reset(); }
      $("#message-view-actions")?.removeAttribute("hidden");
      $("#message-modal").hidden = false;
    }

    function setupReply() {
      const button = $("#message-reply-button");
      const form = $("#reply-message-form");
      const cancel = $("#reply-cancel-button");
      if (!button || !form) return;

      const body = $("#reply-message-body");
      const counter = $("#reply-character-count");
      const updateCounter = () => {
        if (body && counter) counter.textContent = `${body.value.length} / 3000`;
      };
      body?.addEventListener("input", updateCounter);

      button.addEventListener("click", () => {
        if (!activeMessage) return;
        form.hidden = false;
        button.hidden = true;
        $("#message-view-actions")?.setAttribute("hidden", "hidden");
        updateCounter();
        body?.focus();
      });

      cancel?.addEventListener("click", () => {
        form.reset();
        form.hidden = true;
        button.hidden = false;
        if (counter) counter.textContent = "0 / 3000";
        $("#message-view-actions")?.removeAttribute("hidden");
      });

      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (!activeMessage || !form.checkValidity()) { form.reportValidity(); return; }
        const submit = form.querySelector("button[type='submit']");
        submit.disabled = true;
        try {
          const response = await fetch(`/api/messages/${encodeURIComponent(activeMessage.id)}/reply`, {
            method: "POST",
            headers: { "Content-Type": "application/json", Accept: "application/json" },
            body: JSON.stringify({ body: form.elements.body.value.trim() })
          });
          const data = await response.json().catch(() => ({}));
          if (!response.ok) throw new Error(data.message || "Could not send the reply.");
          form.reset();
          form.hidden = true;
          button.hidden = false;
          if (counter) counter.textContent = "0 / 3000";
          $("#message-view-actions")?.removeAttribute("hidden");
          alert("Reply sent successfully.");
          await render();
        } catch (error) {
          alert(error.message || "Could not send the reply.");
        } finally {
          submit.disabled = false;
        }
      });
    }

    setupReply();

    function setupCompose() {
      const form = $("#compose-message-form");
      const button = $("#compose-message-button");
      if (!form || !button) return;

      button.addEventListener("click", () => {
        form.reset();
        $("#compose-modal").hidden = false;
        $("#message-recipient").focus();
      });

      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (!form.checkValidity()) { form.reportValidity(); return; }
        const submit = form.querySelector("button[type='submit']");
        submit.disabled = true;
        try {
          const response = await fetch("/api/messages", {
            method: "POST",
            headers: { "Content-Type": "application/json", "Accept": "application/json" },
            body: JSON.stringify({
              recipient_email: form.elements.recipient.value.trim(),
              subject: form.elements.subject.value.trim(),
              body: form.elements.body.value.trim()
            })
          });
          const data = await response.json().catch(() => ({}));
          if (!response.ok) throw new Error(data.message || "Could not send the message.");
          form.reset();
          $("#compose-modal").hidden = true;
          alert("Message sent successfully.");
          await render();
        } catch (error) {
          alert(error.message || "Could not send the message.");
        } finally {
          submit.disabled = false;
        }
      });
    }

    setupCompose();
    render();
    const pollId = window.setInterval(render, 4000);
    window.addEventListener("beforeunload", () => window.clearInterval(pollId), { once: true });
    return { render };
  }

  /* ------------------------------------------------------ Notification center */
  /** Notification list backed by the signed-in user's MySQL notification records. */
  function createNotificationCenter({ emptyTitle, emptyText, onMarkAllRead } = {}) {
    const list = $("#notifications-list");
    const markReadButton = $("#notifications-mark-read");

    async function render() {
      try {
        const response = await fetch("/api/notifications", {
          headers: { "Accept": "application/json" },
          cache: "no-store"
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.message || "Could not load notifications.");

        const notifications = Array.isArray(data.notifications) ? data.notifications : [];
        const unreadCount = notifications.filter((notification) => notification.read === false).length;
        $("#notifications-count").textContent = plural(unreadCount, "unread notification");
        $("#notifications-nav-count").textContent = unreadCount;
        if (markReadButton) markReadButton.disabled = unreadCount === 0;
        list.replaceChildren();

        if (!notifications.length) {
          list.innerHTML = `<div class="communication-empty"><strong>${escapeHtml(emptyTitle)}</strong><p>${escapeHtml(emptyText)}</p></div>`;
          return;
        }

        notifications.forEach((notification) => {
          const status = ["approved", "rejected"].includes(notification.status)
            ? notification.status
            : (notification.read ? "read" : "pending");
          const icon = status === "approved" ? "✓" : status === "rejected" ? "!" : "•";
          const item = document.createElement("article");
          item.className = `notification-item ${status} ${notification.read ? "is-read" : "is-unread"}`;
          item.innerHTML = `
            <span class="notification-status ${status}">${icon}</span>
            <div class="notification-content">
              <div class="notification-title-row">
                <h3 class="notification-title">${escapeHtml(notification.title)}</h3>
                <time class="notification-time">${escapeHtml(formatDate(notification.createdAt))}</time>
              </div>
              <p class="notification-message">${escapeHtml(notification.message)}</p>
            </div>`;
          list.append(item);
        });
      } catch (error) {
        console.warn("Could not load database notifications.", error);
        $("#notifications-count").textContent = "0 unread notifications";
        $("#notifications-nav-count").textContent = "0";
        if (markReadButton) markReadButton.disabled = true;
        list.innerHTML = `<div class="communication-empty"><strong>Notifications unavailable</strong><p>${escapeHtml(error.message || "Could not load notifications right now.")}</p></div>`;
      }
    }

    markReadButton?.addEventListener("click", async () => {
      markReadButton.disabled = true;
      try {
        const response = await fetch("/api/notifications/read-all", { method: "PATCH" });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.message || "Could not mark notifications as read.");
        onMarkAllRead?.();
        await render();
      } catch (error) {
        console.warn("Could not mark notifications as read.", error);
        await render();
      }
    });

    render();

    // Keep the notification counter/list synchronized while the dashboard is open.
    // This is important for multi-user workflows: a Faculty / Staff member can
    // remain on the dashboard while a student submits a new report.
    const pollId = window.setInterval(render, 4000);
    window.addEventListener("beforeunload", () => window.clearInterval(pollId), { once: true });

    return { render };
  }

  return Object.freeze({
    $, $$, escapeHtml, plural, formatDate,
    loadCategories, loadPublicLostReports, loadPublicRecentPosts, renderPublicRecentPosts, renderPublicLostReports, setupPublicRecentSearch,
    getInitials, byNewest, currentUser, initChrome, createRouter,
    createMessageCenter, createNotificationCenter,
    setupPhotoPicker, resetPhotoPicker
  });
})();
