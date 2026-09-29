// Background Service Worker for LeadAI LinkedIn Connector

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === "GET_LINKEDIN_SESSION") {
    getLinkedInCookies()
      .then((data) => sendResponse({ success: true, data }))
      .catch((err) => sendResponse({ success: false, error: err.message }));
    return true; // Indicates async response
  }

  if (request.action === "SYNC_TO_LEADAI") {
    syncToLeadAI(request.apiBaseUrl, request.token)
      .then((res) => sendResponse({ success: true, result: res }))
      .catch((err) => sendResponse({ success: false, error: err.message }));
    return true;
  }
});

async function getLinkedInCookies() {
  const cookieNames = ["li_at", "JSESSIONID", "bcookie", "bscookie", "lidc"];
  const cookies = {};

  for (const name of cookieNames) {
    const cookie = await chrome.cookies.get({
      url: "https://www.linkedin.com",
      name: name,
    });
    if (cookie) {
      cookies[name] = cookie.value;
    }
  }

  if (!cookies.li_at) {
    throw new Error("No active LinkedIn session found. Please log in to linkedin.com first.");
  }

  return cookies;
}

async function syncToLeadAI(apiBaseUrl = "http://localhost:5050", authToken = null) {
  const cookies = await getLinkedInCookies();
  
  const headers = {
    "Content-Type": "application/json",
  };
  if (authToken) {
    headers["Authorization"] = `Bearer ${authToken}`;
  }

  const response = await fetch(`${apiBaseUrl}/api/leadai/linkedin/credentials`, {
    method: "POST",
    headers: headers,
    body: JSON.stringify({
      cookie_li_at: cookies.li_at,
    }),
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || `Server returned ${response.status}`);
  }

  return await response.json();
}
