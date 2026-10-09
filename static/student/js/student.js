/*
 * Student dashboard: Report Lost Item, My Submissions, Messages and Notifications.
 * Lost-item reports are stored in MySQL through the Flask API.
 */
(() => {
  "use strict";

  const FoundIT = window.FoundIT;
  const { $, plural, currentUser } = FoundIT;

  const REPORT_FIELDS = ["item_name", "category", "description", "last_seen", "date_lost"];
  const SUBMIT_LABEL = "Submit Lost Item Report";

  const form = $("#lost-item-form");
  const submitButton = $(".report-submit", form);
  const photoInput = $("#item-photo");
  const photoPreview = $("#photo-preview");
  const photoMessage = $("#photo-message");
  const submissionsList = $("#submissions-list");

  let editingId = null;
  let reports = [];
  let publicRecentPosts = [];

  async function apiRequest(url, options = {}) {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));

    if (!response.ok) {
      const error = new Error(data.message || "The request could not be completed.");
      error.field = data.field;
      throw error;
    }

    return data;
  }

  function resetForm() {
    editingId = null;
    form.reset();
    submitButton.textContent = SUBMIT_LABEL;
    FoundIT.resetPhotoPicker(photoPreview, photoMessage, photoInput);
  }

  function renderSubmissions() {
    $("#submission-count").textContent = plural(reports.length, "item");
    submissionsList.replaceChildren();

    if (!reports.length) {
      submissionsList.innerHTML = `
        <div class="submission-empty">
          <strong>No submissions yet</strong>
          <p>Reports you submit through Report Lost Item will appear here.</p>
        </div>`;
      return;
    }

    reports.forEach((report) => {
      const card = document.createElement("article");
      card.className = "submission-item staff-submission-item";

      const details = document.createElement("div");
      const title = document.createElement("h3");
      title.textContent = report.item_name;

      const meta = document.createElement("div");
      meta.className = "submission-meta";
      meta.textContent =
        `${report.category} · Last seen: ${report.last_seen} · Date lost: ${report.date_lost}`;

      const description = document.createElement("p");
      description.className = "submission-description";
      description.textContent = report.description;

      details.append(title, meta, description);

      if (report.photo_url) {
        const photo = document.createElement("img");
        photo.src = report.photo_url;
        photo.alt = `Photo of ${report.item_name}`;
        photo.className = "submission-photo";
        details.append(photo);
      }

      const status = document.createElement("span");
      status.className = `status-badge ${report.status || "pending"}`;
      status.textContent = report.status
        ? report.status.charAt(0).toUpperCase() + report.status.slice(1)
        : "Pending";
      details.append(status);

      const actions = document.createElement("div");
      actions.className = "submission-actions";

      if (report.status === "pending") {
        const edit = document.createElement("button");
        edit.type = "button";
        edit.textContent = "Edit";
        edit.addEventListener("click", () => editReport(report.id));

        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "delete-submission";
        remove.textContent = "Delete";
        remove.addEventListener("click", () => deleteReport(report.id));

        actions.append(edit, remove);
      }

      card.append(details, actions);
      submissionsList.append(card);
    });
  }

  async function loadPublicBoard() {
    const list = $("#dashboard-recent-list");
    const count = $("#dashboard-recent-count");
    if (!list) return;

    try {
      publicRecentPosts = await FoundIT.loadPublicRecentPosts();
      FoundIT.renderPublicRecentPosts(list, publicRecentPosts, count);
      const lostCount = $("#dashboard-lost-count");
      const foundCount = $("#dashboard-found-count");
      if (lostCount) lostCount.textContent = Number(publicRecentPosts.lostCount ?? 0);
      if (foundCount) foundCount.textContent = Number(publicRecentPosts.foundCount ?? 0);
    } catch (error) {
      console.warn("Could not load public lost-item reports.", error);
      list.innerHTML = `
        <div class="empty-state">
          <h3>Recently posted is unavailable</h3>
          <p>Please refresh the page and try again.</p>
        </div>`;
      if (count) count.textContent = "0 items";
      const lostCount = $("#dashboard-lost-count");
      const foundCount = $("#dashboard-found-count");
      if (lostCount) lostCount.textContent = "0";
      if (foundCount) foundCount.textContent = "0";
    }
  }

  async function loadReports() {
    try {
      const data = await apiRequest("/api/lost-reports");
      reports = data.reports || [];
      renderSubmissions();
    } catch (error) {
      submissionsList.innerHTML = `
        <div class="submission-empty">
          <strong>Could not load submissions</strong>
          <p>${FoundIT.escapeHtml(error.message)}</p>
        </div>`;
    }
  }

  function editReport(id) {
    const report = reports.find((entry) => entry.id === id);
    if (!report || report.status !== "pending") return;

    editingId = id;
    REPORT_FIELDS.forEach((name) => {
      form.elements[name].value = report[name] || "";
    });

    photoInput.value = "";
    FoundIT.resetPhotoPicker(photoPreview, photoMessage, photoInput);
    submitButton.textContent = "Save Changes";
    router.navigate("report-lost-item");
  }

  async function deleteReport(id) {
    if (!confirm("Delete this submission? This cannot be undone.")) return;

    try {
      await apiRequest(`/api/lost-reports/${id}`, { method: "DELETE" });
      await loadReports();
    } catch (error) {
      alert(error.message);
    }
  }

  async function saveReport(event) {
    event.preventDefault();

    if (!form.checkValidity()) {
      form.reportValidity();
      return;
    }

    const formData = new FormData();
    REPORT_FIELDS.forEach((name) => {
      formData.append(name, form.elements[name].value.trim());
    });

    [...photoInput.files].forEach((file) => formData.append("photo", file));

    submitButton.disabled = true;

    try {
      const url = editingId
        ? `/api/lost-reports/${editingId}`
        : "/api/lost-reports";
      const method = editingId ? "PATCH" : "POST";

      await apiRequest(url, {
        method,
        body: formData
      });

      resetForm();
      await loadReports();
      router.navigate("my-submissions");
    } catch (error) {
      if (error.field && form.elements[error.field]) {
        form.elements[error.field].setCustomValidity(error.message);
        form.elements[error.field].reportValidity();
        form.elements[error.field].setCustomValidity("");
      }
      // Always show the server's reason. This prevents a failed 400 response
      // from looking like the Submit button did nothing.
      alert(error.message || "The report could not be submitted.");
    } finally {
      submitButton.disabled = false;
    }
  }

  FoundIT.initChrome();
  FoundIT.loadCategories(["#item-category"]);
  FoundIT.setupPublicRecentSearch(
    $("#dashboard-recent-search"),
    $("#dashboard-recent-list"),
    $("#dashboard-recent-count"),
    () => publicRecentPosts
  );
  loadPublicBoard();
  FoundIT.setupPhotoPicker(photoInput, photoPreview, photoMessage);
  FoundIT.createMessageCenter();
  FoundIT.createNotificationCenter({
    emptyTitle: "No report updates yet",
    emptyText: "Verification or rejection updates for your reported lost items will appear here."
  });

  form.addEventListener("submit", saveReport);
  $("#cancel-report").addEventListener("click", () => {
    resetForm();
    router.navigate("dashboard");
  });

  const router = FoundIT.createRouter({
    dashboard: { panel: "#dashboard-view", nav: "#dashboard-nav" },
    "report-lost-item": { panel: "#report-lost-item", nav: "#report-lost-nav" },
    "my-submissions": {
      panel: "#my-submissions",
      nav: "#my-submissions-nav",
      render: loadReports
    },
    messages: { panel: "#messages-panel", nav: "#messages-nav" },
    notifications: { panel: "#notifications-panel", nav: "#notifications-nav" }
  });

  // Keep the current user's identity available for existing communication UI.
  currentUser();
})();
