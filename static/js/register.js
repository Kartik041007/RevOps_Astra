/* RevOps Astra: registration page behaviour.
   Change REGISTER_ENDPOINT and DEFAULT_REDIRECT to match your Flask routes. */
const REGISTER_ENDPOINT = "/api/register";
const DEFAULT_REDIRECT = "/login";

const form = document.getElementById("register-form");
const card = document.querySelector(".card");
const nameInput = document.getElementById("name");
const companyInput = document.getElementById("company");
const emailInput = document.getElementById("email");
const passwordInput = document.getElementById("password");
const confirmInput = document.getElementById("confirm");
const termsInput = document.getElementById("terms");
const submitBtn = document.getElementById("submit-btn");
const submitLabel = submitBtn.querySelector(".label");
const formError = document.getElementById("form-error");
const formOk = document.getElementById("form-ok");
const revealBtn = document.getElementById("reveal");
const capsHint = document.getElementById("caps-hint");
const strengthEl = document.getElementById("strength");
const strengthLabel = document.getElementById("strength-label");

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;
const STRENGTH_TEXT = ["Enter a password", "Too weak", "Fair", "Good", "Strong"];

/* ---------- Helpers ---------- */
function setFieldError(name, message) {
  const field = document.getElementById(`field-${name}`);
  const hint = document.getElementById(`${name}-hint`);
  if (!field || !hint) return;
  field.classList.toggle("invalid", Boolean(message));
  if (message) field.classList.remove("valid");
  hint.hidden = !message;
  hint.textContent = message || "";
}

function setFieldValid(name, isValid) {
  const field = document.getElementById(`field-${name}`);
  if (field) field.classList.toggle("valid", isValid && !field.classList.contains("invalid"));
}

function showFormError(message) {
  formOk.hidden = true;
  formError.textContent = message;
  formError.hidden = false;
  card.classList.remove("shake");
  // restart the shake animation
  void card.offsetWidth;
  card.classList.add("shake");
}

function clearErrors() {
  formError.hidden = true;
  formError.textContent = "";
  ["name", "company", "email", "password", "confirm", "terms"].forEach((n) => setFieldError(n, ""));
}

function setLoading(isLoading) {
  submitBtn.disabled = isLoading;
  submitBtn.classList.toggle("loading", isLoading);
  submitLabel.textContent = isLoading ? "Creating account…" : "Create account";
  submitBtn.setAttribute("aria-busy", String(isLoading));
}

/* Only follow redirects that stay on this site */
function safeRedirect(target) {
  if (typeof target === "string" && target.startsWith("/") && !target.startsWith("//") && !target.includes("\\")) {
    return target;
  }
  return null;
}

function csrfHeaders() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  return meta && meta.content ? { "X-CSRFToken": meta.content } : {};
}

/* ---------- Password rules and strength ---------- */
function passwordChecks(value) {
  return {
    len: value.length >= 8,
    case: /[a-z]/.test(value) && /[A-Z]/.test(value),
    num: /\d/.test(value)
  };
}

function strengthLevel(value) {
  if (!value) return 0;
  const checks = passwordChecks(value);
  let score = Object.values(checks).filter(Boolean).length;
  if (/[^A-Za-z0-9]/.test(value) || value.length >= 12) score += 1;
  if (score === 0) return 1;
  return Math.min(4, Math.max(1, score));
}

function updateStrength() {
  const value = passwordInput.value;
  const checks = passwordChecks(value);

  document.querySelectorAll("#rules li").forEach((li) => {
    li.classList.toggle("ok", Boolean(checks[li.dataset.rule]));
  });

  const level = strengthLevel(value);
  strengthEl.dataset.level = String(level);
  strengthLabel.textContent = STRENGTH_TEXT[level];

  const allMet = checks.len && checks.case && checks.num;
  setFieldValid("password", allMet);
}

function updateMatch() {
  if (!confirmInput.value) {
    setFieldError("confirm", "");
    setFieldValid("confirm", false);
    return;
  }
  if (confirmInput.value === passwordInput.value) {
    setFieldError("confirm", "");
    setFieldValid("confirm", true);
  } else {
    setFieldError("confirm", "The passwords do not match.");
  }
}

/* ---------- Validation ---------- */
function validate() {
  let valid = true;
  const name = nameInput.value.trim();
  const company = companyInput.value.trim();
  const email = emailInput.value.trim();
  const checks = passwordChecks(passwordInput.value);

  if (name.length < 2) {
    setFieldError("name", "Enter your full name.");
    valid = false;
  }
  if (!company) {
    setFieldError("company", "Enter your company name.");
    valid = false;
  }
  if (!email) {
    setFieldError("email", "Enter your work email.");
    valid = false;
  } else if (!EMAIL_PATTERN.test(email)) {
    setFieldError("email", "Enter a valid email address, like name@company.com.");
    valid = false;
  }
  if (!passwordInput.value) {
    setFieldError("password", "Create a password.");
    valid = false;
  } else if (!(checks.len && checks.case && checks.num)) {
    setFieldError("password", "Your password needs every item in the list above.");
    valid = false;
  }
  if (!confirmInput.value) {
    setFieldError("confirm", "Repeat your password.");
    valid = false;
  } else if (confirmInput.value !== passwordInput.value) {
    setFieldError("confirm", "The passwords do not match.");
    valid = false;
  }
  if (!termsInput.checked) {
    setFieldError("terms", "Accept the Terms and Privacy Policy to continue.");
    valid = false;
  }

  if (!valid) {
    const first = document.querySelector(".field.invalid input");
    if (first) first.focus();
  }
  return valid;
}

/* Show per-field messages sent back by the server, e.g. { "errors": { "email": "..." } } */
function applyServerErrors(errors) {
  let applied = false;
  Object.keys(errors || {}).forEach((key) => {
    if (document.getElementById(`field-${key}`)) {
      const message = Array.isArray(errors[key]) ? errors[key][0] : errors[key];
      setFieldError(key, String(message));
      applied = true;
    }
  });
  if (applied) {
    const first = document.querySelector(".field.invalid input");
    if (first) first.focus();
  }
  return applied;
}

/* ---------- Submit ---------- */
form.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearErrors();
  formOk.hidden = true;
  if (!validate()) return;

  setLoading(true);

  try {
    const response = await fetch(REGISTER_ENDPOINT, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", Accept: "application/json", ...csrfHeaders() },
      body: JSON.stringify({
        name: nameInput.value.trim(),
        company: companyInput.value.trim(),
        email: emailInput.value.trim(),
        password: passwordInput.value,
        terms_accepted: termsInput.checked
      })
    });

    let data = {};
    try { data = await response.json(); } catch (e) { /* response had no JSON body */ }

    if (response.ok) {
      formError.hidden = true;
      formOk.textContent = data.message || "Account created. Taking you to sign in…";
      formOk.hidden = false;
      submitLabel.textContent = "Account created";
      submitBtn.classList.remove("loading");
      submitBtn.setAttribute("aria-busy", "false");
      const target = safeRedirect(data.redirect) || DEFAULT_REDIRECT;
      setTimeout(() => window.location.assign(target), 900);
      return;
    }

    if (response.status === 409) {
      setFieldError("email", data.error || "An account with this email already exists. Try signing in instead.");
      emailInput.focus();
    } else if ((response.status === 400 || response.status === 422) && applyServerErrors(data.errors)) {
      // field messages are already shown
    } else if (response.status === 429) {
      showFormError(data.error || data.message || "Too many attempts. Wait a few minutes and try again.");
    } else {
      showFormError(data.error || data.message || "We could not create your account right now. Try again in a moment.");
    }
  } catch (error) {
    console.error("Registration request failed:", error);
    showFormError("Could not reach the server. Check your connection and try again.");
  } finally {
    // keep the button locked after a successful sign-up so it cannot be sent twice
    if (formOk.hidden) setLoading(false);
  }
});

/* ---------- Live feedback ---------- */
nameInput.addEventListener("input", () => setFieldError("name", ""));
companyInput.addEventListener("input", () => setFieldError("company", ""));
emailInput.addEventListener("input", () => setFieldError("email", ""));
emailInput.addEventListener("blur", () => {
  const value = emailInput.value.trim();
  if (value && !EMAIL_PATTERN.test(value)) {
    setFieldError("email", "Enter a valid email address, like name@company.com.");
  } else if (value) {
    setFieldValid("email", true);
  }
});
passwordInput.addEventListener("input", () => {
  setFieldError("password", "");
  updateStrength();
  if (confirmInput.value) updateMatch();
});
confirmInput.addEventListener("input", updateMatch);
termsInput.addEventListener("change", () => setFieldError("terms", ""));

/* ---------- Show / hide both passwords ---------- */
revealBtn.addEventListener("click", () => {
  const show = passwordInput.type === "password";
  passwordInput.type = show ? "text" : "password";
  confirmInput.type = show ? "text" : "password";
  revealBtn.setAttribute("aria-pressed", String(show));
  revealBtn.setAttribute("aria-label", show ? "Hide passwords" : "Show passwords");
  passwordInput.focus();
});

/* ---------- Caps Lock hint ---------- */
function checkCaps(event) {
  if (typeof event.getModifierState !== "function") return;
  capsHint.hidden = !event.getModifierState("CapsLock");
}
[passwordInput, confirmInput].forEach((input) => {
  input.addEventListener("keydown", checkCaps);
  input.addEventListener("keyup", checkCaps);
  input.addEventListener("blur", () => { capsHint.hidden = true; });
});

/* ---------- Theme toggle (same behaviour as the other pages) ---------- */
function effectiveTheme() {
  const set = document.documentElement.getAttribute("data-theme");
  if (set) return set;
  return window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

try {
  const saved = localStorage.getItem("astra-theme");
  if (saved === "light" || saved === "dark") {
    document.documentElement.setAttribute("data-theme", saved);
  }
} catch (e) { /* storage unavailable */ }

document.getElementById("theme-btn").addEventListener("click", () => {
  const next = effectiveTheme() === "dark" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", next);
  try { localStorage.setItem("astra-theme", next); } catch (e) { /* storage unavailable */ }
});

updateStrength();