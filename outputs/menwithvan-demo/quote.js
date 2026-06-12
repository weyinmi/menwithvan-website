const quoteForm = document.querySelector(".quote-panel");
const quoteResult = document.querySelector("#quote-result");
const vansSelect = quoteForm?.querySelector('select[name="luton-vans"]');
const moversSelect = quoteForm?.querySelector('select[name="movers"]');
const moverCapacityNote = quoteForm?.querySelector("#mover-capacity-note");
const additionalStopList = quoteForm?.querySelector("#additional-stop-list");
const addStopButton = quoteForm?.querySelector("#add-stop");

const pounds = new Intl.NumberFormat("en-GB", {
  style: "currency",
  currency: "GBP",
});

let lastQuotePayload = null;
let lastQuote = null;

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

function renderQuote(quote, payload) {
  const totals = quote.totals;
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
        <dd>${pounds.format(quote.overtime.hourlyRateIncVat)} / hour</dd>
      </div>
    </dl>
    <p class="quote-distance">Route distance: ${quote.distance.miles} miles. Minimum booking: ${quote.rates.minimumHours} hours. Overtime after the booked time is ${pounds.format(quote.overtime.hourlyRateIncVat)} per extra hour or part-hour, payable to the driver on completion.</p>
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
          Access notes
          <textarea name="access-notes" rows="3" placeholder="Parking, lift booking, loading bay, concierge, timing restrictions, fragile items"></textarea>
        </label>
        <label class="full">
          Payment choice
          <select name="payment-option" required>
            <option value="deposit">Pay 25% deposit, balance on completion</option>
            <option value="full">Pay full amount online</option>
          </select>
          <small>Any overtime beyond the booked hours is charged at ${pounds.format(quote.overtime.hourlyRateIncVat)} per extra hour or part-hour and is payable on completion.</small>
        </label>
        <label class="terms-check full">
          <input type="checkbox" name="terms-accepted" required>
          I accept the <a href="terms-and-conditions.html" target="_blank" rel="noopener">terms and conditions</a>.
        </label>
      </div>
      <button type="submit">Continue to secure payment</button>
    </form>
  `;
  revealQuoteResult();
}

function renderPaymentRedirect(result) {
  const amountDue = result.payment?.amountDueNow || result.quote.totals.deposit25;
  quoteResult.className = "quote-result success";
  quoteResult.innerHTML = `
    <div class="quote-result-head">
      <span>Booking saved</span>
      <strong>${result.reference}</strong>
    </div>
    <p>${result.message}</p>
    <dl class="quote-breakdown">
      <div>
        <dt>Amount due now</dt>
        <dd>${pounds.format(amountDue)}</dd>
      </div>
      <div>
        <dt>Total including VAT</dt>
        <dd>${pounds.format(result.quote.totals.totalIncVat)}</dd>
      </div>
    </dl>
    <p class="quote-distance">Opening secure Stripe checkout now. If it does not open, use the payment button below.</p>
    <p><a class="payment-link" href="${result.checkoutUrl}">Open secure payment</a></p>
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
      moverCapacityNote.textContent = `${vans} Luton van${vans === 1 ? "" : "s"} allows ${minMovers}-${maxMovers} men in total. These are the movers arriving to load, carry and move your items.`;
    }
  };

  updateMoverOptions();
  vansSelect?.addEventListener("change", updateMoverOptions);
  addStopButton?.addEventListener("click", () => addAdditionalStop());
  additionalStopList?.addEventListener("click", (event) => {
    if (event.target.matches(".remove-stop")) {
      event.target.closest(".additional-stop-row")?.remove();
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

    showQuoteMessage("loading", "Calculating quote", "Checking route distance, vans, movers, packing option, stairs and VAT.");

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

    const bookingPanel = quoteResult.querySelector(".booking-panel");
    const actions = event.target.closest(".quote-actions");
    if (!bookingPanel) return;

    if (actions) actions.hidden = true;
    bookingPanel.hidden = false;

    window.requestAnimationFrame(() => {
      bookingPanel.scrollIntoView({ behavior: "smooth", block: "start" });
      bookingPanel.querySelector("input, select, textarea, button")?.focus({ preventScroll: true });
    });
  });

  quoteResult.addEventListener("change", (event) => {
    if (!event.target.matches('input[name="move-date"]')) return;

    const bookingPanel = event.target.closest(".booking-panel");
    const timeField = bookingPanel?.querySelector("[data-time-field]");
    const timeInputs = bookingPanel?.querySelectorAll('input[name="move-time"]') || [];
    const hasDate = Boolean(event.target.value);

    if (timeField) timeField.hidden = !hasDate;
    timeInputs.forEach((input) => {
      input.disabled = !hasDate;
      if (!hasDate) input.checked = false;
    });

    if (hasDate && timeField) {
      window.requestAnimationFrame(() => {
        timeField.scrollIntoView({ behavior: "smooth", block: "nearest" });
      });
    }
  });

  quoteResult.addEventListener("submit", async (event) => {
    if (!event.target.matches(".booking-panel")) return;
    event.preventDefault();

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
        accessNotes: data.get("access-notes"),
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
}
