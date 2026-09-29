// Popup script for LeadAI Extension

document.addEventListener("DOMContentLoaded", async () => {
  const statusDot = document.getElementById("status-dot");
  const statusText = document.getElementById("status-text");
  const syncBtn = document.getElementById("sync-btn");
  const msg = document.getElementById("msg");

  let activeSession = null;

  // Query background for LinkedIn session
  chrome.runtime.sendMessage({ action: "GET_LINKEDIN_SESSION" }, (response) => {
    if (response && response.success && response.data.li_at) {
      activeSession = response.data;
      statusDot.className = "status-dot active";
      statusText.innerText = "Connected & Active";
      statusText.style.color = "#34d399";
      syncBtn.disabled = false;
    } else {
      statusDot.className = "status-dot warning";
      statusText.innerText = "Please log into LinkedIn";
      statusText.style.color = "#f59e0b";
      msg.className = "msg error";
      msg.innerText = "Open linkedin.com and sign in first.";
    }
  });

  syncBtn.addEventListener("click", async () => {
    if (!activeSession) return;
    
    syncBtn.disabled = true;
    syncBtn.innerText = "Syncing with LeadAI...";
    msg.innerText = "";

    // Query active tab to see if LeadAI is open or post directly to backend
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      const activeTab = tabs[0];
      if (activeTab && (activeTab.url.includes("localhost") || activeTab.url.includes("127.0.0.1") || activeTab.url.includes("leadai"))) {
        // Post message to the web page directly
        chrome.tabs.sendMessage(activeTab.id, {
          action: "SET_LINKEDIN_SESSION",
          data: activeSession,
        }, () => {
          // Also invoke background sync
          syncToLocalAPI();
        });
      } else {
        syncToLocalAPI();
      }
    });

    function syncToLocalAPI() {
      chrome.runtime.sendMessage(
        {
          action: "SYNC_TO_LEADAI",
          apiBaseUrl: "http://localhost:5050",
        },
        (res) => {
          syncBtn.disabled = false;
          syncBtn.innerText = "⚡ 1-Click Sync to LeadAI";
          if (res && res.success) {
            msg.className = "msg success";
            msg.innerText = "✅ Successfully linked to LeadAI Dashboard!";
          } else {
            msg.className = "msg error";
            msg.innerText = res ? res.error : "Sync failed. Ensure LeadAI is running.";
          }
        }
      );
    }
  });
});
