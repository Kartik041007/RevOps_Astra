/* RevOps Astra: login page behaviour.
   Change AUTH_ENDPOINT and DEFAULT_REDIRECT to match your Flask routes. */
const AUTH_ENDPOINT = "/api/login";
const DEFAULT_REDIRECT = "/dashboard";

const form = document.getElementById("login-form");
const card = document.querySelector(".card");
const emailInput = document.getElementById("email");
const passwordInput = document.getElementById("password");
const rememberInput = document.getElementById("remember");
const submitBtn = document.getElementById("submit-btn");
const submitLabel = submitBtn.querySelector(".label");
const formError = document.getElementById("form-error");
const revealBtn = document.getElementById("reveal");
const capsHint = document.getElementById("caps-hint");

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;

/* ---------- Helpers ---------- */
function setFieldError(fieldId, hintId, message) {
  const field = document.getElementById(fieldId);
  const hint = document.getElementById(hintId);
  field.classList.toggle("invalid", Boolean(message));
  hint.hidden = !message;
  hint.textContent = message || "";
}

function showFormError(message) {
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
  setFieldError("field-email", "email-hint", "");
  setFieldError("field-password", "password-hint", "");
}

function setLoading(isLoading) {
  submitBtn.disabled = isLoading;
  submitBtn.classList.toggle("loading", isLoading);
  submitLabel.textContent = isLoading ? "Signing in…" : "Sign in";
  submitBtn.setAttribute("aria-busy", String(isLoading));
}

/* Only follow redirects that stay on this site */
function safeRedirect(target) {
  if (typeof target === "string" && target.startsWith("/") && !target.startsWith("//") && !target.includes("\\")) {
    return target;
  }
  return null;
}

function redirectTarget(serverTarget) {
  const params = new URLSearchParams(window.location.search);
  return safeRedirect(serverTarget) || safeRedirect(params.get("next")) || DEFAULT_REDIRECT;
}

function csrfHeaders() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  return meta && meta.content ? { "X-CSRFToken": meta.content } : {};
}

/* ---------- Validation ---------- */
function validate() {
  let valid = true;
  const email = emailInput.value.trim();

  if (!email) {
    setFieldError("field-email", "email-hint", "Enter your work email.");
    valid = false;
  } else if (!EMAIL_PATTERN.test(email)) {
    setFieldError("field-email", "email-hint", "Enter a valid email address, like name@company.com.");
    valid = false;
  } else {
    setFieldError("field-email", "email-hint", "");
  }

  if (!passwordInput.value) {
    setFieldError("field-password", "password-hint", "Enter your password.");
    valid = false;
  } else {
    setFieldError("field-password", "password-hint", "");
  }

  if (!valid) {
    const firstInvalid = document.querySelector(".field.invalid input");
    if (firstInvalid) firstInvalid.focus();
  }
  return valid;
}

/* ---------- Submit ---------- */
form.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearErrors();
  if (!validate()) return;

  setLoading(true);

  try {
    const response = await fetch(AUTH_ENDPOINT, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", Accept: "application/json", ...csrfHeaders() },
      body: JSON.stringify({
        email: emailInput.value.trim(),
        password: passwordInput.value,
        remember: rememberInput.checked
      })
    });

    let data = {};
    try { data = await response.json(); } catch (e) { /* response had no JSON body */ }

    if (response.ok) {
      submitLabel.textContent = "Welcome back";
      window.location.assign(redirectTarget(data.redirect));
      return;
    }

    passwordInput.value = "";
    passwordInput.focus();

    if (response.status === 429) {
      showFormError(data.error || data.message || "Too many sign-in attempts. Wait a few minutes and try again.");
    } else if (response.status === 400 || response.status === 401 || response.status === 403) {
      showFormError(data.error || data.message || "The email or password is incorrect.");
    } else {
      showFormError(data.error || data.message || "Sign-in is unavailable right now. Try again in a moment.");
    }
  } catch (error) {
    console.error("Login request failed:", error);
    showFormError("Could not reach the server. Check your connection and try again.");
  } finally {
    setLoading(false);
  }
});

/* Clear a field's error as soon as the person edits it */
emailInput.addEventListener("input", () => setFieldError("field-email", "email-hint", ""));
passwordInput.addEventListener("input", () => setFieldError("field-password", "password-hint", ""));

/* ---------- Show / hide password ---------- */
revealBtn.addEventListener("click", () => {
  const show = passwordInput.type === "password";
  passwordInput.type = show ? "text" : "password";
  revealBtn.setAttribute("aria-pressed", String(show));
  revealBtn.setAttribute("aria-label", show ? "Hide password" : "Show password");
  passwordInput.focus();
});

/* ---------- Caps Lock hint ---------- */
function checkCaps(event) {
  if (typeof event.getModifierState !== "function") return;
  capsHint.hidden = !event.getModifierState("CapsLock");
}
passwordInput.addEventListener("keydown", checkCaps);
passwordInput.addEventListener("keyup", checkCaps);
passwordInput.addEventListener("blur", () => { capsHint.hidden = true; });

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