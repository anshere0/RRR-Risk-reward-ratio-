/**
 * Delta Options Risk-Reward Engine - Background Service Worker
 * 
 * Proxies API requests from the content script to the local 127.0.0.1 engine,
 * bypassing HTTPS/HTTP Mixed Content restrictions in Brave & Chrome.
 */

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === 'FETCH_API') {
    // F9: Lock background bridge: sender must be our extension and url must start with http://127.0.0.1:8000/
    if (!sender || sender.id !== chrome.runtime.id) {
      sendResponse({ success: false, error: 'Unauthorized sender ID' });
      return false;
    }

    const { url, options } = message;
    if (!url || typeof url !== 'string' || !url.startsWith('http://127.0.0.1:8000/')) {
      sendResponse({ success: false, error: 'Access denied: URL must start with http://127.0.0.1:8000/' });
      return false;
    }

    fetch(url, options || {})
      .then(async (res) => {
        if (!res.ok) {
          const text = await res.text();
          throw new Error(`HTTP ${res.status}: ${text}`);
        }
        const data = await res.json();
        sendResponse({ success: true, data });
      })
      .catch((err) => {
        sendResponse({ success: false, error: err.message });
      });
    return true; // Asynchronous sendResponse
  }
});
