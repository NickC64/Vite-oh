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

document.addEventListener("click", (event) => {
  const target = event.target;
  if (!(target instanceof Element)) {
    return;
  }
  const opener = target.closest("[data-dialog-open]");
  if (opener instanceof HTMLElement) {
    const dialog = document.getElementById(opener.dataset.dialogOpen);
    if (dialog instanceof HTMLDialogElement) {
      dialog.showModal();
    }
    return;
  }
  const closer = target.closest("[data-dialog-close]");
  if (closer instanceof HTMLElement) {
    closer.closest("dialog")?.close();
    return;
  }
  if (target instanceof HTMLDialogElement) {
    target.close();
  }
});

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
  const duration = form.querySelector("[data-duration]");
  const durationSummary = form.querySelector("[data-duration-summary]");
  const previewDeadline = document.querySelector("[data-preview-deadline]");
  const count = document.querySelector("[data-title-count]");
  const formatDuration = (minutes) => {
    const units = [
      [10080, "week"],
      [1440, "day"],
      [60, "hour"],
    ];
    for (const [unitMinutes, name] of units) {
      if (minutes >= unitMinutes && minutes % unitMinutes === 0) {
        const amount = minutes / unitMinutes;
        return `${amount.toLocaleString()} ${name}${amount === 1 ? "" : "s"}`;
      }
    }
    return `${minutes.toLocaleString()} minute${minutes === 1 ? "" : "s"}`;
  };
  const update = () => {
    previewTitle.textContent = title.value.trim() || "Your proposal title";
    previewContext.textContent =
      context.value.trim() || "Optional context will appear here.";
    count.textContent = title.value.length;
    const selected = form.querySelector("[name=proposal_type] option:checked");
    const hasType = Boolean(selected?.value);
    previewType.textContent = hasType ? `Type · ${selected.textContent}` : "";
    previewType.hidden = !hasType;
    const durationMinutes = Number(duration.value);
    if (Number.isInteger(durationMinutes) && durationMinutes > 0) {
      const label = formatDuration(durationMinutes);
      durationSummary.textContent = `Selected: ${label}.`;
      previewDeadline.textContent = `Fixed ${label} after creation`;
    }
  };
  form.addEventListener("input", update);
  update();
}

function renderRelativeTimes(root = document) {
  for (const time of root.querySelectorAll("[data-relative]")) {
    const deadline = new Date(time.dataset.relative);
    if (Number.isNaN(deadline.getTime())) {
      continue;
    }
    const seconds = Math.round((deadline - new Date()) / 1000);
    const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
    const [amount, unit] =
      Math.abs(seconds) < 3600
        ? [Math.round(seconds / 60), "minute"]
        : Math.abs(seconds) < 86400
          ? [Math.round(seconds / 3600), "hour"]
          : [Math.round(seconds / 86400), "day"];
    time.textContent = formatter.format(amount, unit);
    time.title = new Intl.DateTimeFormat(undefined, {
      dateStyle: "full",
      timeStyle: "long",
    }).format(deadline);
  }
}

function renderLocalDateTimes(root = document) {
  for (const time of root.querySelectorAll("[data-local-datetime]")) {
    const instant = new Date(time.dateTime || time.dataset.localDatetime);
    if (Number.isNaN(instant.getTime())) {
      continue;
    }
    const options =
      time.dataset.localDatetime === "date"
        ? { dateStyle: "medium" }
        : { dateStyle: "medium", timeStyle: "short" };
    time.textContent = new Intl.DateTimeFormat(undefined, options).format(instant);
    time.title = new Intl.DateTimeFormat(undefined, {
      dateStyle: "full",
      timeStyle: "long",
    }).format(instant);
  }
}

renderRelativeTimes();
renderLocalDateTimes();
document.addEventListener("htmx:beforeRequest", (event) => {
  const source = event.detail.elt;
  if (source instanceof HTMLFormElement) {
    source.closest("dialog")?.close();
  }
});
document.addEventListener("htmx:afterSwap", (event) => {
  renderRelativeTimes(event.detail.target);
  renderLocalDateTimes(event.detail.target);
  if (event.detail.target.querySelector?.("[data-refresh-page]")) {
    window.setTimeout(() => window.location.reload(), 500);
  }
});

let memberSearchTimer;
let memberSearchRequest;

document.addEventListener("input", (event) => {
  const input = event.target;
  if (!(input instanceof HTMLInputElement) || !input.matches("[data-member-search]")) {
    return;
  }
  const dialog = input.closest("dialog");
  const status = dialog?.querySelector("[data-member-search-status]");
  const results = dialog?.querySelector("[data-member-search-results]");
  const memberId = dialog?.querySelector("[data-member-id]");
  const selected = dialog?.querySelector("[data-selected-member]");
  const submit = dialog?.querySelector("[data-nudge-submit]");
  if (!(status instanceof HTMLElement) || !(results instanceof HTMLElement)) {
    return;
  }
  if (memberId instanceof HTMLInputElement) {
    memberId.value = "";
  }
  if (selected instanceof HTMLElement) {
    selected.hidden = true;
    selected.textContent = "";
  }
  if (submit instanceof HTMLButtonElement) {
    submit.disabled = true;
  }
  results.replaceChildren();
  memberSearchRequest?.abort();
  window.clearTimeout(memberSearchTimer);
  const query = input.value.trim().replace(/\s+/g, " ");
  if (query.length < 2) {
    status.textContent = "Enter at least two characters.";
    return;
  }
  status.textContent = "Searching…";
  memberSearchTimer = window.setTimeout(async () => {
    memberSearchRequest = new AbortController();
    try {
      const response = await fetch(
        `/app/guilds/${encodeURIComponent(input.dataset.guildId)}\/members?q=${encodeURIComponent(query)}`,
        {
          headers: { Accept: "application/json" },
          signal: memberSearchRequest.signal,
        },
      );
      const body = await response.json();
      if (!response.ok) {
        throw new Error(body.detail || "Member search is unavailable.");
      }
      const members = Array.isArray(body.members) ? body.members.slice(0, 8) : [];
      results.replaceChildren();
      for (const member of members) {
        const choice = document.createElement("button");
        choice.type = "button";
        choice.dataset.memberChoice = String(member.user_id);
        choice.dataset.memberName = String(member.display_name);
        choice.textContent = String(member.display_name);
        results.append(choice);
      }
      status.textContent = members.length
        ? `${members.length} matching member${members.length === 1 ? "" : "s"}`
        : "No matching members found.";
    } catch (error) {
      if (error.name !== "AbortError") {
        status.textContent = error.message || "Member search is unavailable.";
      }
    }
  }, 300);
});

document.addEventListener("click", (event) => {
  const target = event.target;
  if (!(target instanceof Element)) {
    return;
  }
  const choice = target.closest("[data-member-choice]");
  if (!(choice instanceof HTMLButtonElement)) {
    return;
  }
  const dialog = choice.closest("dialog");
  const memberId = dialog?.querySelector("[data-member-id]");
  const selected = dialog?.querySelector("[data-selected-member]");
  const submit = dialog?.querySelector("[data-nudge-submit]");
  if (memberId instanceof HTMLInputElement) {
    memberId.value = choice.dataset.memberChoice || "";
  }
  if (selected instanceof HTMLElement) {
    selected.textContent = `Selected: ${choice.dataset.memberName}`;
    selected.hidden = false;
  }
  if (submit instanceof HTMLButtonElement) {
    submit.disabled = false;
  }
});

for (const button of document.querySelectorAll("[data-history-back]")) {
  button.addEventListener("click", () => {
    if (history.length > 1) {
      history.back();
    } else {
      location.assign("/");
    }
  });
}
