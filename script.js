const API = "/api";
const grid = document.getElementById("listingGrid");
const searchInput = document.getElementById("searchInput");
const sortSelect = document.getElementById("sortSelect");
const listingBackdrop = document.getElementById("listingBackdrop");
const listingForm = document.getElementById("listingForm");
const authBackdrop = document.getElementById("authBackdrop");
const authForm = document.getElementById("authForm");
let activeCategory = "All";
let activeType = "All";
let viewingOwnListings = false;
let editingId = null;
let authMode = "login";
let currentUser = null;
let csrfToken = null;
let pendingAction = null;
let currentThread = null;
let reportListingId = null;
let searchTimer;

function formatPrice(price) {
  return price === 0 ? "Free" : new Intl.NumberFormat("en-NG", {
    style: "currency", currency: "NGN", maximumFractionDigits: 0
  }).format(price);
}

function formatDate(value) {
  return new Intl.DateTimeFormat("en-NG", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

function toast(message, error = false) {
  const element = document.getElementById("toast");
  element.textContent = message;
  element.classList.toggle("error", error);
  element.classList.add("show");
  window.clearTimeout(toast.timer);
  toast.timer = window.setTimeout(() => element.classList.remove("show"), 3200);
}

async function request(path, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  const headers = { ...options.headers };
  if (options.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
  if (!["GET", "HEAD", "OPTIONS"].includes(method) && csrfToken) headers["X-Loop-CSRF"] = csrfToken;
  let response;
  try {
    response = await fetch(`${API}${path}`, { ...options, method, headers, credentials: "same-origin" });
  } catch (error) {
    throw new Error("Cannot reach Loop. Start the server with: python server.py");
  }
  let result;
  try {
    result = await response.json();
  } catch (error) {
    throw new Error("The server returned an unreadable response.");
  }
  if (!response.ok) {
    if (response.status === 401) {
      currentUser = null;
      csrfToken = null;
      updateProfileUI();
    }
    throw new Error(result.error || "The request could not be completed.");
  }
  return result;
}

function makeElement(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function openAuth(callback = null) {
  pendingAction = callback;
  authBackdrop.hidden = false;
  authForm.elements.email.focus();
}

function requireAuth(callback) {
  if (currentUser) callback();
  else openAuth(callback);
}

function updateProfileUI() {
  const button = document.getElementById("profileButton");
  const hint = document.getElementById("profileHint");
  if (currentUser) {
    button.textContent = `${currentUser.display_name} · Sign out`;
    button.setAttribute("aria-label", `Sign out ${currentUser.display_name}`);
    hint.textContent = `Signed in as ${currentUser.display_name} (${currentUser.email}).`;
  } else {
    button.textContent = "Sign in";
    button.setAttribute("aria-label", "Sign in or create an account");
    hint.textContent = "Sign in to post, message sellers, or place a demo order.";
  }
  document.getElementById("moderationNav").hidden = !currentUser?.is_admin;
}

function setAuthMode(mode) {
  authMode = mode;
  const registering = mode === "register";
  const verifying = mode === "verify";
  document.getElementById("authTitle").textContent = verifying
    ? "Verify your email"
    : registering ? "Join Loop" : "Welcome back";
  document.getElementById("authIntro").textContent = verifying
    ? "Enter the 6-digit code sent to your PAU email."
    : registering
      ? "Create an account to trade with your campus community."
      : "Sign in to manage listings and message the community.";
  document.getElementById("nameField").hidden = !registering;
  authForm.elements.display_name.required = registering;
  document.getElementById("passwordField").hidden = verifying;
  authForm.elements.password.required = !verifying;
  authForm.elements.password.autocomplete = registering ? "new-password" : "current-password";
  document.getElementById("otpField").hidden = !verifying;
  authForm.elements.otp.required = verifying;
  authForm.elements.email.readOnly = verifying;
  document.getElementById("authSubmit").textContent = verifying
    ? "Verify email"
    : registering ? "Create account" : "Sign in";
  document.getElementById("authToggle").textContent = verifying
    ? "Use a different email"
    : registering
      ? "Already have an account? Sign in"
      : "New to Loop? Create an account";
}

async function refreshSession() {
  try {
    const result = await request("/auth/me");
    currentUser = result.user;
    csrfToken = result.csrf_token;
  } catch (error) {
    currentUser = null;
    csrfToken = null;
    console.error(error);
  }
  updateProfileUI();
}

function makeListingCard(item) {
  const card = makeElement("article", `listing-card${item.status === "sold" ? " is-sold" : ""}`);
  const image = makeElement("div", "listing-image");
  image.setAttribute("aria-hidden", "true");
  if (item.image_url) {
    const photo = makeElement("img", "listing-photo");
    photo.src = item.image_url;
    photo.alt = "";
    image.append(photo);
  } else {
    image.append(document.createTextNode(item.category === "Books" ? "▤" : item.category === "Tech" ? "◖" : "◇"));
  }
  image.append(makeElement("span", "condition", item.condition));
  if (item.status !== "active") {
    image.append(makeElement("span", "sold-badge", item.status === "sold" ? "SOLD" : "HIDDEN"));
  }

  const body = makeElement("div", "listing-body");
  const meta = makeElement("div", "listing-meta");
  const listingValue = item.listing_type === "sale"
    ? formatPrice(item.price)
    : item.listing_type === "swap" ? "Swap" : "Requested";
  meta.append(makeElement("h3", "", item.title), makeElement("span", "listing-value", listingValue));
  body.append(meta);
  if (item.description) body.append(makeElement("p", "listing-description", item.description));
  if (item.listing_type === "swap") {
    body.append(makeElement("p", "trade-details", `Wanted in exchange: ${item.swap_for}`));
  } else if (item.listing_type === "request") {
    body.append(makeElement("p", "trade-details", "Looking for this item"));
  }
  body.append(
    makeElement("p", "listing-location", `● ${item.location}`),
    makeElement("p", "seller-name", `Listed by ${item.seller_name}`)
  );

  const actions = makeElement("div", "listing-actions");
  if (!item.is_owner && item.owner_id && item.status === "active") {
    const messageLabel = item.listing_type === "request" ? "Respond to request"
      : item.listing_type === "swap" ? "Propose a swap" : "Message seller";
    const message = makeElement("button", "small-button secondary-button", messageLabel);
    message.type = "button";
    message.addEventListener("click", () => requireAuth(() =>
      openConversation(item.id, item.owner_id, item.title, item.seller_name)));
    actions.append(message);
    if (item.listing_type === "sale") {
      const buy = makeElement("button", "small-button contact-button", item.price ? "Demo checkout" : "Reserve for free");
      buy.type = "button";
      buy.addEventListener("click", () => requireAuth(() => placeDemoOrder(item)));
      actions.append(buy);
    }
    const report = makeElement("button", "small-button secondary-button", "Report");
    report.type = "button";
    report.addEventListener("click", () => requireAuth(() => openReportForm(item)));
    actions.append(report);
  } else if (!item.owner_id) {
    actions.append(makeElement("span", "small-button disabled-button", "Sample listing"));
  }
  if (item.is_owner && item.status === "active") {
    const edit = makeElement("button", "small-button secondary-button", "Edit");
    edit.type = "button";
    edit.addEventListener("click", () => openListingForm(item));
    const remove = makeElement("button", "small-button delete-button", "Delete");
    remove.type = "button";
    remove.addEventListener("click", () => deleteListing(item));
    actions.append(edit, remove);
  }
  body.append(actions);
  card.append(image, body);
  return card;
}

async function renderListings() {
  grid.replaceChildren(makeElement("p", "loading-message", "Loading listings…"));
  const params = new URLSearchParams({
    q: searchInput.value.trim(),
    category: activeCategory,
    type: activeType,
    sort: sortSelect.value
  });
  if (viewingOwnListings) params.set("mine", "1");
  try {
    const result = await request(`/listings?${params}`);
    if (!result.listings.length) {
      grid.replaceChildren(makeElement("p", "empty-message",
        viewingOwnListings ? "You have not posted any listings yet." : "No listings match your search."));
      return;
    }
    grid.replaceChildren(...result.listings.map(makeListingCard));
  } catch (error) {
    console.error(error);
    grid.replaceChildren(makeElement("p", "empty-message", error.message));
  }
}

async function updateStats() {
  try {
    const result = await request("/stats");
    document.getElementById("impactCount").textContent = result.active_listings;
  } catch (error) {
    console.error(error);
    document.getElementById("impactCount").textContent = "—";
  }
}

function openListingForm(item = null) {
  editingId = item ? item.id : null;
  listingForm.reset();
  listingForm.elements.listing_type.value = "sale";
  listingForm.elements.price.value = "0";
  listingForm.elements.condition.value = "Good";
  document.getElementById("formEyebrow").textContent = item ? "EDIT LISTING" : "NEW LISTING";
  document.getElementById("listingModalTitle").textContent = item ? "Update your item" : "Share an item";
  listingForm.querySelector('[type="submit"]').textContent = item ? "Save changes" : "Publish listing";
  const preview = document.getElementById("imagePreview");
  preview.hidden = !item?.image_url;
  if (item) {
    for (const field of ["title", "description", "category", "condition", "listing_type", "swap_for", "price", "location"]) {
      listingForm.elements[field].value = item[field];
    }
    if (item.image_url) preview.src = item.image_url;
  }
  updateListingTypeFields();
  listingBackdrop.hidden = false;
  listingForm.elements.title.focus();
}

function updateListingTypeFields() {
  const type = listingForm.elements.listing_type.value;
  const isSale = type === "sale";
  const isSwap = type === "swap";
  const priceInput = listingForm.elements.price;
  const swapInput = listingForm.elements.swap_for;
  const anyCondition = Array.from(listingForm.elements.condition.options)
    .find(option => option.value === "Any");
  anyCondition.disabled = type !== "request";
  document.getElementById("priceField").hidden = !isSale;
  priceInput.disabled = !isSale;
  priceInput.required = isSale;
  document.getElementById("swapField").hidden = !isSwap;
  swapInput.disabled = !isSwap;
  swapInput.required = isSwap;
  document.getElementById("listingTypeHint").textContent = isSale
    ? "Set a price in naira, or enter 0 to give it away."
    : isSwap
      ? "List an item you have and say what you would like in exchange."
      : "Describe the item you need; other users can message you to offer one.";
  if (type === "request") listingForm.elements.condition.value = "Any";
  else if (listingForm.elements.condition.value === "Any") listingForm.elements.condition.value = "Good";
}

function closeModal(id) {
  document.getElementById(id).hidden = true;
  if (id === "listingBackdrop") {
    editingId = null;
    document.getElementById("imagePreview").hidden = true;
  }
}

function readImage(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(new Error("Could not read the selected image."));
    reader.readAsDataURL(file);
  });
}

async function deleteListing(item) {
  if (!window.confirm(`Delete "${item.title}"? This cannot be undone.`)) return;
  try {
    await request(`/listings/${item.id}`, { method: "DELETE" });
    toast("Listing deleted.");
    await renderListings();
    await updateStats();
  } catch (error) {
    toast(error.message, true);
  }
}

async function placeDemoOrder(item) {
  if (!window.confirm(`Continue with demo checkout for ${formatPrice(item.price)}?\n\nThis records a test order only. No money will be charged.`)) return;
  try {
    const result = await request("/orders", {
      method: "POST",
      body: JSON.stringify({ listing_id: item.id })
    });
    toast(result.notice);
    await renderListings();
    await updateStats();
  } catch (error) {
    toast(error.message, true);
  }
}

async function renderInbox() {
  const container = document.getElementById("conversationList");
  container.replaceChildren(makeElement("p", "loading-message", "Loading inbox…"));
  try {
    const result = await request("/conversations");
    const unread = result.conversations.reduce((sum, item) => sum + item.unread, 0);
    const badge = document.getElementById("unreadCount");
    badge.hidden = unread === 0;
    badge.textContent = unread ? String(unread) : "";
    if (!result.conversations.length) {
      container.replaceChildren(makeElement("p", "empty-message", "No messages yet. Start a conversation from a listing."));
      return;
    }
    container.replaceChildren(...result.conversations.map(item => {
      const button = makeElement("button", "conversation-item");
      const details = makeElement("span", "conversation-details");
      details.append(makeElement("strong", "", item.other_name), makeElement("span", "", `${item.listing_title} · ${item.last_message}`));
      button.append(details, makeElement("time", "", formatDate(item.updated_at)));
      if (item.unread) button.append(makeElement("span", "unread-pill", String(item.unread)));
      button.addEventListener("click", () => openConversation(item.listing_id, item.other_id, item.listing_title, item.other_name));
      return button;
    }));
  } catch (error) {
    container.replaceChildren(makeElement("p", "empty-message", error.message));
  }
}

async function updateUnreadBadge() {
  const badge = document.getElementById("unreadCount");
  if (!currentUser) {
    badge.hidden = true;
    return;
  }
  try {
    const result = await request("/conversations");
    const unread = result.conversations.reduce((sum, item) => sum + item.unread, 0);
    badge.hidden = unread === 0;
    badge.textContent = unread ? String(unread) : "";
  } catch (error) {
    console.error(error);
  }
}

async function openConversation(listingId, otherId, listingTitle, otherName) {
  currentThread = { listingId, otherId };
  document.getElementById("inboxTitle").textContent = "Inbox";
  document.getElementById("conversationView").hidden = true;
  document.getElementById("threadView").hidden = false;
  document.getElementById("threadHeading").textContent = `${otherName} · ${listingTitle}`;
  document.getElementById("messageList").replaceChildren(makeElement("p", "loading-message", "Loading messages…"));
  document.getElementById("inboxBackdrop").hidden = false;
  await renderThread();
}

async function renderThread() {
  if (!currentThread) return;
  try {
    const result = await request(`/conversations/${currentThread.listingId}/${currentThread.otherId}/messages`);
    const container = document.getElementById("messageList");
    if (!result.messages.length) {
      container.replaceChildren(makeElement("p", "empty-message", "Start the conversation with a message."));
      return;
    }
    container.replaceChildren(...result.messages.map(message => {
      const bubble = makeElement("article", `message-bubble${message.sender_id === currentUser.id ? " mine" : ""}`);
      bubble.append(makeElement("strong", "", message.sender_name), makeElement("p", "", message.body),
        makeElement("time", "", formatDate(message.created_at)));
      return bubble;
    }));
    container.scrollTop = container.scrollHeight;
  } catch (error) {
    toast(error.message, true);
  }
}

async function renderOrders() {
  const container = document.getElementById("ordersList");
  container.replaceChildren(makeElement("p", "loading-message", "Loading orders…"));
  try {
    const result = await request("/orders");
    if (!result.orders.length) {
      container.replaceChildren(makeElement("p", "empty-message", "No demo orders yet."));
      return;
    }
    container.replaceChildren(...result.orders.map(order => {
      const row = makeElement("article", "order-item");
      row.append(makeElement("strong", "", order.title),
        makeElement("span", "", `${formatPrice(order.amount)} · ${order.buyer_id === currentUser.id ? `Bought from ${order.seller_name}` : `Sold to ${order.buyer_name}`}`),
        makeElement("small", "", `SIMULATED · ${formatDate(order.created_at)}`));
      return row;
    }));
  } catch (error) {
    container.replaceChildren(makeElement("p", "empty-message", error.message));
  }
}

function openReportForm(item) {
  reportListingId = item.id;
  document.getElementById("reportListingTitle").textContent = `Listing: ${item.title}`;
  document.getElementById("reportForm").reset();
  document.getElementById("reportBackdrop").hidden = false;
}

async function renderReports() {
  const container = document.getElementById("reportsList");
  container.replaceChildren(makeElement("p", "loading-message", "Loading reports…"));
  try {
    const result = await request("/admin/reports");
    if (!result.reports.length) {
      container.replaceChildren(makeElement("p", "empty-message", "No reports are waiting for review."));
      return;
    }
    container.replaceChildren(...result.reports.map(report => {
      const card = makeElement("article", "report-item");
      card.append(
        makeElement("strong", "", report.title),
        makeElement("span", "", `Reason: ${report.reason} · Reported by ${report.reporter_name}`),
        makeElement("p", "", report.details || "No additional details."),
        makeElement("small", "", `Listing status: ${report.listing_status} · ${formatDate(report.created_at)}`)
      );
      const actions = makeElement("div", "listing-actions");
      for (const [action, label, className] of [
        ["hide", "Hide listing", "delete-button"],
        ["dismiss", "Dismiss report", "secondary-button"]
      ]) {
        const button = makeElement("button", `small-button ${className}`, label);
        button.type = "button";
        button.addEventListener("click", async () => {
          button.disabled = true;
          try {
            await request(`/admin/reports/${report.id}`, {
              method: "POST",
              body: JSON.stringify({ action })
            });
            toast(action === "hide" ? "Listing hidden from the marketplace." : "Report dismissed.");
            await renderReports();
            await renderListings();
            await updateStats();
          } catch (error) {
            button.disabled = false;
            toast(error.message, true);
          }
        });
        actions.append(button);
      }
      card.append(actions);
      return card;
    }));
  } catch (error) {
    container.replaceChildren(makeElement("p", "empty-message", error.message));
  }
}

document.querySelectorAll(".category-filters .filter").forEach(button => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".filter").forEach(item => item.classList.remove("active"));
    button.classList.add("active");
    activeCategory = button.dataset.category;
    renderListings();
  });
  document.querySelectorAll(".type-filters .filter").forEach(button => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".type-filters .filter").forEach(item => item.classList.remove("active"));
      button.classList.add("active");
      activeType = button.dataset.type;
      renderListings();
    });
  });
  document.getElementById("listingTypeSelect").addEventListener("change", updateListingTypeFields);
});
searchInput.addEventListener("input", () => {
  window.clearTimeout(searchTimer);
  searchTimer = window.setTimeout(renderListings, 180);
});
sortSelect.addEventListener("change", renderListings);

document.getElementById("discoverNav").addEventListener("click", () => {
  viewingOwnListings = false;
  document.getElementById("browse-heading").textContent = "Available near you";
  document.getElementById("discoverNav").classList.add("active");
  document.getElementById("myListingsNav").classList.remove("active");
  renderListings();
});
document.getElementById("myListingsNav").addEventListener("click", () => requireAuth(() => {
  viewingOwnListings = true;
  document.getElementById("browse-heading").textContent = "My listings";
  document.getElementById("discoverNav").classList.remove("active");
  document.getElementById("myListingsNav").classList.add("active");
  renderListings();
}));
document.getElementById("inboxNav").addEventListener("click", () => requireAuth(async () => {
  currentThread = null;
  document.getElementById("threadView").hidden = true;
  document.getElementById("conversationView").hidden = false;
  document.getElementById("inboxBackdrop").hidden = false;
  await renderInbox();
}));
document.getElementById("ordersNav").addEventListener("click", () => requireAuth(async () => {
  document.getElementById("ordersBackdrop").hidden = false;
  await renderOrders();
}));
document.getElementById("moderationNav").addEventListener("click", () => requireAuth(async () => {
  document.getElementById("moderationBackdrop").hidden = false;
  await renderReports();
}));
document.getElementById("newListingButton").addEventListener("click", () =>
  requireAuth(() => openListingForm()));
document.getElementById("profileButton").addEventListener("click", async () => {
  if (!currentUser) {
    setAuthMode("login");
    openAuth();
    return;
  }
  if (!window.confirm(`Sign out ${currentUser.display_name}?`)) return;
  try {
    await request("/auth/logout", { method: "POST", body: "{}" });
    currentUser = null;
    csrfToken = null;
    viewingOwnListings = false;
    updateProfileUI();
    await renderListings();
    toast("Signed out.");
  } catch (error) {
    toast(error.message, true);
  }
});

document.getElementById("authToggle").addEventListener("click", () => {
  if (authMode === "verify") {
    authForm.elements.otp.value = "";
    setAuthMode("login");
  } else {
    setAuthMode(authMode === "login" ? "register" : "login");
  }
});
authForm.addEventListener("submit", async event => {
  event.preventDefault();
  const button = document.getElementById("authSubmit");
  button.disabled = true;
  const payload = Object.fromEntries(new FormData(authForm));
  try {
    const verifying = authMode === "verify";
    const result = await request(verifying ? "/auth/verify-otp" : `/auth/${authMode === "register" ? "register" : "login"}`, {
      method: "POST",
      body: JSON.stringify(verifying
        ? { email: payload.email, token: payload.otp }
        : payload)
    });
    if (result.verification_required) {
      authForm.elements.password.value = "";
      authForm.elements.otp.value = "";
      setAuthMode("verify");
      document.getElementById("authIntro").textContent = result.message;
      authForm.elements.otp.focus();
      toast("Check your PAU email for the 6-digit code.");
      return;
    }
    currentUser = result.user;
    csrfToken = result.csrf_token;
    updateProfileUI();
    authBackdrop.hidden = true;
    authForm.reset();
    const completedMode = authMode;
    setAuthMode("login");
    toast(completedMode === "register"
      ? "Your account is ready."
      : completedMode === "verify" ? "Your email is verified." : "Welcome back.");
    const action = pendingAction;
    pendingAction = null;
    if (action) await action();
    await renderListings();
    await updateUnreadBadge();
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
});

listingForm.addEventListener("change", async event => {
  if (event.target.name !== "image") return;
  const file = event.target.files[0];
  const preview = document.getElementById("imagePreview");
  if (!file) {
    preview.hidden = true;
    return;
  }
  if (!["image/jpeg", "image/png", "image/webp"].includes(file.type) || file.size > 3 * 1024 * 1024) {
    event.target.value = "";
    toast("Choose a JPEG, PNG, or WebP image up to 3 MB.", true);
    preview.hidden = true;
    return;
  }
  preview.src = URL.createObjectURL(file);
  preview.hidden = false;
});

listingForm.addEventListener("submit", async event => {
  event.preventDefault();
  const submitButton = listingForm.querySelector('[type="submit"]');
  submitButton.disabled = true;
  const values = Object.fromEntries(new FormData(listingForm));
  values.price = values.listing_type === "sale" ? Number(values.price) : 0;
  const wasEditing = Boolean(editingId);
  const imageFile = listingForm.elements.image.files[0];
  try {
    if (imageFile) values.image = await readImage(imageFile);
    else delete values.image;
    const path = editingId ? `/listings/${editingId}` : "/listings";
    await request(path, {
      method: editingId ? "PUT" : "POST",
      body: JSON.stringify(values)
    });
    closeModal("listingBackdrop");
    toast(wasEditing ? "Your listing has been updated." : "Your listing is live.");
    viewingOwnListings = true;
    document.getElementById("browse-heading").textContent = "My listings";
    document.getElementById("discoverNav").classList.remove("active");
    document.getElementById("myListingsNav").classList.add("active");
    await renderListings();
    await updateStats();
  } catch (error) {
    toast(error.message, true);
  } finally {
    submitButton.disabled = false;
  }
});

document.getElementById("reportForm").addEventListener("submit", async event => {
  event.preventDefault();
  const submitButton = event.currentTarget.querySelector('[type="submit"]');
  submitButton.disabled = true;
  try {
    const values = Object.fromEntries(new FormData(event.currentTarget));
    await request("/reports", {
      method: "POST",
      body: JSON.stringify({ ...values, listing_id: reportListingId })
    });
    closeModal("reportBackdrop");
    toast("Report sent to the moderation team.");
  } catch (error) {
    toast(error.message, true);
  } finally {
    submitButton.disabled = false;
  }
});

document.getElementById("messageForm").addEventListener("submit", async event => {
  event.preventDefault();
  if (!currentThread) return;
  const form = event.currentTarget;
  const button = form.querySelector("button");
  button.disabled = true;
  try {
    await request("/messages", {
      method: "POST",
      body: JSON.stringify({
        listing_id: currentThread.listingId,
        recipient_id: currentThread.otherId,
        body: form.elements.body.value
      })
    });
    form.reset();
    await renderThread();
    await renderInbox();
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
});

document.getElementById("backToInbox").addEventListener("click", async () => {
  currentThread = null;
  document.getElementById("threadView").hidden = true;
  document.getElementById("conversationView").hidden = false;
  await renderInbox();
});
document.querySelectorAll("[data-close]").forEach(button =>
  button.addEventListener("click", () => closeModal(button.dataset.close)));
document.querySelectorAll(".modal-backdrop").forEach(backdrop =>
  backdrop.addEventListener("click", event => {
    if (event.target === backdrop) closeModal(backdrop.id);
  }));
document.getElementById("authToggle").addEventListener("keydown", event => {
  if (event.key === "Escape") closeModal("authBackdrop");
});
document.addEventListener("keydown", event => {
  if (event.key === "Escape") {
    document.querySelectorAll(".modal-backdrop:not([hidden])").forEach(modal => closeModal(modal.id));
  }
});

setAuthMode("login");
refreshSession().then(() => {
  renderListings();
  updateStats();
  updateUnreadBadge();
});
window.setInterval(updateUnreadBadge, 25000);
