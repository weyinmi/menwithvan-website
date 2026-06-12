const quoteForm = document.querySelector(".quote-panel");
const quoteResult = document.querySelector("#quote-result");
const vansSelect = quoteForm?.querySelector('select[name="luton-vans"]');
const moversSelect = quoteForm?.querySelector('select[name="movers"]');
const moverCapacityNote = quoteForm?.querySelector("#mover-capacity-note");
const vanDropdown = quoteForm?.querySelector("[data-van-dropdown]");
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

function preferredTimeOptions() {
  const slots = [];
  for (let hour = 8; hour <= 10; hour += 1) {
    slots.push(`${String(hour).padStart(2, "0")}:00`);
    if (hour < 10) slots.push(`${String(hour).padStart(2, "0")}:30`);
  }
  for (let hour = 13; hour <= 21; hour += 1) {
    slots.push(`${String(hour).padStart(2, "0")}:00`);
    if (hour < 21) slots.push(`${String(hour).padStart(2, "0")}:30`);
  }

  return slots
    .map((slot) => {
      const [hourText, minute] = slot.split(":");
      const hour = Number(hourText);
      const period = hour >= 12 ? "pm" : "am";
      const displayHour = hour > 12 ? hour - 12 : hour;
      return `<option value="${slot}">${displayHour}:${minute} ${period}</option>`;
    })
    .join("");
}

function showQuoteMessage(type, title, message) {
  quoteResult.hidden = false;
  quoteResult.className = `quote-result ${type}`;
  quoteResult.innerHTML = `
    <strong>${title}</strong>
    <p>${message}</p>
  `;
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

  quoteResult.hidden = false;
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
    <form class="booking-panel">
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
          <div class="field-label">Preferred moving date and arrival time</div>
          <div class="date-time-grid">
            <label>
              Moving date
              <input type="date" name="move-date" required>
            </label>
            <label>
              Arrival time
              <select name="move-time" required>
                <option value="">Select a time</option>
                ${preferredTimeOptions()}
              </select>
            </label>
          </div>
          <small>Available starts are 8:00am-10:00am, then 1:00pm-9:00pm in 30-minute intervals.</small>
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

  const syncVanDropdown = () => {
    if (!vanDropdown || !vansSelect) return;

    const value = vansSelect.value;
    const selectedOption = Array.from(vanDropdown.querySelectorAll("[data-value]")).find((option) => option.dataset.value === value);
    const button = vanDropdown.querySelector(".van-dropdown-button");
    const menu = vanDropdown.querySelector(".van-dropdown-menu");

    vanDropdown.querySelectorAll("[role='option']").forEach((option) => {
      option.setAttribute("aria-selected", String(option === selectedOption));
    });

    if (button && selectedOption) {
      button.innerHTML = selectedOption.innerHTML;
      button.setAttribute("aria-expanded", String(menu && !menu.hidden));
    }
  };

  const closeVanDropdown = () => {
    if (!vanDropdown) return;
    const button = vanDropdown.querySelector(".van-dropdown-button");
    const menu = vanDropdown.querySelector(".van-dropdown-menu");
    if (menu) menu.hidden = true;
    button?.setAttribute("aria-expanded", "false");
  };

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
      moverCapacityNote.textContent = `${vans} Luton van${vans === 1 ? "" : "s"} means ${vans} vehicle${vans === 1 ? "" : "s"} arriving. It allows ${minMovers}-${maxMovers} men. Maximum online booking is 5 vans and 15 men.`;
    }

    syncVanDropdown();
  };

  updateMoverOptions();
  vansSelect?.addEventListener("change", updateMoverOptions);
  vanDropdown?.addEventListener("click", (event) => {
    const button = event.target.closest(".van-dropdown-button");
    const option = event.target.closest("[data-value]");
    const menu = vanDropdown.querySelector(".van-dropdown-menu");

    if (button && menu) {
      menu.hidden = !menu.hidden;
      button.setAttribute("aria-expanded", String(!menu.hidden));
      return;
    }

    if (option && vansSelect) {
      vansSelect.value = option.dataset.value;
      vansSelect.dispatchEvent(new Event("change", { bubbles: true }));
      closeVanDropdown();
    }
  });
  document.addEventListener("click", (event) => {
    if (vanDropdown && !vanDropdown.contains(event.target)) closeVanDropdown();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeVanDropdown();
  });
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

    showQuoteMessage("loading", "Calculating quote", "Checking route distance, vans, movers, stairs and VAT.");

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
