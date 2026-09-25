// Content Script for LeadAI Web App Bridge

console.log("[LeadAI Extension] Bridge loaded on page:", window.location.href);

// Announce extension presence to the web page
function announceReady() {
  window.postMessage(
    {
      source: "leadai-extension",
      type: "LEADAI_EXTENSION_READY",
      version: "1.0.0",
      installed: true,
    },
    "*"
  );
}

// Listen for messages from the LeadAI Angular Web App
window.addEventListener("message", (event) => {
  // Only accept messages from same origin/trusted window
  if (event.source !== window || !event.data || !event.data.type) {
    return;
  }

  if (event.data.type === "LEADAI_CHECK_EXTENSION") {
    announceReady();
    return;
  }

  if (event.data.type === "LEADAI_REQUEST_LINKEDIN_SESSION") {
    console.log("[LeadAI Extension] Requesting LinkedIn session from background service worker...");
    chrome.runtime.sendMessage({ action: "GET_LINKEDIN_SESSION" }, (response) => {
      if (chrome.runtime.lastError) {
        window.postMessage(
          {
            source: "leadai-extension",
            type: "LEADAI_LINKEDIN_SESSION_RESPONSE",
            success: false,
            error: chrome.runtime.lastError.message,
          },
          "*"
        );
        return;
      }

      window.postMessage(
        {
          source: "leadai-extension",
          type: "LEADAI_LINKEDIN_SESSION_RESPONSE",
          success: response ? response.success : false,
          data: response ? response.data : null,
          error: response ? response.error : "Unknown error",
        },
        "*"
      );
    });
  }
});

// Broadcast readiness
announceReady();
setTimeout(announceReady, 500);
setTimeout(announceReady, 1500);
