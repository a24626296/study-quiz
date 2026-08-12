// edit-mode.js
// 讓「複習清單」跟「線上複習」頁面上標了 data-editkey 的文字,
// 可以直接雙擊之後修改文字內容(例如科目名稱打錯字、題目敘述不通順、排版怪怪的...)。
//
// 運作方式:
// - 雙擊有 data-editkey 屬性的元素 -> 變成可編輯,Enter 或點別的地方存檔,Esc 還原。
// - 修改的內容存在「這個瀏覽器」的 localStorage(依網址區分:複習清單一份、
//   每支影片的線上複習頁各一份),重新整理或下次打開都還會在。
// - 因為是存在瀏覽器本機,所以換一台裝置或清瀏覽器資料就會不見;
//   如果之後管線(pipeline)重新產生 index.html,只要影片 ID 沒變,
//   之前對「科目名稱」的修改一樣會自動套用回去。
// - viewer.html 的題目/選項是雙語(中文/英文)切換顯示,所以那些欄位的修改
//   會分開存中文版跟英文版,切換語言的時候各自保留。
(function () {
  'use strict';

  function pageKey() {
    if (location.pathname.indexOf('viewer.html') !== -1) {
      var params = new URLSearchParams(location.search);
      return 'viewer::' + (params.get('id') || 'unknown');
    }
    return 'index';
  }

  var STORAGE_KEY = 'studyquiz_text_edits::' + pageKey();

  function loadEdits() {
    try {
      return JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');
    } catch (e) {
      return {};
    }
  }

  function saveEdits(edits) {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(edits));
    } catch (e) {
      // 存不進去(例如無痕模式關掉 localStorage)就算了,至少畫面上還是改好的
    }
  }

  function currentLang() {
    var enBtn = document.getElementById('btn-en');
    return (enBtn && enBtn.classList.contains('active')) ? 'en' : 'zh';
  }

  // 雙語題目/選項(.hoverable 底下的 .main-text)修改時,要存回 dataset,
  // 這樣中英文切換(setLang)時才不會把手動修改的內容洗掉。
  function applyOne(el) {
    var key = el.dataset.editkey;
    if (!key) return;
    var edits = loadEdits();
    var hoverable = el.closest('.hoverable');
    if (hoverable) {
      var changed = false;
      ['zh', 'en'].forEach(function (lang) {
        var k = key + '::' + lang;
        if (Object.prototype.hasOwnProperty.call(edits, k)) {
          hoverable.dataset[lang] = edits[k];
          changed = true;
        }
      });
      el.textContent = hoverable.dataset[currentLang()] || el.textContent;
      if (changed) el.classList.add('qe-edited');
    } else if (Object.prototype.hasOwnProperty.call(edits, key)) {
      el.textContent = edits[key];
      el.classList.add('qe-edited');
    }
  }

  function applyEdits(root) {
    (root || document).querySelectorAll('[data-editkey]').forEach(applyOne);
  }

  // viewer.js 在非同步抓完題目資料、動態產生畫面之後,會呼叫這個函式,
  // 確保雙擊修改過的文字(尤其是 #subject 這種本來就存在、只是被
  // textContent 蓋掉的元素)能重新套用回去。
  window.qeApplyEdits = function () {
    applyEdits(document);
  };

  var activeCommit = null;

  function startEdit(el) {
    if (el.getAttribute('contenteditable') === 'true') return;
    var original = el.textContent;

    el.setAttribute('contenteditable', 'true');
    el.classList.add('qe-editing');
    el.focus();

    var sel = window.getSelection();
    var range = document.createRange();
    range.selectNodeContents(el);
    sel.removeAllRanges();
    sel.addRange(range);

    function cleanup() {
      el.removeAttribute('contenteditable');
      el.classList.remove('qe-editing');
      el.removeEventListener('blur', onBlur);
      el.removeEventListener('keydown', onKeydown);
      if (activeCommit === commit) activeCommit = null;
    }

    function commit() {
      var value = el.textContent;
      var key = el.dataset.editkey;
      var hoverable = el.closest('.hoverable');
      var edits = loadEdits();
      if (hoverable) {
        var lang = currentLang();
        edits[key + '::' + lang] = value;
        hoverable.dataset[lang] = value;
      } else {
        edits[key] = value;
      }
      saveEdits(edits);
      if (value !== original) el.classList.add('qe-edited');
      cleanup();
    }

    function revert() {
      el.textContent = original;
      cleanup();
    }

    function onBlur() {
      commit();
    }
    function onKeydown(ev) {
      if (ev.key === 'Enter' && !ev.shiftKey) {
        ev.preventDefault();
        el.blur();
      } else if (ev.key === 'Escape') {
        ev.preventDefault();
        revert();
      }
    }

    el.addEventListener('blur', onBlur);
    el.addEventListener('keydown', onKeydown);

    // 記住目前正在編輯、還沒存檔的這一格,萬一使用者切分頁/關視窗時
    // 還沒點開別的地方,靠這個把還沒存到的內容強制存起來。
    activeCommit = commit;
  }

  // 分頁被切到背景、或視窗/分頁要關閉之前,強制把還在編輯中、
  // 尚未點開別處存檔的文字先存起來,避免最後一筆修改遺失。
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'hidden' && activeCommit) activeCommit();
  });
  window.addEventListener('beforeunload', function () {
    if (activeCommit) activeCommit();
  });
  window.addEventListener('pagehide', function () {
    if (activeCommit) activeCommit();
  });

  // 複習清單頁面裡,科目名稱/日期文字是包在整張卡片的連結(<a>)裡面,
  // 單純用 dblclick 監聽會來不及擋下第一下點擊觸發的頁面跳轉。
  // 所以這裡先攔住 click,延遲一小段時間才真的跳轉;如果這段時間內
  // 偵測到第二次點擊(dblclick),就取消跳轉、改成進入編輯模式。
  document.addEventListener('click', function (e) {
    var el = e.target.closest('[data-editkey]');
    if (!el) return;
    var link = el.closest('a');
    if (!link) return;
    e.preventDefault();
    if (link._qeClickTimer) return;
    link._qeClickTimer = setTimeout(function () {
      link._qeClickTimer = null;
      if (!el.isContentEditable) {
        window.location.href = link.href;
      }
    }, 280);
  });

  document.addEventListener('dblclick', function (e) {
    var el = e.target.closest('[data-editkey]');
    if (!el) return;
    var link = el.closest('a');
    if (link && link._qeClickTimer) {
      clearTimeout(link._qeClickTimer);
      link._qeClickTimer = null;
    }
    e.preventDefault();
    startEdit(el);
  });

  function init() {
    applyEdits(document);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  // viewer.html 的題目清單是抓完 JSON 之後才用 JS 動態建立 DOM,
  // 用 MutationObserver 偵測新增的節點,自動把已存的修改套用上去。
  var observer = new MutationObserver(function (mutations) {
    mutations.forEach(function (m) {
      m.addedNodes.forEach(function (node) {
        if (node.nodeType !== 1) return;
        if (node.matches && node.matches('[data-editkey]')) applyOne(node);
        if (node.querySelectorAll) applyEdits(node);
      });
    });
  });
  observer.observe(document.body, { childList: true, subtree: true });

  // 清除這個頁面所有手動修改過的文字(還原成系統原本產生的內容)
  window.clearTextEdits = function () {
    if (confirm('確定要清除這個頁面所有手動修改過的文字嗎?(會還原成系統原本產生的內容,需要重新整理頁面)')) {
      try { localStorage.removeItem(STORAGE_KEY); } catch (e) {}
      location.reload();
    }
  };
})();
