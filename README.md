# 台灣生活查詢站

一個**全自動更新**的台灣生活資訊網站：發票對獎、今日油價、匯率換算、坪數／民國年／薪資換算。
架好之後由 **GitHub Actions 每 4 小時自動抓資料、自動重新發布**，你的電腦關機也照常運作；主機費用 0 元，靠 Google AdSense 廣告賺錢。

## 運作方式

```
GitHub Actions（每 4 小時）
  └─ scripts/build.py
       ├─ 抓 財政部發票 RSS、中油油價 API、匯率 API
       ├─ 存到 data/（失敗就沿用上次的資料，網站不會壞）
       └─ 產生 33+ 個靜態頁面 → dist/
  └─ 部署到 GitHub Pages（免費主機）
  └─ 檢查資料是否過舊 → 有問題 GitHub 會寄 email 給你
```

## 一、部署（約 5 分鐘，只要做一次）

1. 在這個資料夾開 PowerShell，執行：
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\deploy.ps1
   ```
2. 瀏覽器會打開 GitHub 登入頁面（沒有帳號可以直接免費註冊），把終端機顯示的 8 碼代碼貼上並授權。
3. 腳本會自動建立 repo、開啟 GitHub Pages、推送程式碼。完成後顯示網址，例如 `https://你的帳號.github.io/tw-tools/`。

> 想要網址更短：執行 `.\deploy.ps1 -RepoName 你的帳號.github.io`，網址就會是 `https://你的帳號.github.io/`。

## 二、賺錢設定（要你本人操作，因為需要身分與收款資料）

### 1. Google Search Console（讓 Google 搜尋得到你）
1. 到 <https://search.google.com/search-console> →「新增資源」→「網址前置字元」貼上你的網址。
2. 驗證方式選「HTML 標記」，複製 `content="..."` 裡的代碼，告訴 Antigravity 幫你加進網站（或自己加到 `scripts/build.py` 的 `<head>`）。
3. 驗證後到「Sitemap」提交 `sitemap.xml`。

### 2. Google AdSense（廣告收入）
1. 網站上線並有一些流量後（建議 2～4 週），到 <https://adsense.google.com> 申請。
2. 拿到發布商 ID（像 `ca-pub-1234567890123456`）後，填進 `config.json` 的 `adsense_client`：
   ```json
   "adsense_client": "ca-pub-1234567890123456"
   ```
3. 推送更新（或直接在 GitHub 網頁上編輯 `config.json`）。網站會自動加入廣告程式碼與 `ads.txt`。
4. 在 AdSense 後台開啟「自動廣告」，Google 會自動決定廣告位置。

> ⚠️ AdSense 審核對 `github.io` 子網域有時較嚴格。若被拒，建議花約 300～400 元/年買一個自己的網域（例如 Cloudflare、Namecheap），
> 填到 `config.json` 的 `custom_domain`，再到網域商設定 DNS（CNAME 指向 `你的帳號.github.io`），然後在 GitHub repo 的 Settings → Pages 填入同一個網域。

### 3.（選用）Google Analytics
到 <https://analytics.google.com> 建立資源，把 `G-XXXXXXX` 填進 `config.json` 的 `ga_id`。

## 三、日常維護

基本上**不用管**。只有在以下情況需要處理：

| 狀況 | 你會怎麼知道 | 怎麼處理 |
|---|---|---|
| 資料來源改版、抓不到資料 | GitHub 寄「workflow failed」email | 打開 Antigravity，請它「修好 tw-tools 的資料抓取」 |
| 想加新工具／新頁面 | — | 請 Antigravity 加，推送後自動上線 |

本機預覽：
```powershell
python scripts/build.py           # 抓資料並產生網站
python -m http.server 8000 -d dist  # 打開 http://localhost:8000
```

## 實際的期待

- 新網站通常要 **1～3 個月** 才會開始有穩定的 Google 搜尋流量。
- 台灣中文流量的 AdSense 收入大約是 **每 1,000 次瀏覽 30～150 元台幣**。每天 1,000 人次瀏覽 ≈ 每月 1,000～4,000 元。
- 發票對獎流量在**單數月 25 日開獎當天**會暴增，是這個站最主要的流量來源。
- 想增加收入：請 Antigravity 持續加新的工具頁面（每個頁面都是一個新的搜尋入口），例如：勞健保試算、所得稅試算、BMI、油耗計算、各縣市油價等。
