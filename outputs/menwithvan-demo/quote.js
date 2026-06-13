const quoteForm = document.querySelector(".quote-panel");
const quoteResult = document.querySelector("#quote-result");
const vansSelect = quoteForm?.querySelector('select[name="luton-vans"]');
const moversSelect = quoteForm?.querySelector('select[name="movers"]');
const moverCapacityNote = quoteForm?.querySelector("#mover-capacity-note");
const additionalStopList = quoteForm?.querySelector("#additional-stop-list");
const addStopButton = quoteForm?.querySelector("#add-stop");
const BOOKING_DRAFT_KEY = "menwithvan.bookingDraft.v1";
const BOOKING_DRAFT_TTL_MS = 7 * 24 * 60 * 60 * 1000;

const pounds = new Intl.NumberFormat("en-GB", {
  style: "currency",
  currency: "GBP",
});

let lastQuotePayload = null;
let lastQuote = null;

function loadDraft() {
  const stores = [];
  ["localStorage", "sessionStorage"].forEach((name) => {
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
    localStorage.setItem(BOOKING_DRAFT_KEY, JSON.stringify(draft));
  } catch (error) {
    try {
      sessionStorage.setItem(BOOKING_DRAFT_KEY, JSON.stringify(draft));
    } catch (storageError) {
      // Draft saving is a convenience only; the booking flow must still work if storage is unavailable.
    }
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

function revealQuoteResult() {
  quoteResult.hidden = false;
  quoteResult.setAttribute("tabindex", "-1");
  window.requestAnimationFrame(() => {
    quoteResult.scrollIntoView({ behavior: "smooth", block: "start" });
    quoteResult.focus({ preventScroll: true });
  });
}

function showQuoteMessage(type, title, message) {
  quoteResult.className = `quote-result ${type}`;
  quoteResult.innerHTML = `
    <strong>${title}</strong>
    <p>${message}</p>
  `;
  revealQuoteResult();
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
    moveType: data.get("move-type"),
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

function restoreQuoteFormDraft() {
  const draft = loadDraft().quoteForm;
  if (!draft || !quoteForm) return "";

  setNamedValue(quoteForm, "move-type", draft.moveType);
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

function saveBookingFormDraft(bookingPanel) {
  if (!bookingPanel) return;
  saveDraft({ booking: bookingFormDraft(bookingPanel) });
}

function setTimeFieldState(bookingPanel, hasDate, selectedTime = "") {
  const timeField = bookingPanel?.querySelector("[data-time-field]");
  const timeInputs = bookingPanel?.querySelectorAll('input[name="move-time"]') || [];

  if (timeField) timeField.hidden = !hasDate;
  timeInputs.forEach((input) => {
    input.disabled = !hasDate;
    if (!hasDate) {
      input.checked = false;
      return;
    }
    input.checked = input.value === selectedTime;
  });
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
}

function openBookingPanel({ focus = true } = {}) {
  const bookingPanel = quoteResult.querySelector(".booking-panel");
  const actions = quoteResult.querySelector(".quote-actions");
  if (!bookingPanel) return;

  if (actions) actions.hidden = true;
  bookingPanel.hidden = false;
  saveDraft({ bookingOpen: true });

  if (!focus) return;
  window.requestAnimationFrame(() => {
    bookingPanel.scrollIntoView({ behavior: "smooth", block: "start" });
    bookingPanel.querySelector("input, select, textarea, button")?.focus({ preventScroll: true });
  });
}

function renderQuote(quote, payload, options = {}) {
  const totals = quote.totals;
  const overtimeHourlyIncVat = quote.overtime.hourlyRateIncVat;
  const overtimeHalfHourIncVat = quote.overtime.halfHourRateIncVat ?? overtimeHourlyIncVat / 2;
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
              <dt>${item.label}</dt>
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
        <dt>25% deposit today</dt>
        <dd>${pounds.format(totals.deposit25)}</dd>
      </div>
      <div>
        <dt>Balance after deposit</dt>
        <dd>${pounds.format(totals.balanceAfterDeposit)}</dd>
      </div>
      <div>
        <dt>Overtime rate after booked hours</dt>
        <dd>${pounds.format(overtimeHourlyIncVat)} / hour inc VAT<br><small>${pounds.format(overtimeHalfHourIncVat)} per 30 mins inc VAT</small></dd>
      </div>
    </dl>
    <p class="quote-distance">Route distance: ${quote.distance.miles} miles. Minimum booking: ${quote.rates.minimumHours} hours. Overtime after the booked time is ${pounds.format(overtimeHourlyIncVat)} per hour including VAT, billed every 30 minutes at ${pounds.format(overtimeHalfHourIncVat)} including VAT, payable to the driver on completion.</p>
    <ul>
      ${quote.messages.map((message) => `<li>${message}</li>`).join("")}
    </ul>
    <div class="quote-actions">
      <button type="button" class="show-booking-form">Continue to booking details</button>
      <p>Next step: full addresses, moving date, arrival time and payment choice.</p>
    </div>
    <form class="booking-panel" hidden>
      <h3>Book this move</h3>
      <p>Confirm the move details, then continue to secure Stripe checkout. Once payment is completed, your selected date and arrival time are confirmed.</p>
      <div class="form-grid">
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
        <div class="booking-date-time full">
          <div class="field-label">Preferred moving date</div>
          <label class="date-picker-card">
            Moving date
            <input type="date" name="move-date" required>
          </label>
          <div class="time-field" data-time-field hidden>
            <div class="field-label">Available arrival times</div>
            <div class="time-slot-grid" role="radiogroup" aria-label="Arrival time">
              ${preferredTimeRadios()}
            </div>
          </div>
          <small>Choose a date first, then select a start time. Available starts are 8:00am-10:00am, then 1:00pm-9:00pm in 30-minute intervals.</small>
        </div>
        <p class="address-hint-note full">We have prefilled the address boxes from the postcode where possible. Please complete the door number, flat, building name and any missing details before payment.</p>
        <label class="full">
          Full pickup address
          <textarea name="pickup-address" rows="3" required>${escapeHtml(addressHint(quote, "pickup", payload.pickup))}</textarea>
          <small>Prefilled from postcode. Please add door number, flat, building name and any missing street/access detail.</small>
        </label>
        <label class="full">
          Full delivery address
          <textarea name="delivery-address" rows="3" required>${escapeHtml(addressHint(quote, "delivery", payload.delivery))}</textarea>
          <small>Prefilled from postcode. Please add door number, flat, building name and any missing street/access detail.</small>
        </label>
        ${extraAddressFields}
        <label class="full">
          Payment choice
          <select name="payment-option" required>
            <option value="deposit">Pay 25% deposit, balance on completion</option>
            <option value="full">Pay full amount online</option>
          </select>
          <small>Any overtime beyond the booked hours is charged at ${pounds.format(overtimeHourlyIncVat)} per hour including VAT, billed every 30 minutes at ${pounds.format(overtimeHalfHourIncVat)} including VAT, and is payable on completion.</small>
        </label>
        <label class="terms-check full">
          <input type="checkbox" name="terms-accepted" required>
          I accept the <a href="terms-and-conditions.html" target="_blank" rel="noopener">terms and conditions</a>.
        </label>
      </div>
      <button type="submit">Continue to secure payment</button>
    </form>
  `;
  if (options.restoreBookingDraft) {
    const draft = loadDraft();
    const bookingPanel = quoteResult.querySelector(".booking-panel");
    applyBookingFormDraft(bookingPanel, draft.booking);
    if (draft.bookingOpen) openBookingPanel({ focus: false });
  }
  revealQuoteResult();
}

function renderPaymentRedirect(result) {
  const amountDue = result.payment?.amountDueNow || result.quote.totals.deposit25;
  const paymentLabel = result.paymentOption === "full" ? "Full online payment" : "25% booking deposit";
  const balanceText = result.paymentOption === "full"
    ? "No balance remains after this online payment."
    : `${pounds.format(result.quote.totals.balanceAfterDeposit)} balance is payable on completion.`;
  quoteResult.className = "quote-result success";
  quoteResult.innerHTML = `
    <div class="quote-result-head">
      <span>Secure Stripe checkout</span>
      <strong>${result.reference}</strong>
    </div>
    <p>Your booking details have been saved. Complete the secure Stripe payment to confirm your selected moving date and arrival time.</p>
    <dl class="quote-breakdown">
      <div>
        <dt>Payment type</dt>
        <dd>${paymentLabel}</dd>
      </div>
      <div>
        <dt>Due now</dt>
        <dd>${pounds.format(amountDue)}</dd>
      </div>
      <div>
        <dt>Total including VAT</dt>
        <dd>${pounds.format(result.quote.totals.totalIncVat)}</dd>
      </div>
      <div>
        <dt>After payment</dt>
        <dd>${balanceText}</dd>
      </div>
    </dl>
    <p class="quote-distance">Opening secure Stripe checkout now. If it does not open automatically, use the button below. If you return from Stripe, your booking draft is saved on this device.</p>
    <p class="calendar-actions"><a class="payment-link" href="${result.checkoutUrl}">Continue to secure Stripe checkout</a></p>
  `;
}

function renderBookingConfirmation(result) {
  quoteResult.className = "quote-result success";
  quoteResult.innerHTML = `
    <div class="quote-result-head">
      <span>Booking request received</span>
      <strong>${result.reference}</strong>
    </div>
    <p>${result.message}</p>
    <dl class="quote-breakdown">
      <div>
        <dt>Total including VAT</dt>
        <dd>${pounds.format(result.quote.totals.totalIncVat)}</dd>
      </div>
      <div>
        <dt>25% deposit</dt>
        <dd>${pounds.format(result.quote.totals.deposit25)}</dd>
      </div>
      <div>
        <dt>Balance after deposit</dt>
        <dd>${pounds.format(result.quote.totals.balanceAfterDeposit)}</dd>
      </div>
    </dl>
    <p class="quote-distance">The office can now see this request in the admin dashboard.</p>
  `;
}

if (quoteForm && quoteResult) {
  quoteResult.hidden = true;
  const restoredMover = restoreQuoteFormDraft();

  const updateMoverOptions = () => {
    if (!vansSelect || !moversSelect) return;

    const vans = firstNumber(vansSelect.value) || 1;
    const minMovers = vans;
    const maxMovers = Math.min(15, vans * 3);
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
      moverCapacityNote.textContent = `${vans} Luton van${vans === 1 ? "" : "s"} allows ${minMovers}-${maxMovers} movers in total. These are the people arriving to load, carry and move your items.`;
    }
  };

  updateMoverOptions();
  restoreMoverValue(restoredMover);
  saveQuoteFormDraft();
  vansSelect?.addEventListener("change", () => {
    updateMoverOptions();
    saveQuoteFormDraft();
  });
  quoteForm.addEventListener("input", saveQuoteFormDraft);
  quoteForm.addEventListener("change", saveQuoteFormDraft);
  addStopButton?.addEventListener("click", () => {
    addAdditionalStop();
    saveQuoteFormDraft();
  });
  additionalStopList?.addEventListener("click", (event) => {
    if (event.target.matches(".remove-stop")) {
      event.target.closest(".additional-stop-row")?.remove();
      saveQuoteFormDraft();
    }
  });

  quoteForm.addEventListener("submit", async (event) => {
    event.preventDefault();

    const data = new FormData(quoteForm);
    const additionalStops = cleanList(data.getAll("additional-stop"));
    const payload = {
      moveType: data.get("move-type"),
      lutonVans: firstNumber(data.get("luton-vans")),
      movers: firstNumber(data.get("movers")),
      hours: firstNumber(data.get("estimated-hours")),
      packAndMove: data.get("pack-and-move") === "yes",
      pickup: data.get("pickup"),
      delivery: data.get("delivery"),
      additionalStops,
      pickupStairs: firstNumber(data.get("pickup-stairs")),
      deliveryStairs: firstNumber(data.get("delivery-stairs")),
      items: data.get("items"),
    };

    if (!payload.pickup || !payload.delivery) {
      showQuoteMessage("error", "Postcodes needed", "Enter both pickup and delivery postcodes so we can calculate the mileage.");
      return;
    }

    showQuoteMessage("loading", "Calculating quote", "Checking route distance, vans, movers, packing option, floors/stairs and VAT.");

    try {
      const response = await fetch("/api/quote", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const result = await response.json();

      if (!response.ok) {
        showQuoteMessage("error", "Quote needs attention", (result.errors || [result.error || "Please check the details."]).join(" "));
        return;
      }

      renderQuote(result, payload);
    } catch (error) {
      showQuoteMessage("error", "Quote service unavailable", "The calculator could not be reached. Please try again or contact the office.");
    }
  });

  quoteResult.addEventListener("click", (event) => {
    if (!event.target.matches(".show-booking-form")) return;

    openBookingPanel();
  });

  quoteResult.addEventListener("change", (event) => {
    const bookingPanel = event.target.closest(".booking-panel");

    if (!event.target.matches('input[name="move-date"]')) return;

    const hasDate = Boolean(event.target.value);

    setTimeFieldState(bookingPanel, hasDate, bookingPanel?.querySelector('input[name="move-time"]:checked')?.value || "");
    saveBookingFormDraft(bookingPanel);

    const timeField = bookingPanel?.querySelector("[data-time-field]");
    if (hasDate && timeField) {
      window.requestAnimationFrame(() => {
        timeField.scrollIntoView({ behavior: "smooth", block: "nearest" });
      });
    }
  });

  quoteResult.addEventListener("input", (event) => {
    const bookingPanel = event.target.closest(".booking-panel");
    if (bookingPanel) saveBookingFormDraft(bookingPanel);
  });

  quoteResult.addEventListener("change", (event) => {
    const bookingPanel = event.target.closest(".booking-panel");
    if (bookingPanel) saveBookingFormDraft(bookingPanel);
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
    saveBookingFormDraft(event.target);
    saveDraft({ bookingOpen: true });

    if (!lastQuotePayload || !lastQuote) {
      showQuoteMessage("error", "Quote needed", "Please calculate the quote again before booking.");
      return;
    }

    const data = new FormData(event.target);
    const payload = {
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

    showQuoteMessage("loading", "Sending booking request", "Saving your move details securely and preparing payment.");

    try {
      const response = await fetch("/api/bookings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const result = await response.json();
      if (!response.ok) {
        showQuoteMessage("error", "Booking needs attention", (result.errors || [result.error || "Please check the details."]).join(" "));
        return;
      }
      if (result.checkoutUrl) {
        renderPaymentRedirect(result);
        window.location.assign(result.checkoutUrl);
        return;
      }
      renderBookingConfirmation(result);
    } catch (error) {
      showQuoteMessage("error", "Booking service unavailable", "The booking request could not be saved. Please try again or contact the office.");
    }
  });

  const draft = loadDraft();
  if (draft.quote && draft.quotePayload) {
    renderQuote(draft.quote, draft.quotePayload, { restoreBookingDraft: true });
  }
}
