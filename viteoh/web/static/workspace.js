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
    const selected = form.querySelector("[name=proposal_type]:checked");
    previewType.textContent =
      `${selected?.closest("label")?.querySelector("strong")?.textContent || "General"} proposal`;
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
