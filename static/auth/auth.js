/* FoundIT authentication pages: login and create-account behaviour. */
(() => {
  "use strict";

  // Same e-mail rules as the server (app.py): first.last.<id>.tc@umindanao.edu.ph
  const NAME_PART = "[a-z][a-z0-9_-]*";
  const DOMAIN = "\\.tc@umindanao\\.edu\\.ph$";
  const EMAIL_PATTERNS = {
    student: new RegExp(`^${NAME_PART}\\.${NAME_PART}\\.\\d{6}${DOMAIN}`, "i"),
    faculty_staff: new RegExp(`^${NAME_PART}\\.${NAME_PART}\\.\\d{4}${DOMAIN}`, "i"),
    admin: new RegExp(`^${NAME_PART}\\.${NAME_PART}\\.\\d{4}${DOMAIN}`, "i")
  };

  const ID_LABELS = { student: "Student ID", faculty_staff: "Faculty / Staff ID" };
  const ID_DIGITS = { student: 6, faculty_staff: 4 };

  const byId = (id) => document.getElementById(id);

  /* ------------------------------------------------------------ Field helpers */
  function messageFor(input) {
    return document.querySelector(`[data-message-for="${input.id}"]`);
  }

  function setFieldError(input, message) {
    input.classList.add("is-invalid");
    const element = messageFor(input);
    if (element) element.textContent = message;
  }

  function clearField(input) {
    input.classList.remove("is-invalid");
    const element = messageFor(input);
    if (element) element.textContent = "";
  }

  function clearForm(form) {
    form.querySelectorAll(".is-invalid").forEach((input) => input.classList.remove("is-invalid"));
    form.querySelectorAll(".field-message").forEach((message) => { message.textContent = ""; });
    const status = form.querySelector(".form-status");
    if (status) {
      status.textContent = "";
      status.className = "form-status";
    }
  }

  function setStatus(id, message, type) {
    const status = byId(id);
    if (!status) return;
    status.textContent = message;
    status.className = `form-status ${type}`;
  }

  /* --------------------------------------------------------------- Validation */
  function isSchoolEmail(email) {
    return Object.values(EMAIL_PATTERNS).some((pattern) => pattern.test(email));
  }

  function emailContainsId(email, accountId) {
    return email.toLowerCase().endsWith(`.${accountId}.tc@umindanao.edu.ph`);
  }

  function isValidPassword(password) {
    return password.length >= 8 && /[^A-Za-z0-9]/.test(password);
  }

  /* ----------------------------------------------------------------- Behaviour */
  function setupPasswordToggles() {
    document.querySelectorAll("[data-toggle-password]").forEach((button) => {
      button.addEventListener("click", () => {
        const input = byId(button.dataset.togglePassword);
        if (!input) return;
        const showing = input.type === "text";
        input.type = showing ? "password" : "text";
        button.textContent = showing ? "Show" : "Hide";
        button.setAttribute("aria-label", showing ? "Show password" : "Hide password");
      });
    });
  }

  function setupLoginForm() {
    const form = byId("login-form");
    if (!form) return;

    const email = byId("login-email");
    const password = byId("login-password");
    const submit = form.querySelector("button[type='submit']");

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      clearForm(form);

      let valid = true;
      if (!isSchoolEmail(email.value.trim())) {
        setFieldError(email, "Enter a valid UMindanao school email.");
        valid = false;
      }
      if (!password.value) {
        setFieldError(password, "Enter your password.");
        valid = false;
      }
      if (!valid) return;

      submit.disabled = true;
      try {
        const response = await fetch(form.dataset.action, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email: email.value.trim(), password: password.value })
        });
        const result = await response.json();

        if (response.ok) {
          window.location.assign(result.redirect);
          return;
        }
        const field = result.field === "password" ? password : email;
        setFieldError(field, result.message);
      } catch (error) {
        setStatus("login-status", "Could not reach the server. Please try again.", "error");
      } finally {
        submit.disabled = false;
      }
    });
  }

  function setupRegistrationForm() {
    const form = byId("register-form");
    if (!form) return;

    const role = byId("register-role");
    const id = byId("register-id");
    const idLabel = byId("register-id-label");
    const idHint = byId("register-id-hint");

    role.addEventListener("change", () => {
      const label = ID_LABELS[role.value];
      const digits = ID_DIGITS[role.value];
      idLabel.textContent = label;
      id.placeholder = `${digits}-digit ${label}`;
      id.maxLength = digits;
      id.value = "";
      idHint.textContent = `${label} must contain exactly ${digits} digits.`;
      clearField(id);
    });

    id.addEventListener("input", () => {
      id.value = id.value.replace(/\D/g, "");
    });

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      clearForm(form);

      const firstName = byId("register-first-name");
      const lastName = byId("register-last-name");
      const email = byId("register-email");
      const password = byId("register-password");
      const confirmPassword = byId("register-confirm-password");
      const submit = form.querySelector("button[type='submit']");
      const digits = ID_DIGITS[role.value];
      const isStudent = role.value === "student";
      let valid = true;

      if (!new RegExp(`^\\d{${digits}}$`).test(id.value.trim())) {
        setFieldError(id, `Enter exactly ${digits} digits.`);
        valid = false;
      }
      if (!firstName.value.trim()) {
        setFieldError(firstName, "Enter your first name.");
        valid = false;
      }
      if (!lastName.value.trim()) {
        setFieldError(lastName, "Enter your last name.");
        valid = false;
      }

      if (!EMAIL_PATTERNS[role.value].test(email.value.trim())) {
        setFieldError(email, isStudent
          ? "Use your school email like firstname.lastname.123456.tc@umindanao.edu.ph"
          : "Use your school email like firstname.lastname.1234.tc@umindanao.edu.ph");
        valid = false;
      } else if (!emailContainsId(email.value.trim(), id.value.trim())) {
        setFieldError(email, "The ID inside the school email must match the account ID.");
        valid = false;
      }

      if (!isValidPassword(password.value)) {
        setFieldError(password, "Use at least 8 characters with at least one symbol.");
        valid = false;
      }
      if (confirmPassword.value !== password.value) {
        setFieldError(confirmPassword, "Passwords do not match.");
        valid = false;
      }
      if (!valid) return;

      submit.disabled = true;
      try {
        const response = await fetch(form.dataset.action, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            role: role.value,
            school_id: id.value.trim(),
            first_name: firstName.value.trim(),
            last_name: lastName.value.trim(),
            email: email.value.trim(),
            password: password.value,
            confirm_password: confirmPassword.value
          })
        });
        const result = await response.json();

        if (response.ok) {
          setStatus("register-status", result.message, "success");
          form.reset();
          role.dispatchEvent(new Event("change"));
          return;
        }

        const fields = {
          school_id: id,
          first_name: firstName,
          last_name: lastName,
          email,
          password,
          confirm_password: confirmPassword
        };
        setFieldError(fields[result.field] || email, result.message || "Could not create the account.");
      } catch (error) {
        setStatus("register-status", "Could not reach the server. Please try again.", "error");
      } finally {
        submit.disabled = false;
      }
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    setupPasswordToggles();
    setupLoginForm();
    setupRegistrationForm();
  });
})();
