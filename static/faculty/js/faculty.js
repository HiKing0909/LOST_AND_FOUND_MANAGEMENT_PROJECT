/*
 * Faculty / Staff dashboard: reporting, submissions, report verification and claims.
 * Lost-item reports, found-item reports, and claim requests are stored in MySQL.
 * Messages and notifications are stored and isolated per signed-in account in MySQL.
 */
(() => {
  "use strict";

  const FoundIT = window.FoundIT;
  const {
    $, escapeHtml, plural,
    formatDate, byNewest, currentUser
  } = FoundIT;

  async function apiRequest(url, options = {}) {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));

    if (!response.ok) {
      throw new Error(data.message || "The request could not be completed.");
    }

    return data;
  }

  const LOST_FIELDS = ["item_name", "category", "description", "last_seen", "date_lost"];
  const FOUND_FIELDS = ["item_name", "category", "description", "found_location", "date_found"];
  const STATUS_LABELS = { pending: "Pending", verified: "Verified", approved: "Approved", rejected: "Rejected", accepted: "Accepted" };
  const LOST_SUBMIT_LABEL = "Submit Lost Item Report";

  let editingReportId = null;
  let publicRecentPosts = [];

  const isPending = (item) => !item.status || item.status === "pending";
  const statusClass = (status) => (status in STATUS_LABELS ? status : "pending");
  const readFields = (form, fields) =>
    Object.fromEntries(fields.map((name) => [name, form.elements[name].value.trim()]));

  function emptyState(title, text) {
    return `<div class="staff-empty"><strong>${escapeHtml(title)}</strong><p>${escapeHtml(text)}</p></div>`;
  }

  /* --------------------------------------------------------------- Dashboard */
  async function renderDashboard() {
    let lostReports = [];
    let pendingClaims = [];
    try {
      const data = await apiRequest("/api/faculty/lost-reports");
      lostReports = data.reports || [];
    } catch (error) {
      console.warn("Could not load database lost reports.", error);
    }

    try {
      publicRecentPosts = await FoundIT.loadPublicRecentPosts();
      const claimData = await apiRequest("/api/faculty/claims");
      pendingClaims = claimData.claims || [];
    } catch (error) {
      console.warn("Could not load public posts or claims.", error);
    }

    $("#dashboard-lost-count").textContent = Number(publicRecentPosts.lostCount ?? 0);
    $("#dashboard-found-count").textContent = Number(publicRecentPosts.foundCount ?? 0);
    $("#dashboard-pending-reports").textContent = lostReports.filter((report) => report.reporter_role === "student" && isPending(report)).length;
    $("#dashboard-pending-claims").textContent = pendingClaims.length;

    await updateNavBadges();

    const list = $("#dashboard-recent-list");
    FoundIT.renderPublicRecentPosts(list, publicRecentPosts, $("#dashboard-recent-count"));
  }


  async function updateNavBadges() {
    try {
      const data = await apiRequest("/api/faculty/lost-reports");
      $("#verify-nav-count").textContent =
        (data.reports || []).filter((report) => report.reporter_role === "student" && isPending(report)).length;
    } catch (error) {
      $("#verify-nav-count").textContent = "0";
    }

    try {
      const data = await apiRequest("/api/faculty/claims");
      const claims = data.claims || [];
      $("#claims-nav-count").textContent = claims.length;
    } catch (error) {
      $("#claims-nav-count").textContent = "0";
    }
  }


  /* ------------------------------------------------------------ Report forms */
  function setupLostForm() {
    const form = $("#lost-item-form");
    const submitButton = $(".report-submit", form);
    const photoInput = $("#item-photo");
    const preview = $("#photo-preview");
    const message = $("#photo-message");
    FoundIT.setupPhotoPicker(photoInput, preview, message);

    const reset = () => {
      editingReportId = null;
      form.reset();
      submitButton.textContent = LOST_SUBMIT_LABEL;
      FoundIT.resetPhotoPicker(preview, message, photoInput);
    };

    $("#cancel-report").addEventListener("click", () => {
      reset();
      router.navigate("dashboard");
    });

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!form.checkValidity()) {
        form.reportValidity();
        return;
      }

      const formData = new FormData();
      LOST_FIELDS.forEach((name) => {
        formData.append(name, form.elements[name].value.trim());
      });
      [...photoInput.files].forEach((file) => formData.append("photo", file));

      submitButton.disabled = true;

      try {
        const url = editingReportId
          ? `/api/lost-reports/${editingReportId}`
          : "/api/faculty/lost-reports";
        const method = editingReportId ? "PATCH" : "POST";

        await apiRequest(url, { method, body: formData });

        reset();
        await renderMySubmissions();
        await renderDashboard();
        router.navigate("my-submissions");
      } catch (error) {
        alert(error.message);
      } finally {
        submitButton.disabled = false;
      }
    });

    return {
      startEditing(report) {
        editingReportId = report.id;
        LOST_FIELDS.forEach((name) => {
          form.elements[name].value = report[name] || "";
        });
        photoInput.value = "";
        FoundIT.resetPhotoPicker(preview, message, photoInput);
        submitButton.textContent = "Save Changes";
      }
    };
  }


  function setupFoundForm() {
    const form = $("#found-item-form");
    const photoInput = $("#found-item-photo");
    const preview = $("#found-photo-preview");
    const message = $("#found-photo-message");
    FoundIT.setupPhotoPicker(photoInput, preview, message);

    const reset = () => {
      form.reset();
      FoundIT.resetPhotoPicker(preview, message, photoInput);
    };

    $("#cancel-found-report").addEventListener("click", () => {
      reset();
      router.navigate("dashboard");
    });

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!form.checkValidity()) { form.reportValidity(); return; }
      const formData = new FormData();
      FOUND_FIELDS.forEach((name) => formData.append(name, form.elements[name].value.trim()));
      [...photoInput.files].forEach((file) => formData.append("photo", file));
      const button = $(".found-submit", form);
      button.disabled = true;
      try {
        await apiRequest("/api/faculty/found-reports", { method: "POST", body: formData });
        reset();
        await renderMySubmissions();
        await renderDashboard();
        router.navigate("my-submissions");
      } catch (error) {
        alert(error.message);
      } finally {
        button.disabled = false;
      }
    });
  }

  /* ----------------------------------------------------------- My Submissions */
  async function renderMySubmissions() {
    let databaseReports = [];
    try {
      const data = await apiRequest("/api/faculty/lost-reports");
      const user = currentUser();
      const accountId = document.body.dataset.userAccountId;

      databaseReports = (data.reports || []).filter(
        (report) =>
          report.reporter_role === "faculty_staff" &&
          report.reporter_id === accountId
      );
    } catch (error) {
      console.warn("Could not load database lost reports.", error);
    }

    let foundReports = [];
    try {
      const data = await apiRequest("/api/faculty/found-reports");
      foundReports = data.reports || [];
    } catch (error) {
      console.warn("Could not load found-item reports.", error);
    }
    const reports = [
      ...databaseReports.map((report) => ({ ...report, type: "lost" })),
      ...foundReports.map((report) => ({ ...report, type: "found" }))
    ].sort(byNewest);

    const list = $("#submissions-list");
    $("#submission-count").textContent = plural(reports.length, "item");
    list.replaceChildren();

    if (!reports.length) {
      list.innerHTML = `
        <div class="submission-empty">
          <strong>No submissions yet</strong>
          <p>Reports you submit through Report Lost Item or Report Found Item will appear here.</p>
        </div>`;
      return;
    }

    reports.forEach((report) => {
      const isFound = report.type === "found";
      const canManageLost = !isFound && (!report.status || report.status === "pending");
      const meta = isFound
        ? `${report.category} · Found: ${report.found_location} · Date: ${report.date_found}`
        : `${report.category} · Last seen: ${report.last_seen} · Date: ${report.date_lost}`;

      const card = document.createElement("article");
      card.className = "submission-item staff-submission-item";
      card.innerHTML = `
        <div>
          <h3>${escapeHtml(report.item_name || "Untitled item")}</h3>
          <div class="submission-meta">${escapeHtml(meta)}</div>
          <p class="submission-description">${escapeHtml(report.description || "No description provided.")}</p>
          ${report.photo_url ? `<img class="submission-photo" src="${escapeHtml(report.photo_url)}" alt="Photo of ${escapeHtml(report.item_name || "reported item")}">` : ""}
          <span class="status-badge ${isFound ? "verified" : statusClass(report.status)}">${isFound ? "Verified" : STATUS_LABELS[statusClass(report.status)]}</span>
        </div>
        <div class="submission-actions">
          ${canManageLost ? `<button type="button" data-action="edit">Edit</button><button type="button" class="delete-submission" data-action="delete">Delete</button>` : ""}
        </div>`;

      $("[data-action='edit']", card)?.addEventListener("click", () => {
        lostForm.startEditing(report);
        router.navigate("report-lost-item");
      });

      $("[data-action='delete']", card)?.addEventListener("click", async () => {
        if (!confirm("Delete this submission? This cannot be undone.")) return;
        try {
          await apiRequest(`/api/lost-reports/${report.id}`, { method: "DELETE" });
          await renderMySubmissions();
          await renderDashboard();
        } catch (error) {
          alert(error.message);
        }
      });



      list.append(card);
    });
  }


  /* ---------------------------------------------- Verify reports / claim review */
  function renderClaimCard(item, state) {
    const itemName = item.item_name || "Untitled";
    const fallbackLetter = escapeHtml(itemName.trim().charAt(0).toUpperCase() || "?");
    const visual = item.photo_url
      ? `<img class="review-photo" src="${escapeHtml(item.photo_url)}" alt="Photo of ${escapeHtml(itemName)}" onerror="this.hidden=true; this.nextElementSibling.hidden=false;">`
      : "";
    const fallback = `<div class="review-photo review-photo-fallback" ${item.photo_url ? "hidden" : ""} aria-label="No photo available">${fallbackLetter}</div>`;
    const person = item.requester_name || "User";
    const statusLabel = state === "pending" ? "CLAIM REQUEST" : (state === "approved" ? "APPROVED CLAIM" : "RETURNED CLAIM");
    const statusClassName = state === "pending" ? "pending" : (state === "approved" ? "accepted" : "returned");
    const dateText = state === "pending"
      ? `Submitted: ${formatDate(item.createdAt)}`
      : (state === "approved"
        ? `Approved: ${formatDate(item.reviewedAt || item.createdAt)}`
        : `Returned: ${formatDate(item.returnedAt || item.createdAt)}`);
    const action = state === "pending"
      ? ""
      : (state === "approved"
        ? `<button type="button" class="primary-action" data-return-claim="${escapeHtml(String(item.id))}">Returned to the Owner</button>`
        : `<button type="button" class="secondary-action" data-review-claim="${escapeHtml(String(item.id))}">Review</button><button type="button" class="primary-action" data-print-claim="${escapeHtml(String(item.id))}">Print</button>`);

    const card = document.createElement("article");
    card.className = `review-item claim-history-item claim-${statusClassName}`;
    card.innerHTML = `
      <div class="review-item-content">
        <div class="review-item-visual">${visual}${fallback}</div>
        <div class="review-item-main">
          <div class="review-item-top">
            <div>
              <span class="review-type claim-state-badge ${statusClassName}">${statusLabel}</span>
              <h3>${escapeHtml(itemName)}</h3>
            </div>
            <time>${escapeHtml(dateText)}</time>
          </div>
          <div class="review-meta">
            <span><strong>Claimant:</strong> ${escapeHtml(person)}</span>
            <span><strong>Category:</strong> ${escapeHtml(item.category || "Other")}</span>
            <span><strong>Found at:</strong> ${escapeHtml(item.found_location || "Not provided")}</span>
          </div>
          <p>${escapeHtml(item.claim_message || "No claim explanation provided.")}</p>
          ${state === "returned" && item.receiptNo ? `<div class="claim-return-meta claim-receipt-no"><strong>Receipt No.:</strong> ${escapeHtml(item.receiptNo)}</div>` : ""}
          ${state === "returned" && item.returnedBy ? `<div class="claim-return-meta"><strong>Returned by:</strong> ${escapeHtml(item.returnedBy)}</div>` : ""}
        </div>
      </div>
      <div class="review-actions">${action}</div>`;
    return card;
  }

  async function renderReviewList(kind) {
    const isReport = kind === "report";

    if (isReport) {
      let reports = [];
      try {
        const data = await apiRequest("/api/faculty/lost-reports");
        reports = (data.reports || []).filter((report) => report.reporter_role === "student" && isPending(report));
      } catch (error) {
        console.warn("Could not load lost reports.", error);
      }
      reports.sort(byNewest);
      const list = $("#verify-list");
      $("#verify-report-count").textContent = `${reports.length} pending`;
      await updateNavBadges();
      list.replaceChildren();
      if (!reports.length) {
        list.innerHTML = emptyState("No pending lost-item reports", "There is nothing waiting for review right now.");
        return;
      }
      reports.forEach((item) => {
        const person = item.reporter_name || "User";
        const place = `Last seen: ${item.last_seen || "Not provided"}`;
        const card = document.createElement("article");
        card.className = "review-item";
        const itemName = item.item_name || "Untitled";
        const fallbackLetter = escapeHtml(itemName.trim().charAt(0).toUpperCase() || "?");
        const visual = item.photo_url
          ? `<img class="review-photo" src="${escapeHtml(item.photo_url)}" alt="Photo of ${escapeHtml(itemName)}" onerror="this.hidden=true; this.nextElementSibling.hidden=false;">`
          : "";
        const fallback = `<div class="review-photo review-photo-fallback" ${item.photo_url ? "hidden" : ""} aria-label="No photo available">${fallbackLetter}</div>`;
        card.innerHTML = `
          <div class="review-item-content">
            <div class="review-item-visual">${visual}${fallback}</div>
            <div class="review-item-main">
              <div class="review-item-top">
                <div><span class="review-type">LOST ITEM REPORT</span><h3>${escapeHtml(itemName)}</h3></div>
                <time>${escapeHtml(formatDate(item.createdAt || item.date_lost))}</time>
              </div>
              <div class="review-meta">
                <span><strong>Submitted by:</strong> ${escapeHtml(person)}</span>
                <span><strong>Category:</strong> ${escapeHtml(item.category || "Other")}</span>
                <span>${escapeHtml(place)}</span>
              </div>
              <p>${escapeHtml(item.description || "No description provided.")}</p>
            </div>
          </div>
          <div class="review-actions">
            <button type="button" class="secondary-action" data-decision="rejected">Reject</button>
            <button type="button" class="primary-action" data-decision="verified">Verify Report</button>
          </div>`;
        card.querySelectorAll("[data-decision]").forEach((button) => {
          button.addEventListener("click", () => openReviewConfirmation(item, kind, button.dataset.decision));
        });
        list.append(card);
      });
      return;
    }

    let pending = [], approved = [], returned = [];
    try {
      const data = await apiRequest("/api/faculty/claims");
      pending = data.claims || [];
      approved = data.approved_claims || [];
      returned = data.returned_claims || [];
    } catch (error) {
      console.warn("Could not load claim requests.", error);
    }

    pending.sort(byNewest);
    approved.sort((a, b) => new Date(b.reviewedAt || b.createdAt) - new Date(a.reviewedAt || a.createdAt));
    returned.sort((a, b) => new Date(b.returnedAt || b.createdAt) - new Date(a.returnedAt || a.createdAt));

    $("#manage-claims-count").textContent = `${pending.length} pending`;
    await updateNavBadges();

    const pendingList = $("#claims-list");
    const approvedList = $("#approved-claims-list");
    const returnedList = $("#returned-claims-list");
    pendingList.replaceChildren();
    approvedList.replaceChildren();
    returnedList.replaceChildren();

    if (!pending.length) {
      pendingList.innerHTML = emptyState("No pending claim requests", "There is nothing waiting for review right now.");
    } else {
      pending.forEach((item) => {
        const card = renderClaimCard(item, "pending");
        const action = document.createElement("div");
        action.className = "review-actions";
        action.innerHTML = `<button type="button" class="secondary-action" data-decision="rejected">Reject</button><button type="button" class="primary-action" data-decision="accepted">Accept Claim</button>`;
        const oldActions = card.querySelector(".review-actions");
        oldActions.replaceWith(action);
        action.querySelectorAll("[data-decision]").forEach((button) => {
          button.addEventListener("click", () => openReviewConfirmation(item, "claim", button.dataset.decision));
        });
        pendingList.append(card);
      });
    }

    if (!approved.length) {
      approvedList.innerHTML = emptyState("No approved claims", "Accepted claims will appear here until the item is returned to the owner.");
    } else {
      approved.forEach((item) => {
        const card = renderClaimCard(item, "approved");
        card.querySelector("[data-return-claim]").addEventListener("click", () => openReturnConfirmation(item));
        approvedList.append(card);
      });
    }

    const renderReturned = () => {
      const search = $("#returned-claims-search");
      const term = (search ? search.value : "").trim().toLowerCase();
      const visible = !term ? returned : returned.filter((item) =>
        [item.receiptNo, item.item_name, item.requester_name, item.returnedBy, item.found_location]
          .some((value) => String(value || "").toLowerCase().includes(term)));
      returnedList.replaceChildren();
      if (!returned.length) {
        returnedList.innerHTML = emptyState("No returned claims", "Completed returns will appear here with a printable receipt.");
        return;
      }
      if (!visible.length) {
        returnedList.innerHTML = emptyState("No matching receipts", "Try a receipt number such as RCP-2026-000001, an item name, or a claimant name.");
        return;
      }
      visible.forEach((item) => {
        const card = renderClaimCard(item, "returned");
        card.querySelector("[data-review-claim]").addEventListener("click", () => previewClaimReceipt(item.id));
        card.querySelector("[data-print-claim]").addEventListener("click", () => printClaimReceipt(item.id));
        returnedList.append(card);
      });
    };
    const returnedSearch = $("#returned-claims-search");
    if (returnedSearch) returnedSearch.oninput = renderReturned;
    renderReturned();
  }


  function openReviewConfirmation(item, kind, decision) {
    const overlay = $("#faculty-action-confirm");
    const title = $("#faculty-action-confirm-title");
    const message = $("#faculty-action-confirm-message");
    const icon = $("#faculty-action-confirm-icon");
    const confirmButton = $("#faculty-action-confirm-proceed");
    const itemName = item.item_name || item.itemName || "this item";
    if (!overlay || !title || !message || !icon || !confirmButton) return;

    const verified = decision === "verified";
    const isClaim = kind === "claim";
    const accepted = decision === "accepted";
    title.textContent = isClaim
      ? (accepted ? "Confirm Claim Acceptance" : "Confirm Claim Rejection")
      : (verified ? "Confirm Report Verification" : "Confirm Report Rejection");
    icon.textContent = (accepted || verified) ? "?" : "!";
    icon.classList.toggle("is-rejected", !(accepted || verified));
    message.textContent = isClaim
      ? (accepted
        ? `Are you sure you want to accept the claim request for “${itemName}”? This will mark the claimant’s request as accepted.`
        : `Are you sure you want to reject the claim request for “${itemName}”? The claimant will be notified that the request was rejected.`)
      : (verified
        ? `Are you sure you want to verify the lost-item report for “${itemName}”? Once verified, it will be published on the public Recently Posted board.`
        : `Are you sure you want to reject the lost-item report for “${itemName}”? It will not be published on the public Recently Posted board.`);

    confirmButton.textContent = isClaim
      ? (accepted ? "Yes, Accept Claim" : "Yes, Reject Claim")
      : (verified ? "Yes, Verify Report" : "Yes, Reject Report");
    confirmButton.classList.toggle("is-danger", !(accepted || verified));
    confirmButton.dataset.itemId = String(item.id);
    confirmButton.dataset.kind = kind;
    confirmButton.dataset.decision = decision;
    overlay.hidden = false;
    confirmButton.focus();
  }

  function closeReviewConfirmation() {
    const overlay = $("#faculty-action-confirm");
    if (overlay) overlay.hidden = true;
  }

  async function confirmReviewAction() {
    const button = $("#faculty-action-confirm-proceed");
    if (!button) return;
    const itemId = button.dataset.itemId;
    const kind = button.dataset.kind;
    const decision = button.dataset.decision;
    closeReviewConfirmation();

    const endpoint = kind === "report" ? "/api/faculty/lost-reports" : "/api/faculty/claims";
    const data = await apiRequest(endpoint);
    const collection = kind === "report" ? (data.reports || []) : (data.claims || []);
    const item = collection.find((entry) => String(entry.id) === itemId);
    if (!item) {
      alert(`This ${kind === "claim" ? "claim request" : "report"} is no longer available for review. Please refresh the page and try again.`);
      return;
    }
    await reviewItem(item, kind, decision);
  }

  async function reviewItem(item, kind, decision) {
    const isReport = kind === "report";

    try {
      if (isReport) {
        await apiRequest(`/api/faculty/lost-reports/${item.id}/review`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status: decision })
        });
      } else {
        await apiRequest(`/api/faculty/claims/${item.id}/review`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status: decision })
        });
      }

      await renderReviewList(kind);
      await renderDashboard();
      await notifications.render();
    } catch (error) {
      alert(error.message);
    }
  }

  function openReturnConfirmation(item) {
    const overlay = $("#faculty-action-confirm");
    const title = $("#faculty-action-confirm-title");
    const message = $("#faculty-action-confirm-message");
    const icon = $("#faculty-action-confirm-icon");
    const confirmButton = $("#faculty-action-confirm-proceed");
    if (!overlay || !title || !message || !icon || !confirmButton) return;
    const itemName = item.item_name || "this item";
    title.textContent = "Confirm Item Return";
    icon.textContent = "✓";
    icon.classList.remove("is-rejected");
    message.textContent = `Are you sure the item “${itemName}” has been physically returned to ${item.requester_name || "the verified owner"}? This will permanently mark the claim as returned.`;
    confirmButton.textContent = "Yes, Mark as Returned";
    confirmButton.classList.remove("is-danger");
    confirmButton.dataset.returnClaimId = String(item.id);
    delete confirmButton.dataset.itemId;
    delete confirmButton.dataset.kind;
    delete confirmButton.dataset.decision;
    overlay.hidden = false;
    confirmButton.focus();
  }

  async function confirmReturnAction() {
    const button = $("#faculty-action-confirm-proceed");
    const claimId = button?.dataset.returnClaimId;
    if (!claimId) return false;
    closeReviewConfirmation();
    try {
      await apiRequest(`/api/faculty/claims/${claimId}/return`, { method: "PATCH" });
      await renderReviewList("claim");
      await renderDashboard();
      await notifications.render();
      return true;
    } catch (error) {
      alert(error.message);
      return false;
    }
  }

  /** Builds the 9cm x 9cm receipt document. mode: "print" (own window) or "preview" (inside the Review modal). */
  function buildReceiptHtml(receipt, mode = "print") {
    const isPreview = mode === "preview";
    const date = (value) => value ? formatDate(value) : "Not recorded";
    const safe = (value) => escapeHtml(value || "Not provided");
    const claimantRole = receipt.requester_role === "faculty_staff" ? "Faculty / Staff" : "Student";
    const receiptNumber = receipt.receipt_no || "Not recorded";
    const reportNumber = receipt.found_report_id ? `FND-${String(receipt.found_report_id).padStart(6, "0")}` : "Not recorded";
    const row = (label, value, extra = "") =>
      `<div class="row ${extra}"><span class="k">${label}</span><span class="v">${value}</span></div>`;

    return `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>FoundIT Return Receipt - ${safe(receiptNumber)}</title>
  <style>
    /* Receipt size: change --size to resize (default 9cm x 9cm square). */
    :root { --size: 90mm; }
    @page { size: var(--size) var(--size); margin: 0; }
    * { box-sizing: border-box; }
    html, body { margin: 0; padding: 0; }
    body {
      background: #e4e4e4;
      color: #111;
      font-family: "Courier New", Courier, monospace;
      font-size: 7pt;
      line-height: 1.25;
      -webkit-print-color-adjust: exact;
      print-color-adjust: exact;
    }
    .receipt {
      width: var(--size);
      height: var(--size);
      margin: ${isPreview ? "0 auto" : "18px auto 10px"};
      padding: 3.5mm;
      background: #fff;
      box-shadow: 0 2px 10px rgba(0,0,0,.25);
      overflow: hidden;
      display: flex;
      flex-direction: column;
    }
    .head { text-align: center; padding-bottom: 1.5mm; border-bottom: 1px dashed #111; }
    .brand { margin: 0; font-family: Arial, Helvetica, sans-serif; font-size: 13pt; font-weight: 900; letter-spacing: 2px; line-height: 1; }
    .sub { margin: 1px 0 0; font-size: 6pt; letter-spacing: .5px; text-transform: uppercase; }
    .title { margin: 1.5mm 0 0; font-family: Arial, Helvetica, sans-serif; font-size: 8pt; font-weight: 800; letter-spacing: 1.2px; }
    .block { padding: 1.2mm 0; border-bottom: 1px dashed #111; }
    .block-title { margin: 0 0 .6mm; font-family: Arial, Helvetica, sans-serif; font-size: 5.5pt; font-weight: 800; letter-spacing: 1px; text-transform: uppercase; }
    .row { display: flex; gap: 2mm; justify-content: space-between; align-items: baseline; }
    .k { flex: 0 0 auto; color: #444; white-space: nowrap; }
    .v { flex: 1 1 auto; min-width: 0; text-align: right; font-weight: 700; overflow-wrap: anywhere;
         display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
    .row.big .v { font-size: 8pt; }
    .sign { margin-top: auto; display: grid; grid-template-columns: 1fr 1fr; gap: 6mm; padding-top: 5mm; }
    .sig { border-top: 1px solid #111; padding-top: 1mm; text-align: center; font-size: 5.5pt; line-height: 1.2; }
    .sig b { display: block; font-size: 6pt; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .foot { margin-top: 1.5mm; text-align: center; font-size: 5.5pt; color: #333; }
    .no-print { margin: 6px auto 14px; text-align: center; }
    .no-print button { padding: 7px 14px; border: 1px solid #888; background: #fff; cursor: pointer; font: 13px Arial, sans-serif; }
    @media print {
      body { background: #fff; }
      .receipt { margin: 0; box-shadow: none; page-break-inside: avoid; }
      .no-print { display: none !important; }
    }
  </style>
</head>
<body>
  <main class="receipt">
    <header class="head">
      <p class="brand">FOUNDIT</p>
      <p class="sub">Lost and Found System</p>
      <p class="title">ITEM RETURN RECEIPT</p>
    </header>

    <section class="block">
      ${row("Receipt No.", safe(receiptNumber))}
      ${row("Date Returned", safe(date(receipt.returned_at)))}
    </section>

    <section class="block">
      <h2 class="block-title">Item</h2>
      ${row("Item", safe(receipt.item_name), "big")}
      ${row("Category", safe(receipt.category))}
      ${row("Found At", safe(receipt.found_location))}
      ${row("Date Found", safe(date(receipt.date_found)))}
      ${row("Found Item Ref.", safe(reportNumber))}
    </section>

    <section class="block">
      <h2 class="block-title">Received By (Claimant)</h2>
      ${row("Name", safe(receipt.requester_name), "big")}
      ${row("ID / Role", `${safe(receipt.requester_id)} · ${safe(claimantRole)}`)}
    </section>

    <section class="block">
      <h2 class="block-title">Processed By</h2>
      ${row("Approved", safe(receipt.reviewed_by))}
      ${row("Released", safe(receipt.returned_by))}
    </section>

    <div class="sign">
      <div class="sig"><b>${safe(receipt.requester_name)}</b>Claimant Signature</div>
      <div class="sig"><b>${safe(receipt.returned_by)}</b>Faculty / Staff Signature</div>
    </div>

    <footer class="foot">Item released to verified owner. Keep this receipt.</footer>
  </main>
  ${isPreview ? "" : `<div class="no-print"><button type="button" onclick="window.print()">Print Receipt</button></div>
  <script>
    window.addEventListener('load', () => { window.focus(); window.print(); });
    window.addEventListener('afterprint', () => window.close());
  </script>`}
</body>
</html>`;
  }

  async function fetchClaimReceipt(claimId) {
    const data = await apiRequest(`/api/faculty/claims/${claimId}/receipt`);
    return data.receipt;
  }

  /** Review: shows exactly what the printed receipt will look like, without printing. */
  async function previewClaimReceipt(claimId) {
    try {
      const receipt = await fetchClaimReceipt(claimId);
      let overlay = document.getElementById("receipt-preview-overlay");
      if (!overlay) {
        overlay = document.createElement("div");
        overlay.id = "receipt-preview-overlay";
        overlay.className = "receipt-preview-overlay";
        overlay.setAttribute("role", "dialog");
        overlay.setAttribute("aria-modal", "true");
        overlay.setAttribute("aria-labelledby", "receipt-preview-title");
        overlay.innerHTML = `
          <div class="receipt-preview-modal">
            <h2 id="receipt-preview-title">Receipt Review</h2>
            <p>This is how the printed receipt will look (9 cm × 9 cm).</p>
            <div class="receipt-preview-frame-wrap"><iframe class="receipt-preview-frame" title="Receipt preview"></iframe></div>
            <div class="receipt-preview-actions">
              <button type="button" class="secondary-action" data-receipt-close>Close</button>
              <button type="button" class="primary-action" data-receipt-print>Print</button>
            </div>
          </div>`;
        document.body.append(overlay);
        const close = () => { overlay.hidden = true; };
        overlay.addEventListener("click", (event) => { if (event.target === overlay) close(); });
        overlay.querySelector("[data-receipt-close]").addEventListener("click", close);
        document.addEventListener("keydown", (event) => { if (event.key === "Escape" && !overlay.hidden) close(); });
        overlay.querySelector("[data-receipt-print]").addEventListener("click", () => {
          const frame = overlay.querySelector("iframe");
          frame.contentWindow.focus();
          frame.contentWindow.print();
        });
      }
      overlay.querySelector("iframe").srcdoc = buildReceiptHtml(receipt, "preview");
      overlay.hidden = false;
    } catch (error) {
      alert(error.message);
    }
  }

  async function printClaimReceipt(claimId) {
    try {
      const receipt = await fetchClaimReceipt(claimId);
      const printWindow = window.open("", "_blank", "width=480,height=600");
      if (!printWindow) {
        alert("The receipt could not open because the browser blocked the print window. Please allow pop-ups for FoundIT and try again.");
        return;
      }
      printWindow.document.write(buildReceiptHtml(receipt, "print"));
      printWindow.document.close();
    } catch (error) {
      alert(error.message);
    }
  }

  /* ------------------------------------------------------------------- Setup */
  $("#faculty-action-confirm-cancel")?.addEventListener("click", closeReviewConfirmation);
  $("#faculty-action-confirm-proceed")?.addEventListener("click", async () => {
    if ($("#faculty-action-confirm-proceed")?.dataset.returnClaimId) {
      await confirmReturnAction();
      return;
    }
    await confirmReviewAction();
  });
  $("#faculty-action-confirm")?.addEventListener("click", (event) => {
    if (event.target.id === "faculty-action-confirm") closeReviewConfirmation();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && $("#faculty-action-confirm") && !$("#faculty-action-confirm").hidden) {
      closeReviewConfirmation();
    }
  });

  FoundIT.initChrome();
  FoundIT.loadCategories(["#item-category", "#found-item-category"]);
  FoundIT.setupPublicRecentSearch(
    $("#dashboard-recent-search"),
    $("#dashboard-recent-list"),
    $("#dashboard-recent-count"),
    () => publicRecentPosts
  );
  const lostForm = setupLostForm();
  setupFoundForm();

  FoundIT.createMessageCenter();
  const notifications = FoundIT.createNotificationCenter({
    emptyTitle: "No notifications yet",
    emptyText: "New pending lost-item reports will appear here for review. Reviewed reports remain in this history."
  });

  const router = FoundIT.createRouter({
    "dashboard": { panel: "#dashboard-view", nav: "#dashboard-nav", render: renderDashboard },
    "report-lost-item": { panel: "#report-lost-item", nav: "#report-lost-nav" },
    "report-found-item": { panel: "#report-found-item", nav: "#report-found-nav" },
    "my-submissions": { panel: "#my-submissions", nav: "#my-submissions-nav", render: renderMySubmissions },
    "verify-report": { panel: "#verify-report", nav: "#verify-report-nav", render: () => renderReviewList("report") },
    "manage-claims": { panel: "#manage-claims", nav: "#manage-claims-nav", render: () => renderReviewList("claim") },
    "messages": { panel: "#messages-panel", nav: "#messages-nav" },
    "notifications": { panel: "#notifications-panel", nav: "#notifications-nav" }
  });
})();
