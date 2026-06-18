const pounds = new Intl.NumberFormat("en-GB", { style: "currency", currency: "GBP" });
const draftKey = "menwithvan.bookingDraft.v1";
const statusBox = document.querySelector("#payment-status");
const sessionId = new URLSearchParams(window.location.search).get("session_id");

function clearNode(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

function textElement(tag, text, className = "") {
  const element = document.createElement(tag);
  if (className) element.className = className;
  element.textContent = text;
  return element;
}

function safeCalendarUrl(value, expectedHost = "") {
  try {
    const url = new URL(String(value || ""), window.location.origin);
    if (url.origin === window.location.origin && url.pathname.endsWith("/calendar.ics")) return url.href;
    if (expectedHost && url.protocol === "https:" && url.hostname === expectedHost) return url.href;
  } catch (error) {
    // Invalid links are omitted from the page.
  }
  return "";
}

function setSimpleStatus(className, title, message) {
  statusBox.className = `quote-result ${className}`;
  clearNode(statusBox);
  statusBox.append(textElement("strong", title), textElement("p", message));
}

function appendBreakdown(label, value, list) {
  const item = document.createElement("div");
  item.append(textElement("dt", label), textElement("dd", value));
  list.append(item);
}

async function showPaymentStatus() {
  if (!sessionId) {
    setSimpleStatus("error", "Payment reference missing", "If Stripe has taken payment, please contact the office with your Stripe receipt email.");
    return;
  }

  try {
    const response = await fetch(`/api/payments/session?session_id=${encodeURIComponent(sessionId)}`);
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Payment session not found.");

    statusBox.className = "quote-result success";
    clearNode(statusBox);

    const heading = document.createElement("div");
    heading.className = "quote-result-head";
    heading.append(textElement("span", "Booking reference"), textElement("strong", result.reference || "Confirmed booking"));
    statusBox.append(heading);

    const paidText = result.paidAt
      ? "Stripe has confirmed the payment."
      : "Stripe is still confirming the payment; this normally updates shortly.";
    statusBox.append(textElement("p", paidText));

    const breakdown = document.createElement("dl");
    breakdown.className = "quote-breakdown";
    appendBreakdown("Payment status", String(result.paymentStatus || "").replaceAll("_", " "), breakdown);
    appendBreakdown("Total including VAT", pounds.format(Number(result.totalIncVat || 0)), breakdown);
    appendBreakdown("Balance remaining", pounds.format(Number(result.balanceAmount || 0)), breakdown);
    statusBox.append(breakdown);

    const icsUrl = safeCalendarUrl(result.calendar?.icsUrl);
    const googleUrl = safeCalendarUrl(result.calendar?.googleUrl, "calendar.google.com");
    if (icsUrl || googleUrl) {
      statusBox.append(textElement("p", "Add this confirmed move to your calendar.", "quote-distance"));
      const actions = document.createElement("p");
      actions.className = "calendar-actions";
      if (icsUrl) {
        const icsLink = textElement("a", "Apple / Outlook / Android", "payment-link");
        icsLink.href = icsUrl;
        actions.append(icsLink);
      }
      if (googleUrl) {
        const googleLink = textElement("a", "Google Calendar", "payment-link secondary-link");
        googleLink.href = googleUrl;
        googleLink.target = "_blank";
        googleLink.rel = "noopener";
        actions.append(googleLink);
      }
      statusBox.append(actions);
    }

    try {
      localStorage.removeItem(draftKey);
      sessionStorage.removeItem(draftKey);
    } catch (storageError) {
      // Payment is already confirmed; draft cleanup is only a convenience.
    }
  } catch (error) {
    setSimpleStatus("error", "Payment saved", "We could not load the booking reference here, but Stripe will still email a payment receipt if payment completed.");
  }
}

showPaymentStatus();
