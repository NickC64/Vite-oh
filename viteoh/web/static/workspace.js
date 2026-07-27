const themeQuery = matchMedia("(prefers-color-scheme: dark)");
const themeColor = document.querySelector("[data-theme-color]");

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  if (themeColor) {
    themeColor.content = theme === "dark" ? "#1e1f22" : "#ffffff";
  }
  for (const label of document.querySelectorAll("[data-theme-label]")) {
    label.textContent =
      theme === "dark" ? "Switch to light theme" : "Switch to dark theme";
  }
}

applyTheme(document.documentElement.dataset.theme || "light");

for (const toggle of document.querySelectorAll("[data-theme-toggle]")) {
  toggle.addEventListener("click", () => {
    const next =
      document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    localStorage.setItem("viteoh-theme", next);
    applyTheme(next);
  });
}

themeQuery.addEventListener("change", (event) => {
  if (!localStorage.getItem("viteoh-theme")) {
    applyTheme(event.matches ? "dark" : "light");
  }
});

for (const image of document.querySelectorAll("[data-guild-icon]")) {
  image.addEventListener("error", () => {
    image.hidden = true;
  });
}

const drawerOpen = document.querySelector("[data-drawer-open]");
const drawer = document.querySelector("#workspace-navigation");
let drawerReturnFocus = null;

function closeDrawer() {
  document.body.classList.remove("drawer-open");
  drawerOpen?.setAttribute("aria-expanded", "false");
  if (drawer instanceof HTMLElement) {
    drawer.removeAttribute("tabindex");
  }
  if (drawerReturnFocus instanceof HTMLElement) {
    drawerReturnFocus.focus();
  }
}

drawerOpen?.addEventListener("click", () => {
  drawerReturnFocus = document.activeElement;
  document.body.classList.add("drawer-open");
  drawerOpen.setAttribute("aria-expanded", "true");
  if (drawer instanceof HTMLElement) {
    drawer.tabIndex = -1;
    drawer.focus();
  }
});

for (const closer of document.querySelectorAll("[data-drawer-close]")) {
  closer.addEventListener("click", closeDrawer);
}

for (const opener of document.querySelectorAll("[data-dialog-open]")) {
  opener.addEventListener("click", () => {
    const dialog = document.getElementById(opener.dataset.dialogOpen);
    if (dialog instanceof HTMLDialogElement) {
      dialog.showModal();
    }
  });
}

for (const closer of document.querySelectorAll("[data-dialog-close]")) {
  closer.addEventListener("click", () => {
    closer.closest("dialog")?.close();
  });
}

for (const dialog of document.querySelectorAll("dialog")) {
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) {
      dialog.close();
    }
  });
}

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && document.body.classList.contains("drawer-open")) {
    closeDrawer();
  }
});

const form = document.querySelector("[data-proposal-form]");
if (form) {
  const title = form.querySelector("[data-title]");
  const context = form.querySelector("[data-context]");
  const previewTitle = document.querySelector("[data-preview-title]");
  const previewContext = document.querySelector("[data-preview-context]");
  const previewType = document.querySelector("[data-preview-type]");
  const count = document.querySelector("[data-title-count]");
  const update = () => {
    previewTitle.textContent = title.value.trim() || "Your proposal title";
    previewContext.textContent =
      context.value.trim() || "Optional context will appear here.";
    count.textContent = title.value.length;
    const selected = form.querySelector("[name=proposal_type] option:checked");
    const hasType = Boolean(selected?.value);
    previewType.textContent = hasType ? `Type · ${selected.textContent}` : "";
    previewType.hidden = !hasType;
  };
  form.addEventListener("input", update);
  update();
}

for (const time of document.querySelectorAll("[data-relative]")) {
  const deadline = new Date(time.dataset.relative);
  const seconds = Math.round((deadline - new Date()) / 1000);
  const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  const [amount, unit] =
    Math.abs(seconds) < 3600
      ? [Math.round(seconds / 60), "minute"]
      : Math.abs(seconds) < 86400
        ? [Math.round(seconds / 3600), "hour"]
        : [Math.round(seconds / 86400), "day"];
  time.textContent = formatter.format(amount, unit);
}

for (const button of document.querySelectorAll("[data-history-back]")) {
  button.addEventListener("click", () => {
    if (history.length > 1) {
      history.back();
    } else {
      location.assign("/");
    }
  });
}
