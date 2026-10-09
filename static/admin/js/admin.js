/*
 * Admin dashboard: accounts, reported items, audit log, messages and notifications.
 * Accounts, reports, audit history, messages and notifications are backed by MySQL.
 */
(() => {
  "use strict";

  const FoundIT = window.FoundIT;
  const { $, escapeHtml, plural, formatDate } = FoundIT;

  const admin = FoundIT.currentUser();
  const shortDate = (value) => {
    const date = new Date(value);
    if (!value) return "—";
    return Number.isNaN(date.getTime()) ? String(value) : new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(date);
  };
  const normalizeStatus = (value) => String(value || "pending").toLowerCase().replace(/\s+/g, "_");
  const matches = (haystack, search) => !search || haystack.toLowerCase().includes(search);

  function emptyRow(columns, title, text) {
    return `<tr><td colspan="${columns}" class="empty-table"><strong>${escapeHtml(title)}</strong><span>${escapeHtml(text)}</span></td></tr>`;
  }

  /* ------------------------------------------------------------------- Data */
  let accountsCache = [];

  function getAccounts() {
    return accountsCache.slice();
  }

  async function loadAccounts() {
    const response = await fetch("/api/admin/accounts", {
      headers: { "Accept": "application/json" },
      cache: "no-store"
    });

    if (!response.ok) {
      const result = await response.json().catch(() => ({}));
      throw new Error(result.message || "Could not load accounts.");
    }

    const result = await response.json();
    accountsCache = Array.isArray(result.accounts) ? result.accounts : [];
  }


  let itemsCache = [];

  function getItems() {
    return itemsCache.slice().sort((a, b) =>
      new Date(b.createdAt || b.date || 0) - new Date(a.createdAt || a.date || 0)
    );
  }

  async function loadItems() {
    const response = await fetch("/api/admin/items", {
      headers: { "Accept": "application/json" },
      cache: "no-store"
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.message || "Could not load reported items.");
    itemsCache = Array.isArray(result.items) ? result.items : [];
  }

  let auditCache = [];

  function getAudit() {
    return auditCache.slice().sort((a, b) => new Date(b.timestamp || 0) - new Date(a.timestamp || 0));
  }

  async function loadAudit() {
    const response = await fetch("/api/admin/audit", {
      headers: { "Accept": "application/json" },
      cache: "no-store"
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.message || "Could not load the audit log.");
    auditCache = Array.isArray(result.audit) ? result.audit : [];
  }

  /* ------------------------------------------------------------- Categories */
  let categoriesCache = [];

  function getCategories() {
    return categoriesCache.slice().sort((a, b) =>
      Number(a.sortOrder) - Number(b.sortOrder) || a.name.localeCompare(b.name)
    );
  }

  async function loadCategories() {
    const response = await fetch("/api/admin/categories", {
      headers: { "Accept": "application/json" },
      cache: "no-store"
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.message || "Could not load categories.");
    categoriesCache = Array.isArray(result.categories) ? result.categories : [];
  }

  function renderCategories() {
    const search = $("#category-search").value.trim().toLowerCase();
    const categories = getCategories().filter((category) =>
      matches(`${category.name} ${category.sortOrder} ${category.isActive ? "active" : "inactive"}`, search)
    );
    const body = $("#categories-table-body");
    $("#categories-count").textContent = plural(categories.length, "category");
    $("#categories-nav-count").textContent = getCategories().filter((category) => category.isActive).length;
    body.replaceChildren();

    if (!categories.length) {
      body.innerHTML = emptyRow(6, "No categories found", "Create a category or change the search term.");
      return;
    }

    categories.forEach((category) => {
      const row = document.createElement("tr");
      row.innerHTML = `
        <td><span class="item-name">${escapeHtml(category.name)}</span></td>
        <td>${escapeHtml(category.sortOrder)}</td>
        <td><span class="status-pill ${category.isActive ? "active" : "suspended"}">${category.isActive ? "Active" : "Inactive"}</span></td>
        <td>${escapeHtml(formatDate(category.createdAt))}</td>
        <td>${escapeHtml(formatDate(category.updatedAt))}</td>
        <td><div class="table-actions"><button class="small-action edit" type="button">Edit</button></div></td>`;
      $("button", row).addEventListener("click", () => openCategoryEditor(category));
      body.append(row);
    });
  }

  function openCategoryEditor(category) {
    const form = $("#category-form");
    form.dataset.categoryId = category.id;
    $("#edit-category-name").value = category.name;
    $("#edit-category-sort").value = category.sortOrder;
    $("#edit-category-status").value = String(category.isActive);
    $("#category-modal").hidden = false;
    $("#edit-category-name").focus();
  }

  async function createCategory(event) {
    event.preventDefault();
    const name = $("#new-category-name").value.trim();
    const sortOrder = Number($("#new-category-sort").value);
    try {
      const response = await fetch("/api/admin/categories", {
        method: "POST",
        headers: { "Content-Type": "application/json", "Accept": "application/json" },
        body: JSON.stringify({ name, sortOrder })
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.message || "Could not create the category.");
      event.currentTarget.reset();
      $("#new-category-sort").value = 0;
      await Promise.all([loadCategories(), loadAudit()]);
      await FoundIT.loadCategories(["#item-category", "#found-item-category", "#edit-item-category"]);
      refreshAll();
    } catch (error) {
      alert(error.message);
    }
  }

  async function saveCategory(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const categoryId = form.dataset.categoryId;
    if (!categoryId) return;
    const name = $("#edit-category-name").value.trim();
    const sortOrder = Number($("#edit-category-sort").value);
    const isActive = $("#edit-category-status").value === "true";
    try {
      const response = await fetch(`/api/admin/categories/${encodeURIComponent(categoryId)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json", "Accept": "application/json" },
        body: JSON.stringify({ name, sortOrder, isActive })
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.message || "Could not update the category.");
      $("#category-modal").hidden = true;
      await Promise.all([loadCategories(), loadAudit()]);
      await FoundIT.loadCategories(["#item-category", "#found-item-category", "#edit-item-category"]);
      refreshAll();
    } catch (error) {
      alert(error.message);
    }
  }

  /* -------------------------------------------------------------- Dashboard */
  function updateCounts() {
    const accounts = getAccounts();
    const items = getItems();
    const auditCount = getAudit().length;

    $("#dashboard-account-count").textContent = accounts.length;
    $("#dashboard-active-count").textContent = accounts.filter((account) => account.status === "active").length;
    $("#dashboard-item-count").textContent = items.length;
    $("#dashboard-audit-count").textContent = auditCount;
    $("#accounts-nav-count").textContent = accounts.length;
    $("#items-nav-count").textContent = items.length;
    $("#audit-nav-count").textContent = auditCount;
  }

  function renderDashboard() {
    updateCounts();
    const recent = getAudit().slice(0, 8);
    const list = $("#dashboard-recent-list");
    $("#dashboard-recent-count").textContent = plural(recent.length, "recent action");
    list.replaceChildren();

    if (!recent.length) {
      list.innerHTML = `
        <div class="empty-state">
          <div class="empty-illustration" aria-hidden="true"><div class="empty-illustration-back"></div><div class="empty-illustration-front"><span>+</span></div></div>
          <h3>No administrative activity yet</h3>
          <p>Account and item actions performed by an admin will appear here.</p>
        </div>`;
      return;
    }

    recent.forEach((entry) => {
      const article = document.createElement("article");
      article.className = "admin-activity-item";
      article.innerHTML = `
        <span class="admin-activity-action">${escapeHtml(entry.action)}</span>
        <span class="admin-activity-copy"><strong>${escapeHtml(entry.targetId)}</strong><small>${escapeHtml(entry.details)}</small></span>
        <time>${escapeHtml(formatDate(entry.timestamp))}</time>
        <span class="admin-activity-arrow">→</span>`;
      list.append(article);
    });
  }

  /* --------------------------------------------------------------- Accounts */
  function renderAccounts() {
    const search = $("#account-search").value.trim().toLowerCase();
    const statusFilter = $("#account-status-filter").value;
    const accounts = getAccounts().filter((account) =>
      matches(`${account.id} ${account.name} ${account.email} ${account.role} ${account.status}`, search) &&
      (statusFilter === "all" || account.status === statusFilter));

    const body = $("#accounts-table-body");
    $("#accounts-count").textContent = plural(accounts.length, "account");
    body.replaceChildren();

    if (!accounts.length) {
      body.innerHTML = emptyRow(8, "No accounts found", "Accounts will be listed here once they are stored in the MySQL database.");
      return;
    }

    accounts.forEach((account) => {
      const pending = account.status === "pending";
      const suspended = account.status === "suspended";
      const actionLabel = pending || suspended ? "Activate" : "Suspend";
      const actionClass = pending || suspended ? "activate" : "suspend";
      const statusLabel = pending ? "Pending" : suspended ? "Suspended" : "Active";
      const row = document.createElement("tr");
      row.innerHTML = `
        <td><span class="account-id">${escapeHtml(account.id)}</span></td>
        <td><span class="person-name">${escapeHtml(account.name)}</span></td>
        <td><span class="email-cell">${escapeHtml(account.email)}</span></td>
        <td><span class="type-pill ${account.role === "Student" ? "student" : "faculty"}">${escapeHtml(account.role)}</span></td>
        <td>${escapeHtml(formatDate(account.createdAt))}</td>
        <td>${escapeHtml(formatDate(account.updatedAt))}</td>
        <td><span class="status-pill ${account.status}">${statusLabel}</span></td>
        <td><div class="table-actions"><button class="small-action ${actionClass}" type="button">${actionLabel}</button></div></td>`;
      $("button", row).addEventListener("click", () => toggleAccountStatus(account.role_code, account.id));
      body.append(row);
    });
  }

  async function toggleAccountStatus(role, id) {
    const accounts = getAccounts();
    const account = accounts.find((item) => item.role_code === role && String(item.id) === String(id));
    if (!account) return;

    const nextStatus = account.status === "active" ? "suspended" : "active";
    const verb = nextStatus === "active" ? "activate" : "suspend";

    if (!confirm(`Are you sure you want to ${verb} ${account.name}'s account?`)) return;

    try {
      const response = await fetch(`/api/admin/accounts/${encodeURIComponent(role)}/${encodeURIComponent(id)}/status`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json", "Accept": "application/json" },
        body: JSON.stringify({ status: nextStatus })
      });
      const result = await response.json().catch(() => ({}));

      if (!response.ok) {
        throw new Error(result.message || "Could not update the account.");
      }

      await Promise.all([loadAccounts(), loadAudit()]);
      refreshAll();
    } catch (error) {
      alert(error.message);
    }
  }


  /* ------------------------------------------------------------------ Items */
  function itemStatusClass(status) {
    const normalized = normalizeStatus(status);
    if (normalized === "verified" || normalized === "approved" || normalized === "recorded") return "active";
    return normalized === "rejected" ? "suspended" : "pending";
  }

  function renderItems() {
    const search = $("#item-search").value.trim().toLowerCase();
    const typeFilter = $("#item-type-filter").value;
    const items = getItems().filter((item) =>
      matches(`${item.id} ${item.name || item.item_name} ${item.category} ${item.reporter || item.reporter_name} ${item.location} ${item.type} ${item.status}`, search) &&
      (typeFilter === "all" || item.type === typeFilter));

    const body = $("#items-table-body");
    $("#items-count").textContent = plural(items.length, "item");
    body.replaceChildren();

    if (!items.length) {
      body.innerHTML = emptyRow(9, "No reported items found", "Items from Student and Faculty / Staff submissions will appear here.");
      return;
    }

    items.forEach((item) => {
      const row = document.createElement("tr");
      row.innerHTML = `
        <td><span class="account-id">${escapeHtml(item.id)}</span></td>
        <td><span class="item-name">${escapeHtml(item.item_name || item.name)}</span></td>
        <td><span class="type-pill ${item.type}">${escapeHtml(item.type)}</span></td>
        <td>${escapeHtml(item.category)}</td>
        <td>${escapeHtml(item.reporter_name || "Unknown reporter")}<br><span class="muted-cell">${escapeHtml(item.reporter_role === "student" ? "Student" : "Faculty / Staff")}</span></td>
        <td>${escapeHtml(item.location || "Not provided")}</td>
        <td>${escapeHtml(shortDate(item.date))}</td>
        <td><span class="status-pill ${itemStatusClass(item.status)}">${escapeHtml(item.status)}</span></td>
        <td><div class="table-actions">
          <button class="small-action edit" type="button">Edit</button>
          <button class="small-action delete" type="button">Delete</button>
        </div></td>`;
      $(".edit", row).addEventListener("click", () => openItemEditor(item));
      $(".delete", row).addEventListener("click", () => deleteItem(item));
      body.append(row);
    });
  }

  function openItemEditor(item) {
    const form = $("#item-form");
    form.dataset.itemId = item.id;
    form.dataset.itemType = item.type;
    $("#item-modal-title").textContent = `Edit ${item.item_name || item.name}`;
    $("#edit-item-name").value = item.item_name || item.name || "";
    $("#edit-item-category").value = item.category || "";
    $("#edit-item-location").value = item.location || "";
    $("#edit-item-date").value = item.date ? String(item.date).slice(0, 10) : "";
    $("#edit-item-description").value = item.description || "";

    const status = $("#edit-item-status");
    if (item.type === "found") {
      status.innerHTML = `<option value="verified">Verified</option>`;
      status.value = "verified";
    } else {
      status.innerHTML = `
        <option value="pending">Pending</option>
        <option value="verified">Verified</option>
        <option value="rejected">Rejected</option>`;
      status.value = item.status || "pending";
    }

    $("#item-modal").hidden = false;
    $("#edit-item-name").focus();
  }

  async function saveItemEdits(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const itemId = form.dataset.itemId;
    const itemType = form.dataset.itemType;
    if (!itemId || !itemType) return;

    const name = $("#edit-item-name").value.trim();
    const category = $("#edit-item-category").value.trim();
    const location = $("#edit-item-location").value.trim();
    const date = $("#edit-item-date").value;
    const description = $("#edit-item-description").value.trim();
    const status = $("#edit-item-status").value;

    try {
      const response = await fetch(`/api/admin/items/${encodeURIComponent(itemType)}/${encodeURIComponent(itemId)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json", "Accept": "application/json" },
        body: JSON.stringify({
          item_name: name,
          category,
          description,
          location,
          date,
          status
        })
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.message || "Could not update the item report.");

      $("#item-modal").hidden = true;
      await Promise.all([loadItems(), loadAudit()]);
      refreshAll();
    } catch (error) {
      alert(error.message);
    }
  }

  async function deleteItem(item) {
    const itemName = item.item_name || item.name || "this item";
    const warning = item.type === "found"
      ? `Delete the Found Item report “${itemName}”? Any claim requests attached to this item will also be removed. This cannot be undone.`
      : `Delete the Lost Item report “${itemName}”? This cannot be undone.`;
    if (!confirm(warning)) return;

    try {
      const response = await fetch(`/api/admin/items/${encodeURIComponent(item.type)}/${encodeURIComponent(item.id)}`, {
        method: "DELETE",
        headers: { "Accept": "application/json" }
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.message || "Could not delete the item report.");

      await Promise.all([loadItems(), loadAudit()]);
      refreshAll();
    } catch (error) {
      alert(error.message);
    }
  }

  /* -------------------------------------------------------------- Audit log */
  function renderAudit() {
    const search = $("#audit-search").value.trim().toLowerCase();
    const typeFilter = $("#audit-type-filter").value;
    const entries = getAudit().filter((entry) =>
      matches(`${entry.adminName} ${entry.action} ${entry.targetId} ${entry.details}`, search) &&
      (typeFilter === "all" || entry.targetType === typeFilter));

    const body = $("#audit-table-body");
    $("#audit-count").textContent = plural(entries.length, "action");
    body.replaceChildren();

    if (!entries.length) {
      body.innerHTML = emptyRow(5, "No audit entries found", "Administrative account and item actions will be recorded here.");
      return;
    }

    entries.forEach((entry) => {
      const row = document.createElement("tr");
      row.innerHTML = `
        <td>${escapeHtml(formatDate(entry.timestamp))}</td>
        <td><span class="person-name">${escapeHtml(entry.adminName)}</span><br><span class="muted-cell">${escapeHtml(entry.adminEmail)}</span></td>
        <td><span class="audit-action">${escapeHtml(entry.action)}</span></td>
        <td><span class="account-id">${escapeHtml(entry.targetId)}</span></td>
        <td><span class="audit-details">${escapeHtml(entry.details)}</span></td>`;
      body.append(row);
    });
  }

  /* ------------------------------------------------------------------ Setup */
  // Re-renders everything that can show data changed by an admin action.
  function refreshAll() {
    renderDashboard();
    renderAccounts();
    renderItems();
    renderCategories();
    renderAudit();
    notifications.render();
  }

  FoundIT.initChrome();
  FoundIT.createMessageCenter();
  const notifications = FoundIT.createNotificationCenter({
    emptyTitle: "No notifications yet",
    emptyText: "Administrative events and system notifications will appear here."
  });

  $("#item-form").addEventListener("submit", saveItemEdits);
  $("#category-create-form").addEventListener("submit", createCategory);
  $("#category-form").addEventListener("submit", saveCategory);
  [["#account-search", "#account-status-filter", renderAccounts],
   ["#item-search", "#item-type-filter", renderItems],
   ["#category-search", null, renderCategories],
   ["#audit-search", "#audit-type-filter", renderAudit]].forEach(([search, filter, render]) => {
    $(search).addEventListener("input", render);
    if (filter) $(filter).addEventListener("input", render);
  });

  FoundIT.createRouter({
    "dashboard": { panel: "#dashboard-view", nav: "#dashboard-nav", render: renderDashboard },
    "manage-accounts": { panel: "#manage-accounts-panel", nav: "#accounts-nav", render: renderAccounts },
    "manage-items": { panel: "#manage-items-panel", nav: "#items-nav", render: renderItems },
    "manage-categories": { panel: "#manage-categories-panel", nav: "#categories-nav", render: renderCategories },
    "audit-log": { panel: "#audit-log-panel", nav: "#audit-nav", render: renderAudit },
    "messages": { panel: "#messages-panel", nav: "#messages-nav" },
    "notifications": { panel: "#notifications-panel", nav: "#notifications-nav" }
  });
  Promise.all([loadAccounts(), loadItems(), loadCategories(), loadAudit()])
    .then(async () => {
      await FoundIT.loadCategories(["#item-category", "#found-item-category", "#edit-item-category"]);
      refreshAll();
    })
    .catch((error) => {
      console.error(error);
      updateCounts();
      renderAccounts();
      renderItems();
      renderCategories();
      renderAudit();
      notifications.render();
    });
})();
