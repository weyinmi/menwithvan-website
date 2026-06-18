const draftKey = "menwithvan.bookingDraft.v1";
const reference = new URLSearchParams(window.location.search).get("ref");
const cancelHeading = document.querySelector("#cancel-heading");
const cancelReference = document.querySelector("#cancel-reference");
const draftStatus = document.querySelector("#draft-status");
const startFresh = document.querySelector("#start-fresh");

if (reference) {
  cancelHeading.textContent = reference;
  cancelReference.textContent = `Booking ${reference} is still marked as payment pending.`;
}

try {
  const draft = JSON.parse(sessionStorage.getItem(draftKey) || localStorage.getItem(draftKey) || "{}");
  draftStatus.textContent = draft.quote
    ? "Saved. You can continue from where you stopped."
    : "No saved draft was found on this browser.";
} catch (error) {
  draftStatus.textContent = "No saved draft was found on this browser.";
}

startFresh.addEventListener("click", () => {
  try {
    localStorage.removeItem(draftKey);
    sessionStorage.removeItem(draftKey);
  } catch (storageError) {
    // The fresh quote link still works even if browser storage is blocked.
  }
});
