const quoteForm = document.querySelector(".quote-panel");
const quoteResult = document.querySelector("#quote-result");
const vansSelect = quoteForm?.querySelector('select[name="luton-vans"]');
const moversSelect = quoteForm?.querySelector('select[name="movers"]');
const moverCapacityNote = quoteForm?.querySelector("#mover-capacity-note");
const additionalStopList = quoteForm?.querySelector("#additional-stop-list");
const addStopButton = quoteForm?.querySelector("#add-stop");
const BOOKING_DRAFT_KEY = "menwithvan.bookingDraft.v1";
const BOOKING_DRAFT_TTL_MS = 24 * 60 * 60 * 1000;
const MOVERS_PER_LUTON_VAN = 3;
const STRIPE_JS_URL = "https://js.stripe.com/v3/";

const pounds = new Intl.NumberFormat("en-GB", {
  style: "currency",
  currency: "GBP",
});

let lastQuotePayload = null;
let lastQuote = null;
let quoteAutoUpdateTimer = null;
let quoteRequestCounter = 0;
let stripeJsPromise = null;
let embeddedCheckout = null;
let currentPaymentReference = "";
let paymentRefreshTimer = null;
let paymentRefreshCounter = 0;
const calendarStateByPanel = new WeakMap();

function loadDraft() {
  const stores = [];
  ["sessionStorage", "localStorage"].forEach((name) => {
    try {
      if (window[name]) stores.push(window[name]);
    } catch (error) {
      // Some privacy modes block storage access; continue without draft restore.
    }
  });

  for (const store of stores) {
    try {
      const raw = store.getItem(BOOKING_DRAFT_KEY);
      if (!raw) continue;

      const draft = JSON.parse(raw);
      if (draft.expiresAt && Date.parse(draft.expiresAt) < Date.now()) {
        store.removeItem(BOOKING_DRAFT_KEY);
        continue;
      }
      return draft;
    } catch (error) {
      // Try the next storage option.
    }
  }

  return {};
}

function saveDraft(partial) {
  const draft = {
    ...loadDraft(),
    ...partial,
    updatedAt: new Date().toISOString(),
    expiresAt: new Date(Date.now() + BOOKING_DRAFT_TTL_MS).toISOString(),
  };

  try {
    sessionStorage.setItem(BOOKING_DRAFT_KEY, JSON.stringify(draft));
  } catch (error) {
    // Draft saving is a convenience only; the booking flow must still work if storage is unavailable.
  }
}

function firstNumber(value) {
  const match = String(value || "").match(/\d+/);
  return match ? Number(match[0]) : 0;
}

function cleanList(values) {
  return values.map((value) => String(value || "").trim()).filter(Boolean);
}

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function safeStripeCheckoutUrl(value) {
  try {
    const url = new URL(String(value || ""), window.location.href);
    if (url.protocol === "https:" && url.hostname === "checkout.stripe.com") {
      return url.href;
    }
  } catch (error) {
    // Invalid checkout URLs are handled by the caller.
  }
  return "";
}

function safeStripePublishableKey(value) {
  const key = String(value || "").trim();
  return key.startsWith("pk_") ? key : "";
}

function safeStripeClientSecret(value) {
  const secret = String(value || "").trim();
  return secret.startsWith("cs_") && secret.includes("_secret_") ? secret : "";
}

function loadStripeJs() {
  if (window.Stripe) return Promise.resolve(window.Stripe);
  if (stripeJsPromise) return stripeJsPromise;

  stripeJsPromise = new Promise((resolve, reject) => {
    const existing = document.querySelector(`script[src="${STRIPE_JS_URL}"]`);
    if (existing) {
      existing.addEventListener("load", () => resolve(window.Stripe), { once: true });
      existing.addEventListener("error", () => reject(new Error("Stripe could not be loaded.")), { once: true });
      return;
    }

    const script = document.createElement("script");
    script.src = STRIPE_JS_URL;
    script.async = true;
    script.addEventListener("load", () => resolve(window.Stripe), { once: true });
    script.addEventListener("error", () => reject(new Error("Stripe could not be loaded.")), { once: true });
    document.head.append(script);
  });

  return stripeJsPromise;
}

function destroyEmbeddedCheckout(options = {}) {
  const { removePanel = true } = options;
  if (embeddedCheckout) {
    try {
      if (typeof embeddedCheckout.destroy === "function") embeddedCheckout.destroy();
      if (typeof embeddedCheckout.unmount === "function") embeddedCheckout.unmount();
    } catch (error) {
      // A fresh payment panel can still be mounted if cleanup is unavailable.
    }
    embeddedCheckout = null;
  }
  if (removePanel) {
    document.querySelector("[data-embedded-payment-panel]")?.remove();
    currentPaymentReference = "";
  }
}

function addressHint(quote, key, fallback = "") {
  const hint = quote.addressHints?.[key];
  return hint?.formatted || fallback || "";
}

function additionalAddressHint(quote, index, fallback = "") {
  const hint = quote.addressHints?.additionalStops?.[index];
  return hint?.formatted || fallback || "";
}

function preferredTimeSlots() {
  const slots = [];
  for (let hour = 8; hour <= 10; hour += 1) {
    slots.push(`${String(hour).padStart(2, "0")}:00`);
    if (hour < 10) slots.push(`${String(hour).padStart(2, "0")}:30`);
  }
  for (let hour = 13; hour <= 21; hour += 1) {
    slots.push(`${String(hour).padStart(2, "0")}:00`);
    if (hour < 21) slots.push(`${String(hour).padStart(2, "0")}:30`);
  }

  return slots;
}

function formatTimeSlot(slot) {
  const [hourText, minute] = slot.split(":");
  const hour = Number(hourText);
  const period = hour >= 12 ? "pm" : "am";
  const displayHour = hour > 12 ? hour - 12 : hour;
  return `${displayHour}:${minute} ${period}`;
}

function formatMoveDateLabel(value) {
  if (!value) return "No date selected";

  try {
    return new Intl.DateTimeFormat("en-GB", {
      weekday: "long",
      day: "numeric",
      month: "long",
      year: "numeric",
    }).format(new Date(`${value}T12:00:00`));
  } catch (error) {
    return value;
  }
}

function startOfToday() {
  const now = new Date();
  return new Date(now.getFullYear(), now.getMonth(), now.getDate());
}

function dateFromIso(value) {
  const match = String(value || "").match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!match) return null;
  const [, year, month, day] = match;
  return new Date(Number(year), Number(month) - 1, Number(day));
}

function isoFromDate(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function monthStart(date) {
  return new Date(date.getFullYear(), date.getMonth(), 1);
}

function addMonths(date, amount) {
  return new Date(date.getFullYear(), date.getMonth() + amount, 1);
}

function sameDate(left, right) {
  return Boolean(left && right && left.getFullYear() === right.getFullYear() && left.getMonth() === right.getMonth() && left.getDate() === right.getDate());
}

function getCalendarState(bookingPanel) {
  const existing = calendarStateByPanel.get(bookingPanel);
  if (existing) return existing;

  const selectedDate = dateFromIso(bookingPanel?.elements?.["move-date"]?.value);
  const state = {
    currentMonth: monthStart(selectedDate || startOfToday()),
  };
  calendarStateByPanel.set(bookingPanel, state);
  return state;
}

function renderBookingCalendar(bookingPanel) {
  const grid = bookingPanel?.querySelector("[data-calendar-grid]");
  const monthLabel = bookingPanel?.querySelector("[data-calendar-month]");
  const prevButton = bookingPanel?.querySelector("[data-calendar-prev]");
  if (!bookingPanel || !grid || !monthLabel) return;

  const state = getCalendarState(bookingPanel);
  const today = startOfToday();
  const selectedDate = dateFromIso(bookingPanel.elements?.["move-date"]?.value);
  const monthDate = state.currentMonth;
  const firstDay = new Date(monthDate.getFullYear(), monthDate.getMonth(), 1);
  const daysInMonth = new Date(monthDate.getFullYear(), monthDate.getMonth() + 1, 0).getDate();
  const leadingBlanks = (firstDay.getDay() + 6) % 7;

  monthLabel.textContent = new Intl.DateTimeFormat("en-GB", {
    month: "long",
    year: "numeric",
  }).format(monthDate);

  if (prevButton) {
    prevButton.disabled = monthDate <= monthStart(today);
  }

  grid.innerHTML = "";
  for (let index = 0; index < leadingBlanks; index += 1) {
    const blank = document.createElement("span");
    blank.className = "calendar-empty";
    grid.append(blank);
  }

  for (let day = 1; day <= daysInMonth; day += 1) {
    const date = new Date(monthDate.getFullYear(), monthDate.getMonth(), day);
    const iso = isoFromDate(date);
    const button = document.createElement("button");
    button.type = "button";
    button.className = "calendar-day";
    button.textContent = String(day);
    button.dataset.calendarDate = iso;
    button.disabled = date < today;
    button.classList.toggle("is-today", sameDate(date, today));
    button.classList.toggle("is-selected", sameDate(date, selectedDate));
    if (sameDate(date, selectedDate)) button.setAttribute("aria-current", "date");
    grid.append(button);
  }
}

function selectCalendarDate(bookingPanel, isoDate) {
  const field = bookingPanel?.elements?.["move-date"];
  if (!bookingPanel || !field) return;

  field.value = isoDate;
  bookingPanel.querySelectorAll('input[name="move-time"]').forEach((input) => {
    input.checked = false;
  });
  setTimeFieldState(bookingPanel, true, "");
  renderBookingCalendar(bookingPanel);
  saveBookingFormDraft(bookingPanel);
  refreshAvailability(bookingPanel, "");

  const timeField = bookingPanel.querySelector("[data-time-field]");
  if (timeField) {
    window.requestAnimationFrame(() => {
      timeField.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
  }
}

function preferredTimeRadios() {
  return preferredTimeSlots()
    .map(
      (slot) => `
        <label class="time-slot">
          <input type="radio" name="move-time" value="${slot}" required disabled>
          <span>${formatTimeSlot(slot)}</span>
        </label>
      `
    )
    .join("");
}

function updateScheduleSummary(bookingPanel) {
  if (!bookingPanel) return;

  const dateValue = bookingPanel.elements?.["move-date"]?.value || "";
  const selectedTime = bookingPanel.querySelector('input[name="move-time"]:checked')?.value || "";
  const dateLabel = bookingPanel.querySelector("[data-selected-date-label]");
  const timeLabel = bookingPanel.querySelector("[data-selected-time-label]");
  const summaryTime = bookingPanel.querySelector("[data-selected-start-label]");
  const scheduleCard = bookingPanel.querySelector("[data-schedule-card]");

  if (dateLabel) dateLabel.textContent = formatMoveDateLabel(dateValue);
  if (timeLabel) timeLabel.textContent = selectedTime ? formatTimeSlot(selectedTime) : "Choose arrival time";
  if (summaryTime) {
    summaryTime.hidden = !selectedTime;
    summaryTime.textContent = selectedTime ? `Start time: ${formatTimeSlot(selectedTime)}` : "";
  }
  scheduleCard?.classList.toggle("has-date", Boolean(dateValue));
  scheduleCard?.classList.toggle("has-time", Boolean(selectedTime));
}

function revealQuoteResult({ scroll = true } = {}) {
  quoteResult.hidden = false;
  quoteResult.setAttribute("tabindex", "-1");
  if (!scroll) return;
  window.requestAnimationFrame(() => {
    quoteResult.scrollIntoView({ behavior: "smooth", block: "start" });
    quoteResult.focus({ preventScroll: true });
  });
}

function showQuoteMessage(type, title, message, options = {}) {
  destroyEmbeddedCheckout();
  quoteResult.className = `quote-result ${type}`;
  quoteResult.innerHTML = `
    <strong>${escapeHtml(title)}</strong>
    <p>${escapeHtml(message)}</p>
  `;
  revealQuoteResult(options);
}

function addAdditionalStop(value = "") {
  if (!additionalStopList) return;

  const row = document.createElement("div");
  row.className = "additional-stop-row";
  row.innerHTML = `
    <input type="text" name="additional-stop" placeholder="Additional stop postcode or area" value="${escapeHtml(value)}">
    <button type="button" class="remove-stop" aria-label="Remove additional stop">Remove</button>
  `;
  additionalStopList.append(row);
}

function setNamedValue(form, name, value) {
  const field = form?.elements?.[name];
  if (!field || value === undefined || value === null) return;

  if (field instanceof RadioNodeList) {
    field.value = value;
    return;
  }

  if (field.type === "checkbox") {
    field.checked = Boolean(value);
    return;
  }

  field.value = value;
}

function quoteFormDraft() {
  if (!quoteForm) return {};

  const data = new FormData(quoteForm);
  return {
    lutonVans: data.get("luton-vans"),
    movers: data.get("movers"),
    estimatedHours: data.get("estimated-hours"),
    packAndMove: data.get("pack-and-move"),
    pickup: data.get("pickup"),
    delivery: data.get("delivery"),
    additionalStops: data.getAll("additional-stop"),
    pickupStairs: data.get("pickup-stairs"),
    deliveryStairs: data.get("delivery-stairs"),
    items: data.get("items"),
  };
}

function saveQuoteFormDraft() {
  saveDraft({ quoteForm: quoteFormDraft() });
}

function buildQuotePayload() {
  const data = new FormData(quoteForm);

  return {
    moveType: "Removal booking",
    lutonVans: firstNumber(data.get("luton-vans")),
    movers: firstNumber(data.get("movers")),
    hours: firstNumber(data.get("estimated-hours")),
    packAndMove: data.get("pack-and-move") === "yes",
    pickup: data.get("pickup"),
    delivery: data.get("delivery"),
    additionalStops: cleanList(data.getAll("additional-stop")),
    pickupStairs: firstNumber(data.get("pickup-stairs")),
    deliveryStairs: firstNumber(data.get("delivery-stairs")),
    items: data.get("items"),
  };
}

function hasQuoteLocations(payload) {
  return Boolean(String(payload.pickup || "").trim() && String(payload.delivery || "").trim());
}

function restoreQuoteFormDraft() {
  const draft = loadDraft().quoteForm;
  if (!draft || !quoteForm) return "";

  setNamedValue(quoteForm, "luton-vans", draft.lutonVans);
  setNamedValue(quoteForm, "estimated-hours", draft.estimatedHours);
  setNamedValue(quoteForm, "pack-and-move", draft.packAndMove);
  setNamedValue(quoteForm, "pickup", draft.pickup);
  setNamedValue(quoteForm, "delivery", draft.delivery);
  setNamedValue(quoteForm, "pickup-stairs", draft.pickupStairs);
  setNamedValue(quoteForm, "delivery-stairs", draft.deliveryStairs);
  setNamedValue(quoteForm, "items", draft.items);

  if (additionalStopList) {
    additionalStopList.innerHTML = "";
    (draft.additionalStops || []).filter(Boolean).forEach((stop) => addAdditionalStop(stop));
  }

  return draft.movers || "";
}

function restoreMoverValue(value) {
  if (!value || !moversSelect) return;

  const match = Array.from(moversSelect.options).find((option) => option.value === value || option.textContent === value);
  if (match) moversSelect.value = match.value;
}

async function restoreSharedDraftFromUrl(refreshMoverOptions) {
  const token = new URLSearchParams(window.location.search).get("draft");
  if (!token) return false;

  showQuoteMessage("loading", "Opening saved quote", "Loading the saved moving details.");

  try {
    const response = await fetch(`/api/booking-drafts/${encodeURIComponent(token)}`);
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Saved quote link could not be opened.");

    saveDraft({
      quoteForm: result.quoteForm,
      quotePayload: result.quotePayload,
      quote: result.quote,
      booking: result.booking,
      bookingOpen: true,
    });

    if (result.quoteForm && quoteForm) {
      setNamedValue(quoteForm, "luton-vans", result.quoteForm.lutonVans);
      setNamedValue(quoteForm, "estimated-hours", result.quoteForm.estimatedHours);
      setNamedValue(quoteForm, "pack-and-move", result.quoteForm.packAndMove);
      setNamedValue(quoteForm, "pickup", result.quoteForm.pickup);
      setNamedValue(quoteForm, "delivery", result.quoteForm.delivery);
      setNamedValue(quoteForm, "pickup-stairs", result.quoteForm.pickupStairs);
      setNamedValue(quoteForm, "delivery-stairs", result.quoteForm.deliveryStairs);
      setNamedValue(quoteForm, "items", result.quoteForm.items);

      if (additionalStopList) {
        additionalStopList.innerHTML = "";
        (result.quoteForm.additionalStops || []).filter(Boolean).forEach((stop) => addAdditionalStop(stop));
      }

      refreshMoverOptions?.();
      restoreMoverValue(result.quoteForm.movers);
      saveQuoteFormDraft();
    }

    if (result.quote && result.quotePayload) {
      renderQuote(result.quote, result.quotePayload, { restoreBookingDraft: true, scrollResult: true });
      openBookingPanel({ focus: false });
    }

    window.history.replaceState(null, "", `${window.location.pathname}${window.location.hash || "#quote"}`);
    return true;
  } catch (error) {
    showQuoteMessage("error", "Saved quote unavailable", error.message || "This saved quote link could not be opened.");
    return true;
  }
}

function bookingFormDraft(bookingPanel) {
  const data = new FormData(bookingPanel);
  return {
    customerName: data.get("customer-name"),
    customerEmail: data.get("customer-email"),
    customerPhone: data.get("customer-phone"),
    moveDate: data.get("move-date"),
    moveTime: data.get("move-time"),
    pickupAddress: data.get("pickup-address"),
    deliveryAddress: data.get("delivery-address"),
    additionalAddresses: data.getAll("additional-address"),
    paymentOption: data.get("payment-option"),
    termsAccepted: data.get("terms-accepted") === "on",
  };
}

function bookingSubmissionPayload(bookingPanel) {
  const data = new FormData(bookingPanel);
  return {
    quoteInputs: lastQuotePayload,
    customer: {
      name: data.get("customer-name"),
      email: data.get("customer-email"),
      phone: data.get("customer-phone"),
    },
    booking: {
      moveDate: data.get("move-date"),
      moveTime: data.get("move-time"),
      pickupAddress: data.get("pickup-address"),
      deliveryAddress: data.get("delivery-address"),
      additionalAddresses: cleanList(data.getAll("additional-address")),
      paymentOption: data.get("payment-option"),
      termsAccepted: data.get("terms-accepted") === "on",
    },
  };
}

function saveBookingFormDraft(bookingPanel) {
  if (!bookingPanel) return;
  saveDraft({ booking: bookingFormDraft(bookingPanel) });
}

function setTimeFieldState(bookingPanel, hasDate, selectedTime = "") {
  const timeField = bookingPanel?.querySelector("[data-time-field]");
  const timeInputs = bookingPanel?.querySelectorAll('input[name="move-time"]') || [];

  if (timeField) timeField.hidden = !hasDate;
  setAvailabilityNote(
    bookingPanel,
    hasDate ? "Checking available times..." : "Select a moving date to view arrival times.",
    hasDate ? "checking" : ""
  );
  timeInputs.forEach((input) => {
    input.disabled = true;
    if (!hasDate) {
      input.checked = false;
      return;
    }
    input.checked = input.value === selectedTime;
  });
  updateScheduleSummary(bookingPanel);
}

function quoteCapacityRequest() {
  const payload = lastQuotePayload || buildQuotePayload();
  return {
    vans: payload.lutonVans || 1,
    movers: payload.movers || 1,
    hours: payload.hours || 2,
  };
}

function setAvailabilityNote(bookingPanel, message, type = "") {
  const note = bookingPanel?.querySelector("[data-availability-note]");
  if (!note) return;
  note.hidden = !message;
  note.textContent = message;
  note.className = `availability-note ${type}`.trim();
}

async function refreshAvailability(bookingPanel, selectedTime = "") {
  const moveDate = bookingPanel?.elements?.["move-date"]?.value;
  const timeInputs = bookingPanel?.querySelectorAll('input[name="move-time"]') || [];
  if (!bookingPanel || !moveDate) return;

  const capacity = quoteCapacityRequest();
  const params = new URLSearchParams({
    date: moveDate,
    vans: String(capacity.vans),
    movers: String(capacity.movers),
    hours: String(capacity.hours),
  });

  setAvailabilityNote(bookingPanel, "Checking available times...", "checking");
  timeInputs.forEach((input) => {
    input.disabled = true;
    input.closest(".time-slot")?.classList.remove("unavailable");
  });

  try {
    const response = await fetch(`/api/availability?${params.toString()}`);
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Availability could not be checked.");

    const slots = new Map((result.slots || []).map((slot) => [slot.time, slot]));
    let availableCount = 0;

    let selectedTimeAvailable = false;

    timeInputs.forEach((input) => {
      const slot = slots.get(input.value);
      const available = Boolean(slot?.available);
      input.disabled = !available;
      input.checked = available && input.value === selectedTime;
      if (available && input.value === selectedTime) selectedTimeAvailable = true;
      const label = input.closest(".time-slot");
      label?.classList.toggle("unavailable", !available);
      if (label) {
        label.title = available
          ? `${slot.remainingVans} Luton van${slot.remainingVans === 1 ? "" : "s"} still available`
          : slot?.reason || "Unavailable";
      }
      if (available) availableCount += 1;
    });

    if (selectedTimeAvailable) {
      setAvailabilityNote(bookingPanel, "");
    } else if (availableCount) {
      setAvailabilityNote(bookingPanel, "Choose one of the available arrival times below.", "ok");
    } else {
      setAvailabilityNote(bookingPanel, "No times are available for this date. Please choose another date.", "warn");
    }
  } catch (error) {
    setAvailabilityNote(bookingPanel, "Times could not be checked. Please try again or contact the office.", "warn");
  }
  updateScheduleSummary(bookingPanel);
}

function applyBookingFormDraft(bookingPanel, draft) {
  if (!bookingPanel || !draft) return;

  setNamedValue(bookingPanel, "customer-name", draft.customerName);
  setNamedValue(bookingPanel, "customer-email", draft.customerEmail);
  setNamedValue(bookingPanel, "customer-phone", draft.customerPhone);
  setNamedValue(bookingPanel, "move-date", draft.moveDate);
  setNamedValue(bookingPanel, "pickup-address", draft.pickupAddress);
  setNamedValue(bookingPanel, "delivery-address", draft.deliveryAddress);
  setNamedValue(bookingPanel, "payment-option", draft.paymentOption);
  setNamedValue(bookingPanel, "terms-accepted", draft.termsAccepted);

  bookingPanel.querySelectorAll('[name="additional-address"]').forEach((field, index) => {
    field.value = draft.additionalAddresses?.[index] || field.value;
  });
  setTimeFieldState(bookingPanel, Boolean(draft.moveDate), draft.moveTime);
  renderBookingCalendar(bookingPanel);
  if (draft.moveDate) refreshAvailability(bookingPanel, draft.moveTime);
}

function openBookingPanel({ focus = true } = {}) {
  const bookingPanel = quoteResult.querySelector(".booking-panel");
  const actions = quoteResult.querySelector(".quote-actions");
  if (!bookingPanel) return;

  if (actions) actions.hidden = true;
  bookingPanel.hidden = false;
  saveDraft({ bookingOpen: true });
  renderBookingCalendar(bookingPanel);
  const selectedTime = bookingPanel.querySelector('input[name="move-time"]:checked')?.value || "";
  if (bookingPanel.elements?.["move-date"]?.value) refreshAvailability(bookingPanel, selectedTime);

  if (!focus) return;
  window.requestAnimationFrame(() => {
    bookingPanel.scrollIntoView({ behavior: "smooth", block: "start" });
    bookingPanel.querySelector("input, select, textarea, button")?.focus({ preventScroll: true });
  });
}

function setBookingSubmitState(bookingPanel, message = "", type = "") {
  if (!bookingPanel) return;

  let status = bookingPanel.querySelector("[data-booking-submit-status]");
  if (!status) {
    status = document.createElement("p");
    status.dataset.bookingSubmitStatus = "true";
    status.className = "booking-submit-status";
    bookingPanel.querySelector('button[type="submit"]')?.before(status);
  }

  status.hidden = !message;
  status.textContent = message;
  status.className = `booking-submit-status ${type}`.trim();
}

function renderQuote(quote, payload, options = {}) {
  destroyEmbeddedCheckout();
  const totals = quote.totals;
  const overtimeHourlyTotal = quote.overtime.hourlyRateIncVat;
  const overtimeHalfHourTotal = quote.overtime.halfHourRateIncVat ?? overtimeHourlyTotal / 2;
  const status =
    quote.pricingStatus === "confirmed"
      ? "Confirmed rate basis"
      : "Guide price";
  const extraStops = payload.additionalStops || [];
  const extraAddressFields = extraStops
    .map(
      (stop, index) => `
        <label class="full">
          Additional stop ${index + 1} full address
          <textarea name="additional-address" rows="3" required>${escapeHtml(additionalAddressHint(quote, index, stop))}</textarea>
          <small>Prefilled from postcode. Please add door number, flat, building name and any missing street/access detail.</small>
        </label>
      `
    )
    .join("");
  const displayMessages = (quote.messages || [])
    .map((message) => {
      const text = String(message || "");
      if (text.includes("Once online payment is completed")) {
        return "Once online payment is completed, we send a confirmation email confirming the booking is final.";
      }
      return text;
    })
    .filter((message) => {
      const text = message.toLowerCase();
      return !text.includes("minimum booking");
    })
    .sort((first, second) => {
      const firstIsOvertime = first.toLowerCase().startsWith("overtime after the booked time");
      const secondIsOvertime = second.toLowerCase().startsWith("overtime after the booked time");
      return Number(secondIsOvertime) - Number(firstIsOvertime);
    });

  lastQuote = quote;
  lastQuotePayload = payload;
  saveDraft({ quote, quotePayload: payload });

  quoteResult.className = "quote-result success";
  quoteResult.innerHTML = `
    <div class="quote-result-head">
      <span>${status}</span>
      <strong>${pounds.format(totals.totalIncVat)}</strong>
    </div>
    <dl class="quote-breakdown">
      ${quote.lineItems
        .map(
          (item) => `
            <div>
              <dt>${escapeHtml(item.label)}</dt>
              <dd>${pounds.format(item.amountExVat)}</dd>
            </div>
          `
        )
        .join("")}
      <div>
        <dt>VAT</dt>
        <dd>${pounds.format(totals.vat)}</dd>
      </div>
      <div class="total">
        <dt>Total including VAT</dt>
        <dd>${pounds.format(totals.totalIncVat)}</dd>
      </div>
      <div>
        <dt>Overtime rate after booked hours</dt>
        <dd>${pounds.format(overtimeHourlyTotal)} / hour<br><small>${pounds.format(overtimeHalfHourTotal)} per 30 mins</small></dd>
      </div>
    </dl>
    <ul>
      ${displayMessages.map((message) => `<li>${escapeHtml(message)}</li>`).join("")}
    </ul>
    <div class="quote-actions">
      <button type="button" class="show-booking-form">Continue to booking details</button>
      <p>Next step: full addresses, moving date, arrival time and payment choice. Your price updates automatically if you change the quote details above.</p>
    </div>
    <form class="booking-panel booking-experience" hidden>
      <div class="booking-hero-card">
        <span class="booking-step-pill">Step 2 of 3</span>
        <div>
          <h3>Lock in your moving date and crew.</h3>
          <p>Pick a moving date, choose an arrival time, then confirm with secure payment.</p>
        </div>
      </div>

      <div class="booking-progress-strip" aria-label="Booking progress">
        <span>Quote ready</span>
        <span>Date and time</span>
        <span>Secure payment</span>
      </div>

      <section class="booking-section schedule-section" data-schedule-card>
        <div class="booking-section-head">
          <span>Plan the move</span>
          <h4>Moving date and arrival time</h4>
          <p>Select a moving date. Available arrival times will appear beside it.</p>
        </div>
        <div class="schedule-board">
          <div class="calendar-card">
            <div class="calendar-picker" data-calendar>
              <input type="hidden" name="move-date" required data-calendar-value>
              <div class="calendar-label">Moving date</div>
              <div class="calendar-nav">
                <button type="button" data-calendar-prev aria-label="Previous month">‹</button>
                <strong data-calendar-month></strong>
                <button type="button" data-calendar-next aria-label="Next month">›</button>
              </div>
              <div class="calendar-weekdays" aria-hidden="true">
                <span>Mon</span>
                <span>Tue</span>
                <span>Wed</span>
                <span>Thu</span>
                <span>Fri</span>
                <span>Sat</span>
                <span>Sun</span>
              </div>
              <div class="calendar-grid" data-calendar-grid></div>
            </div>
            <div class="selected-date-card">
              <small>Selected date</small>
              <strong data-selected-date-label>No date selected</strong>
              <span class="selected-time-summary" data-selected-start-label hidden></span>
            </div>
          </div>
          <div class="time-field availability-card" data-time-field hidden>
            <div class="availability-card-head">
              <div>
                <span>Available arrival times</span>
                <strong data-selected-time-label>Choose arrival time</strong>
              </div>
              <small>Only available times are shown.</small>
            </div>
            <p class="availability-note" data-availability-note>Select a moving date to view arrival times.</p>
            <div class="time-slot-grid" role="radiogroup" aria-label="Arrival time">
              ${preferredTimeRadios()}
            </div>
          </div>
        </div>
      </section>

      <section class="booking-section">
        <div class="booking-section-head">
          <span>Your details</span>
          <h4>Who should we contact?</h4>
        </div>
        <div class="form-grid booking-field-grid">
          <label>
            Full name
            <input name="customer-name" autocomplete="name" required>
          </label>
          <label>
            Email
            <input type="email" name="customer-email" autocomplete="email" required>
          </label>
          <label>
            Phone
            <input name="customer-phone" autocomplete="tel" required>
          </label>
        </div>
      </section>

      <section class="booking-section address-section">
        <div class="booking-section-head">
          <span>Addresses</span>
          <h4>Complete the full pickup and delivery addresses.</h4>
          <p>We prefill what we can from the postcode. Please add door number, flat, building name and any missing street details.</p>
        </div>
        <div class="form-grid booking-field-grid address-field-grid">
          <label class="address-card">
            Full pickup address
            <textarea name="pickup-address" rows="3" required>${escapeHtml(addressHint(quote, "pickup", payload.pickup))}</textarea>
            <small>Include door number, flat number, building name, parking/loading bay or concierge details if relevant.</small>
          </label>
          <label class="address-card">
            Full delivery address
            <textarea name="delivery-address" rows="3" required>${escapeHtml(addressHint(quote, "delivery", payload.delivery))}</textarea>
            <small>Include the exact delivery entrance and any access detail needed for the crew.</small>
          </label>
          ${extraAddressFields}
        </div>
      </section>

      <section class="booking-section payment-section">
        <div class="booking-section-head">
          <span>Payment</span>
          <h4>Confirm with secure Stripe payment.</h4>
        </div>
        <div class="form-grid booking-field-grid">
          <div class="payment-choice-panel full">
            <div class="payment-choice-copy">
              <span>Payment choice</span>
              <strong>Choose how to confirm your booking.</strong>
            </div>
            <div class="payment-choice-grid" role="radiogroup" aria-label="Payment choice">
              <label class="payment-choice-card">
                <input type="radio" name="payment-option" value="deposit" checked required>
                <span class="payment-choice-card-body">
                  <span class="payment-choice-eyebrow">25% today</span>
                  <strong>Pay 25% deposit</strong>
                  <small>Secure the booking now. The remaining balance is paid on completion.</small>
                </span>
              </label>
              <label class="payment-choice-card">
                <input type="radio" name="payment-option" value="full">
                <span class="payment-choice-card-body">
                  <span class="payment-choice-eyebrow">Settle online</span>
                  <strong>Pay full amount</strong>
                  <small>Pay the full quoted total now and keep completion day simpler.</small>
                </span>
              </label>
            </div>
            <p class="payment-choice-note">Overtime after the booked time is ${pounds.format(overtimeHourlyTotal)} per hour, billed every 30 minutes at ${pounds.format(overtimeHalfHourTotal)}, payable on completion by cash, card or bank transfer.</p>
          </div>
          <label class="terms-check full">
            <input type="checkbox" name="terms-accepted" required>
            <span>I accept the <a href="terms-and-conditions.html" target="_blank" rel="noopener">terms and conditions</a>.</span>
          </label>
        </div>
      </section>

      <button type="submit">Continue to secure payment</button>
    </form>
  `;
  if (options.restoreBookingDraft) {
    const draft = loadDraft();
    const bookingPanel = quoteResult.querySelector(".booking-panel");
    applyBookingFormDraft(bookingPanel, draft.booking);
    if (draft.bookingOpen) openBookingPanel({ focus: false });
  }
  revealQuoteResult({ scroll: options.scrollResult !== false });
}

function paymentSummary(result) {
  const amountDue = result.payment?.amountDueNow || result.quote.totals.deposit25;
  const paymentLabel = result.paymentOption === "full" ? "Full payment" : "25% deposit";
  const balanceText = result.paymentOption === "full"
    ? "No balance remains after this online payment."
    : `${pounds.format(result.quote.totals.balanceAfterDeposit)} balance is payable on completion.`;
  const inputs = result.quote.inputs || {};
  const moveDate = result.booking?.moveDate || "";
  const moveTime = result.booking?.moveTime || "";
  const selectedRate = result.quote.overtime?.hourlyRateIncVat || result.quote.rates?.selectedJobHourlyRateIncVat || 0;
  const halfHourRate = result.quote.overtime?.halfHourRateIncVat || selectedRate / 2;

  return {
    amountDue,
    paymentLabel,
    balanceText,
    inputs,
    selectedRate,
    halfHourRate,
    moveDateLabel: formatMoveDateLabel(moveDate),
    moveTimeLabel: moveTime ? formatTimeSlot(moveTime) : "Selected arrival time",
    vehicleText: `${inputs.lutonVans || 1} Luton van${Number(inputs.lutonVans || 1) === 1 ? "" : "s"}`,
    moverText: `${inputs.movers || 1} ${Number(inputs.movers || 1) === 1 ? "man" : "men"}`,
    bookedHoursText: `${inputs.hours || 2} booked hour${Number(inputs.hours || 2) === 1 ? "" : "s"}`,
  };
}

async function mountEmbeddedPayment(result) {
  const mount = document.querySelector("#embedded-checkout");
  const status = document.querySelector("[data-embedded-payment-status]");
  const publishableKey = safeStripePublishableKey(result.stripePublishableKey);
  const clientSecret = safeStripeClientSecret(result.stripeClientSecret);

  if (!mount || !status) return;
  if (!publishableKey || !clientSecret) {
    status.hidden = false;
    status.textContent = "The secure payment form could not be prepared. Please contact the office to complete payment.";
    status.classList.add("warn");
    return;
  }

  try {
    status.hidden = false;
    status.textContent = "Loading secure Stripe payment form...";
    status.classList.remove("ok");
    status.classList.remove("warn");
    const Stripe = await loadStripeJs();
    if (!Stripe) throw new Error("Stripe did not load.");

    destroyEmbeddedCheckout({ removePanel: false });
    embeddedCheckout = await Stripe(publishableKey).initEmbeddedCheckout({ clientSecret });
    embeddedCheckout.mount("#embedded-checkout");
    status.textContent = "";
    status.hidden = true;
  } catch (error) {
    status.hidden = false;
    status.textContent = "Stripe payment form could not be loaded. Please refresh and try again, or contact the office.";
    status.classList.add("warn");
  }
}

function renderEmbeddedPayment(result) {
  destroyEmbeddedCheckout();
  currentPaymentReference = result.reference || "";
  const summary = paymentSummary(result);
  const paymentPanel = document.createElement("section");
  paymentPanel.className = "embedded-payment-panel";
  paymentPanel.dataset.embeddedPaymentPanel = "true";
  paymentPanel.setAttribute("aria-label", "Secure payment");

  paymentPanel.innerHTML = `
    <div class="embedded-payment-shell">
      <section class="embedded-payment-summary" aria-label="Booking payment summary">
        <span class="booking-step-pill">Step 3 of 3</span>
        <div class="embedded-payment-head">
          <span>Secure payment</span>
          <strong>${escapeHtml(summary.paymentLabel)}</strong>
          <p>Your moving date and details are ready. Complete the secure payment below, or go back to adjust the booking.</p>
        </div>
        <div class="payment-amount-card">
          <small>Due today</small>
          <strong>${pounds.format(summary.amountDue)}</strong>
          <span>${escapeHtml(summary.paymentLabel)} to confirm the booking</span>
        </div>
        <div class="payment-summary-lines" aria-label="Payment summary">
          <p><span>Date:</span> ${escapeHtml(summary.moveDateLabel)} at ${escapeHtml(summary.moveTimeLabel)}</p>
          <p><span>Crew:</span> ${escapeHtml(summary.vehicleText)}, ${escapeHtml(summary.moverText)}, ${escapeHtml(summary.bookedHoursText)}</p>
          <p><span>Total:</span> ${pounds.format(result.quote.totals.totalIncVat)} including VAT</p>
          <p><span>Balance:</span> ${escapeHtml(summary.balanceText)}</p>
        </div>
        <div class="payment-overtime-note">
          <strong>Overtime, if needed</strong>
          <span>${pounds.format(summary.selectedRate)} per hour, billed every 30 minutes at ${pounds.format(summary.halfHourRate)}, payable on completion by cash, card or bank transfer.</span>
        </div>
        <button type="button" class="payment-edit-button" data-back-to-booking>Change booking or payment choice</button>
        <p class="secure-payment-note">Card details are entered directly into Stripe. Men With Van does not see or store card numbers.</p>
      </section>
      <section class="embedded-payment-form-card" aria-label="Stripe secure payment form">
        <div class="embedded-payment-form-head">
          <span>Stripe secure checkout</span>
          <strong>Complete payment</strong>
        </div>
        <p class="embedded-payment-status" data-embedded-payment-status>Preparing secure payment form...</p>
        <div id="embedded-checkout" class="embedded-checkout-frame"></div>
      </section>
    </div>
  `;

  const bookingPanel = quoteResult.querySelector(".booking-panel");
  if (bookingPanel) {
    bookingPanel.insertAdjacentElement("afterend", paymentPanel);
  } else {
    quoteResult.append(paymentPanel);
  }

  quoteResult.hidden = false;
  window.requestAnimationFrame(() => {
    paymentPanel.scrollIntoView({ behavior: "smooth", block: "start" });
  });
  mountEmbeddedPayment(result);
}

function markPaymentRefreshing(message) {
  const panel = document.querySelector("[data-embedded-payment-panel]");
  const status = panel?.querySelector("[data-embedded-payment-status]");
  if (!panel || !status) return;

  panel.classList.add("is-refreshing");
  status.hidden = false;
  status.textContent = message;
  status.classList.remove("ok");
  status.classList.remove("warn");
  destroyEmbeddedCheckout({ removePanel: false });
}

function paymentRefreshApplies(target) {
  return Boolean(target?.name && ["payment-option", "move-time"].includes(target.name));
}

async function refreshExistingPayment(bookingPanel) {
  if (!bookingPanel || !currentPaymentReference || !document.querySelector("[data-embedded-payment-panel]")) return;
  if (!lastQuotePayload || !lastQuote) return;

  const data = new FormData(bookingPanel);
  if (!data.get("move-date") || !data.get("move-time")) return;

  const requestId = ++paymentRefreshCounter;
  markPaymentRefreshing("Updating the secure payment amount...");
  setBookingSubmitState(bookingPanel, "Updating Step 3 with the latest payment choice...", "loading");

  try {
    const response = await fetch(`/api/bookings/${encodeURIComponent(currentPaymentReference)}/payment-session`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(bookingSubmissionPayload(bookingPanel)),
    });
    const result = await response.json();
    if (requestId !== paymentRefreshCounter) return;
    if (!response.ok) throw new Error((result.errors || [result.error || "Payment could not be refreshed."]).join(" "));

    setBookingSubmitState(bookingPanel, "");
    renderEmbeddedPayment(result);
  } catch (error) {
    const status = document.querySelector("[data-embedded-payment-status]");
    if (status) {
      status.textContent = error.message || "Payment could not be refreshed. Please press Continue to secure payment again.";
      status.classList.add("warn");
    }
    setBookingSubmitState(bookingPanel, error.message || "Payment could not be refreshed. Please try again.", "warn");
  }
}

function schedulePaymentRefresh(bookingPanel) {
  if (!bookingPanel || !currentPaymentReference || !document.querySelector("[data-embedded-payment-panel]")) return;

  window.clearTimeout(paymentRefreshTimer);
  paymentRefreshTimer = window.setTimeout(() => {
    refreshExistingPayment(bookingPanel);
  }, 450);
}

function returnToBookingDetails() {
  destroyEmbeddedCheckout();
  const bookingPanel = quoteResult.querySelector(".booking-panel");
  if (bookingPanel) {
    bookingPanel.hidden = false;
    window.requestAnimationFrame(() => {
      bookingPanel?.querySelector('[name="payment-option"]')?.scrollIntoView({ behavior: "smooth", block: "center" });
    });
    return;
  }

  if (lastQuote && lastQuotePayload) {
    renderQuote(lastQuote, lastQuotePayload, { restoreBookingDraft: true, scrollResult: true });
    openBookingPanel({ focus: false });
    return;
  }

  requestQuote({ scrollResult: true });
}

function renderPaymentRedirect(result) {
  destroyEmbeddedCheckout();
  const checkoutUrl = safeStripeCheckoutUrl(result.checkoutUrl);
  if (!checkoutUrl) {
    showQuoteMessage(
      "error",
      "Payment link unavailable",
      "The booking was saved, but the secure Stripe checkout link could not be verified. Please contact the office to complete payment."
    );
    return;
  }
  const summary = paymentSummary(result);
  quoteResult.className = "quote-result success payment-panel";
  quoteResult.innerHTML = `
    <div class="quote-result-head">
      <span>Secure Stripe checkout</span>
      <strong>${escapeHtml(result.reference)}</strong>
    </div>
    <p>Your booking details have been saved. Complete the secure Stripe payment to confirm your selected moving date and arrival time.</p>
    <dl class="quote-breakdown">
      <div>
        <dt>Payment type</dt>
        <dd>${escapeHtml(summary.paymentLabel)}</dd>
      </div>
      <div>
        <dt>Due now</dt>
        <dd>${pounds.format(summary.amountDue)}</dd>
      </div>
      <div>
        <dt>Total including VAT</dt>
        <dd>${pounds.format(result.quote.totals.totalIncVat)}</dd>
      </div>
      <div>
        <dt>After payment</dt>
        <dd>${escapeHtml(summary.balanceText)}</dd>
      </div>
    </dl>
    <p class="quote-distance">Opening secure Stripe checkout now. If it does not open automatically, use the button below. If you return from Stripe, your booking draft is saved on this device.</p>
    <p class="calendar-actions"><a class="payment-link" href="${checkoutUrl}">Continue to secure Stripe checkout</a></p>
  `;
}

function renderBookingConfirmation(result) {
  destroyEmbeddedCheckout();
  quoteResult.className = "quote-result success";
  quoteResult.innerHTML = `
    <div class="quote-result-head">
      <span>Booking request received</span>
      <strong>${escapeHtml(result.reference)}</strong>
    </div>
    <p>${escapeHtml(result.message)}</p>
    <dl class="quote-breakdown">
      <div>
        <dt>Total including VAT</dt>
        <dd>${pounds.format(result.quote.totals.totalIncVat)}</dd>
      </div>
    </dl>
    <p class="quote-distance">The office can now see this request in the admin dashboard.</p>
  `;
}

async function requestQuote({ live = false, scrollResult = true } = {}) {
  const payload = buildQuotePayload();

  if (!hasQuoteLocations(payload)) {
    if (!live) {
      showQuoteMessage("error", "Postcodes needed", "Enter both pickup and delivery postcodes so we can calculate the mileage.");
    }
    return false;
  }

  const bookingPanel = quoteResult.querySelector(".booking-panel");
  if (bookingPanel) saveBookingFormDraft(bookingPanel);

  const requestId = ++quoteRequestCounter;
  if (!live || quoteResult.hidden) {
    showQuoteMessage(
      "loading",
      live ? "Updating quote" : "Calculating quote",
      "Checking route distance, vans, movers, packing option, floors/stairs and VAT.",
      { scroll: !live && scrollResult }
    );
  } else {
    quoteResult.setAttribute("aria-busy", "true");
  }

  try {
    const response = await fetch("/api/quote", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const result = await response.json();

    if (requestId !== quoteRequestCounter) return false;

    if (!response.ok) {
      showQuoteMessage("error", "Quote needs attention", (result.errors || [result.error || "Please check the details."]).join(" "), {
        scroll: !live && scrollResult,
      });
      return false;
    }

    renderQuote(result, payload, { restoreBookingDraft: true, scrollResult: !live && scrollResult });
    return true;
  } catch (error) {
    if (requestId === quoteRequestCounter) {
      showQuoteMessage("error", "Quote service unavailable", "The calculator could not be reached. Please try again or contact the office.", {
        scroll: !live && scrollResult,
      });
    }
    return false;
  } finally {
    if (requestId === quoteRequestCounter) quoteResult.removeAttribute("aria-busy");
  }
}

const liveQuoteFields = new Set([
  "luton-vans",
  "movers",
  "estimated-hours",
  "pack-and-move",
  "pickup",
  "delivery",
  "additional-stop",
  "pickup-stairs",
  "delivery-stairs",
]);

function shouldLiveUpdateQuote(target) {
  return Boolean(target?.name && liveQuoteFields.has(target.name));
}

function scheduleLiveQuoteUpdate() {
  const payload = buildQuotePayload();
  if (!hasQuoteLocations(payload)) return;

  window.clearTimeout(quoteAutoUpdateTimer);
  quoteAutoUpdateTimer = window.setTimeout(() => {
    requestQuote({ live: true, scrollResult: false });
  }, 650);
}

if (quoteForm && quoteResult) {
  quoteResult.hidden = true;
  const restoredMover = restoreQuoteFormDraft();

  const updateMoverOptions = () => {
    if (!vansSelect || !moversSelect) return;

    const vans = firstNumber(vansSelect.value) || 1;
    const minMovers = vans;
    const maxMovers = vans * MOVERS_PER_LUTON_VAN;
    const current = firstNumber(moversSelect.value);

    moversSelect.innerHTML = "";
    for (let movers = minMovers; movers <= maxMovers; movers += 1) {
      const option = document.createElement("option");
      option.value = `${movers} ${movers === 1 ? "man" : "men"}`;
      option.textContent = option.value;
      moversSelect.append(option);
    }

    const nextValue = Math.min(Math.max(current || minMovers, minMovers), maxMovers);
    moversSelect.value = `${nextValue} ${nextValue === 1 ? "man" : "men"}`;

    if (moverCapacityNote) {
      moverCapacityNote.textContent = `Total men needed to load and unload. ${vans} Luton van${vans === 1 ? "" : "s"} can be booked with ${minMovers}-${maxMovers} men.`;
    }
  };

  updateMoverOptions();
  restoreMoverValue(restoredMover);
  saveQuoteFormDraft();
  vansSelect?.addEventListener("change", () => {
    updateMoverOptions();
    saveQuoteFormDraft();
    scheduleLiveQuoteUpdate();
  });
  quoteForm.addEventListener("input", (event) => {
    saveQuoteFormDraft();
    if (shouldLiveUpdateQuote(event.target)) scheduleLiveQuoteUpdate();
  });
  quoteForm.addEventListener("change", (event) => {
    saveQuoteFormDraft();
    if (shouldLiveUpdateQuote(event.target)) scheduleLiveQuoteUpdate();
  });
  addStopButton?.addEventListener("click", () => {
    addAdditionalStop();
    saveQuoteFormDraft();
  });
  additionalStopList?.addEventListener("click", (event) => {
    if (event.target.matches(".remove-stop")) {
      event.target.closest(".additional-stop-row")?.remove();
      saveQuoteFormDraft();
      scheduleLiveQuoteUpdate();
    }
  });

  quoteForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    window.clearTimeout(quoteAutoUpdateTimer);
    requestQuote({ scrollResult: true });
  });

  quoteResult.addEventListener("click", (event) => {
    if (event.target.closest("[data-back-to-booking]")) {
      returnToBookingDetails();
      return;
    }

    if (!event.target.matches(".show-booking-form")) return;

    openBookingPanel();
  });

  quoteResult.addEventListener("change", (event) => {
    const bookingPanel = event.target.closest(".booking-panel");

    if (!event.target.matches('input[name="move-date"]')) return;

    const hasDate = Boolean(event.target.value);
    const selectedTime = bookingPanel?.querySelector('input[name="move-time"]:checked')?.value || "";

    setTimeFieldState(bookingPanel, hasDate, selectedTime);
    saveBookingFormDraft(bookingPanel);
    if (hasDate) refreshAvailability(bookingPanel, selectedTime);

    const timeField = bookingPanel?.querySelector("[data-time-field]");
    if (hasDate && timeField) {
      window.requestAnimationFrame(() => {
        timeField.scrollIntoView({ behavior: "smooth", block: "nearest" });
      });
    }
  });

  quoteResult.addEventListener("input", (event) => {
    const bookingPanel = event.target.closest(".booking-panel");
    if (bookingPanel) {
      if (event.target.matches('input[name="move-time"]')) {
        setAvailabilityNote(bookingPanel, "");
      }
      updateScheduleSummary(bookingPanel);
      saveBookingFormDraft(bookingPanel);
    }
  });

  quoteResult.addEventListener("change", (event) => {
    const bookingPanel = event.target.closest(".booking-panel");
    if (bookingPanel) {
      updateScheduleSummary(bookingPanel);
      saveBookingFormDraft(bookingPanel);
      if (paymentRefreshApplies(event.target)) schedulePaymentRefresh(bookingPanel);
    }
  });

  quoteResult.addEventListener("click", (event) => {
    const bookingPanel = event.target.closest(".booking-panel");
    if (!bookingPanel) return;

    if (event.target.closest("[data-calendar-prev]")) {
      const state = getCalendarState(bookingPanel);
      state.currentMonth = addMonths(state.currentMonth, -1);
      renderBookingCalendar(bookingPanel);
      return;
    }

    if (event.target.closest("[data-calendar-next]")) {
      const state = getCalendarState(bookingPanel);
      state.currentMonth = addMonths(state.currentMonth, 1);
      renderBookingCalendar(bookingPanel);
      return;
    }

    const dayButton = event.target.closest("[data-calendar-date]");
    if (dayButton && !dayButton.disabled) {
      selectCalendarDate(bookingPanel, dayButton.dataset.calendarDate);
    }
  });

  quoteResult.addEventListener("click", (event) => {
    const termsLink = event.target.closest('a[href="terms-and-conditions.html"]');
    if (!termsLink) return;

    const bookingPanel = termsLink.closest(".booking-panel");
    if (bookingPanel) {
      saveBookingFormDraft(bookingPanel);
      saveDraft({ bookingOpen: true });
    }
  });

  quoteResult.addEventListener("submit", async (event) => {
    if (!event.target.matches(".booking-panel")) return;
    event.preventDefault();
    const bookingPanel = event.target;
    const submitButton = bookingPanel.querySelector('button[type="submit"]');
    saveBookingFormDraft(bookingPanel);
    saveDraft({ bookingOpen: true });
    destroyEmbeddedCheckout();

    if (!lastQuotePayload || !lastQuote) {
      showQuoteMessage("error", "Quote needed", "Please calculate the quote again before booking.");
      return;
    }

    const data = new FormData(event.target);
    if (!data.get("move-date")) {
      setAvailabilityNote(bookingPanel, "Choose your moving date from the calendar.", "warn");
      bookingPanel.querySelector("[data-calendar]")?.scrollIntoView({ behavior: "smooth", block: "center" });
      return;
    }
    if (!data.get("move-time")) {
      setAvailabilityNote(bookingPanel, "Choose an available arrival time.", "warn");
      bookingPanel.querySelector("[data-time-field]")?.scrollIntoView({ behavior: "smooth", block: "center" });
      return;
    }

    const payload = bookingSubmissionPayload(bookingPanel);

    setBookingSubmitState(bookingPanel, "Saving your move details securely and preparing the payment section...", "loading");
    if (submitButton) submitButton.disabled = true;

    try {
      const response = await fetch("/api/bookings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const result = await response.json();
      if (!response.ok) {
        setBookingSubmitState(bookingPanel, (result.errors || [result.error || "Please check the details."]).join(" "), "warn");
        return;
      }
      setBookingSubmitState(bookingPanel, "");
      if (result.stripeClientSecret && result.stripePublishableKey) {
        renderEmbeddedPayment(result);
        return;
      }
      if (result.checkoutUrl) {
        renderPaymentRedirect(result);
        const checkoutUrl = safeStripeCheckoutUrl(result.checkoutUrl);
        if (checkoutUrl) window.location.assign(checkoutUrl);
        return;
      }
      renderBookingConfirmation(result);
    } catch (error) {
      setBookingSubmitState(bookingPanel, "The booking request could not be saved. Please try again or contact the office.", "warn");
    } finally {
      if (submitButton) submitButton.disabled = false;
    }
  });

  restoreSharedDraftFromUrl(updateMoverOptions).then((restoredSharedDraft) => {
    if (restoredSharedDraft) return;

    const draft = loadDraft();
    if (draft.quote && draft.quotePayload) {
      requestQuote({ live: true, scrollResult: false });
    }
  });
}
