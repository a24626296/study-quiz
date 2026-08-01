// 這把金鑰是「限定只能從你的網站網域呼叫」的公開用 Gemini API Key,
// 不是你放在 GitHub Secrets 那把主要金鑰(那把是給雲端出題流程用的,不能公開)。
//
// 設定方式:
// 1. 去 Google AI Studio 或 Google Cloud Console 另外建立一把新的 Gemini API Key
// 2. 找到這把金鑰的「應用程式限制」設定,選擇「HTTP 轉介網址(網站)」
// 3. 只允許你的網站網域,例如:https://your-username.github.io/*
//    (本機測試的話可以額外加一條 http://localhost:*)
// 4. 把底下這行的空字串換成你剛建立的金鑰即可啟用 AI 問答功能
//
// 這個檔案只會在第一次沒有這個檔案時被建立,之後你自己編輯過的內容不會被覆蓋掉。

window.PUBLIC_GEMINI_API_KEY = "";
