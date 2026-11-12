document.addEventListener("DOMContentLoaded", () => {
  const input = document.getElementById("id_draw_date");
  if (!input) return;

  const timezone = input.dataset.timezone;
  const shortcuts = document.createElement("div");
  shortcuts.className = "draw-date-shortcuts";
  shortcuts.style.display = "flex";
  shortcuts.style.flexWrap = "wrap";
  shortcuts.style.gap = "8px";
  shortcuts.style.marginTop = "8px";

  function setDrawTime(minutesFromNow) {
    const target = new Date(Date.now() + minutesFromNow * 60_000);
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: timezone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    }).formatToParts(target);
    const values = Object.fromEntries(
      parts
        .filter((part) => part.type !== "literal")
        .map((part) => [part.type, part.value]),
    );

    input.value = `${values.year}-${values.month}-${values.day}T${values.hour}:${values.minute}`;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }

  [
    { label: "In 30 minutes", minutes: 30 },
    { label: "In 1 hour", minutes: 60 },
  ].forEach(({ label, minutes }) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "button";
    button.textContent = label;
    button.addEventListener("click", () => setDrawTime(minutes));
    shortcuts.append(button);
  });

  input.insertAdjacentElement("afterend", shortcuts);
});
