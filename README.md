<div align="center">
  <h1 id="逢甲大學自動搶課機器人">
    <a href="https://github.com/HappyGroupHub/FCU-AutoClass" target="_blank">逢甲大學 - 自動搶課機器人</a>
  </h1>

[![Total Downloads](https://img.shields.io/github/downloads/HappyGroupHub/FCU-AutoClass/total?style=for-the-badge
)](https://github.com/HappyGroupHub/FCU-AutoClass/releases)
[![Current Version](https://img.shields.io/github/v/release/HappyGroupHub/FCU-AutoClass?style=for-the-badge
)](https://github.com/HappyGroupHub/FCU-AutoClass/releases)
[![LICENSE](https://img.shields.io/github/license/HappyGroupHub/FCU-AutoClass?style=for-the-badge
)](https://github.com/HappyGroupHub/FCU-AutoClass/blob/master/LICENSE)

</div>


## 介紹

### [ 2024/8/28 - 實測加選成功! ]
這個程式可以協助你在逢甲大學的選課系統中，無時無刻追蹤你想要的課程，當有任何人退選時，自動幫你搶課加選。

目前支援: 多課程加選、多課程平行查詢 (HTTP 引擎)、無視窗加選、出錯自動重啟、Discord 通知

![image](./readme_imgs/demo01.gif)
<img src="./readme_imgs/success.jpg" width="600" height="338" />

## 使用方法

1. 從 [這裡](https://github.com/HappyGroupHub/FCU-AutoClass/releases) 下載最新的版本的程式
2. 解壓縮檔案後會生成一個資料夾
3. 打開 `config.yml` 並完成填寫裡面的資料 (詳細說明請看下面介紹)
4. 開啟 `run.bat` 後就完成了!

## 在 macOS 上使用

macOS 不提供打包好的執行檔，請從原始碼執行，Apple Silicon 與 Intel 皆支援。

### 前置需求

* [Google Chrome](https://www.google.com/chrome/)
* Python 3.11 (從 [python.org](https://www.python.org/downloads/) 下載安裝檔，或使用 `brew install python@3.11`)
* macOS 13 (Ventura) 或以上

### 安裝步驟

1. 下載程式碼，建議使用 `git clone`：
   ```
   git clone https://github.com/0BlueYan0/FCU-AutoClass.git
   ```
   或是到 GitHub 頁面點選 `Code > Download ZIP` 下載原始碼
2. 在終端機中建立虛擬環境並安裝套件：
   ```
   cd FCU-AutoClass
   python3.11 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```
3. 對 `run.command` 點兩下執行，第一次執行會自動產生 `config.yml`
4. 打開 `config.yml` 並完成填寫裡面的資料 (詳細說明請看下面介紹)
5. 再次開啟 `run.command` 後就完成了! (也可以直接在終端機執行 `.venv/bin/python app.py`)

### macOS 安全性提示

* 使用 `git clone` 取得的檔案可以直接執行；但如果是**從瀏覽器下載的 ZIP**，第一次打開 `run.command`
  會被系統阻擋。解決方式：對 `run.command` 按右鍵選「打開」，或在終端機執行
  `xattr -d com.apple.quarantine run.command`。萬用替代方案：直接在終端機輸入 `bash run.command`
* 如果出現「沒有權限」，請在終端機執行 `chmod +x run.command`

## 關於 config.yml

```yaml
# ++--------------------------------++
# | FCU-AutoClass                    |
# | Made by LD (MIT License)         |
# ++--------------------------------++

# FCU Account
username: ''
password: ''

# Class to join
# If you have more than one class to join, please separate them with space.
# Example: class_id: '0001 0002'
# The less class_id you have, the more rate you can get the class you want.
class_id: ''

# Headless mode
# If you want to run this script in headless mode, please set this to true.
headless: false

# Engine
# http: after login, query/add every class in parallel with raw HTTP requests
#       (one thread per class, fastest, recommended).
# selenium: the old browser-driven loop, one class after another (fallback).
engine: 'http'

# Query interval (seconds) between two seat queries of the SAME class.
# Lower = faster, but hits the school server harder and may get your
# account/IP blocked. 0.5 is the recommended minimum; values below 0.3 are
# raised to 0.3.
query_interval: 0.5

# Discord notification
# Paste your Discord webhook URL here to get notified when a class is joined.
# Leave it empty ('') to disable notifications.
discord_webhook_url: ''
```

* `username`: 請填入你的學號(帳號)
* `password`: 請填入你的選課系統密碼
* `class_id`: 請填入你想要加選的課程代碼，如果有多個課程請用空格隔開 (例如: `'0001 0002'`)
* `headless`: 如果你想要讓程式在背景執行，請填入 `true`，否則請填入 `false`
* `engine`: (選填) `http` (預設) 登入後改用 HTTP 請求直接查名額/加選，每門課各自一條執行緒平行進行，速度最快；
  `selenium` 則沿用舊的瀏覽器逐課輪流方式 (備用)
* `query_interval`: (選填) 同一門課兩次查名額之間的間隔秒數，預設 `0.5`。數字越小越快，但對學校選課系統的負擔越大，
  有被封鎖帳號/IP 的風險；低於 `0.3` 會自動調成 `0.3`
* `discord_webhook_url`: (選填) 填入 Discord webhook 網址後，加選成功、全部完成、程式因錯誤停止時都會傳訊息到該頻道，
  程式啟動時也會先傳一則測試訊息讓你確認網址有效。留空 `''` 則不會發送任何通知。
  取得方式：Discord 頻道 → 編輯頻道 → 整合 → Webhook → 新 Webhook → 複製 Webhook 網址

## HTTP 引擎與平行查詢

預設的 `http` 引擎登入後只把瀏覽器留在背景，之後所有「查名額 → 加選」都改用 HTTP 請求直接送 ASP.NET postback，
不再操作網頁；每門課各自一條執行緒同時進行。舊版每門課每輪要 2–4 秒而且逐課輪流，現在每門課每輪只剩一次伺服器往返。

* 所有執行緒共用同一個登入 session，選課系統伺服器端會把同一個 session 的請求排隊處理，
  所以 N 門課時每門課的實際間隔大約是 N × 伺服器回應時間 (約 0.2–0.5 秒)，仍遠快於舊版。
* 第一次使用建議先用 `--dry-run` 跑一兩分鐘確認正常：只查名額並記錄，不會送出加選
  ```
  .venv\Scripts\python app.py --dry-run
  ```
* 選課系統頁面改版、或 log 出現「找不到課程列/按鈕」時，執行 `discover_form.py` (只發 3 個請求、不會加選)，
  檢查 `logs/discovery-*/` 的輸出 (內含學號姓名，分享前請先去除) 後再回報：
  ```
  set PYTHONUTF8=1
  .venv\Scripts\python discover_form.py
  ```
* Selenium 抓到的 chromedriver 版本和 Chrome 不合時，到 [Chrome for Testing](https://googlechromelabs.github.io/chrome-for-testing/)
  下載對應版本的 chromedriver，把 `chromedriver.exe` 放到專案的 `drivers\` 資料夾即可；也可用環境變數 `CHROMEDRIVER` 指定路徑。
* 想回到舊的瀏覽器逐課輪流方式，把 `config.yml` 的 `engine` 改成 `selenium`。

## 遇到任何問題嗎?

如果你在使用上面有遇到任何問題或bug，甚至是有建議想提出，請到 [這裡](https://github.com/HappyGroupHub/FCU-AutoClass/issues)
提出你的想法!

程式遇到 Bug 要提交問題的時候，歡迎附上資料夾內 `FCU-AutoCLass > logs > xxx.txt` 最新的紀錄檔，方便我做除錯。

## 版權

此專案的版權規範採用 **MIT License** - 至 [LICENSE](LICENSE) 查看更多相關聲明