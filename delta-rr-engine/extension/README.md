# Delta Exchange Options Risk-Reward Sidelay (Chrome Extension)

A floating, resizable, draggable 24/7 in-page overlay designed specifically for **Delta Exchange** (`india.delta.exchange` and `delta.exchange`).

It reads live BTC index chart structure (`.DEXBTUSD`), calculates accurate option prices at your chart target and stop loss, and outputs the exact **Risk:Reward Ratio** and dollar risk/reward for any option you pick from the 24/7 Option Chain.

---

## 🚀 How to Install & Run

### Step 1: Start the Local Risk-Reward Engine Backend
Make sure your local engine is running:
```cmd
cd delta-rr-engine
start.bat
```
*(Or run `.\.venv\Scripts\python.exe -m rr_engine`)*
The backend will run on `http://127.0.0.1:8000`.

---

### Step 2: Load the Sidelay Extension in Your Browser (Chrome, Brave, Edge)
1. Open your browser and navigate to:
   - Chrome: `chrome://extensions/`
   - Brave: `brave://extensions/`
   - Edge: `edge://extensions/`
2. Enable **Developer mode** (toggle switch in the top right corner).
3. Click the **Load unpacked** button (top left).
4. Select the extension directory:
   ```
   C:\Users\ansha\Downloads\RRR\delta-rr-engine\extension
   ```
5. The extension **Delta BTC Options Risk-Reward Sidelay** is now active!

---

### Step 3: Open Delta Exchange
1. Go to [https://india.delta.exchange](https://india.delta.exchange) (or [https://delta.exchange](https://delta.exchange)).
2. The floating **⚡ DELTA RR** window will appear directly on top of your Delta Exchange screen!

---

## 🎯 Features & Controls

| Feature | How It Works |
| :--- | :--- |
| **Move / Reposition** | Click and drag the top header bar anywhere on your screen. |
| **Resize / Reshape** | Click and drag the bottom-right corner to make the window wider, narrower, taller, or shorter. |
| **Minimize to Pill** | Click the `_` button in the header. It collapses into a small floating badge `[ ⚡ BTC $63,450 \| RR 2.45 ]`. Click it again anytime to expand. |
| **Full 24/7 Option Chain** | Browse all Calls and Puts across all strike prices for today, tomorrow, or future expiries with live Bids, Asks, Marks, and IVs. |
| **1-Click Trade Selection** | Click on any Call or Put cell in the table. The engine immediately selects that contract and calculates the Risk:Reward! |
| **Auto-Sync with Delta URL** | When you click an option contract inside Delta Exchange's own UI, the overlay automatically detects it and analyzes that contract! |
| **Pure Chart RR Math** | Support and resistance pivots on the 5m spot index chart map directly to option target and stop levels without artificial decay crushing your numbers. |
| **Trader in Control (No "NO TRADE" Blocks)** | Sub-optimal conditions are displayed as informative advisory notes (e.g. counter-trend, wide spread) rather than locking you out with a "NO TRADE" screen. |
